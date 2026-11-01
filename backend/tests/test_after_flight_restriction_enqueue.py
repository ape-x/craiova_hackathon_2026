"""Report facts survive known setup failures, but not unexpected transaction faults."""
from datetime import timedelta

import pytest

from app.airport_restrictions import AirportRestrictions
from app.store import DomainError, utcnow
from test_airport_restriction_reports import report, stable
from test_recovery_jobs import setup
from test_after_flight_recovery import connection, env, setup as connected_setup


def test_typed_restriction_queue_does_not_claim_saved_profile_reuse(connected_setup):
    e = connected_setup
    request = report(e.store, airport_code="LHR", operations=["arrivals"], recovery={
        "group_ids": ["G1"], "modes": ["flight", "bus"], "max_provider_calls": 6,
        "after_flight_transfer": connection()})
    result = AirportRestrictions(e.store, e.jobs).report("CASE", request)
    assert result["applied"] and result["recovery_job"]["status"] == "queued"
    assert result["command_interpretation_calls"] == 0
    assert "configurația salvată" not in result["explanation"]
    assert not e.source.calls and not e.flights.calls


@pytest.mark.parametrize("status", [422, 409])
def test_postflight_prerequisite_failure_preserves_only_known_report_result(setup, monkeypatch, status):
    _, store, jobs, _, travel = setup
    now = utcnow().replace(second=0, microsecond=0)
    request = report(store, airport_code="LHR", recovery={"group_ids": ["G1"], "modes": ["flight", "bus"], "max_provider_calls": 6,
        "after_flight_transfer": {"group_id": "G1", "via_airport": "OTP", "destination_airport": "CRA",
            "pickup_window_start": (now+timedelta(hours=12)).isoformat(),
            "pickup_window_end": (now+timedelta(hours=14)).isoformat(),
            "airport_processing_minutes": 60, "suitcases": 1, "handbags": 1}})
    called = []
    def reject(world, supplied, cab):
        called.append(supplied.after_flight_transfer)
        raise DomainError(status, "Explicit processing window requires review")
    monkeypatch.setattr("app.after_flight_recovery.validate_after_flight_job", reject)
    before = stable(store)
    service = AirportRestrictions(store, jobs)
    if status == 422:
        result = service.report("CASE", request)
        assert result["applied"] and result["recovery_blocked"]["code"] == "airport_transfer_setup_required"
        assert result["report"]["airport_code"] == "LHR" and "recovery_job" not in result
        again = service.report("CASE", request)
        assert again["replayed"] and again["report"] == result["report"]
        assert store.read().version == before["version"]+1
    else:
        with pytest.raises(DomainError) as error:
            service.report("CASE", request)
        assert error.value.status == 409 and stable(store) == before
    assert len(called) == 1 and not travel.calls and jobs.list("CASE")["jobs"] == []
