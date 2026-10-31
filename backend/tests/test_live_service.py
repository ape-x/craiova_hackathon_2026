"""Live operational mutations use source data and never call booking APIs."""
import asyncio
import copy
import csv
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.live_data import MANIFEST_COLUMNS, ManifestImport, ManualResourceInput, PassengerGenerationInput
from app.live_service import ExternalConfirmation, HotelQuoteImport, LiveOperations
from app.models import AcceptRequest, Airport, Allocation, Itinerary, Leg, Offer, Reservation, Resource
from app.providers.base import ProviderError
from app.service import RecoveryService
from app.solver import resource_cost, units, validate_allocations
from app.store import DomainError, Store


class Provider:
    hotel_offer_provider = "liteapi"

    def __init__(self):
        self.calls = 0
        self.after_request = None
        self.error = None

    async def offers(self, airport, query):
        self.calls += 1
        if self.after_request:
            self.after_request()
        if self.error:
            raise self.error
        return [{"id": "hotel-real-id", "name": "Supplier hotel", "provider": "liteapi",
                 "latitude": 44.31, "longitude": 23.80,
                 "provider_offer_id": f"quoted-offer-{self.calls}", "currency": "EUR", "total_price": "176.45",
                 "checkin": query.checkin.isoformat(), "checkout": query.checkout.isoformat(),
                 "adults": query.adults, "rooms": query.rooms, "quoted_at": datetime.now(timezone.utc).isoformat(),
                 "pricing": {"booking_total": {"amount": "176.45", "currency": "EUR"}, "excluded_fee_totals": [],
                             "mandatory_total": {"amount": "176.45", "currency": "EUR"}, "status": "complete",
                             "unresolved_reasons": [], "payment_timing": "unconfirmed"},
                 "expires_at": None, "room_rates": [{"room_name": "Supplier room"}]}]


@pytest.fixture
def ops(tmp_path):
    settings = Settings(_env_file=None, operation_mode="live", database_path=str(tmp_path / "live.sqlite3"))
    store = Store(settings.database_path, operation_mode="live")
    recovery = RecoveryService(settings, store)
    return LiveOperations(store, recovery, SimpleNamespace(provider=Provider()))


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def evidence():
    return {"source_name": "Operator statement", "source_reference": "dispatch-123", "confirmed_by": "Duty operator", "observed_at": now()}


def manifest_request(ops, *, group_id="REAL-GROUP", passenger_prefix="ACTUAL", **changes):
    anchor = now()
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=MANIFEST_COLUMNS)
    writer.writeheader()
    for i in range(2):
        writer.writerow({"passenger_id": f"{passenger_prefix}-{i}", "group_id": group_id, "name": f"Imported person {i}",
            "origin": "CRA", "destination": "OTP", "original_flight_id": "ACTUAL-FLIGHT",
            "ready_at": (anchor+timedelta(minutes=30)).isoformat(), "deadline": (anchor+timedelta(days=3)).isoformat(),
            "group_budget_eur": "500.00", "accessible": "false", "priority": "2", "is_adult": "true"})
    return ManifestImport(**({"expected_version": ops.store.read().version, "idempotency_key": "manifest-import-123", "csv_text": text.getvalue(), **evidence()} | changes))


def test_manifest_fixture_keeps_shared_group_dates_when_clock_advances(ops, monkeypatch):
    anchor = now()
    ticks = iter(anchor + timedelta(seconds=step) for step in range(10))
    monkeypatch.setitem(manifest_request.__globals__, "now", lambda: next(ticks))
    request = manifest_request(ops)
    records = list(csv.DictReader(io.StringIO(request.csv_text)))
    assert len({row["ready_at"] for row in records}) == 1
    assert len({row["deadline"] for row in records}) == 1
    ops.manifest(request)
    assert ops.store.read().groups[0].size == 2


def resource_request(ops, **changes):
    return ManualResourceInput(**({"expected_version": ops.store.read().version, "idempotency_key": "resource-import-123",
        "id": "FLIGHT-REAL", "kind": "flight", "name": "Confirmed operator service", "origin": "CRA", "destination": "OTP",
        "departure_at": now()+timedelta(hours=2), "arrival_at": now()+timedelta(hours=4), "capacity": 4,
        "unit_price_eur": "40.00", "accessible": True, "vehicle_id": None,
        "valid_until": now()+timedelta(hours=1), **evidence()} | changes))


