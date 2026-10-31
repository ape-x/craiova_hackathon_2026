"""Source import/archive integration with local fixtures; real network is blocked."""
import copy
from datetime import timedelta

import pytest

from app.inventory_lifecycle import archive_resources, archived_location, ensure_inventory_capacity, resource_history
from app.live_travel import append_travel_quote, retire_travel_quotes
from app.models import Airport, Resource
from app.scenario_schema import MAX_AIRPORTS, MAX_RESOURCES
from app.store import DomainError, utcnow
from test_live_travel import live, quote, search
from test_live_service import ops, manifest_request, hotel_request, resource_request
from test_transfer_planning import setup as transfer_setup, import_request
from test_hotel_recovery_jobs import package, package_request, world_snapshot
from test_recovery_jobs import setup, run_next, request as flight_job_request


def import_flight(store, value, key):
    def apply(world):
        return append_travel_quote(world, value, search().model_dump(mode="json"), world.groups[0], "flight")
    return store.mutate(key, {"quote": value}, store.read().version, apply)


def filler(world, identity):
    return Resource(id=identity, kind="bus", name="Retired fixture quote", origin=world.groups[0].origin,
        destination=world.groups[0].destination, departure=world.sim_time+60, arrival=world.sim_time+120,
        capacity=2, cost_per_person=1, available=False, source="fixture supplier")


def test_more_than_lifetime_limit_revisions_preserve_exact_history_and_never_restore_old_fare(live):
    base = utcnow()-timedelta(seconds=10)
    first = None
    for number in range(MAX_RESOURCES+5):
        value = quote(quoted_at=(base+timedelta(milliseconds=number)).isoformat(), total_price=f"{100+number}.00")
        result = import_flight(live.store, value, f"revision-{number}")
        if first is None:
            first = (value, result["resource_ids"][0])
        assert len(live.store.read().resources) == 1
    world = live.store.read()
    history = resource_history(world, external_offer_id="offer-real", group_id="G-ONE")
    assert len({item.id for item in history}) == MAX_RESOURCES+5
    original = next(item for item in history if item.id == first[1])
    assert original.provenance == first[0] and original.quote_total_cost == 100 and not original.available
    assert first[1] not in {item.id for item in world.resources}
    before = world_snapshot(live.store)
    with pytest.raises(DomainError, match="retired or archived"):
        import_flight(live.store, first[0], "do-not-revive")
    with pytest.raises(DomainError, match="older revision"):
        import_flight(live.store, {**first[0], "total_price": "1.00"}, "do-not-regress")
    assert world_snapshot(live.store) == before


def test_candidate_pin_survives_next_import_compaction_until_independent_validation(live, monkeypatch):
    import app.inventory_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "MAX_RESOURCES", 2)
    world = live.store.read()
    first = append_travel_quote(world, quote(provider_offer_id="FIRST"), search().model_dump(), world.groups[0], "flight")["resource_ids"][0]
    candidate = world.resources[0]
    candidate.valid_until = (utcnow()-timedelta(seconds=1)).isoformat()
    world.resources.append(filler(world, "DISCARDABLE"))
    append_travel_quote(world, quote(provider_offer_id="SECOND"), search().model_dump(), world.groups[0], "flight", keep_resource_ids={first})
    assert len(world.resources) == 2 and first in {item.id for item in world.resources}
    assert "DISCARDABLE" not in {item.id for item in world.resources}
    assert candidate.valid_until == next(item.valid_until for item in world.resources if item.id == first)


def test_archived_coach_station_cannot_be_reassigned_to_new_coordinates(live):
    original = quote("bus")
    def seed(world):
        result = append_travel_quote(world, original, search("bus").model_dump(), world.groups[0], "bus")
        retire_travel_quotes(world, world.groups[0].id, result["resource_ids"])
        ensure_inventory_capacity(world, extra_locations=MAX_AIRPORTS-sum(item.kind == "airport" for item in world.airports))
    live.store.mutate("coach-archive", {}, live.store.read().version, seed)
    assert not live.store.read().resources and all(item.kind == "airport" for item in live.store.read().airports)
    changed = copy.deepcopy(original)
    changed["segments"][0]["departure_station"]["latitude"] += 0.01
    changed["provider_offer_id"] = "different-offer"
    world = live.store.read()
    with pytest.raises(DomainError, match="Location identity"):
        append_travel_quote(world, changed, search("bus").model_dump(), world.groups[0], "bus")
    assert not world.resources and all(item.kind == "airport" for item in world.airports)


