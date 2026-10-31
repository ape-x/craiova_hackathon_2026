"""Scoped command dispatch uses isolated SQLite and mocked adapters only."""
import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.airport_catalog import lookup_airport
from app.live_commands import LiveCommands, literal_call_limit
from app.models import Airport, CommandRequest, PassengerGroup, RecoveryIncident, IncidentFlightReference
from app.providers import AIProvider, ProviderError
from app.recovery_jobs import RecoveryJobs
from app.service import RecoveryService
from app.store import DomainError, Store, utcnow


def intent(**changes):
    return {"action": "start", "incident_id": "RC-ONE", "group_ids": [], "all_linked_groups": True,
            "objective": "balanced", "modes": ["flight"], "max_total_calls": None,
            "call_limit_reference": None, "requires_input_changes": False, **changes}


def interpretation(**changes):
    return {"events": [], "strategies": [], "explanation": "Mock interpretation, not a completed search.",
            "focus_id": None, "recovery": intent(), "operational_report": None, **changes}


class FakeAI:
    configured = True
    def __init__(self):
        self.calls = []
        self.result = interpretation()
        self.before_return = None

    async def interpret(self, text, world, context=""):
        self.calls.append((text, world, context))
        if self.before_return:
            await self.before_return()
        return copy.deepcopy(self.result)

    async def choose(self, *args):
        raise AssertionError("A scoped search command must not run global planning")


@pytest.fixture
def command_setup(tmp_path):
    settings = Settings(_env_file=None, operation_mode="live", database_path=str(tmp_path / "commands.sqlite3"))
    store = Store(settings.database_path, operation_mode="live")
    def seed(world):
        for code in ("CRA", "OTP"):
            node = lookup_airport(code)
            world.airports.append(Airport(id=code, code=code, name=node["name"], lat=node["lat"], lon=node["lon"]))
        for group_id in ("G1", "G2", "OUT"):
            world.groups.append(PassengerGroup(id=group_id, name="Planning group " + group_id, size=1,
                origin="CRA", destination="OTP", ready_at=world.sim_time + 120,
                deadline=world.sim_time + 900, budget=500, token="private-token-" + group_id,
                source="manifest_import", adult_count=1, members=["PRIVATE PASSENGER"],
                original_flight_id="RO321" if group_id != "OUT" else "RO999",
                incident_id="RC-ONE" if group_id != "OUT" else None))
        world.incidents.append(RecoveryIncident(id="RC-ONE", created_at=utcnow().isoformat(), kind="destination_unavailable",
            description="Operator input", requested_flight_number="RO321", date_utc="2026-11-01",
            flight_ref=IncidentFlightReference(lookup_id="private-lookup", selection_id="private-flight-reference"),
            group_ids=["G1", "G2"], status="open"))
    store.mutate("fixture-world", {}, store.read().version, seed)
    ai = FakeAI()
    service = RecoveryService(settings, store, ai)
    jobs = RecoveryJobs(settings, store, service, SimpleNamespace(), ai)
    service.live_commands = LiveCommands(store, jobs)
    return settings, store, service, jobs, ai


def request(store, text="Recuperează zborul RO321", **changes):
    return CommandRequest(**{"expected_version": store.read().version, "idempotency_key": "scoped-command-key", "text": text, **changes})


@pytest.mark.asyncio
async def test_default_scope_queues_truthful_receipt_without_mutation_and_replays_once(command_setup):
    _, store, service, jobs, ai = command_setup
    before = store.read()
    req = request(store)
    response = await service.command(req)
    assert response["applied"] is False and response["incident_id"] == "RC-ONE"
    assert response["command_interpretation_calls"] == 1 and response["recovery_total_call_limit"] == 8
    job = response["recovery_job"]
    assert job["group_ids"] == ["G1", "G2"] and job["max_provider_calls"] == 7
    assert job["status"] == "queued" and job["calls_dispatched"] == 0
    assert "nu dovedește" in response["explanation"]
    assert store.read().version == before.version and store.read().groups == before.groups
    service.live_commands = LiveCommands(store, jobs)  # process-independent storage
    replay = await service.command(req)
    assert replay["replayed"] and replay["recovery_job"]["id"] == job["id"]
    assert len(ai.calls) == 1 and len(jobs.list("RC-ONE")["jobs"]) == 1
    with store.connect() as conn:
        receipt = conn.execute("SELECT result FROM idempotency WHERE key=?", ("command:" + req.idempotency_key,)).fetchone()[0]
    assert json.loads(receipt)["world"]["observations"] == []
    assert json.loads(receipt)["world"]["incidents"][0]["flight"] is None