def hotel_request(ops, **changes):
    local = now().astimezone(ZoneInfo("Europe/Bucharest"))
    checkin = (local+timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    checkout = (local+timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    return HotelQuoteImport(**({"expected_version": ops.store.read().version, "idempotency_key": "hotel-import-123",
        "group_id": "REAL-GROUP", "airport_code": "CRA", "checkin": checkin.date(), "checkout": checkout.date(),
        "rooms": 1, "checkin_at": checkin, "checkout_at": checkout, "stay_times_confirmed": True} | changes))


def test_manifest_and_resource_mutations_are_atomic_idempotent_and_preserve_sources(ops):
    request = manifest_request(ops)
    first = ops.manifest(request)
    assert len(first["world"]["groups"]) == 1 and first["world"]["budget"] == 500
    assert first["world"]["groups"][0]["source"] == "manifest_import"
    assert ops.manifest(request)["replayed"]
    assert len(ops.store.read().groups) == 1
    result = ops.resource(resource_request(ops))
    assert result["world"]["resources"][0]["source"] == "operator_confirmed"
    assert result["world"]["resources"][0]["booking_status"] == "unbooked"
    assert all(airport["available"] for airport in result["world"]["airports"])
    assert all(airport["operational_status"] == "unknown" for airport in result["world"]["airports"])
    before = ops.store.read().model_dump_json()
    with pytest.raises(DomainError):
        ops.manifest(manifest_request(ops, idempotency_key="duplicate-manifest"))
    assert ops.store.read().model_dump_json() == before


def test_explicit_test_passenger_generation_never_generates_resources(ops):
    request = PassengerGenerationInput(expected_version=ops.store.read().version, idempotency_key="generate-passengers",
        passengers=5, group_size=2, origin="CRA", destination="OTP", original_flight_id="ACTUAL-FLIGHT",
        ready_at=now()+timedelta(minutes=30), deadline=now()+timedelta(days=1), group_budget_eur="100",
        accessible=False, priority=1, is_adult=True)
    result = ops.generate(request)
    assert [group["size"] for group in result["world"]["groups"]] == [2,2,1]
    assert all(group["source"] == "synthetic" for group in result["world"]["groups"])
    assert result["world"]["resources"] == [] and result["world"]["budget"] == 300


@pytest.mark.asyncio
async def test_hotel_import_is_bound_to_group_exact_amount_and_actual_expiry_only(ops):
    ops.manifest(manifest_request(ops))
    request = hotel_request(ops)
    result = await ops.hotels(request)
    resource = result["world"]["resources"][0]
    assert resource["source"] == "Nuitee / LiteAPI"
    assert resource["eligible_group_ids"] == ["REAL-GROUP"]
    assert resource["quote_total_cost"] == 176.45 and resource["quote_rooms"] == resource["capacity"] == 1
    assert resource["budget_total_cost"] == 176.45
    assert resource["valid_until"] is None and resource["requires_revalidation"] is True
    assert resource["external_offer_id"] == "quoted-offer-1"
    assert resource["provenance"]["room_rates"][0]["room_name"] == "Supplier room"
    assert resource["accessible"] is False
    node = next(item for item in result["world"]["airports"] if item["id"] == resource["origin"])
    assert node["kind"] == "hotel" and node["diversion_eligible"] is False
    assert node["lat"] == 44.31 and node["lon"] == 23.80
    assert resource["origin"] == resource["destination"] != "CRA"
    assert resource["provenance"]["property_location"]["timezone"] == "Europe/Bucharest"
    assert resource["provenance"]["ground_connections_verified"] is False
    assert (await ops.hotels(request))["replayed"] and ops.real_data.provider.calls == 1
    replacement = await ops.hotels(hotel_request(ops, idempotency_key="hotel-refresh-123"))
    assert len(replacement["world"]["resources"]) == 1
    assert replacement["world"]["resources"][0]["external_offer_id"] == "quoted-offer-2"


@pytest.mark.asyncio
@pytest.mark.parametrize("other_status", ["proposed", "accepted", "confirmed"])
async def test_hotel_refresh_preserves_other_groups_complete_journey(ops, other_status):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    await ops.hotels(hotel_request(ops))

    def attach_journeys(world):
        selected = world.groups[0]
        selected.status = "offered"
        other = selected.model_copy(deep=True, update={"id": "OTHER-GROUP", "token": selected.token + "-other",
            "status": "confirmed" if other_status == "confirmed" else "offered"})
        world.groups.append(other)
        world.budget += other.budget
        hotel = next(resource for resource in world.resources if resource.kind == "hotel")
        flight = next(resource for resource in world.resources if resource.kind == "flight")
        flight.departure, flight.arrival = hotel.arrival + 90, hotel.arrival + 210
        expiry = (now() + timedelta(minutes=10)).isoformat()
        transfers = [Resource(id=identity, name=identity, kind="bus", origin=origin, destination=destination,
            departure=departure, arrival=arrival, capacity=2, cost_per_person=0, quote_total_cost=20, source="fixture_supplier",
            valid_until=expiry, eligible_group_ids=[selected.id]) for identity, origin, destination, departure, arrival in (
                ("HOTEL-OUT", "CRA", hotel.origin, hotel.departure - 60, hotel.departure - 20),
                ("HOTEL-BACK", hotel.origin, "CRA", hotel.arrival + 10, hotel.arrival + 30))]
        world.resources.extend(transfers)
        for passenger, itinerary_resources, status in (
            (selected, [transfers[0], hotel, transfers[1], flight], "proposed"),
            (other, [flight], other_status),
        ):
            legs = [Leg(resource_id=resource.id, kind=resource.kind, origin=resource.origin, destination=resource.destination,
                departure=resource.departure, arrival=resource.arrival, cost=resource_cost(resource, passenger)) for resource in itinerary_resources]
            offer = Offer(id="O-" + passenger.id, group_id=passenger.id, status=status, expires_at=expiry,
                created_version=world.version + 1, itinerary=Itinerary(legs=legs, arrival=legs[-1].arrival, total_cost=sum(leg.cost for leg in legs)),
                confirmed_at=now().isoformat() if status == "confirmed" else None,
                external_bookings=[{"confirmation_reference": "FIXTURE-OTHER-BOOKING"}] if status == "confirmed" else [])
            world.offers.append(offer)
            world.reservations.extend(Reservation(id=f"R-{passenger.id}-{resource.id}", offer_id=offer.id,
                group_id=passenger.id, resource_id=resource.id, quantity=units(resource, passenger), status=status,
                expires_at=expiry) for resource in itinerary_resources)
        validation = validate_allocations(world, [Allocation(group_id=offer.group_id, itinerary=offer.itinerary)
            for offer in world.offers if offer.status != "confirmed"])
        assert validation["valid"], validation

    ops.store.mutate("attach-two-hotel-journeys", {}, ops.store.read().version, attach_journeys)
    before = ops.store.read()
    protected = [item.model_dump_json() for collection in (before.groups, before.offers, before.reservations)
        for item in collection if getattr(item, "group_id", item.id) == "OTHER-GROUP"]
    old_hotel = next(resource for resource in before.resources if resource.kind == "hotel")
    request = hotel_request(ops, idempotency_key="scoped-hotel-refresh")
    result = await ops.hotels(request)
    after = ops.store.read()
    assert protected == [item.model_dump_json() for collection in (after.groups, after.offers, after.reservations)
        for item in collection if getattr(item, "group_id", item.id) == "OTHER-GROUP"]
    assert after.version == before.version + 1 and ops.real_data.provider.calls == 2
    assert next(offer for offer in after.offers if offer.group_id == "REAL-GROUP").status == "invalidated"
    assert all(item.status == "released" for item in after.reservations if item.group_id == "REAL-GROUP")
    assert next(group for group in after.groups if group.id == "REAL-GROUP").status == "waiting"
    assert old_hotel.id not in {resource.id for resource in after.resources}
    assert {resource.id: resource.model_dump_json() for resource in before.resources if resource.kind != "hotel"} == {
        resource.id: resource.model_dump_json() for resource in after.resources if resource.kind != "hotel"}
    assert (await ops.hotels(request))["replayed"] and ops.real_data.provider.calls == 2
    assert result["applied"] is True


def alter_hotel_quotes(ops, change):
    original = ops.real_data.provider.offers
    async def offers(airport, query):
        quotes = await original(airport, query)
        change(quotes)
        return quotes
    ops.real_data.provider.offers = offers


@pytest.mark.asyncio
async def test_hotel_budget_includes_extra_fees_while_supplier_quote_stays_exact(ops):
    ops.manifest(manifest_request(ops))
    def change(quotes):
        quote = quotes[0]
        quote["total_price"] = "176.4501"
        quote["pricing"].update(
            booking_total={"amount": "176.4501", "currency": "EUR"},
            excluded_fee_totals=[{"amount": "5.0001", "currency": "EUR"}],
            mandatory_total={"amount": "181.4502", "currency": "EUR"},
        )
    alter_hotel_quotes(ops, change)
    result = await ops.hotels(hotel_request(ops))
    resource = ops.store.read().resources[0]
    assert resource.quote_total_cost == 176.4501
    assert resource.budget_total_cost == 181.46
    assert resource_cost(resource, ops.store.read().groups[0]) == 181.46
    assert resource.provenance["total_price"] == "176.4501"
    assert resource.provenance["pricing"]["mandatory_total"]["amount"] == "181.4502"
    assert result["skipped_incomplete_pricing"] == 0
    assert resource.booking_status == "unbooked"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["fees_not_disclosed", "excluded_fee_amount_missing", "unsupported_budget_currency"])
async def test_incomplete_hotel_pricing_cannot_change_inventory_or_create_budgeted_options(ops, reason):
    ops.manifest(manifest_request(ops))
    def change(quotes):
        quotes[0]["pricing"].update(status="incomplete", mandatory_total=None, unresolved_reasons=[reason])
    alter_hotel_quotes(ops, change)
    before = ops.store.read().model_dump_json()
    with pytest.raises(DomainError, match="unresolved mandatory costs") as error:
        await ops.hotels(hotel_request(ops))
    assert error.value.status == 422
    assert ops.store.read().model_dump_json() == before


@pytest.mark.asyncio
async def test_hotel_import_reports_skipped_incomplete_quotes_without_losing_complete_options(ops):
    ops.manifest(manifest_request(ops))
    def change(quotes):
        incomplete = copy.deepcopy(quotes[0])
        incomplete["id"] = "hotel-with-unresolved-fees"
        incomplete["provider_offer_id"] = "opaque-other-offer"
        incomplete["pricing"].update(status="incomplete", mandatory_total=None, unresolved_reasons=["fees_not_disclosed"])
        quotes.insert(0, incomplete)
    alter_hotel_quotes(ops, change)
    result = await ops.hotels(hotel_request(ops))
    assert len(result["world"]["resources"]) == 1
    assert result["world"]["resources"][0]["provenance"]["id"] == "hotel-real-id"
    assert result["skipped_incomplete_pricing"] == 1
    assert "Skipped 1 quotes with unresolved mandatory costs" in result["explanation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda quote: quote.pop("pricing"),
    lambda quote: quote["pricing"].update(mandatory_total={"amount": "5.00", "currency": "EUR"}),
    lambda quote: quote["pricing"].update(mandatory_total={"amount": "176.45", "currency": "RON"}),
    lambda quote: quote["pricing"].update(status="incomplete"),
])
async def test_hotel_import_rejects_missing_or_inconsistent_normalized_pricing_atomically(ops, change):
    ops.manifest(manifest_request(ops))
    alter_hotel_quotes(ops, lambda quotes: change(quotes[0]))
    before = ops.store.read().model_dump_json()
    with pytest.raises(DomainError) as error:
        await ops.hotels(hotel_request(ops))
    assert error.value.status == 502
    assert ops.store.read().model_dump_json() == before


