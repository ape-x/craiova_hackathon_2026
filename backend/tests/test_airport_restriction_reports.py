"""Offline case restriction transactions, immutable booking facts and exact recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.airport_restrictions import (AirportRestrictionReportInput, AirportRestrictions,
    apply_airport_restriction_report, enqueue_airport_restriction_recovery)
from app.main import create_app
from app.models import (CaseAirportRestriction, IncidentFlightReference, Itinerary, Leg,
    Offer, PassengerGroup, RecoveryIncident, Reservation, Resource)
from app.recovery_jobs import RecoveryJobRequest, scope_fingerprint
from app.store import DomainError, Store, utcnow
from test_live_commands import command_setup
from test_recovery_jobs import request as job_request, setup


def report(store, **changes):
    return AirportRestrictionReportInput(**({"expected_version": store.read().version,
        "idempotency_key": "airport-report-fixture", "airport_code": "OTP",
        "operations": ["arrivals"], "action": "restrict",
        "description": "Operator reports arrivals at OTP unavailable for this case."} | changes))


def stable(store):
    return store.read().model_dump(exclude={"sim_time"})


@pytest.fixture
def restriction_setup(command_setup):
    settings, store, _, jobs, ai = command_setup
    expires = (utcnow() + timedelta(hours=3)).isoformat()
    def seed(world):
        case = world.incidents[0]
        case.unavailable_airport_code = "SBZ"
        other = case.model_copy(deep=True)
        other.id, other.group_ids, other.unavailable_airport_code = "RC-OTHER", ["OUT"], None
        world.incidents.append(other)
        world.groups[1].origin, world.groups[1].destination = "OTP", "CRA"
        world.groups[2].incident_id = other.id
        for identity, destination in (("CONF", "OTP"), ("SEG", "LHR")):
            group = world.groups[0].model_copy(deep=True)
            group.id, group.name, group.token, group.destination = identity, identity, "offline-token-" + identity, destination
            world.groups.append(group)
            case.group_ids.append(identity)
        def resource(identity, kind, origin, destination, **extra):
            return Resource(id=identity, kind=kind, name="Offline sourced itinerary", origin=origin,
                destination=destination, departure=world.sim_time + 200, arrival=world.sim_time + 300,
                capacity=20, cost_per_person=20, source="operator_confirmed", valid_until=expires, **extra)
        world.resources = [resource("F-IN", "flight", "CRA", "OTP"), resource("F-OUT", "flight", "OTP", "CRA"),
            resource("BUS", "bus", "CRA", "OTP"), resource("F-SEG", "flight", "CRA", "LHR",
                provenance={"segments": [{"origin": "CRA", "destination": "LROP"},
                    {"origin": "LROP", "destination": "LHR"}]})]
        for identity, group_id, resource_id, status in (
                ("O-IN", "G1", "F-IN", "proposed"), ("O-BUS", "G1", "BUS", "proposed"),
                ("O-OUT", "G2", "F-OUT", "accepted"), ("O-OTHER", "OUT", "F-IN", "proposed"),
                ("O-CONF", "CONF", "F-IN", "confirmed"), ("O-SEG", "SEG", "F-SEG", "held")):
            item = next(item for item in world.resources if item.id == resource_id)
            group = next(group for group in world.groups if group.id == group_id)
            group.status = "confirmed" if status == "confirmed" else "offered"
            world.offers.append(Offer(id=identity, group_id=group_id, status=status, expires_at=expires,
                created_version=world.version + 1, external_bookings=[{"resource_id": item.id,
                    "confirmation_reference": "OFFLINE-BOOKING"}] if status == "confirmed" else [],
                itinerary=Itinerary(legs=[Leg(resource_id=item.id, kind=item.kind, origin=item.origin,
                    destination=item.destination, departure=item.departure, arrival=item.arrival, cost=20)],
                    arrival=item.arrival, total_cost=20)))
            world.reservations.append(Reservation(id="R-" + identity, offer_id=identity, group_id=group_id,
                resource_id=item.id, quantity=1, status=status, expires_at=expires))
    store.mutate("seed-airport-restriction-fixture", {}, store.read().version, seed)
    return settings, store, jobs, ai, AirportRestrictions(store, jobs)


def test_new_optional_fields_do_not_change_legacy_payload_or_scope_fingerprint(setup):
    _, store, _, _, _ = setup
    world = store.read()
    case = world.incidents[0]
    raw = case.model_dump(mode="json")
    assert not {"airport_restrictions", "airport_restriction_reports", "original_unavailable_airport_code"} & raw.keys()
    assert RecoveryIncident.model_validate(raw).model_dump(mode="json") == raw
    before = scope_fingerprint(world, case.id, ["G1"])
    case.airport_restrictions = []
    case.airport_restriction_reports = []
    assert scope_fingerprint(world, case.id, ["G1"]) == before


@pytest.mark.parametrize(("operation", "invalidated", "conflicts"), [
    ("arrivals", {"O-IN", "O-SEG"}, True),
    ("departures", {"O-OUT", "O-SEG"}, False),
])
def test_directional_report_only_invalidates_dependent_case_proposals_and_preserves_sources(restriction_setup, operation, invalidated, conflicts):
    _, store, _, _, service = restriction_setup
    before = store.read()
    request = report(store, airport_code="LROP", operations=[operation])
    result = service.report("RC-ONE", request)
    after = store.read()
    assert result["applied"] and not result["replayed"] and result["command_interpretation_calls"] == 0
    assert request.airport_code == result["report"]["airport_code"] == "OTP"
    assert set(result["report"]["affected_offer_ids"]) == invalidated
    assert result["manual_review_required"] is conflicts
    assert result["confirmed_conflicts"] == result["report"]["confirmed_conflicts"]
    if conflicts:
        assert result["confirmed_conflicts"] == [{"group_id": "CONF", "offer_id": "O-CONF", "resource_ids": ["F-IN"]}]
    assert after.airports == before.airports and after.resources == before.resources
    assert after.incidents[1] == before.incidents[1]
    assert after.observations == before.observations
    for old, new in zip(before.groups, after.groups):
        assert new.model_dump(exclude={"status"}) == old.model_dump(exclude={"status"})
    for old, new in zip(before.offers, after.offers):
        if old.id in invalidated:
            assert new.status == "invalidated"
        else:
            assert old == new
    for old, new in zip(before.reservations, after.reservations):
        assert new.status == ("released" if old.offer_id in invalidated else old.status)
    case = after.incidents[0]
    assert case.unavailable_airport_code is None and case.original_unavailable_airport_code == "SBZ"
    inherited = [entry for entry in case.airport_restrictions if entry.airport_code == "SBZ"]
    assert {entry.operation for entry in inherited} == {"arrivals", "departures"}
    assert all(entry.inherited_from_legacy and entry.report_id == case.id and entry.received_at == before.incidents[0].created_at for entry in inherited)
    assert case.flight_ref == before.incidents[0].flight_ref and case.description == before.incidents[0].description
    assert case.airport_restriction_reports[-1].id == result["report"]["id"]
    assert store.audit_history()[-1]["type"] == "operator_airport_restriction"
    snapshot = stable(store)
    replay = service.report("RC-ONE", request)
    assert replay["replayed"] and replay["report"] == result["report"] and stable(store) == snapshot


def test_clear_migrates_legacy_one_operation_and_never_restores_offers_or_global_permissions(restriction_setup):
    _, store, _, _, service = restriction_setup
    first = service.report("RC-ONE", report(store, operations=["arrivals", "departures"]))
    before = store.read()
    result = service.report("RC-ONE", report(store, action="clear", operations=["departures"], idempotency_key="clear-departures-fixture"))
    after = store.read()
    assert result["report"]["affected_offer_ids"] == [] and after.offers == before.offers
    assert after.reservations == before.reservations and after.groups == before.groups
    assert after.airports == before.airports and after.resources == before.resources
    assert {(r.airport_code, r.operation) for r in after.incidents[0].airport_restrictions} == {("OTP", "arrivals"), ("SBZ", "arrivals"), ("SBZ", "departures")}
    assert result["manual_review_required"] and result["confirmed_conflicts"] == first["confirmed_conflicts"]
    cleared = service.report("RC-ONE", report(store, airport_code="SBZ", action="clear", idempotency_key="clear-legacy-arrivals"))
    assert cleared["report"]["action"] == "clear"
    assert ("SBZ", "departures") in {(r.airport_code, r.operation) for r in store.read().incidents[0].airport_restrictions}
    assert "does not assert airport permission" in result["explanation"]


def test_clear_first_legacy_update_preserves_the_other_operation(restriction_setup):
    _, store, _, _, service = restriction_setup
    result = service.report("RC-ONE", report(store, airport_code="SBZ", action="clear"))
    case = store.read().incidents[0]
    assert [(entry.airport_code, entry.operation) for entry in case.airport_restrictions] == [("SBZ", "departures")]
    assert result["report"]["migrated_legacy_airport_code"] == case.original_unavailable_airport_code == "SBZ"
    assert case.unavailable_airport_code is None


def test_repeated_updates_are_unique_bounded_and_audit_noop_clears(restriction_setup):
    _, store, _, _, service = restriction_setup
    for index in range(12):
        result = service.report("RC-ONE", report(store, airport_code="LHR", action="clear", idempotency_key=f"clear-unused-{index:02d}"))
        assert result["report"]["affected_offer_ids"] == []
    case = store.read().incidents[0]
    assert len(case.airport_restriction_reports) == 10
    assert len(case.airport_restrictions) == 2
    assert len([item for item in store.audit_history() if item["type"] == "operator_airport_restriction"]) == 12


@pytest.mark.parametrize("groups", [["G1"], ["G1", "OUT"], ["CONF"], ["MISSING"]])
def test_optional_recovery_uses_only_explicit_case_scope_and_preserves_known_report_blockers(restriction_setup, groups):
    _, store, jobs, ai, service = restriction_setup
    request = report(store, operations=["departures"], recovery={"group_ids": groups, "modes": ["flight"], "max_provider_calls": 3})
    result = service.report("RC-ONE", request)
    assert result["applied"] and result["report"]["recovery_group_ids"] == sorted(groups)
    assert result["command_interpretation_calls"] == 0 and result["recovery_provider_call_limit"] == 3
    if groups == ["G1"]:
        assert result["recovery_job"]["group_ids"] == ["G1"]
        assert result["recovery_job"]["max_provider_calls"] == 3
        assert result["recovery_job"]["calls_dispatched"] == 0
        assert result["recovery_job"]["source_version"] == store.read().version
    else:
        assert result["recovery_blocked"]["code"] == "recovery_prerequisite_failed"
        assert "recovery_job" not in result and jobs.list("RC-ONE")["jobs"] == []
    assert ai.calls == []


def test_hotel_mode_is_not_dropped_or_replaced_with_saved_inputs(restriction_setup):
    _, store, jobs, _, service = restriction_setup
    result = service.report("RC-ONE", report(store, operations=["departures"], recovery={"group_ids": ["G1"],
        "modes": ["flight", "hotel", "bus"], "max_provider_calls": 10}))
    assert result["applied"] and result["recovery_blocked"]["code"] == "hotel_setup_required"
    assert jobs.list("RC-ONE")["jobs"] == []


def test_confirmed_case_conflict_blocks_unconfirmed_group_search_until_restriction_is_cleared(restriction_setup):
    _, store, jobs, ai, service = restriction_setup
    before = store.read()
    options = {"group_ids": ["G1"], "max_provider_calls": 3}
    result = service.report("RC-ONE", report(store, recovery=options))
    assert result["applied"] and result["manual_review_required"]
    assert result["recovery_blocked"]["code"] == "recovery_prerequisite_failed"
    assert "any groups in the case" in result["recovery_blocked"]["explanation"]
    assert jobs.list("RC-ONE")["jobs"] == [] and ai.calls == []
    after = store.read()
    assert next(item for item in after.offers if item.id == "O-CONF") == next(item for item in before.offers if item.id == "O-CONF")
    assert next(item for item in after.reservations if item.offer_id == "O-CONF") == next(item for item in before.reservations if item.offer_id == "O-CONF")
    cleared = service.report("RC-ONE", report(store, action="clear", recovery=options,
        idempotency_key="clear-conflict-and-recover"))
    assert cleared["applied"] and not cleared["confirmed_conflicts"]
    assert cleared["recovery_job"]["group_ids"] == ["G1"] and cleared["recovery_job"]["calls_dispatched"] == 0
    assert next(item for item in store.read().offers if item.id == "O-IN").status == "invalidated"


@pytest.mark.parametrize("identity", ["g1", "G" * 41])
def test_recovery_never_normalizes_the_exact_reported_existing_group_identity(setup, identity):
    _, store, jobs, _, travel = setup
    def rename(world):
        world.groups[0].id = identity
        world.incidents[0].group_ids[0] = identity
    store.mutate("rename-offline-group", {}, store.read().version, rename)
    result = AirportRestrictions(store, jobs).report("CASE", report(store, recovery={"group_ids": [identity]}))
    assert result["applied"] and result["report"]["recovery_group_ids"] == [identity]
    assert result["recovery_blocked"]["code"] == "recovery_prerequisite_failed"
    assert store.read().groups[0].id == identity and not travel.calls
    assert jobs.list("CASE")["jobs"] == []


@pytest.mark.parametrize("unknown", [False, True])
def test_optional_recovery_drains_predecessor_and_never_bypasses_unknown_ancestry(setup, unknown):
    _, store, jobs, _, travel = setup
    old = jobs.start("CASE", job_request(store, max_provider_calls=6))["job"]
    _, owner = jobs.ledger.claim_next()
    step = jobs.ledger.dispatch(old["id"], owner, "searching", "Offline source", {"old": True}, ["G1"])
    result = AirportRestrictions(store, jobs).report("CASE", report(store, airport_code="LHR",
        recovery={"group_ids": ["G1"], "max_provider_calls": 3}))
    successor = result["recovery_job"]
    assert successor["stage"] == "waiting_for_previous" and successor["supersedes_job_ids"] == [old["id"]]
    assert jobs.get(old["id"])["job"]["cancel_requested"] and jobs.ledger.claim_next() is None
    if not unknown:
        jobs.ledger.outcome(old["id"], owner, step, "succeeded", "Known offline response", {})
    jobs.ledger.finish(old["id"], owner, "cancelled", "Offline drained")
    claimed = jobs.ledger.claim_next()
    if unknown:
        assert claimed is None and jobs.get(successor["id"])["job"]["status"] == "needs_review"
    else:
        assert claimed[0]["id"] == successor["id"]
    assert successor["max_provider_calls"] == 3 and not travel.calls


@pytest.mark.parametrize("known", [False, True])
def test_savepoint_preserves_report_only_on_known_failure_and_never_partial_job_state(setup, monkeypatch, known):
    _, store, jobs, _, _ = setup
    old = jobs.start("CASE", job_request(store))["job"]
    before = stable(store)
    original = jobs.start_in_transaction
    def partial_failure(*args, **kwargs):
        original(*args, **kwargs)
        if known:
            raise DomainError(429, "Fixture queue rejection", {"code": "recovery_queue_full"})
        raise RuntimeError("Fixture unexpected queue error")
    monkeypatch.setattr(jobs, "start_in_transaction", partial_failure)
    service = AirportRestrictions(store, jobs)
    request = report(store, recovery={"group_ids": ["G1"], "max_provider_calls": 3})
    if known:
        result = service.report("CASE", request)
        assert result["applied"] and result["recovery_blocked"]["code"] == "recovery_queue_unavailable"
        assert service.report("CASE", request)["replayed"]
    else:
        with pytest.raises(RuntimeError):
            service.report("CASE", request)
        assert stable(store) == before
    attempts = jobs.list("CASE")["jobs"]
    assert len(attempts) == 1 and attempts[0]["id"] == old["id"]
    assert attempts[0]["status"] == "queued" and not attempts[0]["cancel_requested"]


@pytest.mark.parametrize("blocked", [False, True])
def test_voice_integration_helper_preserves_prebuilt_identity_instructions_scope_and_total_quota(setup, blocked):
    _, store, jobs, _, travel = setup
    typed = report(store, airport_code="LHR")
    def apply(world):
        return apply_airport_restriction_report(world, "CASE", typed, recovery_group_ids=["G1"])
    def enqueue(conn, response):
        current = store._load(conn)
        supplied = None if blocked else RecoveryJobRequest(expected_version=current.version,
            idempotency_key="voice-exact-prebuilt-key", group_ids=["G1"], max_provider_calls=3,
            instructions="Preserve these exact validated voice instructions.")
        failure = {"code": "insufficient_call_allowance", "explanation": "Original cap leaves insufficient job allowance."} if blocked else None
        enqueue_airport_restriction_recovery(conn, store, jobs, response, typed, job_request=supplied,
            recovery_blocked=failure, interpretation_calls=1, total_call_limit=2 if blocked else 4)
    result = store.mutate("voice-restriction-fixture", typed.model_dump(mode="json"), typed.expected_version, apply, on_commit=enqueue)
    assert result["command_interpretation_calls"] == 1 and result["applied"]
    assert result["recovery_total_call_limit"] == (2 if blocked else 4)
    if blocked:
        assert result["recovery_blocked"]["code"] == "insufficient_call_allowance"
        assert jobs.list("CASE")["jobs"] == []
    else:
        with store.connect() as conn:
            saved = jobs.ledger._load(conn, result["recovery_job"]["id"])["_request"]
        assert saved["idempotency_key"] == "voice-exact-prebuilt-key"
        assert saved["instructions"] == "Preserve these exact validated voice instructions."
        assert saved["max_provider_calls"] == 3 and saved["group_ids"] == ["G1"]
    assert not travel.calls


def test_http_operator_auth_exact_replay_and_changed_payload_conflict(setup):
    settings, store, _, ai, travel = setup
    settings.operator_token = "offline-airport-restriction-token"
    app = create_app(settings)
    app.state.jobs.ai, app.state.jobs.travel = ai, travel
    app.state.jobs.start_worker = lambda: None
    body = report(store, recovery={"group_ids": ["G1"], "max_provider_calls": 3}).model_dump(mode="json")
    path, headers = "/api/incidents/CASE/airport-restrictions", {"Authorization": "Bearer " + settings.operator_token}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        first = client.post(path, json=body, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["recovery_job"]["group_ids"] == ["G1"]
        again = client.post(path, json=body, headers=headers)
        assert again.status_code == 200 and again.json()["replayed"]
        assert again.json()["report"] == first.json()["report"]
        assert client.post(path, json={**body, "operations": ["departures"]}, headers=headers).status_code == 409
    assert not ai.plans and not travel.calls


def test_concurrent_same_key_applies_one_report_and_queues_one_job(setup):
    _, store, jobs, _, _ = setup
    service = AirportRestrictions(store, jobs)
    request = report(store, recovery={"group_ids": ["G1"], "max_provider_calls": 3})
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.report("CASE", request), range(2)))
    assert sorted(item["replayed"] for item in results) == [False, True]
    assert results[0]["report"] == results[1]["report"]
    assert results[0]["recovery_job"]["id"] == results[1]["recovery_job"]["id"]
    assert len(store.read().incidents[0].airport_restriction_reports) == 1


@pytest.mark.parametrize("changes", [
    {"airport_code": "XYZ"}, {"operations": []}, {"operations": ["arrivals", "arrivals"]},
    {"operations": ["both"]}, {"action": "reopen"}, {"description": "a\ncontrol"},
    {"recovery": {"group_ids": ["G1", "G1"]}}, {"recovery": {"group_ids": ["G1"], "max_provider_calls": True}},
])
def test_invalid_report_never_changes_case_or_starts_jobs(setup, changes):
    _, store, jobs, _, _ = setup
    before = stable(store)
    with pytest.raises(ValidationError):
        report(store, **changes)
    assert stable(store) == before and jobs.list("CASE")["jobs"] == []


def test_active_entry_uniqueness_and_capacity_are_enforced_without_partial_mutation(setup):
    _, store, jobs, _, _ = setup
    from app.airport_catalog import _catalog
    codes = [record["iata"] for record in _catalog()[1] if record["iata"] and record["iata"] not in ("OTP", "SBZ")][:64]
    def fill(world):
        world.incidents[0].unavailable_airport_code = None
        world.incidents[0].airport_restrictions = [CaseAirportRestriction(airport_code=code,
            operation="arrivals", report_id="fixture", received_at=utcnow().isoformat(), description="Fixture restriction") for code in codes]
    store.mutate("fill-fixture-restrictions", {}, store.read().version, fill)
    before = stable(store)
    with pytest.raises(DomainError, match="limit"):
        AirportRestrictions(store, jobs).report("CASE", report(store))
    assert stable(store) == before
    case = store.read().incidents[0]
    with pytest.raises(ValidationError):
        case.airport_restrictions = [case.airport_restrictions[0], case.airport_restrictions[0]]


@pytest.mark.parametrize("problem", ["stale", "unknown_case", "simulation"])
def test_report_requires_current_existing_live_case(setup, problem):
    _, store, jobs, _, _ = setup
    if problem == "simulation":
        store.mutate("fixture-simulation", {}, store.read().version, lambda world: setattr(world, "mode", "simulation"))
    request = report(store)
    if problem == "stale":
        request.expected_version -= 1
    before = stable(store)
    with pytest.raises(DomainError):
        AirportRestrictions(store, jobs).report("UNKNOWN" if problem == "unknown_case" else "CASE", request)
    assert stable(store) == before
