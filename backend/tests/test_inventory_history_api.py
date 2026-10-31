"""Archive -> authorized source view -> token-scoped passenger history, offline."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.inventory_lifecycle import archive_resources, ensure_inventory_capacity
from app.main import create_app
from app.models import Airport, Itinerary, Leg, Offer, PassengerGroup, Resource
from app.store import utcnow


OPERATOR = "offline-history-operator-token-123456789"
HEADERS = {"Authorization": "Bearer " + OPERATOR}


@pytest.fixture
def history_app(tmp_path):
    app = create_app(Settings(_env_file=None, operation_mode="live", operator_token=OPERATOR,
                              database_path=str(tmp_path / "history.sqlite3")))
    store = app.state.store
    def seed(world):
        world.airports.append(Airport(id="OTP", code="OTP", name="Bucharest airport",
                                     lat=44.57, lon=26.08, available=False))
        for suffix in ("A", "B"):
            world.groups.append(PassengerGroup(id="GROUP-" + suffix, name="Fixture party " + suffix,
                size=2, adult_count=2, members=["Fixture adult 1", "Fixture adult 2"],
                origin="CRA", destination="OTP", deadline=world.sim_time+1000,
                ready_at=world.sim_time+10, budget=500, original_flight_id="EK5",
                token="private-passenger-"+suffix, source="manifest_import"))
            location = Airport(id="HOTEL-"+suffix, code="HOTEL-"+suffix,
                name="Original property "+suffix, lat=44.32, lon=23.81, kind="hotel", available=False)
            world.airports.append(location)
            resource = Resource(id="HISTORY-"+suffix, kind="hotel", name="Original room "+suffix,
                origin=location.id, destination=location.id, departure=world.sim_time+30,
                arrival=world.sim_time+500, capacity=1, cost_per_person=0, quote_total_cost=123.45,
                quote_rooms=1, source="offline-hotel-fixture", eligible_group_ids=["GROUP-"+suffix],
                capacity_scope="quoted_request", requires_revalidation=True, available=False,
                valid_until=(utcnow()-timedelta(minutes=1)).isoformat(),
                provenance={"provider_offer_id":"supplier-reference-"+suffix,
                            "currency":"EUR", "total_price":"123.45", "group_marker":suffix})
            world.resources.append(resource)
            leg = Leg(resource_id=resource.id, kind="hotel", origin=location.id,
                      destination=location.id, departure=resource.departure, arrival=resource.arrival, cost=123.45)
            world.offers.append(Offer(id="OLD-OFFER-"+suffix, group_id="GROUP-"+suffix,
                itinerary=Itinerary(legs=[leg], arrival=resource.arrival, total_cost=123.45),
                status="expired", expires_at=resource.valid_until, created_version=world.version))
    store.mutate("history-fixture-seed", {}, store.read().version, seed)
    with TestClient(app) as client:
        yield app, client


def compact(app):
    store = app.state.store
    def retire(world):
        archive_resources(world, {"HISTORY-A", "HISTORY-B"}, reason="Expired quote replaced")
        # Real capacity pressure removes A's unreferenced property while B's
        # location is deliberately retained. No protected passenger endpoint moves.
        ensure_inventory_capacity(world, extra_locations=29, keep_location_ids={"HOTEL-B"})
        for index in range(29):
            identity = f"STATION-{index}"
            world.airports.append(Airport(id=identity, code=identity, name="Fixture station",
                                         lat=44, lon=24, kind="station", available=False))
    return store.mutate("history-fixture-compact", {}, store.read().version, retire)


def get_source(app, client, resource_id="HISTORY-A", **kwargs):
    return client.get("/api/inventory/resources/"+resource_id,
                      params={"scenario_id":app.state.store.read().scenario_id}, **kwargs)


def test_operator_record_preserves_source_and_archived_property_without_restoring_supply(history_app):
    app, client = history_app
    original = get_source(app, client, headers=HEADERS)
    assert original.status_code == 200 and original.json()["archived"] is False
    compact(app)
    before = app.state.store.read()
    assert not before.resources and "HOTEL-A" not in {node.id for node in before.airports}
    response = get_source(app, client, headers=HEADERS)
    assert response.status_code == 200
    detail = response.json()
    assert detail["archived"] is True and detail["archived_at"] and detail["archived_version"]
    assert detail["resource"]["provenance"] == original.json()["resource"]["provenance"]
    assert detail["resource"]["quote_total_cost"] == 123.45
    assert detail["locations"][0]["name"] == "Original property A"
    after = app.state.store.read()
    assert after.version == before.version and not after.resources
    assert {node.id for node in after.airports} == {node.id for node in before.airports}


def test_operator_auth_is_checked_before_source_or_archive_read(history_app, monkeypatch):
    app, client = history_app
    scenario = app.state.store.read().scenario_id
    def denied():
        raise AssertionError("Unauthenticated source history must not read the operation")
    monkeypatch.setattr(app.state.store, "read", denied)
    response = client.get("/api/inventory/resources/HISTORY-A", params={"scenario_id":scenario})
    assert response.status_code == 401
    assert "supplier-reference" not in response.text


def test_history_rejects_stale_operation_and_unknown_id(history_app):
    app, client = history_app
    compact(app)
    assert client.get("/api/inventory/resources/HISTORY-A", params={"scenario_id":"different-operation"},
                      headers=HEADERS).status_code == 409
    assert get_source(app, client, "MISSING", headers=HEADERS).status_code == 404
    assert client.get("/api/inventory/resources/HISTORY-A", headers=HEADERS).status_code == 422


def test_passenger_history_contains_only_own_last_offer_and_has_no_live_inventory(history_app):
    app, client = history_app
    compact(app)
    response = client.get("/api/passenger/private-passenger-A")
    assert response.status_code == 200
    state = response.json()
    assert state["offer"] is None and state["resources"] == []
    assert state["last_offer"]["id"] == "OLD-OFFER-A"
    assert [item["id"] for item in state["history_resources"]] == ["HISTORY-A"]
    assert state["history_archived_resource_ids"] == ["HISTORY-A"]
    assert [item["name"] for item in state["history_airports"]] == ["Original property A"]
    assert "supplier-reference-B" not in response.text
    assert "private-passenger-" not in response.text
    assert client.get("/api/passenger/unknown-token").status_code == 404


def test_passenger_without_prior_offer_does_not_receive_history(history_app):
    app, client = history_app
    compact(app)
    def remove_offer(world):
        world.offers = [offer for offer in world.offers if offer.group_id != "GROUP-A"]
    store = app.state.store
    store.mutate("history-fixture-clear-offer", {}, store.read().version, remove_offer)
    state = client.get("/api/passenger/private-passenger-A").json()
    assert state["history_resources"] == state["history_airports"] == state["history_archived_resource_ids"] == []


def test_recorded_last_offer_without_archive_evidence_remains_explicitly_incomplete(history_app):
    app, client = history_app
    # Models loaded from old data can already have missing source rows. Never
    # invent a supplier record from the itinerary's cost and source identifier.
    world = app.state.store.read()
    world.resources = []
    app.state.service.store.read = lambda: world
    state = client.get("/api/passenger/private-passenger-A").json()
    assert state["last_offer"]["id"] == "OLD-OFFER-A"
    assert state["history_resources"] == [] and state["history_archived_resource_ids"] == []
