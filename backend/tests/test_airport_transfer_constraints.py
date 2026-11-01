"""Directional flight restrictions gate sourced returns, not ground pickup."""
from datetime import datetime, timedelta

import pytest

from app.airport_catalog import lookup_airport
from app.models import Airport, CaseAirportRestriction, IncidentFlightReference, RecoveryIncident
from app.scenario_schema import WeatherDefinition
from app.store import DomainError, utcnow
from test_transfer_return import returning, setup


def bind_case(store, code=None, operation=None, *, connection=False, global_departure_block=False):
    def apply(world):
        group = world.groups[0]
        group.incident_id = "DIRECTIONAL-CASE"
        world.incidents = [RecoveryIncident(id=group.incident_id, created_at=utcnow().isoformat(),
            kind="flight_disruption", description="Offline source-bound case", requested_flight_number="FIXTURE",
            date_utc=utcnow().date().isoformat(), group_ids=[group.id], status="open",
            flight_ref=IncidentFlightReference(lookup_id="offline-lookup", selection_id="offline-selection"),
            airport_restrictions=[] if code is None else [CaseAirportRestriction(airport_code=code, operation=operation,
                report_id="OFFLINE-REPORT", received_at=utcnow().isoformat(), description="Offline operator constraint")])]
        if connection:
            ref = lookup_airport("CLJ")
            world.airports.append(Airport(id="CLJ", code="CLJ", name=ref["name"], lat=ref["lat"], lon=ref["lon"],
                weather=WeatherDefinition(condition="fog", visibility_m=None, wind_kts=None,
                    landing_allowed=True, departure_allowed=False) if global_departure_block else None))
            flight = next(item for item in world.resources if item.kind == "flight")
            departure = datetime.fromisoformat(flight.provenance["departure_at"])
            flight.provenance["segments"] = [
                {"origin": "LHR", "destination": "LRCL", "departure_at": departure.isoformat(),
                 "arrival_at": (departure + timedelta(hours=1)).isoformat()},
                {"origin": "LRCL", "destination": "CRA", "departure_at": (departure + timedelta(hours=1, minutes=30)).isoformat(),
                 "arrival_at": flight.provenance["arrival_at"]},
            ]
    store.mutate("directional-transfer-case", {}, store.read().version, apply)


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "operation", "blocked"), [
    ("LHR", "arrivals", False), ("LHR", "departures", True),
    ("CRA", "departures", False), ("CRA", "arrivals", True),
    ("CLJ", "arrivals", True), ("CLJ", "departures", True),
])
async def test_return_preflight_checks_actual_flight_operation_roles_before_sources(returning, code, operation, blocked):
    store, sources, planning, request, _, _, _ = returning
    bind_case(store, code, operation, connection=True)
    if blocked:
        with pytest.raises(DomainError) as error:
            await planning.quote(request())
        assert error.value.status == 409
        assert sources.calls == []
    else:
        response = await planning.quote(request())
        assert response["state"] == "ready", response
        assert len(sources.calls) == 3  # Explicit local fixture adapters only.


@pytest.mark.asyncio
async def test_no_operator_ban_does_not_override_intermediate_source_weather(returning):
    store, sources, planning, request, _, _, _ = returning
    bind_case(store, connection=True, global_departure_block=True)
    with pytest.raises(DomainError):
        await planning.quote(request())
    assert sources.calls == []


@pytest.mark.asyncio
async def test_arrival_restriction_does_not_block_a_declared_ground_pickup(returning):
    store, sources, planning, _, _, initial, _ = returning
    bind_case(store, "LHR", "arrivals")
    response = await planning.quote(initial())
    assert response["state"] == "ready", response
    assert len(sources.calls) == 3
