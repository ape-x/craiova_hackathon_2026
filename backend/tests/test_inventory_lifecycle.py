"""Inventory retirement preserves operational references and durable source facts.

Only temporary SQLite stores are used; conftest blocks all real network access.
"""
from datetime import timedelta
import sqlite3

import pytest

import app.inventory_lifecycle as inventory_lifecycle
from app.airport_arrival import AirportArrivalAssumption, AirportDropoff
from app.initial_position import PositionCoordinates
from app.inventory_lifecycle import (
    archive_resources,
    archived_location,
    ensure_inventory_capacity,
    resource_detail,
    resource_history,
    validate_location_identity,
)
from app.models import Airport, Itinerary, Leg, Offer, PassengerGroup, Reservation, Resource, World
from app.scenario_schema import MAX_AIRPORTS, MAX_RESOURCES
from app.store import DomainError, Store, utcnow


@pytest.fixture
def store(tmp_path):
    result = Store(str(tmp_path / "inventory.sqlite3"), operation_mode="live")

    def seed(world):
        world.airports = [
            Airport(id="LHR", code="LHR", name="Heathrow", lat=51.47, lon=-0.4543),
            Airport(id="OTP", code="OTP", name="Otopeni", lat=44.5711, lon=26.085),
        ]
        world.groups = [PassengerGroup(id="G1", name="Passenger group", size=2,
            origin="LHR", destination="OTP", deadline=world.sim_time+1440,
            ready_at=world.sim_time, budget=1000, original_flight_id="UNIMPORTED-FLIGHT",
            token="inventory-test-token", source="operator_manifest", adult_count=2)]
        world.budget = 10000

    result.mutate("inventory-fixture", {}, result.read().version, seed)
    return result


def quote(world, resource_id="OLD", *, fresh=False, **changes):
    fields = dict(id=resource_id, name="Exact supplier quote", kind="flight",
        origin="LHR", destination="OTP", departure=world.sim_time+120,
        arrival=world.sim_time+240, capacity=2, cost_per_person=40,
        source="fixture-supplier", external_offer_id="supplier-"+resource_id,
        eligible_group_ids=["G1"], quote_total_cost=80, budget_total_cost=80,
        valid_until=(utcnow()+timedelta(hours=2 if fresh else -1)).isoformat(),
        provenance={"provider":"fixture-supplier", "supplier_response":{
            "currency":"EUR", "amount":"80.00", "fare_rules":["nonrefundable"]}})
    fields.update(changes)
    return Resource(**fields)


def offer(world, resource, status, *, external_bookings=None):
    leg = Leg(resource_id=resource.id, kind=resource.kind, origin=resource.origin,
        destination=resource.destination, departure=resource.departure,
        arrival=resource.arrival, cost=80)
    return Offer(id="OF-"+resource.id, group_id="G1", status=status,
        created_version=world.version, expires_at=(utcnow()+timedelta(hours=1)).isoformat(),
        itinerary=Itinerary(legs=[leg], arrival=resource.arrival, total_cost=80),
        external_bookings=external_bookings or [])


@pytest.mark.parametrize("status", ["held", "proposed", "accepted", "confirmed"])
def test_active_offer_resources_cannot_be_archived(store, status):
    world = store.read()
    resource = quote(world)
    world.resources = [resource]
    world.offers = [offer(world, resource, status)]
    before = world.model_dump(mode="json")

    assert archive_resources(world, [resource.id]) == []
    assert world.model_dump(mode="json") == before
    assert resource_detail(world, resource.id)["archived"] is False


@pytest.mark.parametrize("status", ["held", "proposed", "accepted", "confirmed"])
def test_active_reservation_alone_protects_resource(store, status):
    world = store.read()
    resource = quote(world)
    world.resources = [resource]
    world.reservations = [Reservation(id="RS-OLD", offer_id="OF-OLD", group_id="G1",
        resource_id=resource.id, quantity=2, status=status,
        expires_at=(utcnow()+timedelta(hours=1)).isoformat())]

    assert archive_resources(world, [resource.id]) == []
    assert [item.id for item in world.resources] == [resource.id]


@pytest.mark.parametrize("protection", ["resource_booking", "external_booking", "original_flight"])
def test_bookings_and_original_flight_protect_even_inactive_quotes(store, protection):
    world = store.read()
    resource = quote(world)
    world.resources = [resource]
    if protection == "resource_booking":
        resource.booking_status = "confirmed"
    elif protection == "external_booking":
        world.offers = [offer(world, resource, "invalidated", external_bookings=[{
            "resource_id":resource.id, "confirmation_reference":"SUPPLIER-CONFIRMED"}])]
    else:
        world.groups[0].original_flight_id = resource.id
        resource.status = "needs_diversion"

    assert archive_resources(world, [resource.id]) == []
    assert [item.id for item in world.resources] == [resource.id]