@pytest.mark.asyncio
async def test_manual_hotel_replacement_archives_legacy_price_and_compacts_old_properties(ops):
    ops.manifest(manifest_request(ops))
    original = ops.real_data.provider.offers
    async def distinct_property(airport, query):
        values = await original(airport, query)
        values[0]["id"] = "property-"+str(ops.real_data.provider.calls)
        return values
    ops.real_data.provider.offers = distinct_property
    await ops.hotels(hotel_request(ops))
    def legacy_identity(world):
        world.resources[0].id = "HQ-LEGACY-PROPERTY-OFFER"
    ops.store.mutate("legacy-hotel-id", {}, ops.store.read().version, legacy_identity)
    first = ops.store.read().resources[0].model_dump(mode="json")
    for number in range(MAX_AIRPORTS+3):
        await ops.hotels(hotel_request(ops, idempotency_key=f"property-refresh-{number}"))
        assert len(ops.store.read().resources) == 1 and len(ops.store.read().airports) <= MAX_AIRPORTS
    world = ops.store.read()
    assert resource_history(world, resource_id=first["id"])[0].model_dump(mode="json") == {**first, "available": False}
    assert len({item.provenance["id"] for item in resource_history(world, kind="hotel")}) == MAX_AIRPORTS+4
    assert first["origin"] not in {item.id for item in world.airports}
    assert archived_location(world, first["origin"]).kind == "hotel"


def test_manual_resource_identity_cannot_reuse_archived_supplier_record(ops):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    def retire(world):
        world.resources[0].available = False
        archive_resources(world, ["FLIGHT-REAL"])
    ops.store.mutate("retire-manual-service", {}, ops.store.read().version, retire)
    with pytest.raises(DomainError):
        ops.resource(resource_request(ops, idempotency_key="reuse-retired-id", unit_price_eur="1.00"))
    assert not ops.store.read().resources
    assert resource_history(ops.store.read(), resource_id="FLIGHT-REAL")[0].cost_per_person == 40


@pytest.mark.asyncio
async def test_archived_hotel_revision_still_blocks_an_older_cheaper_supplier_quote(ops):
    ops.manifest(manifest_request(ops))
    original = ops.real_data.provider.offers
    quotes = []
    async def revisions(airport, query):
        values = await original(airport, query)
        value = values[0]
        value["provider_offer_id"] = "SAME-ROOM-OFFER"
        value["quoted_at"] = (utcnow()-timedelta(seconds=60 if not quotes else 30)).isoformat()
        quotes.append(copy.deepcopy(value))
        return values
    ops.real_data.provider.offers = revisions
    await ops.hotels(hotel_request(ops))
    old_id = ops.store.read().resources[0].id
    await ops.hotels(hotel_request(ops, idempotency_key="new-hotel-revision"))
    assert old_id not in {item.id for item in ops.store.read().resources}
    older = copy.deepcopy(quotes[0])
    older["total_price"] = "1.00"
    older["pricing"]["booking_total"]["amount"] = "1.00"
    older["pricing"]["mandatory_total"]["amount"] = "1.00"
    async def obsolete(airport, query):
        return [copy.deepcopy(older)]
    ops.real_data.provider.offers = obsolete
    before = world_snapshot(ops.store)
    with pytest.raises(DomainError, match="older hotel quote"):
        await ops.hotels(hotel_request(ops, idempotency_key="older-cheaper-room"))
    assert world_snapshot(ops.store) == before
    assert resource_history(ops.store.read(), resource_id=old_id)[0].quote_total_cost == 176.45


@pytest.mark.asyncio
async def test_apl_can_use_archived_property_evidence_without_reviving_old_room_price(transfer_setup):
    store,sources,service,request,clock = transfer_setup
    hotel_id = request().hotel_node_id
    def compact(world):
        archive_resources(world, ["OLD-HOTEL"])
        ensure_inventory_capacity(world, extra_locations=MAX_AIRPORTS-sum(item.kind == "airport" for item in world.airports))
    store.mutate("retire-room-price", {}, store.read().version, compact)
    assert hotel_id not in {item.id for item in store.read().airports}
    response = await service.quote(request())
    assert response["state"] == "ready" and sources.calls[0] == ("metadata", "hotel-source-id")
    service.import_quote(response["quote_id"], import_request(store))
    world = store.read()
    assert hotel_id in {item.id for item in world.airports}
    assert [item.kind for item in world.resources] == ["bus"]
    assert resource_history(world, resource_id="OLD-HOTEL")[0].available is False


@pytest.mark.asyncio
async def test_apl_sequence_and_exact_receipt_retirement_survive_archival(transfer_setup):
    store,sources,service,request,clock = transfer_setup
    old = await service.quote(request())
    newer = await service.quote(request("newer-source"))
    identity = service.import_quote(newer["quote_id"], import_request(store))["resource_ids"][0]
    before_resource = next(item for item in store.read().resources if item.id == identity).model_dump(mode="json")
    def retire(world):
        retire_travel_quotes(world, "G1", [identity])
    store.mutate("retire-apl", {}, store.read().version, retire)
    before = world_snapshot(store)
    with pytest.raises(DomainError, match="newer APL"):
        service.import_quote(old["quote_id"], import_request(store, "older-after-archive"))
    with pytest.raises(DomainError, match="retired or archived"):
        service.import_quote(newer["quote_id"], import_request(store, "same-after-archive"))
    assert world_snapshot(store) == before
    assert resource_history(store.read(), resource_id=identity)[0].model_dump(mode="json") == {**before_resource, "available": False}