@pytest.mark.parametrize("text,total,job_cap", [
    ("Recover RO321 with maximum 4 calls", 4, 3),
    ("Recuperează RO321 cu cel mult trei apeluri", 3, None),
    ("Recover RO321 with maximum 1 call", 1, None),
])
@pytest.mark.asyncio
async def test_literal_total_allowance_includes_interpretation_even_if_model_requests_more(command_setup, text, total, job_cap):
    _, store, service, jobs, ai = command_setup
    ai.result["recovery"].update(max_total_calls=26, call_limit_reference=text)
    response = await service.command(request(store, text))
    assert response["recovery_total_call_limit"] == total and response["command_interpretation_calls"] == 1
    if job_cap is None:
        assert jobs.list("RC-ONE")["jobs"] == [] and "nu am mărit" in response["explanation"]
    else:
        assert response["recovery_job"]["max_provider_calls"] == job_cap
    assert len(ai.calls) == 1


@pytest.mark.asyncio
async def test_zero_limit_prevents_even_interpretation(command_setup):
    _, store, service, jobs, ai = command_setup
    response = await service.command(request(store, "Recover RO321 with maximum zero calls"))
    assert response["command_interpretation_calls"] == 0 and ai.calls == []
    assert not jobs.list("RC-ONE")["jobs"]


@pytest.mark.asyncio
async def test_model_cannot_raise_allowance_by_citing_only_the_word_calls(command_setup):
    _, store, service, _, ai = command_setup
    ai.result["recovery"].update(max_total_calls=20, call_limit_reference="calls")
    response = await service.command(request(store, "Recover RO321 with bounded calls"))
    assert response["recovery_total_call_limit"] == 8
    assert response["recovery_job"]["max_provider_calls"] == 7


@pytest.mark.asyncio
async def test_all_groups_interpretation_cannot_widen_an_explicit_subset(command_setup):
    _, store, service, jobs, _ = command_setup
    response = await service.command(request(store, "Recover RO321 only for G1"))
    assert "nu am extins" in response["explanation"]
    assert not jobs.list("RC-ONE")["jobs"]


@pytest.mark.parametrize("text", ["Recover RO321", "Recuperează cazul RC-ONE"])
@pytest.mark.asyncio
async def test_case_scoped_strategy_without_recovery_intent_cannot_fall_into_global_planning(command_setup, text):
    _, store, service, jobs, ai = command_setup
    ai.result = interpretation(recovery=None, strategies=[{
        "id": "balanced", "label": "Balanced", "objective": "balanced", "preferred_airport": None,
        "weights": {"recovery": 10, "arrival": 3, "cost": 1, "stability": 2},
    }])
    before = store.read()
    response = await service.command(request(store, text))
    assert response["applied"] is False and "nu am aplicat un plan global" in response["explanation"]
    assert not jobs.list("RC-ONE")["jobs"]
    assert store.read().version == before.version and store.read().groups == before.groups
    assert len(ai.calls) == 1  # FakeAI.choose would fail if global planning ran.


@pytest.mark.asyncio
async def test_history_alone_cannot_authorize_case_or_replay_a_prior_command(command_setup):
    _, store, service, jobs, ai = command_setup
    response = await service.command(request(store, "Da, fă asta", context="Previous request: recover RO321 and G1"))
    assert "Specifică explicit" in response["explanation"] and not jobs.list("RC-ONE")["jobs"]
    assert len(ai.calls) == 1


@pytest.mark.asyncio
async def test_shared_flight_requires_date_or_exact_case_id(command_setup):
    _, store, service, jobs, _ = command_setup
    def add_case(world):
        second = world.incidents[0].model_copy(deep=True)
        second.id, second.date_utc, second.group_ids = "RC-TWO", "2026-11-02", []
        world.incidents.append(second)
    store.mutate("second-case", {}, store.read().version, add_case)
    response = await service.command(request(store))
    assert "RC-ONE" in response["explanation"] and "RC-TWO" in response["explanation"]
    assert not jobs.list("RC-ONE")["jobs"]
    response = await service.command(request(store, "Recover RO 321 on 2026-11-01", idempotency_key="dated-command-key"))
    assert response["recovery_job"]["incident_id"] == "RC-ONE"


