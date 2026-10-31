"""Operator reports use isolated SQLite; all real sockets are disabled by conftest."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.hotel_recovery_profile import PROFILE_KEY, HotelRecoveryProfileError, read_hotel_recovery_profile
from app.main import create_app
from app.models import Itinerary, Leg, Offer, Reservation, Resource
from app.operational_reports import ArrivalReportInput, OperationalReports, ResourceUnavailabilityInput
from app.store import DomainError, utcnow
from test_hotel_recovery_profile import hotel_command_setup
from test_live_commands import command_setup


@pytest.fixture
def reports(hotel_command_setup):
    configuration, store, _, _, _, _ = hotel_command_setup
    expiry = (utcnow() + timedelta(hours=1)).isoformat()
    def seed(world):
        for resource_id in ("Q1", "Q2"):
            world.resources.append(Resource(id=resource_id, name="Supplier dated transfer", kind="bus",
                origin="LHR", destination="OTP", departure=world.sim_time+120, arrival=world.sim_time+240,
                capacity=4, cost_per_person=40, source="fixture-supplier", valid_until=expiry,
                quote_total_cost=80, budget_total_cost=80, external_offer_id="supplier-offer-"+resource_id,
                provenance={"supplier_response": {"currency": "EUR", "price": "80.00", "status": "quoted"},
                    "quoted_at": utcnow().isoformat(), "source_schedule": "preserve exact supplier fact"}))
        for group_id, resource_id, status in (("G1", "Q1", "proposed"), ("G2", "Q1", "accepted"), ("OUT", "Q2", "confirmed")):
            resource = next(item for item in world.resources if item.id == resource_id)
            group = next(item for item in world.groups if item.id == group_id)
            group.status = "confirmed" if status == "confirmed" else "offered"
            world.offers.append(Offer(id="O-"+group_id, group_id=group_id, status=status,
                created_version=world.version+1, expires_at=expiry,
                itinerary=Itinerary(legs=[Leg(resource_id=resource_id, kind="bus", origin=resource.origin,
                    destination=resource.destination, departure=resource.departure, arrival=resource.arrival, cost=80)],
                    arrival=resource.arrival, total_cost=80),
                external_bookings=[{"resource_id": resource_id, "confirmation_reference": "PRIVATE-BOOKING"}] if status == "confirmed" else []))
            world.reservations.append(Reservation(id="R-"+group_id, offer_id="O-"+group_id, group_id=group_id,
                resource_id=resource_id, quantity=1, status=status, expires_at=expiry))
    store.mutate("seed-report-allocations", {}, store.read().version, seed)
    return configuration, store, OperationalReports(store)


def arrival(store, **overrides):
    world = store.read()
    ready = (datetime.fromisoformat(world.epoch) + timedelta(minutes=world.sim_time+60)).replace(second=0, microsecond=0)
    return ArrivalReportInput(**({"expected_version": world.version, "idempotency_key": "report-arrival-001",
        "group_ids": ["G1"], "airport_code": "SBZ", "ready_at": ready,
        "description": "Duty operator reports these passengers at Sibiu."} | overrides))


def unavailable(store, **overrides):
    return ResourceUnavailabilityInput(**({"expected_version": store.read().version,
        "idempotency_key": "report-unavailable-001", "description": "Operator reports this exact bus service has broken down."} | overrides))


def stable(world):
    return world.model_dump(mode="json", exclude={"sim_time"})


def test_arrival_preserves_identity_source_and_unrelated_allocations_with_explicit_provenance(reports):
    _, store, service = reports
    before = store.read()
    old_group = before.groups[0]
    request = arrival(store)
    started = utcnow()
    result = service.arrival("RC-ONE", request)
    after = store.read()
    group = after.groups[0]
    assert result["applied"] and not result["replayed"]
    assert after.version == before.version + 1
    assert group.origin == "SBZ"
    assert group.ready_at == int((request.ready_at - datetime.fromisoformat(after.epoch)).total_seconds()/60)
    assert group.model_dump(exclude={"origin", "ready_at", "provenance", "status"}) == old_group.model_dump(exclude={"origin", "ready_at", "provenance", "status"})
    assert group.provenance[PROFILE_KEY] == old_group.provenance[PROFILE_KEY]
    with pytest.raises(HotelRecoveryProfileError):
        read_hotel_recovery_profile(after, group)
    report = result["report"]
    assert started <= datetime.fromisoformat(report["received_at"]) <= utcnow()
    assert report["source"] == "operator_report" and report["ready_at"] == request.ready_at.isoformat()
    assert report["affected_group_ids"] == ["G1"] and report["affected_offer_ids"] == ["O-G1"]
    entry = group.provenance["arrival_reports"][0]
    assert entry["id"] == report["id"] and entry["previous_origin"] == "LHR"
    assert entry["previous_ready_at"] == old_group.ready_at
    assert after.groups[1:] == before.groups[1:]
    assert after.offers[0].status == "invalidated" and after.reservations[0].status == "released"
    assert after.offers[1:] == before.offers[1:] and after.reservations[1:] == before.reservations[1:]
    assert after.resources == before.resources and after.incidents == before.incidents
    new_node = next(item for item in after.airports if item.id == "SBZ")
    assert new_node.available is False and new_node.operational_status == "unknown"
    assert after.airports[:-1] == before.airports
    assert after.events[-1].type == "operator_arrival_report" and after.events[-1].data == report
    assert after.decisions == before.decisions
    replay = service.arrival("RC-ONE", request)
    assert replay["replayed"] and replay["report"] == report
    assert stable(store.read()) == stable(after)


def test_arrival_can_record_missed_deadline_or_destination_without_inventing_completion(reports):
    _, store, service = reports
    before = store.read()
    ready = datetime.fromisoformat(before.epoch) + timedelta(minutes=before.groups[0].deadline+1)
    service.arrival("RC-ONE", arrival(store, airport_code="OTP", ready_at=ready))
    world = store.read()
    assert world.groups[0].origin == world.groups[0].destination == "OTP"
    assert world.groups[0].deadline == before.groups[0].deadline < world.groups[0].ready_at
    assert world.groups[0].status != "confirmed"
    assert world.resources == before.resources
    assert world.offers[0].external_bookings == []


def test_unavailability_preserves_supplier_facts_and_only_invalidates_dependent_proposals(reports):
    _, store, service = reports
    before = store.read()
    request = unavailable(store)
    result = service.unavailable("Q1", request)
    after = store.read()
    resource = after.resources[0]
    assert not resource.available
    assert resource.model_dump(exclude={"available", "provenance"}) == before.resources[0].model_dump(exclude={"available", "provenance"})
    assert {key: value for key, value in resource.provenance.items() if key != "unavailability_reports"} == before.resources[0].provenance
    assert resource.provenance["unavailability_reports"] == [result["report"]]
    assert result["report"]["affected_group_ids"] == ["G1", "G2"]
    assert result["report"]["affected_offer_ids"] == ["O-G1", "O-G2"]
    assert result["report"]["source"] == "operator_report"
    assert [offer.status for offer in after.offers] == ["invalidated", "invalidated", "confirmed"]
    assert [item.status for item in after.reservations] == ["released", "released", "confirmed"]
    assert after.resources[1:] == before.resources[1:] and after.groups[2] == before.groups[2]
    assert after.offers[2] == before.offers[2] and after.reservations[2] == before.reservations[2]
    assert after.airports == before.airports and after.incidents == before.incidents
    assert after.events[-1].type == "operator_resource_unavailability" and after.events[-1].data == result["report"]
    assert service.unavailable("Q1", request)["replayed"]
    assert stable(store.read()) == stable(after)
    with pytest.raises(DomainError) as collision:
        service.unavailable("Q2", request)
    assert collision.value.status == 409


@pytest.mark.parametrize("action,evidence", [
    ("arrival", "group"), ("arrival", "offer"), ("arrival", "reservation"), ("arrival", "external"),
    ("unavailable", "resource"), ("unavailable", "offer"), ("unavailable", "reservation"), ("unavailable", "external"),
])
def test_confirmed_evidence_requires_operator_review_before_report_mutation(reports, action, evidence):
    _, store, service = reports
    def confirm(world):
        if evidence == "group": world.groups[0].status = "confirmed"
        elif evidence == "resource": world.resources[0].booking_status = "confirmed"
        elif evidence == "offer": world.offers[0].status = "confirmed"
        elif evidence == "reservation": world.reservations[0].status = "confirmed"
        elif evidence == "external": world.offers[0].external_bookings = [{"resource_id": "Q1", "confirmation_reference": "KEEP"}]
    store.mutate("fixture-confirmed-"+evidence, {}, store.read().version, confirm)
    before = stable(store.read())
    with pytest.raises(DomainError) as rejected:
        if action == "arrival": service.arrival("RC-ONE", arrival(store, group_ids=["G1", "G2"]))
        else: service.unavailable("Q1", unavailable(store))
    assert rejected.value.status == 409 and "Operator review" in rejected.value.message
    assert stable(store.read()) == before


@pytest.mark.parametrize("group_ids", [["OUT"], ["G1", "OUT"], ["G1", "MISSING"]])
def test_arrival_scope_rejects_unbound_or_unknown_group_without_partial_change(reports, group_ids):
    _, store, service = reports
    before = stable(store.read())
    with pytest.raises(DomainError):
        service.arrival("RC-ONE", arrival(store, group_ids=group_ids))
    assert stable(store.read()) == before


@pytest.mark.parametrize("changes", [
    {"description": "   "}, {"description": "bad\nreport"}, {"description": "bad\x7freport"},
    {"description": "bad\u202ereport"}, {"description": "x"*2001}, {"group_ids": []},
    {"group_ids": ["G1", "G1"]}, {"group_ids": ["G1\n"]}, {"airport_code": "ZZZ"},
    {"airport_code": "LHR\n"},
    {"ready_at": "2026-11-01T12:00:00"}, {"ready_at": "2026-11-01T12:00:01Z"},
    {"ready_at": "2026-11-01T12:00:00+00:00:30"},
    {"ready_at": "2026-11-01T12:00:00.000001Z"}, {"ready_at": True},
])
def test_arrival_requires_explicit_bounded_typed_inputs(reports, changes):
    _, store, _ = reports
    with pytest.raises(ValidationError):
        arrival(store, **changes)


def test_readiness_offset_is_preserved_and_same_instant_is_used_for_planning(reports):
    _, store, service = reports
    request = arrival(store)
    offset = timezone(timedelta(hours=5, minutes=30))
    request = request.model_copy(update={"ready_at": request.ready_at.astimezone(offset)})
    result = service.arrival("RC-ONE", request)
    assert result["report"]["ready_at"].endswith("+05:30")
    world = store.read()
    assert datetime.fromisoformat(world.epoch) + timedelta(minutes=world.groups[0].ready_at) == request.ready_at


def test_report_history_is_appended_without_discarding_earlier_operator_or_supplier_evidence(reports):
    _, store, service = reports
    first = service.unavailable("Q1", unavailable(store))
    second = service.unavailable("Q1", unavailable(store, idempotency_key="second-outage-confirmation",
        description="A second operator confirms the service remains unavailable."))
    resource = store.read().resources[0]
    assert resource.provenance["unavailability_reports"] == [first["report"], second["report"]]
    assert first["report"]["previous_available"] is True and second["report"]["previous_available"] is False
    assert second["report"]["affected_offer_ids"] == []
    assert not resource.available and resource.provenance["supplier_response"]["status"] == "quoted"


@pytest.mark.parametrize("action", ["arrival", "unavailable"])
def test_concurrent_distinct_reports_cannot_both_apply_the_same_world_version(reports, action):
    _, store, service = reports
    before = store.read()
    if action == "arrival":
        requests = [arrival(store, idempotency_key=f"concurrent-arrival-{i}") for i in range(2)]
        apply = lambda req: service.arrival("RC-ONE", req)
    else:
        requests = [unavailable(store, idempotency_key=f"concurrent-outage-{i}") for i in range(2)]
        apply = lambda req: service.unavailable("Q1", req)
    def run(req):
        try: return apply(req)
        except DomainError as exc: return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, requests))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert [result.status for result in results if isinstance(result, DomainError)] == [409]
    assert store.read().version == before.version + 1


def test_concurrent_same_key_records_exactly_one_report(reports):
    _, store, service = reports
    request = unavailable(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.unavailable("Q1", request), range(2)))
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert results[0]["report"] == results[1]["report"]
    assert len(store.read().resources[0].provenance["unavailability_reports"]) == 1


@pytest.mark.parametrize("action", ["arrival", "unavailable"])
def test_endpoints_require_operator_auth_and_route_ids_are_part_of_request_identity(reports, action):
    configuration, store, _ = reports
    app = create_app(configuration.model_copy(update={"operator_token": "report-test-token"}))
    request = arrival(store) if action == "arrival" else unavailable(store)
    path = "/api/incidents/RC-ONE/arrival-report" if action == "arrival" else "/api/live/resources/Q1/unavailability"
    with TestClient(app) as client:
        assert client.post(path, json=request.model_dump(mode="json")).status_code == 401
        response = client.post(path, json=request.model_dump(mode="json"), headers={"Authorization": "Bearer report-test-token"})
        assert response.status_code == 200, response.text
        assert response.json()["report"]["source"] == "operator_report"
        assert response.json()["applied"] is True
        again = client.post(path, json=request.model_dump(mode="json"), headers={"Authorization": "Bearer report-test-token"})
        assert again.status_code == 200 and again.json()["replayed"]


def test_simulation_reports_and_unknown_route_ids_never_mutate(reports):
    _, store, service = reports
    request = unavailable(store)
    for value in ("MISSING", "bad\nroute", "x"*129):
        before = stable(store.read())
        with pytest.raises(DomainError): service.unavailable(value, request)
        assert stable(store.read()) == before
    store.mutate("fixture-simulation-mode", {}, store.read().version, lambda world: setattr(world, "mode", "simulation"))
    before = stable(store.read())
    with pytest.raises(DomainError): service.arrival("RC-ONE", arrival(store))
    with pytest.raises(DomainError): service.unavailable("Q1", unavailable(store))
    assert stable(store.read()) == before
