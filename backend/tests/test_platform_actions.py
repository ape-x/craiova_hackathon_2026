"""Named voice actions dispatch through existing application boundaries.

All provider traffic is intercepted; these tests never call real services.
"""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.travel import TravelProvider
from test_demo_api import duffel_response
from test_live_service import attach_proposal, confirmation, manifest_request, resource_request


class ActionAI:
    configured = True

    def __init__(self):
        self.calls = []
        self.actions = []
        self.before_return = None

    async def interpret(self, text, world, context=""):
        self.calls.append((text, world, context))
        if self.before_return:
            self.before_return()
        return {"events": [], "strategies": [], "recovery": None,
                "operational_report": None, "focus_id": None,
                "explanation": "Requested application actions.",
                "platform_actions": copy.deepcopy(self.actions)}


@pytest.fixture
def platform(tmp_path):
    config = Settings(_env_file=None, operation_mode="demo",
                      database_path=str(tmp_path / "platform.sqlite3"))
    app = create_app(config)
    ai = ActionAI()
    app.state.service.ai = ai
    client = TestClient(app)  # Deliberately do not start background supplier workers.
    yield app, client, ai
    client.close()


def action(action_name, **arguments):
    return {"name": action_name, "arguments_json": json.dumps(arguments)}


def body(client, key="platform-command-123", **changes):
    return {"text": "Perform the selected application actions.",
            "expected_version": client.get("/api/world").json()["version"],
            "idempotency_key": key, **changes}


def definition(**changes):
    ready = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0)
    return {"origin": "LHR", "destination": "OTP", "passengers": 2,
            "group_size": 2, "group_budget_eur": "2000.00",
            "ready_at": ready.isoformat(),
            "deadline": (ready + timedelta(days=1, hours=14)).isoformat(),
            "description": "Fictional passengers need alternative travel.", **changes}


def snapshot(client):
    world = client.get("/api/world").json()
    return {key: world[key] for key in ("scenario_id", "version", "groups", "incidents", "resources", "offers")}