@pytest.mark.asyncio
async def test_hotel_fetch_cannot_commit_against_changed_world(ops):
    ops.manifest(manifest_request(ops))
    request = hotel_request(ops)
    ops.real_data.provider.after_request = lambda: ops.store.mutate("concurrent-update", {}, request.expected_version, lambda world: {})
    with pytest.raises(DomainError) as error:
        await ops.hotels(request)
    assert error.value.status == 409 and ops.store.read().resources == []


@pytest.mark.asyncio
async def test_hotel_provider_failure_does_not_mutate_or_fall_back(ops):
    ops.manifest(manifest_request(ops))
    before = ops.store.read().version
    ops.real_data.provider.error = ProviderError("Provider unavailable", code="http_503")
    with pytest.raises(ProviderError):
        await ops.hotels(hotel_request(ops))
    assert ops.store.read().version == before and ops.store.read().resources == []


def test_hotel_stay_times_allow_policy_validation_but_require_matching_dates(ops):
    ops.manifest(manifest_request(ops))
    assert hotel_request(ops, stay_times_confirmed=False).stay_times_confirmed is False
    with pytest.raises(ValidationError):
        hotel_request(ops, checkin_at=now()-timedelta(days=3))
    valid = hotel_request(ops)
    with pytest.raises(ValidationError, match="whole-minute"):
        hotel_request(ops, checkin_at=valid.checkin_at.replace(second=59))