@pytest.mark.parametrize("reference", [
    "RO321 on 2026-11-01", "RO 321 din 1 noiembrie 2026",
    "RO321 din1noiembrie2026", "RO321 on 1 November 2026",
    "RO321 on November 1, 2026", "RO321 on 2026/11/01",
    "RO321 on 2026.11.01",
    "RC-ONE din 1 noiembrie 2026", "flight dated 1 November 2026 RO321",
])
@pytest.mark.asyncio
async def test_attached_exact_named_or_numeric_date_selects_the_case(command_setup, reference):
    _, store, service, jobs, _ = command_setup
    def add_case(world):
        second = world.incidents[0].model_copy(deep=True)
        second.id, second.date_utc, second.group_ids = "RC-TWO", "2026-11-02", []
        world.incidents.append(second)
    store.mutate("second-dated-case", {}, store.read().version, add_case)
    response = await service.command(request(store, "Recover " + reference))
    assert response["recovery_job"]["incident_id"] == "RC-ONE"
    assert not jobs.list("RC-TWO")["jobs"]


@pytest.mark.parametrize("reference", [
    "RO321 din 31–1 noiembrie 2026", "RO321 din 31 - 1 noiembrie 2026",
    "RO321 din 31 sau 1 noiembrie 2026",
    "RO321 on 2026-11-01/2026-11-02",
    "RO321 on 1 November 2026 or 2 November 2026",
    "RO321 on 2 November 2026", "RO321 on 2026-11-02",
    "RC-ONE din 2 noiembrie 2026", "RC-ONE on 2026-11-02",
    "RO321 din 31 noiembrie 2026", "RO321 on 131 November 2026",
    "RO321 on 2026-11-010", "RO321 on tomorrow",
    "RO321 on 01/11/2026", "RO321 din 1 noiembrie",
    "RO321 on 1 November 2026, 2 November 2026",
    "RC-ONE on 2026-11-01, RO321 on 2026-11-02",
])
@pytest.mark.asyncio
async def test_ambiguous_invalid_or_conflicting_attached_date_never_queues(command_setup, reference):
    _, store, service, jobs, ai = command_setup
    before = store.read()
    req = request(store, "Recover " + reference)
    result = await service.command(req)
    assert result["applied"] is False and "Data asociată" in result["explanation"]
    assert result["command_interpretation_calls"] == 1 and not jobs.list("RC-ONE")["jobs"]
    assert store.read().version == before.version and store.read().groups == before.groups
    assert (await service.command(req))["replayed"]
    assert len(ai.calls) == 1


@pytest.mark.asyncio
async def test_real_probe_date_range_is_rejected_even_for_a_unique_flight(command_setup):
    _, store, service, jobs, _ = command_setup
    def change_case(world):
        world.incidents[0].requested_flight_number = "EK5"
        world.incidents[0].date_utc = "2026-10-31"
    store.mutate("probe-case-shape", {}, store.read().version, change_case)
    response = await service.command(request(store,
        "Caută recuperarea zborului EK5 din 30-31 octombrie 2026 pentru toate grupurile cazului, cu maximum patru apeluri în total"))
    assert "Data asociată" in response["explanation"] and response["applied"] is False
    assert not jobs.list("RC-ONE")["jobs"]


@pytest.mark.parametrize("case_date", ["31/10/2026", "31.10.2026", "31-10-2026", "31 octombrie 2026"])
@pytest.mark.asyncio
async def test_unambiguous_october_case_date_and_november_search_date_remain_distinct(command_setup, case_date):
    _, store, service, jobs, _ = command_setup
    def change_case(world):
        world.incidents[0].requested_flight_number = "EK5"
        world.incidents[0].date_utc = "2026-10-31"
    store.mutate("exact-probe-case-shape", {}, store.read().version, change_case)
    text = f"Recuperează EK5 din{case_date}, caută alternative pentru2noiembrie2026 cu maximum patru apeluri"
    response = await service.command(request(store, text))
    job = response["recovery_job"]
    assert job["incident_id"] == "RC-ONE" and job["max_provider_calls"] == 3
    assert jobs.ledger.get(job["id"], private=True)["_request"]["instructions"] == text


@pytest.mark.asyncio
async def test_search_date_does_not_disambiguate_two_incident_dates(command_setup):
    _, store, service, jobs, ai = command_setup
    def add_case(world):
        second = world.incidents[0].model_copy(deep=True)
        second.id, second.date_utc, second.group_ids = "RC-TWO", "2026-11-02", ["G2"]
        world.incidents[0].group_ids = ["G1"]
        world.groups[1].incident_id = "RC-TWO"
        world.incidents.append(second)
    store.mutate("ambiguous-case-shape", {}, store.read().version, add_case)
    ai.result["recovery"]["incident_id"] = None
    response = await service.command(request(store, "Recover RO321, search alternatives for 2026-11-02"))
    assert "RC-ONE" in response["explanation"] and "RC-TWO" in response["explanation"]
    assert not jobs.list("RC-ONE")["jobs"] and not jobs.list("RC-TWO")["jobs"]


