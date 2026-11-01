"""Live-source simulation uses fictional demand and real provider boundaries.

All supplier HTTP is intercepted by MockTransport; no external calls are made.
"""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.travel import TravelProvider
from test_recovery_jobs import FakeAI, run_next


def settings(tmp_path, **changes):
    return Settings(_env_file=None, operation_mode="demo",
                    database_path=str(tmp_path / "demo-api.sqlite3"), **changes)


@pytest.fixture
def demo(tmp_path):
    app = create_app(settings(tmp_path))
    client = TestClient(app)  # No lifespan: jobs are claimed explicitly below.
    yield app, client
    client.close()


def definition(world, **changes):
    ready = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0)
    return {
        "expected_version": world["version"], "idempotency_key": "new-demo-case-123",
        "origin": "LHR", "destination": "OTP", "passengers": 5,
        "group_size": 2, "group_budget_eur": "2000.00",
        "ready_at": ready.isoformat(), "deadline": (ready + timedelta(days=1, hours=14)).isoformat(),
        "description": "Fictional passengers need a replacement journey.", **changes,
    }


def test_demo_bootstrap_exposes_mock_demand_and_no_invented_inventory(demo):
    app, client = demo
    world = client.get("/api/world").json()
    config = client.get("/api/config").json()
    assert world["mode"] == "live" and world["demo"] is True
    assert config["operation_mode"] == "demo" and config["demo"] is True
    assert config["simulation_only"] is False
    assert world["resources"] == world["offers"] == world["reservations"] == []
    assert len(world["groups"]) == len(world["incidents"]) == 1
    group, incident = world["groups"][0], world["incidents"][0]
    assert group["source"] == "synthetic" and group["size"] == group["adult_count"] == 2
    assert (group["origin"], group["destination"]) == ("LHR", "OTP")
    assert group["incident_id"] == incident["id"] and incident["group_ids"] == [group["id"]]
    assert incident["source"] == "demo_scenario" and incident["flight_ref"] is None
    assert incident["flight"] is None and incident["flight_context_state"] == "not_applicable"
    assert "hypothetical" in incident["description"]
    assert app.state.store.poll_usage() == app.state.store.fr24_credits_reserved() == 0