@pytest.mark.asyncio
async def test_hotel_stay_uses_property_zone_instead_of_bucharest(ops):
    ops.manifest(manifest_request(ops))
    alter_hotel_quotes(ops, lambda quotes: quotes[0].update(latitude=51.47, longitude=-0.45))
    checkin = (now()+timedelta(days=1)).replace(hour=23, minute=30, second=0)
    checkout = checkin+timedelta(days=1)
    result = await ops.hotels(hotel_request(ops, airport_code="LHR", checkin=checkin.date(),
        checkout=checkout.date(), checkin_at=checkin, checkout_at=checkout))
    assert result["world"]["resources"][0]["provenance"]["property_location"]["timezone"] == "Europe/London"


@pytest.mark.asyncio
async def test_hotel_dates_must_match_the_actual_property_zone(ops):
    ops.manifest(manifest_request(ops))
    checkin = (now()+timedelta(days=1)).replace(hour=23, minute=30, second=0)
    checkout = checkin+timedelta(days=1)
    before = ops.store.read().model_dump_json()
    with pytest.raises(DomainError, match="property's local time zone"):
        await ops.hotels(hotel_request(ops, checkin=checkin.date(), checkout=checkout.date(),
            checkin_at=checkin, checkout_at=checkout))
    assert ops.store.read().model_dump_json() == before