def test_retained_return_transfer_protects_exact_onward_flight(store):
    world = store.read()
    flight = quote(world, origin="OTP", destination="LHR")
    assumption = AirportArrivalAssumption(source="operator_declared", confirmed=True,
        group_id="G1", group_fingerprint="a"*64, scenario_id=world.scenario_id,
        epoch=world.epoch, airport_id="OTP", onward_resource_id=flight.id,
        onward_flight_fingerprint="b"*64, onward_departure_at=utcnow().isoformat(),
        dropoff=AirportDropoff(label="Airport departure entrance",
            coordinates=PositionCoordinates(latitude=44.5711, longitude=26.085)),
        processing_minutes=60)
    transfer = quote(world, "RETURN", fresh=True, kind="bus", transport_mode="private_transfer",
        airport_arrival_assumption=assumption)
    world.resources = [flight, transfer]

    assert archive_resources(world, [flight.id]) == []
    assert {item.id for item in world.resources} == {flight.id, transfer.id}


def test_fresh_quote_and_expired_offer_history_have_distinct_retirement_behavior(store):
    world = store.read()
    old = quote(world)
    fresh = quote(world, "FRESH", fresh=True)
    world.resources = [old, fresh]
    world.offers = [offer(world, old, "expired")]
    world.reservations = [Reservation(id="RS-OLD", offer_id="OF-OLD", group_id="G1",
        resource_id=old.id, quantity=2, status="released", expires_at=old.valid_until)]
    historical_offer = world.offers[0].model_dump(mode="json")

    assert archive_resources(world, [old.id, fresh.id], reason="expired quote") == [old.id]
    assert [item.id for item in world.resources] == [fresh.id]
    assert world.offers[0].model_dump(mode="json") == historical_offer
    assert {item.id for item in resource_history(world)} == {old.id, fresh.id}


def test_capacity_checks_do_not_retire_inventory_without_pressure(store):
    world = store.read()
    world.resources = [quote(world)]
    before = world.model_dump(mode="json")

    ensure_inventory_capacity(world, extra_resources=1, extra_locations=1)

    assert world.model_dump(mode="json") == before
    assert resource_detail(world, "OLD")["archived"] is False


def test_resource_pressure_archives_only_eligible_inventory(store):
    world = store.read()
    old = quote(world)
    world.resources = [old] + [quote(world, f"FRESH-{index}", fresh=True)
        for index in range(MAX_RESOURCES-1)]

    ensure_inventory_capacity(world, extra_resources=1)

    assert len(world.resources)+1 <= MAX_RESOURCES
    assert {item.id for item in world.resources} == {f"FRESH-{index}" for index in range(MAX_RESOURCES-1)}
    detail = resource_detail(world, old.id)
    assert detail["archived"] is True
    assert detail["resource"].model_dump(mode="json") == old.model_dump(mode="json")


@pytest.mark.parametrize("keep_kind", ["resource", "location"])
def test_explicit_incoming_references_are_protected_during_compaction(store, keep_kind):
    world = store.read()
    if keep_kind == "resource":
        world.resources = [quote(world, f"OLD-{index}") for index in range(MAX_RESOURCES)]
        ensure_inventory_capacity(world, extra_resources=1, keep_resource_ids=["OLD-0"])
        assert any(item.id == "OLD-0" for item in world.resources)
        assert len(world.resources)+1 <= MAX_RESOURCES
    else:
        world.airports.extend(Airport(id=f"HOTEL-{index}", code=f"HOTEL-{index}",
            name=f"Sourced property {index}", lat=51.5, lon=-0.2, kind="hotel")
            for index in range(MAX_AIRPORTS-len(world.airports)))
        ensure_inventory_capacity(world, extra_locations=1, keep_location_ids=["HOTEL-0"])
        assert any(item.id == "HOTEL-0" for item in world.airports)
        assert {"LHR", "OTP"}.issubset({item.id for item in world.airports})
        assert len(world.airports)+1 <= MAX_AIRPORTS


