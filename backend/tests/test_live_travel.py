"""Bounded provider quotes bind to a group; searches never invent inventory."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.live_travel import LiveTravel, LiveTravelSearch, TravelQuoteImport
from app.models import PassengerGroup
from app.store import DomainError, Store


def quote(kind="flight", **changes):
    departure = (datetime.now(ZoneInfo("Europe/Bucharest")) + timedelta(days=1)).replace(hour=10, minute=20, second=0, microsecond=0)
    arrival = departure + timedelta(hours=3)
    segments = [{"departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(),
                 "origin": "CRA", "destination": "OTP"}]
    if kind == "bus":
        segments[0].update(departure_station={"id": "station-a", "name": "Craiova coach terminal", "latitude": 44.32, "longitude": 23.81},
                           arrival_station={"id": "station-b", "name": "Bucharest coach terminal", "latitude": 44.45, "longitude": 26.10})
    return {"id": "offer-real", "provider_offer_id": "offer-real", "provider": "duffel" if kind == "flight" else "distribusion",
            "mode": "live", "origin": "CRA", "destination": "OTP", "passengers": 2,
            "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(), "currency": "EUR", "total_price": "123.45",
            "quoted_at": datetime.now(timezone.utc).isoformat(), "expires_at": (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
            "accessibility_confirmed": False, "segments": segments, **changes}


class Provider:
    def __init__(self):
        self.calls = []
        self.state = "ready"
        self.item = quote()

    async def search(self, request):
        self.calls.append(request)
        return {"state": self.state, "source": "Duffel" if request.kind == "flight" else "Distribusion",
                "message": None if self.state == "ready" else "Account is unavailable", "items": [copy.deepcopy(self.item)] if self.state == "ready" else []}


@pytest.fixture
def live(tmp_path):
    store = Store(str(tmp_path / "travel.sqlite3"), operation_mode="live")
    def groups(world):
        for group_id in ("G-ONE", "G-TWO"):
            world.groups.append(PassengerGroup(id=group_id, name="Explicit test group", size=2, members=["Test 1", "Test 2"],
                origin="CRA", destination="OTP", deadline=7*1440, ready_at=world.sim_time,
                accessible=False, budget=500, original_flight_id="SOURCE-FLIGHT", token="test-token-"+group_id,
                source="synthetic", adult_count=2))
        world.budget = 1000
    store.mutate("test-fixture", {}, store.read().version, groups)
    provider = Provider()
    return SimpleNamespace(store=store, provider=provider, travel=LiveTravel(store, provider))


def search(kind="flight", **changes):
    return LiveTravelSearch(**({"group_id": "G-ONE", "kind": kind, "origin_code": "CRA", "destination_code": "OTP",
        "departure_date": (datetime.now(ZoneInfo("Europe/Bucharest"))+timedelta(days=1)).date()} | changes))


def select(live, result, **changes):
    return TravelQuoteImport(**({"expected_version": live.store.read().version, "idempotency_key": "quote-import-123",
        "search_id": result["search_id"], "offer_id": "offer-real", "group_id": "G-ONE"} | changes))


@pytest.mark.asyncio
async def test_search_is_read_only_and_import_preserves_exact_group_quote_then_replays(live):
    before = live.store.read().version
    result = await live.travel.search(search())
    assert live.store.read().version == before and live.store.read().resources == []
    assert live.provider.calls[0].adults == 2
    request = select(live, result)
    imported = live.travel.import_quote(request)
    resource = imported["world"]["resources"][0]
    assert resource["source"] == "duffel" and resource["capacity_scope"] == "quoted_request"
    assert resource["capacity"] == 2 and resource["eligible_group_ids"] == ["G-ONE"]
    assert resource["quote_total_cost"] == 123.45 and resource["cost_per_person"] == 0
    assert resource["external_offer_id"] == "offer-real" and resource["booking_status"] == "unbooked"
    assert resource["provenance"]["total_price"] == "123.45"
    assert live.travel.import_quote(request)["replayed"]
    assert len(live.store.read().resources) == 1 and len(live.provider.calls) == 1


@pytest.mark.asyncio
async def test_search_uses_only_selected_groups_exact_ready_and_deadline(live):
    def distinct_windows(world):
        epoch=datetime.fromisoformat(world.epoch)
        departure=datetime.fromisoformat(quote()["departure_at"])
        start=int((departure-epoch).total_seconds()/60)
        world.groups[0].ready_at,world.groups[0].deadline=start-60,start+240
        world.groups[1].ready_at,world.groups[1].deadline=start+30,start+360
    live.store.mutate("time-windows",{},live.store.read().version,distinct_windows)
    before=live.store.read()
    for group in before.groups:
        await live.travel.search(search(group_id=group.id))
    epoch=datetime.fromisoformat(before.epoch)
    for group,request in zip(before.groups,live.provider.calls):
        assert request.earliest_departure_at==epoch+timedelta(minutes=group.ready_at)
        assert request.latest_arrival_at==epoch+timedelta(minutes=group.deadline)
    assert live.store.read().model_dump()==before.model_dump()


@pytest.mark.asyncio
async def test_search_never_uses_readiness_in_the_past_or_raises_a_passed_deadline(live):
    started=datetime.now(timezone.utc)
    await live.travel.search(search())
    assert live.provider.calls[0].earliest_departure_at>=started
    def elapsed(world):
        world.groups[0].ready_at=world.groups[0].deadline=0
    live.store.mutate("deadline-elapsed",{},live.store.read().version,elapsed)
    with pytest.raises(DomainError) as failure:
        await live.travel.search(search())
    assert failure.value.status==422 and len(live.provider.calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("field",["ready_at","deadline"])
async def test_cached_search_is_invalidated_when_its_group_time_window_changes(live,field):
    result=await live.travel.search(search())
    def update(world):
        setattr(world.groups[0],field,getattr(world.groups[0],field)+1)
    live.store.mutate("window-changed",{},live.store.read().version,update)
    with pytest.raises(DomainError) as failure:
        live.travel.import_quote(select(live,result))
    assert failure.value.status==409 and not live.store.read().resources


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["not_configured", "sandbox", "error"])
async def test_unavailable_provider_keeps_state_explicit_and_creates_no_search_or_resource(live, state):
    live.provider.state = state
    result = await live.travel.search(search())
    assert result["state"] == state and "search_id" not in result
    assert live.travel.searches == {} and live.store.read().resources == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"origin_code": "UNKNOWN"}, {"destination_code": "CRA"}, {"group_id": "MISSING"}])
async def test_invalid_group_and_route_never_call_provider(live, changes):
    with pytest.raises(DomainError):
        await live.travel.search(search(**changes))
    assert live.provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ages", [None, [22], [17, 24], [0, 24], [121, 24]])
async def test_bus_ages_must_cover_declared_group(live, ages):
    with pytest.raises((DomainError, ValidationError)):
        await live.travel.search(search(kind="bus", passenger_ages=ages))
    assert live.provider.calls == []


@pytest.mark.parametrize("ages", [[True, True], ["20", "21"], [20.0, 21.0]])
def test_bus_input_does_not_coerce_noninteger_ages(ages):
    with pytest.raises(ValidationError):
        search(kind="bus", passenger_ages=ages)


@pytest.mark.asyncio
async def test_quote_is_bound_to_original_group_and_current_size(live):
    result = await live.travel.search(search())
    with pytest.raises(DomainError):
        live.travel.import_quote(select(live, result, group_id="G-TWO"))
    live.store.mutate("change-size", {}, live.store.read().version, lambda world: setattr(world.groups[0], "size", 3))
    with pytest.raises(DomainError):
        live.travel.import_quote(select(live, result))
    assert live.store.read().resources == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"mode": "sandbox"}, {"passengers": 1}, {"currency": "USD"}, {"total_price": "NaN"},
    {"total_price": "not-a-number"}, {"origin": "OTP"}, {"provider_offer_id": ""},
    {"expires_at": "invalid"}, {"quoted_at": "invalid"},
    {"expires_at": (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()},
    {"quoted_at": (datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()},
])
async def test_invalid_or_stale_cached_quote_never_mutates_world(live, changes):
    live.provider.item = quote(**changes)
    result = await live.travel.search(search())
    before = live.store.read().version
    with pytest.raises(DomainError):
        live.travel.import_quote(select(live, result))
    assert live.store.read().version == before and live.store.read().resources == []


@pytest.mark.asyncio
async def test_search_expiry_and_reset_reject_old_quote(live):
    result = await live.travel.search(search())
    cached = live.travel.searches[result["search_id"]]
    cached["until"] = 0
    with pytest.raises(DomainError):
        live.travel.import_quote(select(live, result))
    result = await live.travel.search(search())
    live.store.mutate("replace-world", {}, live.store.read().version, lambda world: setattr(world, "scenario_id", "another-operation"))
    with pytest.raises(DomainError):
        live.travel.import_quote(select(live, result))
    assert live.store.read().resources == []


@pytest.mark.asyncio
async def test_bus_import_preserves_real_station_endpoints_without_airport_teleport(live):
    live.provider.item = quote(kind="bus")
    result = await live.travel.search(search(kind="bus", passenger_ages=[23, 45]))
    imported = live.travel.import_quote(select(live, result))
    resource = imported["world"]["resources"][0]
    assert resource["kind"] == "bus" and resource["source"] == "distribusion"
    assert resource["origin"].startswith("ST-") and resource["destination"].startswith("ST-")
    nodes = {node["id"]: node for node in imported["world"]["airports"]}
    assert nodes[resource["origin"]]["kind"] == "station" and nodes[resource["origin"]]["lat"] == 44.32
    assert nodes[resource["destination"]]["name"] == "Bucharest coach terminal"
    assert resource["origin"] != "CRA" and resource["destination"] != "OTP"


@pytest.mark.asyncio
async def test_actual_airport_closure_blocks_flight_quote_import(live):
    result = await live.travel.search(search())
    def close(world):
        world.airports[0].operational_status = "closed"
        world.airports[0].available = False
    live.store.mutate("confirmed-closure", {}, live.store.read().version, close)
    with pytest.raises(DomainError):
        live.travel.import_quote(select(live, result))
    assert live.store.read().resources == []