@pytest.mark.asyncio
async def test_property_location_change_does_not_move_existing_itineraries(ops):
    ops.manifest(manifest_request(ops))
    await ops.hotels(hotel_request(ops))
    before = ops.store.read().model_dump_json()
    alter_hotel_quotes(ops, lambda quotes: quotes[0].update(latitude=44.32))
    with pytest.raises(DomainError, match="property location changed"):
        await ops.hotels(hotel_request(ops, idempotency_key="new-location-attempt"))
    assert ops.store.read().model_dump_json() == before


@pytest.mark.asyncio
async def test_missing_rate_coordinates_resolve_from_matching_supplier_property(ops):
    ops.manifest(manifest_request(ops))
    alter_hotel_quotes(ops, lambda quotes: quotes[0].pop("latitude"))
    async def metadata(hotel_id):
        assert hotel_id == "hotel-real-id"
        return {"latitude": 44.33, "longitude": 23.81}
    ops.real_data.provider.hotel_location = metadata
    result = await ops.hotels(hotel_request(ops))
    node = next(item for item in result["world"]["airports"] if item["kind"] == "hotel")
    assert node["lat"] == 44.33 and node["lon"] == 23.81


def attach_proposal(ops):
    def create(world):
        resource = world.resources[0]
        itinerary = Itinerary(legs=[Leg(resource_id=resource.id, kind="flight", origin="CRA", destination="OTP",
            departure=resource.departure, arrival=resource.arrival, cost=80)], arrival=resource.arrival, total_cost=80)
        expiry = (now()+timedelta(minutes=10)).isoformat()
        world.offers.append(Offer(id="PROPOSAL-1", group_id="REAL-GROUP", itinerary=itinerary, status="proposed", expires_at=expiry, created_version=world.version+1))
        world.reservations.append(Reservation(id="RECORD-1", offer_id="PROPOSAL-1", group_id="REAL-GROUP", resource_id=resource.id, quantity=2, status="proposed", expires_at=expiry))
    ops.store.mutate("attach-test-proposal", {}, ops.store.read().version, create)