@pytest.mark.parametrize("text", [
    "Recuperează RO321 din1noiembrie2026, caută alternative pentru2noiembrie2026",
    "Recover RO321 on 2026-11-01, search alternatives for 2026-11-02",
    "Recover RC-ONE on 1 November 2026 and search alternatives for 2 November 2026",
    "Recover RO321; search alternatives for 2026-11-02",
    "Search alternatives for 2026-11-02 for flight RO321",
])
@pytest.mark.asyncio
async def test_alternative_travel_date_is_preserved_separately_from_case_identity(command_setup, text):
    _, store, service, jobs, _ = command_setup
    response = await service.command(request(store, text))
    job = response["recovery_job"]
    assert job["incident_id"] == "RC-ONE"
    assert jobs.ledger.get(job["id"], private=True)["_request"]["instructions"] == text


@pytest.mark.parametrize("recovery,events", [
    (intent(group_ids=["OUT"], all_linked_groups=False), []),
    (intent(group_ids=["G1"], all_linked_groups=False), []),  # not explicitly named
    (intent(requires_input_changes=True), []),
    (intent(), [{"type": "group_budget", "target_id": "G1", "value": 100}]),
])
@pytest.mark.asyncio
async def test_no_unbound_or_inferred_subset_and_no_partial_constraint_changes(command_setup, recovery, events):
    _, store, service, jobs, ai = command_setup
    ai.result = interpretation(recovery=recovery, events=events)
    before = store.read()
    response = await service.command(request(store))
    assert response["applied"] is False and not jobs.list("RC-ONE")["jobs"]
    assert store.read().version == before.version and store.read().groups == before.groups


@pytest.mark.asyncio
async def test_explicit_subset_and_confirmed_groups_are_protected(command_setup):
    _, store, service, jobs, ai = command_setup
    store.mutate("confirmed-group", {}, store.read().version, lambda world: setattr(world.groups[1], "status", "confirmed"))
    before = store.read()
    response = await service.command(request(store))
    assert response["recovery_job"]["group_ids"] == ["G1"]
    assert "1 grupuri cu rezervări confirmate" in response["explanation"]
    assert store.read().groups[1:] == before.groups[1:]
    # A second selected-group request cannot overwrite the existing queue owner.
    ai.result["recovery"].update(group_ids=["G1"], all_linked_groups=False)
    with pytest.raises(DomainError, match="already owns"):
        await service.command(request(store, "Recover RO321 for G1", idempotency_key="second-subset-command"))
    assert len(jobs.list("RC-ONE")["jobs"]) == 1


@pytest.mark.asyncio
async def test_version_change_during_interpretation_never_queues_job(command_setup):
    _, store, service, jobs, ai = command_setup
    async def change():
        store.mutate("concurrent-change", {}, store.read().version, lambda world: setattr(world, "budget", 200))
    ai.before_return = change
    with pytest.raises(DomainError, match="changed while interpreting"):
        await service.command(request(store))
    assert not jobs.list("RC-ONE")["jobs"]


@pytest.mark.asyncio
async def test_interpretation_survives_crash_before_enqueue(command_setup):
    _, store, service, jobs, ai = command_setup
    req = request(store)
    await service.live_commands.interpret(req, store.read(), ai)
    assert not jobs.list("RC-ONE")["jobs"]
    service.live_commands = LiveCommands(store, jobs)
    response = await service.command(req)
    assert response["recovery_job"]["status"] == "queued" and len(ai.calls) == 1


@pytest.mark.asyncio
async def test_failure_after_receipt_insert_rolls_back_both_job_and_receipt(command_setup, monkeypatch):
    _, store, service, jobs, ai = command_setup
    original = jobs.ledger.create
    def fail_after_receipt(*args, on_created, **kwargs):
        def fail(conn, world, result):
            on_created(conn, world, result)
            raise RuntimeError("simulated process failure before commit")
        return original(*args, on_created=fail, **kwargs)
    monkeypatch.setattr(jobs.ledger, "create", fail_after_receipt)
    req = request(store)
    with pytest.raises(RuntimeError, match="before commit"):
        await service.command(req)
    assert not jobs.list("RC-ONE")["jobs"] and store.replay("command:" + req.idempotency_key, req.model_dump()) is None
    monkeypatch.setattr(jobs.ledger, "create", original)
    response = await service.command(req)
    assert response["recovery_job"]["status"] == "queued" and len(ai.calls) == 1