def test_insufficient_capacity_rolls_back_partial_retirement(store):
    world = store.read()
    world.resources = [quote(world)] + [quote(world, f"FRESH-{index}", fresh=True)
        for index in range(MAX_RESOURCES-1)]
    before = world.model_dump(mode="json")
    historical_before = [item.model_dump(mode="json") for item in resource_history(world)]

    with pytest.raises(DomainError) as error:
        ensure_inventory_capacity(world, extra_resources=2)

    assert error.value.status == 422
    assert error.value.details == {"inventory_capacity_exhausted": True}
    assert world.model_dump(mode="json") == before
    assert [item.model_dump(mode="json") for item in resource_history(world)] == historical_before
    assert resource_detail(world, "OLD")["archived"] is False


def test_location_pressure_preserves_operational_closure_and_group_endpoints(store):
    world = store.read()
    closed = Airport(id="CLOSED", code="CLOSED", name="Closed airport", lat=50, lon=1,
        available=False, diversion_eligible=False, operational_status="closed")
    world.airports.append(closed)
    world.airports.extend(Airport(id=f"HOTEL-{index}", code=f"HOTEL-{index}",
        name=f"Sourced property {index}", lat=51.5, lon=-0.2, kind="hotel")
        for index in range(MAX_AIRPORTS-len(world.airports)))

    ensure_inventory_capacity(world, extra_locations=1)

    assert len(world.airports)+1 <= MAX_AIRPORTS
    assert {"LHR", "OTP", closed.id}.issubset({item.id for item in world.airports})
    assert next(item for item in world.airports if item.id == closed.id) == closed
    retired = [f"HOTEL-{index}" for index in range(MAX_AIRPORTS-3)
        if not any(item.id == f"HOTEL-{index}" for item in world.airports)]
    assert retired
    assert archived_location(world, retired[0]).kind == "hotel"


@pytest.mark.parametrize("constraint", ["open", "closed", "weather"])
def test_nonairport_explicit_operational_constraints_survive_pressure(store, constraint):
    world = store.read()
    node = Airport(id="CONSTRAINED", code="CONSTRAINED", name="Constrained station",
        lat=51, lon=0, kind="station")
    if constraint == "weather":
        node.weather = {"condition":"storm", "visibility_m":None, "wind_kts":None,
            "landing_allowed":False, "departure_allowed":False}
    else:
        node.operational_status = constraint
    world.airports.append(node)
    world.airports.extend(Airport(id=f"UNUSED-{i}", code=f"UNUSED-{i}", name="Unused property",
        lat=51, lon=0, kind="hotel") for i in range(MAX_AIRPORTS-len(world.airports)))
    ensure_inventory_capacity(world, extra_locations=1)
    assert next(item for item in world.airports if item.id == node.id) == node


def test_private_archive_capability_does_not_change_world_equality_or_serialization(store):
    world = store.read()
    before = world.model_dump(mode="json")
    independent = World.model_validate(before)
    assert world == independent and independent == world
    assert world == world.model_copy(deep=True)
    resource_history(world, resource_id="missing")
    assert world == independent
    assert world.model_dump(mode="json") == before
    assert "_inventory_context" not in World.model_json_schema()["properties"]


def test_archive_is_durable_with_full_source_and_location_evidence(store):
    def seed(world):
        world.resources = [quote(world)]
    store.mutate("seed-archive-evidence", {}, store.read().version, seed)
    before = store.read()
    source = before.resources[0].model_dump(mode="json")
    locations = {item.id:item.model_dump(mode="json") for item in before.airports}

    result = store.mutate("archive-evidence", {}, before.version,
        lambda world: {"retired":archive_resources(world, ["OLD"], reason="quote expired")})
    assert result["retired"] == ["OLD"]
    reopened = Store(store.path, operation_mode="live").read()
    detail = resource_detail(reopened, "OLD")

    assert reopened.resources == []
    assert detail["archived"] is True
    assert detail["archive_reason"] == "quote expired"
    assert detail["archived_at"]
    assert detail["archived_version"] == reopened.version
    assert detail["resource"].model_dump(mode="json") == source
    assert {item.id:item.model_dump(mode="json") for item in detail["locations"]} == locations
    history = resource_history(reopened, source="fixture-supplier", external_offer_id="supplier-OLD",
        group_id="G1", kind="flight", origin="LHR")
    assert [item.id for item in history] == ["OLD"]
    assert resource_history(reopened, source="different-supplier") == []
    assert resource_history(reopened, resource_id="MISSING") == []