@pytest.mark.asyncio
async def test_repeated_full_hotel_packages_compact_projected_and_committed_inventory(package):
    store,jobs,ai,travel,sources,fx,inputs = package
    other = store.read().groups[1].model_dump(mode="json")
    for cycle in range(3):
        def fill(world):
            while len(world.resources) < MAX_RESOURCES:
                world.resources.append(filler(world, f"DISCARD-{cycle}-{len(world.resources)}"))
            while len(world.airports) < MAX_AIRPORTS:
                identity = f"ST-UNUSED-{cycle}-{len(world.airports)}"
                world.airports.append(Airport(id=identity,code=identity,name="Unused source station",lat=51,lon=0,kind="station"))
        store.mutate(f"fill-cycle-{cycle}", {}, store.read().version, fill)
        jobs.start("CASE", package_request(store,inputs,idempotency_key=f"repeat-package-{cycle}"))
        result = await run_next(jobs)
        assert result["status"] == "completed", result
        assert result["calls_dispatched"] == 10
        world = store.read()
        current = next(item for item in world.offers if item.status == "proposed")
        assert [leg.kind for leg in current.itinerary.legs] == ["bus", "hotel", "bus", "flight"]
        assert {leg.resource_id for leg in current.itinerary.legs} <= {item.id for item in world.resources}
        assert len(world.resources) <= MAX_RESOURCES and len(world.airports) <= MAX_AIRPORTS
        assert world.groups[1].model_dump(mode="json") == other
    assert len(resource_history(world)) > MAX_RESOURCES


@pytest.mark.asyncio
async def test_package_uses_surviving_revision_when_supplier_batch_reprices_same_offer(package):
    store,jobs,ai,travel,sources,fx,inputs = package
    original = travel.search
    async def revisions(query):
        result = await original(query)
        newer = copy.deepcopy(result["items"][0])
        older = copy.deepcopy(newer)
        older["quoted_at"] = (utcnow()-timedelta(seconds=1)).isoformat()
        older["total_price"] = "10.00"
        result["items"] = [older, newer]
        return result
    travel.search = revisions
    jobs.start("CASE", package_request(store, inputs))
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    flight = next(item for item in store.read().resources if item.kind == "flight")
    assert flight.quote_total_cost == 100 and flight.provenance["total_price"] == "100.00"
    assert not any(item.quote_total_cost == 10 for item in store.read().resources)


@pytest.mark.asyncio
async def test_protected_resource_capacity_failure_keeps_world_and_source_history_atomic(package):
    store,jobs,ai,travel,sources,fx,inputs = package
    def fill(world):
        for number in range(MAX_RESOURCES):
            resource = filler(world, f"BOOKED-{number}")
            resource.booking_status = "confirmed"
            world.resources.append(resource)
    store.mutate("fully-booked-inventory", {}, store.read().version, fill)
    before = world_snapshot(store)
    jobs.start("CASE", package_request(store, inputs))
    result = await run_next(jobs)
    assert result["status"] == "needs_review", result
    assert "Active inventory capacity is full" in result["message"]
    assert world_snapshot(store) == before
    assert len(resource_history(store.read())) == MAX_RESOURCES


@pytest.mark.asyncio
async def test_capacity_blocked_first_offer_does_not_hide_later_existing_feasible_quote(setup):
    _,store,jobs,ai,travel = setup
    jobs.start("CASE", flight_job_request(store))
    assert (await run_next(jobs))["status"] == "completed"
    existing = copy.deepcopy(store.read().resources[0].provenance)
    def fill(world):
        while len(world.resources) < MAX_RESOURCES:
            resource = filler(world, f"RETAINED-{len(world.resources)}")
            resource.booking_status = "confirmed"
            world.resources.append(resource)
    store.mutate("protected-candidate-limit", {}, store.read().version, fill)
    async def alternatives(query):
        newer = {**copy.deepcopy(existing), "id": "NEW-NEEDS-SPACE", "provider_offer_id": "NEW-NEEDS-SPACE"}
        return {"state": "ready", "source": "fixture-supplier", "items": [newer, copy.deepcopy(existing)]}
    travel.handler = alternatives
    jobs.start("CASE", flight_job_request(store, idempotency_key="existing-option-after-full"))
    result = await run_next(jobs)
    assert result["status"] == "completed", result
    assert result["result"]["recovered_passengers"] == 1
    assert any(item["code"] == "inventory_capacity_exhausted" for item in result["gaps"])
    assert len(store.read().resources) == MAX_RESOURCES
    assert not any(item.external_offer_id == "NEW-NEEDS-SPACE" for item in store.read().resources)