def confirmation(ops, **changes):
    return ExternalConfirmation(**({"expected_version": ops.store.read().version, "idempotency_key": "confirm-external-123", "bookings": [{
        "resource_id": "FLIGHT-REAL", "confirmation_reference": "TICKET-PNR-123", "confirmed_by": "Operator",
        "confirmed_at": now().isoformat()}]} | changes))


def test_external_confirmation_records_all_segments_atomically_without_provider_calls(ops):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    request = confirmation(ops)
    result = ops.confirm("PROPOSAL-1", request)
    assert result["offer"]["status"] == "confirmed"
    assert result["offer"]["external_bookings"][0]["confirmation_reference"] == "TICKET-PNR-123"
    assert result["world"]["groups"][0]["status"] == "confirmed"
    assert result["world"]["reservations"][0]["status"] == "confirmed"
    assert ops.real_data.provider.calls == 0
    assert ops.confirm("PROPOSAL-1", request)["replayed"]


@pytest.mark.parametrize("segment_count",[5,6])
def test_complete_extended_live_journey_requires_every_external_confirmation(ops,segment_count):
    ops.manifest(manifest_request(ops))
    def attach(world):
        group=world.groups[0]
        for index,(code,kind) in enumerate((("ST-A","station"),("HP-FIXTURE","hotel"),("ST-B","station"),("ST-C","station"))):
            world.airports.append(Airport(id=code,code=code,name="Fixture sourced location",kind=kind,lat=44.3+index*.01,lon=23.8))
        routes=[("bus","CRA","ST-A"),("bus","ST-A","HP-FIXTURE"),("hotel","HP-FIXTURE","HP-FIXTURE"),("bus","HP-FIXTURE","ST-B")]
        routes+= [("bus","ST-B","OTP")] if segment_count==5 else [("bus","ST-B","ST-C"),("bus","ST-C","OTP")]
        minute=group.ready_at+60
        expiry=(now()+timedelta(minutes=30)).isoformat()
        legs=[]
        for index,(kind,origin,destination) in enumerate(routes):
            duration=360 if kind=="hotel" else 180 if destination=="OTP" else 20
            resource=Resource(id=f"QUOTED-SEGMENT-{index+1}",kind=kind,name="Fixture supplier quote",origin=origin,destination=destination,
                departure=minute,arrival=minute+duration,capacity=1 if kind=="hotel" else group.size,
                room_capacity=2,cost_per_person=0,quote_total_cost=20,source="fixture_supplier",eligible_group_ids=[group.id],
                capacity_scope="quoted_request",requires_revalidation=True,valid_until=expiry,external_offer_id=f"fixture-offer-{index+1}")
            world.resources.append(resource)
            legs.append(Leg(resource_id=resource.id,kind=kind,origin=origin,destination=destination,
                departure=resource.departure,arrival=resource.arrival,cost=20))
            world.reservations.append(Reservation(id=f"SEGMENT-RECORD-{index+1}",offer_id="EXTENDED-PROPOSAL",group_id=group.id,
                resource_id=resource.id,quantity=units(resource,group),status="proposed",expires_at=expiry))
            minute=resource.arrival+10
        world.offers.append(Offer(id="EXTENDED-PROPOSAL",group_id=group.id,status="proposed",created_version=world.version+1,expires_at=expiry,
            itinerary=Itinerary(legs=legs,arrival=legs[-1].arrival,total_cost=20*segment_count)))
        group.status="offered"
    ops.store.mutate("attach-extended-proposal",{},ops.store.read().version,attach)
    before=ops.store.read()
    assert validate_allocations(before,[Allocation(group_id=before.groups[0].id,itinerary=before.offers[0].itinerary)])["valid"]
    bookings=[{"resource_id":leg.resource_id,"confirmation_reference":f"FIXTURE-REFERENCE-{index+1}",
        "confirmed_by":"Fixture operator","confirmed_at":now()} for index,leg in enumerate(before.offers[0].itinerary.legs)]
    missing=confirmation(ops,idempotency_key="missing-extended-reference",bookings=bookings[:-1])
    with pytest.raises(DomainError,match="every itinerary segment") as failure:
        ops.confirm("EXTENDED-PROPOSAL",missing)
    assert failure.value.status==422
    after=ops.store.read()
    assert after.version==before.version and after.offers==before.offers and after.reservations==before.reservations
    assert after.groups==before.groups and after.resources==before.resources
    complete=confirmation(ops,idempotency_key="complete-extended-references",bookings=bookings)
    result=ops.confirm("EXTENDED-PROPOSAL",complete)
    assert result["offer"]["status"]=="confirmed" and len(result["offer"]["external_bookings"])==segment_count
    assert {item["resource_id"] for item in result["offer"]["external_bookings"]}=={resource.id for resource in before.resources}
    assert all(item["status"]=="confirmed" for item in result["world"]["reservations"])
    assert all(item["booking_status"]=="confirmed" for item in result["world"]["resources"])
    assert result["world"]["groups"][0]["status"]=="confirmed" and result["world"]["version"]==before.version+1
    assert ops.confirm("EXTENDED-PROPOSAL",complete)["replayed"] and ops.real_data.provider.calls==0