def test_legacy_removal_is_archived_in_same_store_commit(store):
    def seed(world):
        world.resources = [quote(world)]
    store.mutate("seed-legacy-removal", {}, store.read().version, seed)
    source = store.read().resources[0].model_dump(mode="json")

    def remove(world):
        world.resources = []
    store.mutate("legacy-removal", {}, store.read().version, remove)

    reopened = Store(store.path, operation_mode="live").read()
    assert reopened.resources == []
    assert resource_detail(reopened, "OLD")["resource"].model_dump(mode="json") == source
    assert resource_detail(reopened, "OLD")["archived"] is True


def test_failed_mutation_cannot_leak_staged_archive_into_a_later_commit(store):
    def seed(world):
        world.resources = [quote(world)]
    store.mutate("seed-archive-rollback", {}, store.read().version, seed)
    before = store.read()

    def fail_after_retirement(world):
        assert archive_resources(world, ["OLD"]) == ["OLD"]
        raise DomainError(422, "Incoming package failed validation")

    with pytest.raises(DomainError):
        store.mutate("failed-archive-mutation", {}, before.version, fail_after_retirement)

    unchanged = store.read()
    assert unchanged.version == before.version
    assert resource_detail(unchanged, "OLD")["archived"] is False
    store.mutate("later-unrelated-mutation", {}, unchanged.version, lambda world: {})
    reopened = Store(store.path, operation_mode="live").read()
    assert resource_detail(reopened, "OLD")["archived"] is False
    assert len(resource_history(reopened, resource_id="OLD")) == 1


def test_history_combines_persisted_pending_and_active_resources(store):
    def seed(world):
        world.resources = [quote(world)]
    store.mutate("seed-history-sources", {}, store.read().version, seed)
    store.mutate("archive-history-source", {}, store.read().version,
        lambda world: {"retired":archive_resources(world, ["OLD"])})
    world = Store(store.path, operation_mode="live").read()
    world.resources.extend([quote(world, "PENDING"), quote(world, "FRESH", fresh=True)])
    assert archive_resources(world, ["PENDING"]) == ["PENDING"]

    assert {item.id for item in resource_history(world)} == {"OLD", "PENDING", "FRESH"}
    for resource_id in ("OLD", "PENDING", "FRESH"):
        assert [item.id for item in resource_history(world, resource_id=resource_id)] == [resource_id]
    assert resource_detail(world, "OLD")["archived"] is True
    assert resource_detail(world, "PENDING")["archived"] is True
    assert resource_detail(world, "FRESH")["archived"] is False


def test_projected_world_and_returned_evidence_are_detached_from_live_archive(store):
    def seed(world):
        world.resources = [quote(world)]
    store.mutate("seed-projection", {}, store.read().version, seed)
    original = store.read()
    projected = original.model_copy(deep=True)

    assert archive_resources(projected, ["OLD"]) == ["OLD"]
    projected_history = resource_history(projected, resource_id="OLD")
    projected_history[0].provenance["supplier_response"]["fare_rules"].append("altered")
    detail = resource_detail(projected, "OLD")
    detail["resource"].name = "Altered view"
    detail["locations"][0].lat = 0

    assert resource_detail(projected, "OLD")["resource"].name == "Exact supplier quote"
    assert resource_detail(projected, "OLD")["locations"][0].lat != 0
    assert resource_history(projected, resource_id="OLD")[0].provenance["supplier_response"]["fare_rules"] == ["nonrefundable"]
    assert resource_detail(original, "OLD")["archived"] is False
    assert resource_detail(store.read(), "OLD")["archived"] is False
    assert original.resources[0].model_dump() == store.read().resources[0].model_dump()


@pytest.mark.parametrize("change", [{"lat":51.6}, {"lon":-0.3}, {"kind":"meeting_point"}])
def test_archived_sourced_location_identity_cannot_be_reused(store, change):
    def seed(world):
        world.airports.append(Airport(id="HOTEL-SOURCE", code="HOTEL-SOURCE",
            name="Supplier property", lat=51.5, lon=-0.2, kind="hotel"))
        world.resources = [quote(world, kind="hotel", origin="HOTEL-SOURCE", destination="HOTEL-SOURCE")]
    store.mutate("seed-location-identity", {}, store.read().version, seed)
    node = next(item for item in store.read().airports if item.id == "HOTEL-SOURCE")

    def remove(world):
        world.resources = []
        world.airports = [item for item in world.airports if item.id != node.id]
    store.mutate("retire-location-identity", {}, store.read().version, remove)
    world = Store(store.path, operation_mode="live").read()

    assert archived_location(world, node.id) == node
    validate_location_identity(world, node.model_copy(update={"name":"Display label update"}))
    with pytest.raises(DomainError) as error:
        validate_location_identity(world, node.model_copy(update=change))
    assert error.value.status == 409
    assert archived_location(world, node.id) == node