@pytest.mark.asyncio
async def test_unknown_interpretation_is_never_redispatched(command_setup):
    _, store, service, jobs, ai = command_setup
    async def fail():
        raise ProviderError("Mock transport timeout", provider="OpenAI Responses", code="timeout")
    ai.before_return = fail
    req = request(store)
    for expected_status in (502, 503):
        with pytest.raises(DomainError) as error:
            await service.command(req)
        assert error.value.status == expected_status and error.value.applied is None
        assert error.value.details["automatic_retry"] is False
    assert len(ai.calls) == 1 and not jobs.list("RC-ONE")["jobs"]


@pytest.mark.asyncio
async def test_concurrent_same_command_dispatches_one_interpretation(command_setup):
    _, store, service, jobs, ai = command_setup
    entered, release = asyncio.Event(), asyncio.Event()
    async def pause():
        entered.set()
        await release.wait()
    ai.before_return = pause
    req = request(store)
    first = asyncio.create_task(service.command(req))
    await entered.wait()
    try:
        with pytest.raises(DomainError, match="still processing"):
            await service.command(req)
    finally:
        release.set()
    await first
    assert (await service.command(req))["replayed"]
    assert len(ai.calls) == 1 and len(jobs.list("RC-ONE")["jobs"]) == 1


@pytest.mark.asyncio
async def test_status_reads_recorded_job_without_restarting_search(command_setup):
    _, store, service, jobs, ai = command_setup
    first = await service.command(request(store))
    ai.result["recovery"]["action"] = "status"
    status = await service.command(request(store, "What is the status of RO321?", idempotency_key="status-command-key"))
    assert status["recovery_job"]["id"] == first["recovery_job"]["id"]
    assert "queued" in status["explanation"] and "0/7" in status["explanation"]
    assert len(jobs.list("RC-ONE")["jobs"]) == 1


def test_operator_endpoint_wires_scoped_commands_and_preserves_auth(command_setup):
    from app.main import create_app
    settings, store, _, _, ai = command_setup
    settings.operator_token = "x" * 40
    app = create_app(settings)
    app.state.service.ai = ai
    app.state.jobs.start_worker = lambda: None
    with TestClient(app) as client:
        payload = request(store).model_dump()
        assert client.post("/api/commands", json=payload).status_code == 401
        result = client.post("/api/commands", json=payload, headers={"Authorization": "Bearer " + "x" * 40})
        assert result.status_code == 200, result.json()
        assert result.json()["recovery_job"]["max_provider_calls"] == 7
    assert len(ai.calls) == 1


@pytest.mark.asyncio
async def test_live_schema_is_strict_preserves_model_and_omits_private_flight_data(command_setup):
    settings, store, _, _, _ = command_setup
    settings.openai_api_key = "mock-key"
    world = store.read().model_dump(mode="json")
    world["incidents"][0]["flight"] = {"raw": "PRIVATE RAW FLIGHT"}
    def handler(http_request):
        body = json.loads(http_request.content)
        assert body["model"] == "gpt-6.1-sol" and body["service_tier"] == "ultrafast"
        assert body["store"] is False
        schema = body["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False and "recovery" in schema["required"]
        recovery = schema["$defs"]["LiveRecoveryIntent"]
        assert set(recovery["properties"]) == set(recovery["required"])
        assert recovery["additionalProperties"] is False
        assert "SYNTHETIC PASSENGER RECOVERY SIMULATOR" not in body["instructions"]
        serialized = http_request.content.decode()
        for private in ("PRIVATE RAW FLIGHT", "private-token", "PRIVATE PASSENGER", "private-lookup", "private-flight-reference"):
            assert private not in serialized
        assert "RO321" in serialized and "RC-ONE" in serialized
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(interpretation())}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AIProvider(settings, client=client).interpret("Recover RO321", world)
    assert result["recovery"]["incident_id"] == "RC-ONE"


@pytest.mark.parametrize("text,expected", [
    ("limită de 4 apeluri", 4), ("maximum 200 EUR for 4 passengers", None),
    ("max 7 calls then max 3 calls", 3), ("limit of four supplier API calls", 4),
    ("4 external calls maximum", 4), ("trei apeluri în total", 3),
])
def test_call_limit_uses_call_units_not_fares_or_passengers(text, expected):
    assert literal_call_limit(text) == expected