@pytest.mark.parametrize("accepted", [False, True])
def test_live_decline_releases_proposed_or_accepted_allocations_without_booking_claims(ops, accepted):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    token = ops.store.read().groups[0].token
    if accepted:
        response = ops.recovery.accept("PROPOSAL-1", AcceptRequest(token=token, idempotency_key="accept-live-proposal"))
        assert response["offer"]["status"] == "accepted" and response["simulated"] is False
    before = ops.store.read().version
    result = ops.recovery.decline("PROPOSAL-1", token, "decline-live-proposal")
    assert result["simulated"] is False and result["offer"]["status"] == "declined"
    assert "No supplier cancellation" in result["offer"]["reason"]
    world = ops.store.read()
    assert world.version == before+1
    assert world.groups[0].status == "waiting"
    assert all(row.status == "released" for row in world.reservations)
    assert world.stats["resource_usage"]["FLIGHT-REAL"] == 0
    assert world.offers[0].external_bookings == []
    assert ops.recovery.decline("PROPOSAL-1", token, "decline-live-proposal")["replayed"]
    assert ops.store.read().version == world.version
    assert len([event for event in world.events if event.type == "offer_declined"]) == 1
    assert ops.real_data.provider.calls == 0


def test_passenger_decline_cannot_cancel_a_recorded_external_booking(ops):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    ops.confirm("PROPOSAL-1", confirmation(ops))
    before = ops.store.read()
    with pytest.raises(DomainError) as error:
        ops.recovery.decline("PROPOSAL-1", before.groups[0].token, "decline-booked-proposal")
    assert error.value.status == 409
    after = ops.store.read()
    assert after.version == before.version and after.offers == before.offers
    assert after.reservations == before.reservations
    assert after.groups[0].status == "confirmed"
    assert ops.real_data.provider.calls == 0


def test_wrong_external_reference_and_test_passenger_confirmation_roll_back(ops):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    before = ops.store.read().version
    bad = confirmation(ops, bookings=[{"resource_id":"OTHER", "confirmation_reference":"proof", "confirmed_by":"operator", "confirmed_at":now()}])
    with pytest.raises(DomainError):
        ops.confirm("PROPOSAL-1", bad)
    assert ops.store.read().version == before
    ops.store.mutate("set-test-group", {}, before, lambda world: setattr(world.groups[0], "source", "synthetic"))
    before = ops.store.read().version
    with pytest.raises(DomainError):
        ops.confirm("PROPOSAL-1", confirmation(ops))
    assert ops.store.read().version == before