@pytest.mark.parametrize("table, statement", [
    ("inventory_archive", "UPDATE inventory_archive SET reason='tampered'"),
    ("inventory_archive", "DELETE FROM inventory_archive"),
    ("inventory_archive_groups", "UPDATE inventory_archive_groups SET group_id='TAMPERED'"),
    ("inventory_archive_groups", "DELETE FROM inventory_archive_groups"),
])
def test_sql_cannot_update_or_delete_source_archive_or_its_group_index(store, table, statement):
    def seed(world):
        world.resources = [quote(world)]
    store.mutate("seed-sql-immutability", {}, store.read().version, seed)
    store.mutate("archive-sql-immutability", {}, store.read().version,
        lambda world: {"retired":archive_resources(world, ["OLD"])})
    with store.connect() as conn:
        before = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        assert before
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(statement)
        assert conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == before


def test_sql_replace_cannot_overwrite_immutable_snapshot_even_without_recursive_triggers(store):
    store.mutate("seed-replace-proof", {}, store.read().version,
        lambda world: setattr(world, "resources", [quote(world)]))
    store.mutate("archive-replace-proof", {}, store.read().version,
        lambda world: {"retired":archive_resources(world, ["OLD"])})
    with store.connect() as conn:
        conn.execute("PRAGMA recursive_triggers=OFF")
        original = conn.execute("SELECT * FROM inventory_archive WHERE entity_type='resource'").fetchone()
        replacement = list(original)
        replacement[6] = "tampered reason"
        replacement[11] = '{"forged":"source"}'
        conn.execute("INSERT OR REPLACE INTO inventory_archive VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", replacement)
        assert conn.execute("SELECT * FROM inventory_archive WHERE entity_type='resource'").fetchone() == original


def test_repeated_archive_preserves_first_receipt_for_identical_source_revision(store):
    source = quote(store.read())
    store.mutate("seed-original-revision", {}, store.read().version,
        lambda world: setattr(world, "resources", [source.model_copy(deep=True)]))
    store.mutate("archive-original-revision", {}, store.read().version,
        lambda world: {"retired":archive_resources(world, ["OLD"], reason="original retirement")})
    first = resource_detail(store.read(), "OLD")
    store.mutate("restore-identical-revision", {}, store.read().version,
        lambda world: setattr(world, "resources", [source.model_copy(deep=True)]))
    store.mutate("retire-identical-revision", {}, store.read().version,
        lambda world: {"retired":archive_resources(world, ["OLD"], reason="later retirement")})

    assert resource_detail(store.read(), "OLD") == first
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM inventory_archive WHERE entity_type='resource' AND entity_id='OLD'").fetchone()[0] == 1


def test_late_idempotency_insert_failure_rolls_back_world_archive_and_receipt(store):
    def seed(world):
        world.resources = [quote(world)]
    store.mutate("seed-late-insert-failure", {}, store.read().version, seed)
    before = store.read()
    with store.connect() as conn:
        archive_before = conn.execute("SELECT * FROM inventory_archive ORDER BY rowid").fetchall()
        indexes_before = conn.execute("SELECT * FROM inventory_archive_groups ORDER BY rowid").fetchall()
        conn.execute("""CREATE TRIGGER fail_inventory_receipt BEFORE INSERT ON idempotency
            WHEN NEW.key='fault-inventory-receipt'
            BEGIN SELECT RAISE(ABORT,'injected late receipt failure'); END""")

    def retire(world):
        return {"retired":archive_resources(world, ["OLD"], reason="transactional retirement")}
    with pytest.raises(sqlite3.IntegrityError, match="injected late receipt failure"):
        store.mutate("fault-inventory-receipt", {}, before.version, retire)

    after = store.read()
    assert after.version == before.version
    assert after.resources == before.resources
    assert resource_detail(after, "OLD")["archived"] is False
    with store.connect() as conn:
        assert conn.execute("SELECT * FROM inventory_archive ORDER BY rowid").fetchall() == archive_before
        assert conn.execute("SELECT * FROM inventory_archive_groups ORDER BY rowid").fetchall() == indexes_before
        assert conn.execute("SELECT count(*) FROM idempotency WHERE key='fault-inventory-receipt'").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_inventory_receipt")

    committed = store.mutate("fault-inventory-receipt", {}, before.version, retire)
    assert committed["retired"] == ["OLD"] and not committed["replayed"]
    assert store.mutate("fault-inventory-receipt", {}, before.version, retire)["replayed"]
    assert resource_detail(store.read(), "OLD")["archived_version"] == before.version+1


