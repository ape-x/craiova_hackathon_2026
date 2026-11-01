"""Retained primitive reports must not hide a second current operational fact."""
import pytest

from app.live_commands import LiveCommands
from app.live_operational_commands import has_passenger_position_claim, has_unrepresented_operational_claim
from app.models import Resource
from test_live_commands import command_setup, intent, interpretation, request


@pytest.fixture
def complete_setup(command_setup):
    _, store, _, _, _ = command_setup
    def seed(world):
        for identifier in ("Q1", "Q2"):
            world.resources.append(Resource(id=identifier, name="Offline coach " + identifier,
                kind="bus", origin="CRA", destination="OTP", departure=world.sim_time + 120,
                arrival=world.sim_time + 240, capacity=4, cost_per_person=40,
                source="offline-test-fixture"))
    store.mutate("fixture-coaches", {}, store.read().version, seed)
    return command_setup


def outage(claim="Q1 is unavailable"):
    return {"type": "resource_unavailability", "resource_id": "Q1", "resource_reference": "Q1", "report_reference": claim}


def arrival(claim="G1 is at SBZ ready in 30 minutes"):
    return {"type": "arrival_report", "incident_id": "RC-ONE", "group_ids": ["G1"],
        "all_linked_groups": False, "airport_code": "SBZ", "airport_reference": "SBZ",
        "ready_at": None, "ready_at_reference": "in 30 minutes", "report_reference": claim}


def recovery():
    return intent(group_ids=["G1"], all_linked_groups=False, max_total_calls=4, call_limit_reference="maximum 4 calls")


def unchanged(store, before, jobs, result):
    assert not result["applied"]
    assert "recovery_job" not in result
    assert store.read().model_dump(exclude={"sim_time"}) == before.model_dump(exclude={"sim_time"})
    assert jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.asyncio
async def test_retained_outage_cannot_queue_recovery_from_old_origin_and_replays_clarification(complete_setup):
    _, store, service, jobs, ai = complete_setup
    ai.result = interpretation(operational_report=outage(), recovery=recovery())
    before = store.read()
    req = request(store, "Q1 is unavailable. G1 is at SBZ ready in 30 minutes. Recover RO321 for G1 with maximum 4 calls.")
    result = await service.command(req)
    unchanged(store, before, jobs, result)
    assert before.groups[0].origin == store.read().groups[0].origin == "CRA"
    assert "poziția veche" in result["explanation"]
    assert len(ai.calls) == 1
    service.live_commands = LiveCommands(store, jobs)
    replay = await service.command(req)
    assert replay["replayed"] and replay["explanation"] == result["explanation"]
    assert len(ai.calls) == 1 and jobs.list("RC-ONE")["jobs"] == []


@pytest.mark.parametrize("extra", [
    "G1 at SBZ", "G1 is at SBZ and awaiting instructions", "G1 arrived at SBZ",
    "G1 has arrived at SBZ", "G1 is ready in 30 minutes", "Planning group G1 is at Sibiu",
    "Pasagerii G1 sunt la SBZ", "Grupul G1 este pregătit peste treizeci de minute",
    "Pasagerii zborului RO321 sunt la SBZ", "Passengers from RO321 are at SBZ",
    "G1 is not at SBZ", "G1 is not ready in 30 minutes", "G1 nu este la SBZ",
    "If G1 is at SBZ ready in 30 minutes, use that", "Dacă G1 este la SBZ, folosește asta",
    "G1 might be ready in 30 minutes", "Are G1 ready in 30 minutes?",
])
@pytest.mark.asyncio
async def test_retained_outage_rejects_dropped_current_or_qualified_passenger_assertion(complete_setup, extra):
    _, store, service, jobs, ai = complete_setup
    ai.result = interpretation(operational_report=outage(), recovery=recovery())
    before = store.read()
    result = await service.command(request(store, f"Q1 is unavailable. {extra}. Recover RO321 for G1 with maximum 4 calls."))
    unchanged(store, before, jobs, result)


@pytest.mark.parametrize("kind", ["outage", "arrival"])
@pytest.mark.asyncio
async def test_primitive_quote_cannot_swallow_other_report(complete_setup, kind):
    _, store, service, jobs, ai = complete_setup
    combined = "Q1 is unavailable. G1 is at SBZ ready in 30 minutes"
    ai.result = interpretation(operational_report=outage(combined) if kind == "outage" else arrival(combined), recovery=recovery())
    before = store.read()
    result = await service.command(request(store, combined + ". Recover RO321 for G1 with maximum 4 calls."))
    unchanged(store, before, jobs, result)


@pytest.mark.parametrize("extra", ["Q1 is unavailable", "Autobuzul Q1 este defect", "Q2 broke down", "Q2 failed", "Another bus is broken", "Q1 is not unavailable", "If Q1 is unavailable, use that"])
@pytest.mark.asyncio
async def test_retained_arrival_rejects_dropped_service_condition(complete_setup, extra):
    _, store, service, jobs, ai = complete_setup
    report = arrival()
    ai.result = interpretation(operational_report=report, recovery=recovery())
    before = store.read()
    result = await service.command(request(store, f"{extra}. {report['report_reference']}. Recover RO321 for G1 with maximum 4 calls."))
    unchanged(store, before, jobs, result)