def additional_import(ops, kind):
    if kind == "manifest":
        return ops.manifest, manifest_request(ops, group_id="ADDITIONAL-GROUP", passenger_prefix="ADDITIONAL",
            idempotency_key="additional-manifest")
    if kind == "resource":
        return ops.resource, resource_request(ops, id="ADDITIONAL-FLIGHT", idempotency_key="additional-resource")
    return ops.generate, PassengerGenerationInput(expected_version=ops.store.read().version,
        idempotency_key="additional-planning-group", passengers=2, group_size=2,
        origin="CRA", destination="OTP", original_flight_id="OTHER-FLIGHT",
        ready_at=now()+timedelta(minutes=30), deadline=now()+timedelta(days=1),
        group_budget_eur="100", accessible=False, priority=1, is_adult=True)


@pytest.mark.parametrize("kind", ["manifest", "resource", "generated_group"])
@pytest.mark.parametrize("status", ["proposed", "accepted", "confirmed"])
def test_additive_import_preserves_existing_journey_and_replays_without_changes(ops, kind, status):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    if status == "accepted":
        ops.recovery.accept("PROPOSAL-1", AcceptRequest(token=ops.store.read().groups[0].token,
            idempotency_key="accept-before-unrelated-import"))
    elif status == "confirmed":
        ops.confirm("PROPOSAL-1", confirmation(ops))
    before = ops.store.read()
    perform, request = additional_import(ops, kind)
    result = perform(request)
    after = ops.store.read()
    assert result["applied"] is True and after.version == before.version + 1
    assert after.offers == before.offers and after.reservations == before.reservations
    assert after.offers[0].status == status
    for name in ("groups", "resources", "airports"):
        original = {item.id: item.model_dump_json() for item in getattr(before, name)}
        assert original == {item.id: item.model_dump_json() for item in getattr(after, name) if item.id in original}
    assert len(after.resources) == len(before.resources) + (kind == "resource")
    assert len(after.groups) == len(before.groups) + (kind != "resource")
    expected_budget_increase = 0 if kind == "resource" else 500 if kind == "manifest" else 100
    assert after.budget == before.budget + expected_budget_increase
    if status != "confirmed":
        assert validate_allocations(after, [Allocation(group_id=after.offers[0].group_id,
            itinerary=after.offers[0].itinerary)])["valid"]
    assert perform(request)["replayed"] is True
    assert ops.store.read().model_dump_json() == after.model_dump_json()
    assert ops.real_data.provider.calls == 0


@pytest.mark.parametrize("kind", ["manifest", "resource", "generated_group"])
def test_additive_import_requires_current_version_and_does_not_reuse_occupied_identity(ops, kind):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    perform, stale_request = additional_import(ops, kind)
    token = ops.store.read().groups[0].token
    ops.recovery.accept("PROPOSAL-1", AcceptRequest(token=token,
        idempotency_key="preference-after-import-prepared"))
    before = ops.store.read()
    with pytest.raises(DomainError) as conflict:
        perform(stale_request)
    assert conflict.value.status == 409
    assert ops.store.read().model_dump_json() == before.model_dump_json()
    request = stale_request.model_copy(update={"expected_version": before.version,
        "idempotency_key": stale_request.idempotency_key + "-current"})
    perform(request)
    after = ops.store.read()
    assert after.offers == before.offers and after.reservations == before.reservations
    if kind != "generated_group":
        duplicate = request.model_copy(update={"expected_version": after.version,
            "idempotency_key": request.idempotency_key + "-duplicate"})
        with pytest.raises(DomainError) as collision:
            perform(duplicate)
        assert collision.value.status == (422 if kind == "manifest" else 409)
        assert ops.store.read().model_dump_json() == after.model_dump_json()


def test_operator_flight_confirmation_does_not_override_known_airport_closure(ops):
    ops.manifest(manifest_request(ops))
    def close(world):
        airport = next(item for item in world.airports if item.id == "CRA")
        airport.operational_status, airport.available = "closed", False
    ops.store.mutate("airport-closure", {}, ops.store.read().version, close)
    result = ops.resource(resource_request(ops))
    airport = next(item for item in result["world"]["airports"] if item["id"] == "CRA")
    assert airport["operational_status"] == "closed" and airport["available"] is False