def test_create_demo_case_appends_only_mock_demand_and_replays_exact_identity(demo):
    _, client = demo
    before = client.get("/api/world").json()
    body = definition(before)
    response = client.post("/api/demo/cases", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    world = result["world"]
    assert world["version"] == before["version"] + 1
    assert world["groups"][:len(before["groups"])] == before["groups"]
    assert world["resources"] == world["offers"] == world["reservations"] == []
    added = [group for group in world["groups"] if group["id"] in result["group_ids"]]
    assert [group["size"] for group in added] == [2, 2, 1]
    assert all(group["source"] == "synthetic" and group["adult_count"] == group["size"] for group in added)
    case = next(item for item in world["incidents"] if item["id"] == result["incident_id"])
    assert case["source"] == "demo_scenario" and case["flight_ref"] is None
    assert case["group_ids"] == result["group_ids"]
    assert result["focus_id"] == result["group_ids"][0]
    assert all(group["incident_id"] == case["id"] for group in added)
    replay = client.post("/api/demo/cases", json=body)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["incident_id"] == result["incident_id"]
    assert client.get("/api/world").json()["groups"] == world["groups"]
    changed = client.post("/api/demo/cases", json={**body, "passengers": 6})
    assert changed.status_code == 409


def test_stale_demo_creation_does_not_append_case_or_people(demo):
    _, client = demo
    original = client.get("/api/world").json()
    created = client.post("/api/demo/cases", json=definition(original))
    assert created.status_code == 200
    before = client.get("/api/world").json()
    result = client.post("/api/demo/cases", json=definition(
        original, idempotency_key="stale-demo-case-123"))
    assert result.status_code == 409
    after = client.get("/api/world").json()
    assert after["version"] == before["version"]
    assert after["groups"] == before["groups"] and after["incidents"] == before["incidents"]


@pytest.mark.parametrize("change", [
    {"origin": "ZZZ"}, {"destination": "LHR"}, {"passengers": True},
    {"passengers": 501}, {"group_size": 0}, {"group_size": 13},
    {"group_budget_eur": "-1"}, {"group_budget_eur": "NaN"},
    {"ready_at": "2030-01-01T10:00:00"}, {"ready_at": "2020-01-01T10:00:00Z"},
    {"description": "\n"},
])
def test_invalid_demo_data_is_atomic(demo, change):
    _, client = demo
    before = client.get("/api/world").json()
    response = client.post("/api/demo/cases", json=definition(before, **change))
    assert response.status_code == 422, response.text
    after = client.get("/api/world").json()
    assert after["version"] == before["version"]
    assert after["groups"] == before["groups"] and after["incidents"] == before["incidents"]
    assert after["resources"] == []


@pytest.mark.parametrize("mode", ["live", "simulation"])
def test_demo_creation_cannot_modify_other_modes(tmp_path, mode):
    app = create_app(Settings(_env_file=None, operation_mode=mode,
                              database_path=str(tmp_path / f"{mode}.sqlite3")))
    client = TestClient(app)
    try:
        before = client.get("/api/world").json()
        response = client.post("/api/demo/cases", json=definition(before))
        assert response.status_code == 409, response.text
        after = client.get("/api/world").json()
        assert after["version"] == before["version"] and after["groups"] == before["groups"]
    finally:
        client.close()


def test_demo_creation_remains_operator_authenticated(tmp_path):
    app = create_app(settings(tmp_path, operator_token="fixture-demo-operator-secret"))
    client = TestClient(app)
    try:
        headers = {"Authorization": "Bearer fixture-demo-operator-secret"}
        before = client.get("/api/world", headers=headers).json()
        assert client.post("/api/demo/cases", json=definition(before)).status_code == 401
        assert client.post("/api/demo/cases", json=definition(before), headers=headers).status_code == 200
    finally:
        client.close()


def test_demo_rejects_manual_transport_and_real_manifest(demo):
    _, client = demo
    world = client.get("/api/world").json()
    ready = datetime.now(timezone.utc) + timedelta(days=1)
    evidence = {
        "expected_version": world["version"], "idempotency_key": "demo-manual-source-123",
        "source_name": "Operator inventory", "source_reference": "fixture-reference",
        "confirmed_by": "Fixture operator", "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    body = {**evidence, "id": "MANUAL-FLIGHT", "kind": "flight", "name": "Manual inventory",
            "origin": "LHR", "destination": "OTP", "departure_at": ready.isoformat(),
            "arrival_at": (ready + timedelta(hours=3)).isoformat(), "capacity": 8,
            "unit_price_eur": "100", "accessible": False,
            "valid_until": (ready + timedelta(hours=1)).isoformat()}
    manual = client.post("/api/live/resources", json=body)
    assert manual.status_code == 409, manual.text
    csv = "passenger_id,group_id,name,origin,destination,original_flight_id,ready_at,deadline,group_budget_eur,accessible,priority,is_adult\n"
    csv += f"REAL-P1,REAL-GROUP,Real person,LHR,OTP,EK5,{ready.isoformat()},{(ready + timedelta(days=1)).isoformat()},500,false,1,true\n"
    manifest = client.post("/api/live/import-passengers", json={**evidence, "csv_text": csv})
    assert manifest.status_code == 409, manifest.text
    after = client.get("/api/world").json()
    assert after["version"] == world["version"] and after["groups"] == world["groups"]
    assert after["resources"] == []


def duffel_response(query, now):
    """Fixture with actual Duffel response shape, parsed by the real adapter."""
    day = query["slices"][0]["departure_date"]
    origin, destination = query["slices"][0]["origin"], query["slices"][0]["destination"]
    passengers = [{"id": f"pax-{i}", "type": "adult"} for i in range(len(query["passengers"]))]
    offer = {
        "id": "fixture-live-offer", "live_mode": True, "passengers": passengers,
        "total_amount": "246.90", "total_currency": "EUR",
        "expires_at": (now + timedelta(minutes=20)).isoformat(),
        "owner": {"name": "British Airways", "iata_code": "BA"}, "conditions": {},
        "slices": [{"segments": [{
            "id": "fixture-source-segment", "origin": {"iata_code": origin, "time_zone": "Europe/London"},
            "destination": {"iata_code": destination, "time_zone": "Europe/Bucharest"},
            "departing_at": day + "T12:00:00+00:00", "arriving_at": day + "T15:00:00+00:00",
            "marketing_carrier_flight_number": "886",
        }]}],
    }
    return {"data": {"id": "fixture-source-request", "live_mode": True,
                     "passengers": passengers, "offers": [offer]}}


@pytest.mark.asyncio
async def test_demo_recovery_uses_live_adapter_and_source_evidence_not_seed_inventory(tmp_path):
    config = settings(tmp_path, duffel_access_token="fixture-live-key", duffel_environment="live",
                      flight_offers_provider="duffel")
    app = create_app(config)
    client = TestClient(app)
    calls = []

    def supplier(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.host == "api.duffel.com" and request.url.path == "/air/offer_requests"
        body = json.loads(request.content)["data"]
        assert body["slices"][0]["origin"] == "LHR"
        assert body["slices"][0]["destination"] == "OTP"
        assert body["passengers"] == [{"type": "adult"}, {"type": "adult"}]
        return httpx.Response(200, json=duffel_response(body, datetime.now(timezone.utc)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(supplier)) as http:
        provider = TravelProvider(config, client=http)
        ai = FakeAI()
        jobs = app.state.jobs
        jobs.ai = ai
        jobs.travel = provider
        world = client.get("/api/world").json()
        incident, group = world["incidents"][0], world["groups"][0]
        body = {"expected_version": world["version"], "idempotency_key": "demo-source-job-123",
                "group_ids": [group["id"]], "modes": ["flight"], "objective": "balanced",
                "max_provider_calls": 8, "instructions": "Compare live-source flight choices."}
        response = client.post(f"/api/incidents/{incident['id']}/recovery-jobs", json=body)
        assert response.status_code == 202, response.text
        assert calls == []  # Queueing does not invent supply or dispatch a search.
        result = await run_next(jobs)
        assert result["status"] == "completed", result
        assert result["result"]["recovered_passengers"] == 2
        assert len(calls) == 1 and len(ai.decisions) == 1
        assert len(ai.decisions[0][1]) == 3
        after = client.get("/api/world").json()
        assert after["mode"] == "live" and after["demo"] is True
        assert after["groups"][0]["source"] == "synthetic"
        assert after["incidents"][0]["source"] == "demo_scenario"
        assert len(after["resources"]) == len(after["offers"]) == 1
        resource = after["resources"][0]
        assert resource["source"] == "duffel" and resource["external_offer_id"] == "fixture-live-offer"
        assert resource["capacity_scope"] == "quoted_request" and resource["eligible_group_ids"] == [group["id"]]
        assert resource["quote_total_cost"] == 246.90 and resource["booking_status"] == "unbooked"
        assert resource["provenance"]["mode"] == "live"
        assert datetime.fromisoformat(resource["valid_until"]) > datetime.now(timezone.utc)
        assert after["offers"][0]["status"] == "proposed"
        replay = client.post(f"/api/incidents/{incident['id']}/recovery-jobs", json=body)
        assert replay.status_code == 202 and replay.json()["replayed"] is True
        assert app.state.jobs.ledger.claim_next() is None and len(calls) == 1
    client.close()


@pytest.mark.asyncio
async def test_demo_missing_supplier_access_never_falls_back_to_synthetic_inventory(demo):
    app, client = demo
    app.state.jobs.ai = FakeAI()
    world = client.get("/api/world").json()
    incident, group = world["incidents"][0], world["groups"][0]
    response = client.post(f"/api/incidents/{incident['id']}/recovery-jobs", json={
        "expected_version": world["version"], "idempotency_key": "demo-unconfigured-job-123",
        "group_ids": [group["id"]], "modes": ["flight"], "max_provider_calls": 8,
    })
    assert response.status_code == 202, response.text
    result = await run_next(app.state.jobs)
    assert result["status"] == "blocked" and result["calls_dispatched"] == 0
    assert any(gap["code"] == "flight_provider_not_configured" for gap in result["gaps"])
    after = client.get("/api/world").json()
    assert after["resources"] == after["offers"] == []