def test_named_demo_action_uses_existing_route_and_replays_without_duplicate_case(platform):
    _, client, ai = platform
    before = snapshot(client)
    ai.actions = [action("demo.create_case", **definition())]
    request = body(client)
    response = client.post("/api/commands", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["platform_results"][0]["name"] == "demo.create_case"
    assert result["platform_results"][0]["status"] == "succeeded"
    assert result["applied"] is True
    assert len(result["world"]["incidents"]) == len(before["incidents"]) + 1
    assert len(result["world"]["groups"]) == len(before["groups"]) + 1
    assert result["world"]["resources"] == []
    replay = client.post("/api/commands", json=request)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert len(ai.calls) == 1
    assert snapshot(client)["incidents"] == result["world"]["incidents"]


def test_actions_can_reference_created_case_and_group_list_to_start_recovery(platform):
    app, client, ai = platform
    ai.actions = [
        action("demo.create_case", **definition()),
        action("recovery.start", incident_id="$result.0.incident_id",
               group_ids="$result.0.group_ids", objective="fastest", modes=["flight"],
               max_provider_calls=5, instructions="Use sourced flights only."),
    ]
    request = body(client)
    response = client.post("/api/commands", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [item["status"] for item in result["platform_results"]] == ["succeeded", "succeeded"]
    created = result["platform_results"][0]["result"]
    jobs = app.state.jobs.list(created["incident_id"])["jobs"]
    assert len(jobs) == 1 and jobs[0]["group_ids"] == created["group_ids"]
    assert jobs[0]["objective"] == "fastest" and jobs[0]["calls_dispatched"] == 0
    assert jobs[0]["status"] == "queued"
    assert client.post("/api/commands", json=request).json()["replayed"] is True
    assert len(app.state.jobs.list(created["incident_id"])["jobs"]) == 1
    assert len(ai.calls) == 1


@pytest.mark.parametrize("bad", [
    action("demo.create_case", **definition(passengers=0)),
    action("passengers.rename", group_id="missing", name=""),
    action("not_a_registered_action"),
])
def test_invalid_later_action_is_preflighted_before_first_mutation(platform, bad):
    _, client, ai = platform
    before = snapshot(client)
    ai.actions = [action("demo.create_case", **definition()), bad]
    response = client.post("/api/commands", json=body(client))
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is False
    assert response.json()["platform_results"][-1]["status"] in ("failed", "needs_input")
    assert snapshot(client) == before


@pytest.mark.parametrize("name", [
    "auth.login", "auth.logout", "voice.start", "voice.session",
    "passenger.accept", "passenger.constraints", "offers.accept",
    "/api/auth/login", "https://example.invalid/api/world",
])
def test_auth_voice_raw_routes_and_passenger_impersonation_are_not_dispatchable(platform, name):
    _, client, ai = platform
    before = snapshot(client)
    ai.actions = [action(name, token="must-not-be-used", offer_id="missing")]
    response = client.post("/api/commands", json=body(client))
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is False
    assert response.json()["platform_results"][-1]["status"] in ("failed", "needs_input")
    assert snapshot(client) == before


def test_explicit_selection_supplies_only_current_group_for_named_action(platform):
    _, client, ai = platform
    world = client.get("/api/world").json()
    group, incident = world["groups"][0], world["incidents"][0]
    ai.actions = [action("passengers.rename", group_id="$selection.group_id", name="Selected demo family")]
    response = client.post("/api/commands", json=body(client, scope={
        "incident_id": incident["id"], "group_id": group["id"], "selected_id": group["id"],
    }))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["platform_results"][0]["status"] == "succeeded"
    renamed = next(item for item in result["world"]["groups"] if item["id"] == group["id"])
    assert renamed["name"] == "Selected demo family" and renamed["source"] == "synthetic"


def test_recovery_inherits_exact_selected_case_and_group_without_widening_scope(platform):
    app, client, ai = platform
    world = client.get("/api/world").json()
    selected = world["groups"][0]
    added = client.post("/api/demo/cases", json={
        **definition(), "expected_version": world["version"], "idempotency_key": "unrelated-demo-case-123",
    })
    assert added.status_code == 200
    ai.actions = [action("recovery.start", objective="balanced", modes=["flight"], max_provider_calls=5)]
    response = client.post("/api/commands", json=body(client, scope={"selected_id": selected["id"]}))
    assert response.status_code == 200, response.text
    assert response.json()["platform_results"][0]["status"] == "succeeded"
    jobs = app.state.jobs.list(selected["incident_id"])["jobs"]
    assert len(jobs) == 1 and jobs[0]["group_ids"] == [selected["id"]]
    assert jobs[0]["calls_dispatched"] == 0
    assert app.state.jobs.list(added.json()["incident_id"])["jobs"] == []


@pytest.mark.parametrize("total,queued_limit", [(4, 3), (3, None)])
def test_platform_recovery_respects_total_allowance_including_interpretation(platform, total, queued_limit):
    app, client, ai = platform
    world = client.get("/api/world").json()
    group = world["groups"][0]
    ai.actions = [action("recovery.start", objective="balanced", modes=["flight"], max_provider_calls=5)]
    response = client.post("/api/commands", json=body(
        client, text=f"Start recovery with maximum {total} calls.",
        scope={"selected_id": group["id"]},
    ))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["platform_total_call_limit"] == total
    assert data["command_interpretation_calls"] == 1
    jobs = app.state.jobs.list(group["incident_id"])["jobs"]
    if queued_limit is None:
        assert jobs == []
        assert data["platform_results"][0]["status"] == "failed"
        assert data["recovery_calls_reserved"] == 0
    else:
        assert len(jobs) == 1 and jobs[0]["max_provider_calls"] == queued_limit
        assert data["recovery_calls_reserved"] == queued_limit
        assert data["platform_results"][0]["status"] == "succeeded"


def test_zero_allowance_prevents_platform_interpretation_and_all_actions(platform):
    _, client, ai = platform
    ai.actions = [action("demo.create_case", **definition())]
    before = snapshot(client)
    response = client.post("/api/commands", json=body(
        client, text="Create a new case with maximum zero calls.",
    ))
    assert response.status_code == 200, response.text
    assert response.json()["command_interpretation_calls"] == 0
    assert ai.calls == [] and snapshot(client) == before


@pytest.mark.parametrize("scope", [{"selected_id": "REMOVED-GROUP"}, {"group_id": "REMOVED-GROUP"}])
def test_stale_selection_cannot_dispatch_or_rename_another_group(platform, scope):
    _, client, ai = platform
    ai.actions = [action("passengers.rename", group_id="$selection.group_id", name="Wrong group")]
    before = snapshot(client)
    response = client.post("/api/commands", json=body(client, scope=scope))
    assert response.status_code == 409, response.text
    assert snapshot(client) == before


def test_selection_rejects_group_and_case_from_different_contexts(platform):
    _, client, ai = platform
    world = client.get("/api/world").json()
    original_group = world["groups"][0]
    added = client.post("/api/demo/cases", json={
        **definition(), "expected_version": world["version"], "idempotency_key": "second-context-123",
    }).json()
    ai.actions = [action("passengers.rename", name="Wrong group")]
    before = snapshot(client)
    response = client.post("/api/commands", json=body(client, scope={
        "incident_id": added["incident_id"], "group_id": original_group["id"],
    }))
    assert response.status_code == 409, response.text
    assert snapshot(client) == before


def test_ui_only_command_reports_navigation_without_claiming_data_was_changed(platform):
    _, client, ai = platform
    world = client.get("/api/world").json()
    group = world["groups"][0]
    before = snapshot(client)
    ai.actions = [action("ui.navigate", view="passenger", selected_id=group["id"])]
    response = client.post("/api/commands", json=body(client))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["applied"] is False and snapshot(client) == before
    assert data["platform_results"][0]["status"] == "succeeded"
    assert data["ui_actions"] == [{"name": "ui.navigate", "arguments": {
        "view": "passenger", "selected_id": group["id"],
    }}]


def test_platform_read_receipt_redacts_passenger_credentials_and_member_names(platform):
    _, client, ai = platform
    world = client.get("/api/world").json()
    ai.actions = [action("world.get")]
    response = client.post("/api/commands", json=body(client))
    assert response.status_code == 200, response.text
    receipt = json.dumps(response.json()["platform_results"])
    assert world["groups"][0]["token"] not in receipt
    assert all(member not in receipt for member in world["groups"][0]["members"])
    assert response.json()["applied"] is False


def test_included_router_capabilities_dispatch_to_existing_status_handlers(platform):
    app, client, ai = platform
    expected = {"airport_transfers.status", "coaches.status", "transfers.status", "notifications.list", "inventory.get"}
    assert expected <= set(app.state.platform_actions.capabilities)
    ai.actions = [action("airport_transfers.status"), action("coaches.status"), action("transfers.status")]
    before = snapshot(client)
    response = client.post("/api/commands", json=body(client))
    assert response.status_code == 200, response.text
    assert [result["status"] for result in response.json()["platform_results"]] == ["succeeded"] * 3
    assert response.json()["applied"] is False and snapshot(client) == before


def test_action_batch_limit_is_validated_before_any_dispatch(platform):
    _, client, ai = platform
    ai.actions = [action("demo.create_case", **definition())] + [action("world.get")] * 6
    before = snapshot(client)
    response = client.post("/api/commands", json=body(client))
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is False
    assert response.json()["platform_results"][0]["status"] == "failed"
    assert snapshot(client) == before


@pytest.mark.parametrize("field,value", [
    ("expected_version", 1), ("idempotency_key", "injected-key"), ("scenario_id", "other-scenario"),
])
def test_action_cannot_override_server_owned_transaction_identifiers(platform, field, value):
    _, client, ai = platform
    before = snapshot(client)
    ai.actions = [action("demo.create_case", **definition(**{field: value}))]
    response = client.post("/api/commands", json=body(client))
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is False
    assert response.json()["platform_results"][0]["status"] == "failed"
    assert snapshot(client) == before


def test_reference_failure_after_success_preserves_partial_result_and_replays(platform):
    _, client, ai = platform
    before = snapshot(client)
    ai.actions = [
        action("demo.create_case", **definition()),
        action("passengers.rename", group_id="$result.0.nonexistent_group", name="Wrong target"),
    ]
    request = body(client)
    response = client.post("/api/commands", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["platform_results"][0]["status"] == "succeeded"
    assert result["platform_results"][1]["status"] in ("failed", "needs_input")
    assert len(result["world"]["incidents"]) == len(before["incidents"]) + 1
    assert len(result["world"]["groups"]) == len(before["groups"]) + 1
    assert all(item["name"] != "Wrong target" for item in result["world"]["groups"])
    replay = client.post("/api/commands", json=request)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert len(ai.calls) == 1
    assert snapshot(client)["incidents"] == result["world"]["incidents"]


def test_stale_command_never_calls_interpreter_or_dispatches_actions(platform):
    _, client, ai = platform
    original = body(client)
    created = client.post("/api/demo/cases", json={
        **definition(), "expected_version": original["expected_version"], "idempotency_key": "advance-version-123",
    })
    assert created.status_code == 200
    before = snapshot(client)
    ai.actions = [action("demo.create_case", **definition())]
    response = client.post("/api/commands", json=original)
    assert response.status_code == 409
    assert ai.calls == [] and snapshot(client) == before


def test_concurrent_world_change_stops_remaining_actions_without_undoing_completed_action(platform):
    app, client, ai = platform
    capability = app.state.platform_actions.capabilities["demo.create_case"]
    create_case = capability.endpoint

    def create_then_external_change(**arguments):
        result = create_case(**arguments)
        app.state.store.mutate("concurrent-platform-change", {}, result["world"]["version"],
                               lambda current: setattr(current, "budget", current.budget + 1))
        return result

    capability.endpoint = create_then_external_change
    ai.actions = [
        action("demo.create_case", **definition()),
        action("passengers.rename", group_id="$result.0.group_ids.0", name="Should not run"),
    ]
    before = snapshot(client)
    response = client.post("/api/commands", json=body(client))
    assert response.status_code == 200, response.text
    result = response.json()
    assert [item["status"] for item in result["platform_results"]] == ["succeeded", "failed"]
    assert "changed between actions" in result["platform_results"][1]["error"]
    assert len(result["world"]["incidents"]) == len(before["incidents"]) + 1
    assert all(group["name"] != "Should not run" for group in result["world"]["groups"])


def test_platform_dispatch_preserves_operator_authentication(tmp_path):
    app = create_app(Settings(_env_file=None, operation_mode="demo", operator_token="fixture-operator-token",
                              database_path=str(tmp_path / "auth-platform.sqlite3")))
    ai = ActionAI()
    ai.actions = [action("world.get")]
    app.state.service.ai = ai
    client = TestClient(app)
    try:
        headers = {"Authorization": "Bearer fixture-operator-token"}
        world = client.get("/api/world", headers=headers).json()
        request = {"text": "Read the current world.", "expected_version": world["version"],
                   "idempotency_key": "authenticated-command-123"}
        assert client.post("/api/commands", json=request).status_code == 401
        assert ai.calls == []
        response = client.post("/api/commands", json=request, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["platform_results"][0]["status"] == "succeeded"
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fails,total", [(False, 8), (True, 8), (False, 1)])
async def test_replayed_supplier_search_does_not_repeat_http_provider_call(tmp_path, fails, total):
    config = Settings(_env_file=None, operation_mode="demo", duffel_access_token="fixture-token",
                      duffel_environment="live", flight_offers_provider="duffel",
                      database_path=str(tmp_path / "provider-platform.sqlite3"))
    app = create_app(config)
    ai = ActionAI()
    app.state.service.ai = ai
    client = TestClient(app)
    calls = []

    def provider_reply(request):
        calls.append(request)
        assert request.method == "POST" and request.url.host == "api.duffel.com"
        assert request.url.path == "/air/offer_requests"
        if fails:
            raise httpx.ConnectError("Fixture supplier connection failed", request=request)
        payload = json.loads(request.content)["data"]
        return httpx.Response(200, json=duffel_response(payload, datetime.now(timezone.utc)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider_reply)) as http:
        app.state.travel.provider = TravelProvider(config, client=http)
        world = client.get("/api/world").json()
        group = world["groups"][0]
        day = (datetime.fromisoformat(world["epoch"]) + timedelta(minutes=group["ready_at"])).date().isoformat()
        ai.actions = [action("travel.search", group_id=group["id"], kind="flight",
                             origin_code="LHR", destination_code="OTP", departure_date=day)]
        request = body(client, text=f"Search live flight offers with maximum {total} calls.")
        response = client.post("/api/commands", json=request)
        assert response.status_code == 200, response.text
        result = response.json()
        blocked = total == 1
        assert result["platform_results"][0]["status"] == ("failed" if fails or blocked else "succeeded")
        assert len(calls) == (0 if blocked else 1) and len(ai.calls) == 1
        assert result["world"]["resources"] == []
        replay = client.post("/api/commands", json=request)
        assert replay.status_code == 200 and replay.json()["replayed"] is True
        assert len(calls) == (0 if blocked else 1) and len(ai.calls) == 1
    client.close()


@pytest.fixture
def live_platform(tmp_path):
    app = create_app(Settings(_env_file=None, operation_mode="live",
                              database_path=str(tmp_path / "live-platform.sqlite3")))
    ai = ActionAI()
    app.state.service.ai = ai
    client = TestClient(app)
    live = app.state.live
    live.manifest(manifest_request(live))
    live.resource(resource_request(live))
    attach_proposal(live)
    yield app, client, ai
    client.close()


def evidence_action(app, name):
    live = app.state.live
    if name == "bookings.confirm":
        values = confirmation(live).model_dump(mode="json", exclude={"expected_version", "idempotency_key"})
        return action(name, offer_id="PROPOSAL-1", **values)
    if name == "resources.import":
        values = resource_request(live, id="SECOND-FLIGHT").model_dump(
            mode="json", exclude={"expected_version", "idempotency_key"})
        return action(name, **values)
    values = manifest_request(live, group_id="SECOND-GROUP", passenger_prefix="SECOND").model_dump(
        mode="json", exclude={"expected_version", "idempotency_key"})
    return action(name, **values)


@pytest.mark.parametrize("name", ["bookings.confirm", "resources.import", "passengers.import"])
def test_model_cannot_invent_external_evidence_even_when_present_in_assistant_context(live_platform, name):
    app, client, ai = live_platform
    proposed = evidence_action(app, name)
    ai.actions = [action("passengers.rename", group_id="REAL-GROUP", name="Should not change"), proposed]
    before = snapshot(client)
    response = client.post("/api/commands", json=body(
        client, text="Rename the selected group and record the selected item.",
        context="Assistant suggestion, not supplier evidence: " + proposed["arguments_json"],
    ))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["applied"] is False
    assert data["platform_results"][0]["status"] in ("needs_input", "failed")
    assert snapshot(client) == before


@pytest.mark.parametrize("name", ["bookings.confirm", "resources.import", "passengers.import"])
def test_literal_operator_evidence_still_uses_existing_validated_route(live_platform, name):
    app, client, ai = live_platform
    proposed = evidence_action(app, name)
    ai.actions = [proposed]
    response = client.post("/api/commands", json=body(
        client, text="Record this externally supplied evidence exactly: " + proposed["arguments_json"],
    ))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["platform_results"][0]["status"] == "succeeded", data
    assert data["applied"] is True
    if name == "bookings.confirm":
        assert data["world"]["offers"][0]["status"] == "confirmed"
        assert data["world"]["offers"][0]["external_bookings"] == json.loads(proposed["arguments_json"])["bookings"]
    elif name == "resources.import":
        assert any(item["id"] == "SECOND-FLIGHT" for item in data["world"]["resources"])
    else:
        assert any(item["id"] == "SECOND-GROUP" for item in data["world"]["groups"])


@pytest.mark.parametrize("use_timestamp_year", [False, True])
def test_real_source_metadata_cannot_support_model_invented_capacity(live_platform, use_timestamp_year):
    app, client, ai = live_platform
    supplied = evidence_action(app, "resources.import")
    altered = json.loads(supplied["arguments_json"])
    altered["capacity"] = datetime.fromisoformat(altered["observed_at"]).year if use_timestamp_year else 9999
    ai.actions = [action("resources.import", **altered)]
    before = snapshot(client)
    response = client.post("/api/commands", json=body(
        client, text="Import this externally supplied resource exactly: " + supplied["arguments_json"],
    ))
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is False
    assert response.json()["platform_results"][0]["status"] in ("needs_input", "failed")
    assert snapshot(client) == before


@pytest.mark.parametrize("qualification", ["", "Do not record that ", "If "])
def test_arrival_report_requires_current_unconditional_assertion_with_selected_scope(platform, qualification):
    _, client, ai = platform
    before = snapshot(client)
    group = before["groups"][0]
    ready = datetime.fromisoformat(definition()["ready_at"])
    claim = f"The selected group is ready at LHR on {ready:%Y-%m-%d} at {ready:%H:%M} UTC"
    text = "Show the selected group." if not qualification else qualification + claim + "."
    ai.actions = [action("incidents.report_arrival", airport_code="LHR", ready_at=ready.isoformat(), description=claim)]
    response = client.post("/api/commands", json=body(
        client, text=text, scope={"selected_id": group["id"]},
    ))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["applied"] is False
    assert data["platform_results"][0]["status"] in ("needs_input", "failed")
    assert snapshot(client) == before


def test_literal_arrival_report_preserves_selected_case_and_group(platform):
    _, client, ai = platform
    before = snapshot(client)
    group = before["groups"][0]
    ready = datetime.fromisoformat(definition()["ready_at"])
    claim = f"The selected group is ready at LHR on {ready:%Y-%m-%d} at {ready:%H:%M} UTC"
    ai.actions = [action("incidents.report_arrival", airport_code="LHR", ready_at=ready.isoformat(), description=claim)]
    response = client.post("/api/commands", json=body(
        client, text=claim + ".", scope={"selected_id": group["id"]},
    ))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["platform_results"][0]["status"] == "succeeded", data
    report = data["platform_results"][0]["result"]["report"]
    assert report["group_ids"] == [group["id"]] and report["incident_id"] == group["incident_id"]
    assert report["airport_code"] == "LHR"


def test_relative_selected_arrival_uses_persisted_request_time_instead_of_model_clock(platform):
    app, client, ai = platform
    group = snapshot(client)["groups"][0]
    claim = "The selected group is ready at LHR in 10 minutes"
    ai.actions = [action("incidents.report_arrival", airport_code="LHR",
                         ready_at=definition()["ready_at"], description=claim)]
    request = body(client, text=claim + ".", scope={"selected_id": group["id"]})
    response = client.post("/api/commands", json=request)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["platform_results"][0]["status"] == "succeeded", data
    with app.state.store.connect() as conn:
        timestamp = conn.execute("SELECT created_at FROM live_command_intents WHERE request_key=?",
                                 ("command:" + request["idempotency_key"],)).fetchone()[0]
    anchor = datetime.fromisoformat(timestamp)
    rounded = anchor.replace(second=0, microsecond=0)
    if anchor != rounded:
        rounded += timedelta(minutes=1)
    report = data["platform_results"][0]["result"]["report"]
    assert datetime.fromisoformat(report["ready_at"]) == rounded + timedelta(minutes=10)
    assert report["group_ids"] == [group["id"]] and report["incident_id"] == group["incident_id"]


def test_arrival_report_cannot_change_literally_supplied_airport_or_time(platform):
    _, client, ai = platform
    before = snapshot(client)
    group = before["groups"][0]
    ready = datetime.fromisoformat(definition()["ready_at"])
    claim = f"The selected group is ready at LHR on {ready:%Y-%m-%d} at {ready:%H:%M} UTC"
    ai.actions = [action("incidents.report_arrival", airport_code="OTP",
                         ready_at=(ready + timedelta(hours=1)).isoformat(), description=claim)]
    response = client.post("/api/commands", json=body(client, text=claim, scope={"selected_id": group["id"]}))
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is False
    assert response.json()["platform_results"][0]["status"] in ("needs_input", "failed")
    assert snapshot(client) == before


@pytest.mark.parametrize("name,claim,arguments", [
    ("resources.report_unavailability", "FLIGHT-REAL is unavailable", {"resource_id": "FLIGHT-REAL"}),
    ("incidents.report_airport_restriction", "LHR is closed", {"airport_code": "LHR", "operations": ["arrivals", "departures"], "action": "restrict"}),
])
@pytest.mark.parametrize("affirmative", [False, True])
def test_resource_and_airport_facts_require_affirmative_current_request(live_platform, platform, name, claim, arguments, affirmative):
    app, client, ai = live_platform if name.startswith("resources.") else platform
    before = snapshot(client)
    ai.actions = [action(name, description=claim, **arguments)]
    scope = {} if name.startswith("resources.") else {"selected_id": before["groups"][0]["id"]}
    response = client.post("/api/commands", json=body(
        client, text=("" if affirmative else "Do not record that ") + claim + ".", scope=scope,
    ))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["applied"] is affirmative, data
    if affirmative:
        assert data["platform_results"][0]["status"] == "succeeded"
    else:
        assert data["platform_results"][0]["status"] in ("needs_input", "failed")
        assert snapshot(client) == before


def test_grounded_arrival_can_reference_the_case_and_groups_created_in_same_command(platform):
    _, client, ai = platform
    case = definition()
    ready = datetime.fromisoformat(case["ready_at"])
    claim = f"The newly created group is ready at LHR on {ready:%Y-%m-%d} at {ready:%H:%M} UTC"
    ai.actions = [action("demo.create_case", **case), action("incidents.report_arrival",
        incident_id="$result.0.incident_id", group_ids="$result.0.group_ids",
        airport_code="LHR", ready_at=case["ready_at"], description=claim)]
    response = client.post("/api/commands", json=body(
        client, text="Create a fictional case for two adults from LHR to OTP. " + claim + ".",
    ))
    assert response.status_code == 200, response.text
    data = response.json()
    assert [row["status"] for row in data["platform_results"]] == ["succeeded", "succeeded"], data
    created = data["platform_results"][0]["result"]
    report = data["platform_results"][1]["result"]["report"]
    assert report["incident_id"] == created["incident_id"]
    assert report["group_ids"] == created["group_ids"]


def test_timeout_reports_prior_committed_action_and_replay_never_repeats_it(platform):
    app, client, ai = platform
    app.state.service._planning_timeout_seconds = 1
    invocations = []

    async def never_finishes():
        invocations.append("read")
        await asyncio.Event().wait()

    app.state.platform_actions.capabilities["world.get"].endpoint = never_finishes
    ai.actions = [action("demo.create_case", **definition()), action("world.get")]
    before = snapshot(client)
    request = body(client)
    response = client.post("/api/commands", json=request)
    assert response.status_code == 504, response.text
    failure = response.json()
    assert "no scenario changes were applied" not in failure["detail"].lower()
    assert failure["applied"] is not False
    after = snapshot(client)
    assert len(after["incidents"]) == len(before["incidents"]) + 1
    replay = client.post("/api/commands", json=request)
    assert replay.status_code == 200, replay.text
    data = replay.json()
    assert data["replayed"] is True and data["outcome_unknown"] is True
    assert data["platform_results"][0]["name"] == "demo.create_case"
    assert data["platform_results"][0]["status"] == "succeeded"
    assert data["applied"] is True and snapshot(client) == after
    assert invocations == ["read"] and len(ai.calls) == 1