def test_archive_queries_are_isolated_by_scenario_even_when_resource_ids_match(store):
    def seed(world):
        world.resources = [quote(world)]
    store.mutate("seed-first-scenario", {}, store.read().version, seed)
    store.mutate("retire-first-scenario", {}, store.read().version,
        lambda world: {"retired":archive_resources(world, ["OLD"])})
    old_world = store.read()
    old_source = resource_detail(old_world, "OLD")["resource"]

    def new_scenario(world):
        world.scenario_id = "second-inventory-scenario"
    store.mutate("change-inventory-scenario", {}, old_world.version, new_scenario)
    assert resource_history(store.read(), resource_id="OLD") == []
    assert resource_detail(store.read(), "OLD") is None

    def new_source(world):
        world.resources = [quote(world, name="Second scenario supplier record")]
    store.mutate("seed-second-scenario", {}, store.read().version, new_source)
    store.mutate("retire-second-scenario", {}, store.read().version,
        lambda world: {"retired":archive_resources(world, ["OLD"])})
    reopened = Store(store.path, operation_mode="live").read()

    assert resource_detail(reopened, "OLD")["resource"].name == "Second scenario supplier record"
    assert resource_detail(old_world, "OLD")["resource"] == old_source
    assert len(resource_history(reopened, resource_id="OLD")) == 1
    with pytest.raises(DomainError) as error:
        store.resource_detail("OLD", scenario_id=old_world.scenario_id)
    assert error.value.status == 409
    with store.connect() as conn:
        assert conn.execute("SELECT count(DISTINCT scenario_id) FROM inventory_archive WHERE entity_type='resource' AND entity_id='OLD'").fetchone()[0] == 2


def test_world_loading_is_lazy_and_targeted_resource_reads_filter_archive_sql(store, monkeypatch):
    def seed(world):
        world.resources = [quote(world, f"OLD-{index}") for index in range(25)]
    store.mutate("seed-targeted-history", {}, store.read().version, seed)
    store.mutate("retire-targeted-history", {}, store.read().version,
        lambda world: {"retired":archive_resources(world, [item.id for item in world.resources])})
    statements = []
    original_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(inventory_lifecycle.sqlite3, "connect", traced_connect)
    world = store.read()
    copied = world.model_copy(deep=True)
    assert not any("SELECT" in statement.upper() and "inventory_archive" in statement
        for statement in statements)
    assert "_inventory_context" not in copied.model_dump(mode="json")
    statements.clear()

    detail = resource_detail(copied, "OLD-7")

    assert detail["resource"].id == "OLD-7"
    selects = [statement for statement in statements
        if "SELECT" in statement.upper() and "inventory_archive" in statement]
    assert len(selects) == 1
    assert "a.entity_id='OLD-7'" in selects[0]
    assert "a.scenario_id='"+world.scenario_id+"'" in selects[0]


def test_keep_resource_protection_closes_transitive_onward_dependencies(store):
    world = store.read()
    def dependency(target):
        return AirportArrivalAssumption(source="operator_declared", confirmed=True,
            group_id="G1", group_fingerprint="a"*64, scenario_id=world.scenario_id,
            epoch=world.epoch, airport_id="OTP", onward_resource_id=target,
            onward_flight_fingerprint="b"*64, onward_departure_at=utcnow().isoformat(),
            dropoff=AirportDropoff(label="Airport entrance",
                coordinates=PositionCoordinates(latitude=44.5711, longitude=26.085)),
            processing_minutes=60)
    world.resources = [quote(world, "KEEP", airport_arrival_assumption=dependency("MIDDLE")),
        quote(world, "MIDDLE", airport_arrival_assumption=dependency("FINAL")),
        quote(world, "FINAL")]
    world.resources.extend(quote(world, f"ELIGIBLE-{index}") for index in range(MAX_RESOURCES-3))

    ensure_inventory_capacity(world, extra_resources=1, keep_resource_ids=["KEEP"])

    assert {"KEEP", "MIDDLE", "FINAL"}.issubset({item.id for item in world.resources})
    assert len(world.resources)+1 <= MAX_RESOURCES