@pytest.mark.parametrize("kind,extra", [("arrival", "G1 at OTP"), ("arrival", "G2 at SBZ"), ("outage", "Q2 is unavailable")])
@pytest.mark.asyncio
async def test_second_same_kind_fact_outside_selected_quote_is_not_ignored(complete_setup, kind, extra):
    _, store, service, jobs, ai = complete_setup
    report = arrival() if kind == "arrival" else outage()
    ai.result = interpretation(operational_report=report, recovery=None)
    before = store.read()
    result = await service.command(request(store, f"For RO321: {report['report_reference']}. {extra}."))
    unchanged(store, before, jobs, result)


@pytest.mark.parametrize("claim", ["Q1 is unavailable at SBZ", "Q1 at SBZ is unavailable", "Bus Q1 at SBZ is unavailable", "For passengers G1, Q1 is unavailable at SBZ", "Q1 este indisponibil la SBZ"])
@pytest.mark.asyncio
async def test_service_location_does_not_move_passengers_or_block_valid_outage(complete_setup, claim):
    _, store, service, jobs, ai = complete_setup
    ai.result = interpretation(operational_report=outage(claim), recovery=recovery())
    before = store.read()
    result = await service.command(request(store, claim + ". Recover RO321 for G1 with maximum 4 calls."))
    assert result["applied"] and result["recovery_job"]["status"] == "queued", result["explanation"]
    assert result["recovery_job"]["calls_dispatched"] == 0
    assert store.read().groups[0].origin == "CRA"
    assert store.read().groups[0].ready_at == before.groups[0].ready_at
    assert store.read().resources[0].available is False


@pytest.mark.parametrize("prefix", ["After the diversion, assume ", "After last week's bus breakdown, assume ", "După deviere, presupune "])
@pytest.mark.asyncio
async def test_background_without_new_service_assertion_preserves_arrival_only(complete_setup, prefix):
    _, store, service, jobs, ai = complete_setup
    report = arrival(prefix + "G1 is at SBZ ready in 30 minutes")
    ai.result = interpretation(operational_report=report, recovery=recovery())
    result = await service.command(request(store, report["report_reference"] + ". Recover RO321 for G1 with maximum 4 calls."))
    assert result["applied"] and result["recovery_job"]["status"] == "queued"
    assert store.read().groups[0].origin == "SBZ"
    assert all(item.available for item in store.read().resources)


@pytest.mark.parametrize("text", ["Recover RO321 for G1 via SBZ", "Search for G1 from SBZ", "Find a flight for G1 arriving at SBZ", "Find a transfer for G1 arriving at CRA", "G1 in the same case", "Q1 is unavailable at SBZ", "After the diversion, recover G1", "The aircraft RO321 arrives at SBZ in 30 minutes"])
def test_reject_only_passenger_guard_does_not_infer_readiness_from_route_service_or_aircraft(complete_setup, text):
    _, store, _, _, _ = complete_setup
    assert not has_passenger_position_claim(store.read(), text)


@pytest.mark.parametrize("text", ["G2 at OTP", "G2 at OTP awaiting a decision", "G2 is located at London Heathrow Airport", "Pasagerii sunt la SBZ", "G1 are ready in 20 minutes"])
def test_bundle_residual_guard_includes_bare_position_without_readiness(complete_setup, text):
    _, store, _, _, _ = complete_setup
    assert has_unrepresented_operational_claim(store.read(), text)


@pytest.mark.asyncio
async def test_no_report_interpretation_cannot_ignore_bare_current_group_location(complete_setup):
    _, store, service, jobs, ai = complete_setup
    ai.result = interpretation(operational_report=None, recovery=recovery())
    before = store.read()
    result = await service.command(request(store, "G1 is at SBZ. Recover RO321 for G1 with maximum 4 calls."))
    unchanged(store, before, jobs, result)


@pytest.mark.parametrize("extra", ["SBZ is unavailable", "SBZ arrivals are suspended", "SBZ este indisponibil"])
@pytest.mark.parametrize("swallowed", [False, True])
@pytest.mark.asyncio
async def test_known_service_location_guard_never_hides_separate_airport_change(complete_setup, extra, swallowed):
    _, store, service, jobs, ai = complete_setup
    base = "Q1 is unavailable at SBZ"
    combined = base + ", and " + extra
    ai.result = interpretation(operational_report=outage(combined if swallowed else base), recovery=recovery())
    before = store.read()
    result = await service.command(request(store, combined + ". Recover RO321 for G1 with maximum 4 calls."))
    unchanged(store, before, jobs, result)


@pytest.mark.asyncio
async def test_literal_punctuated_service_name_does_not_hide_dropped_outage(complete_setup):
    _, store, service, jobs, ai = complete_setup
    def rename(world):
        world.resources[0].name = "Bucharest.CAB Sedan"
    store.mutate("fixture-name", {}, store.read().version, rename)
    report = arrival()
    ai.result = interpretation(operational_report=report, recovery=recovery())
    before = store.read()
    result = await service.command(request(store, f"Bucharest.CAB Sedan is unavailable. {report['report_reference']}. Recover RO321 for G1 with maximum 4 calls."))
    unchanged(store, before, jobs, result)


@pytest.mark.asyncio
async def test_future_flight_arrival_search_remains_instruction_not_passenger_report(complete_setup):
    _, store, service, jobs, ai = complete_setup
    ai.result = interpretation(operational_report=outage(), recovery=recovery())
    text = "Q1 is unavailable. For RO321, search for a flight for G1 arriving at SBZ, maximum 4 calls."
    result = await service.command(request(store, text))
    assert result["applied"] and result["recovery_job"]["status"] == "queued", result["explanation"]
    assert store.read().groups[0].origin == "CRA"
    assert jobs.ledger.get(result["recovery_job"]["id"], private=True)["_request"]["instructions"] == text
