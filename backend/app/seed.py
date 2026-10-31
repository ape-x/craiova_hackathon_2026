from __future__ import annotations

import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .models import Airport, AuditEvent, PassengerGroup, Resource, World


def empty_live_world() -> World:
    """A real geographic starting point, with no invented operational inventory."""
    from .airport_catalog import lookup_airport
    item = lookup_airport("CRA")
    epoch = datetime.now(ZoneInfo("Europe/Bucharest")).replace(hour=0, minute=0, second=0, microsecond=0)
    airport = Airport(id=item["code"], code=item["code"], name=item["name"], lat=item["lat"], lon=item["lon"], available=False, operational_status="unknown")
    return World(scenario_id=secrets.token_hex(8), epoch=epoch.isoformat(), mode="live", sim_time=0, budget=0, airports=[airport], resources=[], groups=[])


def seed_world() -> World:
    airports = [
        Airport(id="CRA", code="CRA", name="Craiova International", lat=44.3181, lon=23.8886),
        Airport(id="OTP", code="OTP", name="Bucharest Henri Coandă", lat=44.5711, lon=26.0850),
        Airport(id="SBZ", code="SBZ", name="Sibiu International", lat=45.7856, lon=24.0913),
        Airport(id="LTN", code="LTN", name="London Luton", lat=51.8747, lon=-0.3683, diversion_eligible=False),
    ]
    resources: list[Resource] = []
    def add(id, kind, origin, destination, dep, arr, cap, cost, accessible=True, available=True):
        resources.append(Resource(id=id, kind=kind, name=id if kind != "hotel" else f"{origin} overnight hotel", origin=origin, destination=destination, departure=dep, arrival=arr, capacity=cap, cost_per_person=cost, accessible=accessible, available=available, vehicle_id=id if kind=="bus" else None, status="scheduled" if available else "cancelled"))
    add("F101", "flight", "CRA", "LTN", 660, 870, 120, 0, available=False)
    add("F201", "flight", "LTN", "CRA", 600, 750, 60, 0)
    add("F110", "flight", "CRA", "LTN", 870, 1080, 28, 180)
    add("F210", "flight", "OTP", "LTN", 1020, 1230, 52, 140)
    add("F211", "flight", "OTP", "LTN", 1140, 1350, 36, 100)
    add("F310", "flight", "SBZ", "LTN", 1050, 1260, 32, 120)
    add("F111", "flight", "CRA", "LTN", 1920, 2130, 48, 75)
    add("F212", "flight", "OTP", "LTN", 1920, 2130, 44, 65)
    add("F311", "flight", "SBZ", "LTN", 1950, 2160, 30, 60)
    add("B12", "bus", "CRA", "OTP", 690, 930, 32, 35)
    add("B04", "bus", "CRA", "OTP", 720, 960, 16, 42, accessible=False)
    add("B07", "bus", "CRA", "OTP", 750, 990, 24, 48)
    add("B09", "bus", "CRA", "SBZ", 690, 930, 28, 32)
    add("B18", "bus", "CRA", "SBZ", 750, 990, 12, 45, accessible=False)
    add("B22", "bus", "OTP", "CRA", 840, 1080, 24, 40)
    add("B23", "bus", "OTP", "CRA", 930, 1170, 20, 38)
    add("B24", "bus", "OTP", "CRA", 1080, 1320, 16, 30, accessible=False)
    add("B31", "bus", "SBZ", "CRA", 840, 1080, 28, 35)
    add("B32", "bus", "SBZ", "CRA", 960, 1200, 20, 32)
    add("B33", "bus", "SBZ", "CRA", 1110, 1350, 12, 28, accessible=False)
    for code,cap,cost in [("CRA",18,70),("OTP",14,95),("SBZ",12,80)]:
        add(f"H-{code}", "hotel", code, code, 1320, 1860, cap, cost)
    groups = []
    surnames = ["Popescu", "Ionescu", "Dumitrescu", "Stan", "Marin", "Radu", "Dobre", "Stoica", "Munteanu", "Tudor", "Enache", "Petrescu"]
    for flight, count, origin, destination, status in [("F101",120,"CRA","LTN","waiting"),("F201",60,"CRA","CRA","scheduled")]:
        remaining, j = count, 0
        while remaining:
            size = min([4,2,3,1,5,2,1,3,4,2,1,2][j%12], remaining)
            idx = len(groups)+1
            accessible = idx % 9 == 1
            groups.append(PassengerGroup(id=f"G{idx:03}", name=f"{surnames[(idx-1)%len(surnames)]} · {idx:02}", size=size,
                members=[f"Synthetic passenger {idx:02}-{m+1}" for m in range(size)], origin=origin, destination=destination,
                deadline=1380 if j%5==0 else 2220, ready_at=600 if flight=="F101" else 780, accessible=accessible,
                budget=size*340, original_flight_id=flight, status=status, token=secrets.token_urlsafe(32), priority=3 if accessible else 2 if size>2 else 1))
            remaining -= size
            j += 1
    world = World(scenario_id=secrets.token_hex(8), epoch="2026-10-31T00:00:00+02:00", airports=airports, resources=resources, groups=groups)
    world.events.append(AuditEvent(id=secrets.token_hex(8),type="scenario_started",timestamp=datetime.now(timezone.utc).isoformat(),version=1,target_id="F101",description="Synthetic scenario: F101 cancelled; 120 passengers need recovery. F201 has 60 arriving passengers. No real booking or flight instruction is issued."))
    return world
