"""Fictional inventory. No external reservation or notification is performed."""
from datetime import datetime, timedelta
import secrets


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def catalog(start: str, end: str) -> tuple[list, list]:
    begin = datetime.fromisoformat(start)
    hotels = []
    specs = [
        ("h1", "Aeroport Garden", "Otopeni", 12, 15, [(2, 16, 260, False), (4, 9, 410, False), (2, 3, 280, True)]),
        ("h2", "Northline Hotel", "Băneasa", 18, 24, [(2, 12, 310, False), (4, 8, 455, False), (2, 2, 330, True)]),
        ("h3", "Carpathia Park", "Otopeni", 8, 12, [(2, 22, 235, False), (3, 9, 335, False), (2, 1, 255, True)]),
        ("h4", "Central Residence", "București", 24, 35, [(2, 10, 350, False), (4, 4, 490, False), (2, 2, 370, True)]),
    ]
    for hid, name, area, km, minutes, room_specs in specs:
        rooms = []
        for capacity, count, rate, accessible in room_specs:
            for _ in range(count):
                rooms.append({"id": f"{hid}-r{len(rooms)+1:03}", "capacity": capacity,
                              "rate": rate, "accessible": accessible})
        hotels.append({"id": hid, "name": name, "area": area, "distance_km": km,
                       "travel_minutes": minutes, "address": f"Strada Aviației {km}, {area} · adresă fictivă",
                       "available_from": start, "available_until": end,
                       "status": "available", "rooms": rooms})
    vehicles = []
    specs = [
        ("t1", "AeroTransfer", [(53, 60, 0, 580), (53, 60, 0, 580), (20, 24, 0, 310)]),
        ("t2", "Nord Shuttle", [(49, 55, 0, 540), (20, 24, 0, 290), (8, 12, 2, 240)]),
        ("t3", "City Mobility", [(30, 36, 0, 390), (16, 20, 2, 270), (16, 20, 1, 270)]),
    ]
    for pid, name, fleet in specs:
        for i, (seats, bags, wheelchairs, price) in enumerate(fleet):
            vehicles.append({"id": f"{pid}-v{i+1}", "provider_id": pid, "provider": name,
                             "label": ("Autocar" if seats > 30 else "Microbuz") + f" · {seats} locuri",
                             "seats": seats, "bags": bags, "wheelchairs": wheelchairs, "price": price,
                             "available_from": iso(begin + timedelta(minutes=20)),
                             "available_until": iso(min(begin + timedelta(hours=4), datetime.fromisoformat(end))), "status": "available"})
    return hotels, vehicles


def demo_groups(start: str, end: str | None = None) -> list:
    begin = datetime.fromisoformat(start)
    latest = datetime.fromisoformat(end) - timedelta(minutes=35) if end else begin + timedelta(minutes=150)
    groups = []
    surnames = ["Popescu", "Ionescu", "Dumitrescu", "Stan", "Marin", "Dobre", "Radu", "Ilie", "Tudor", "Matei", "Oprea", "Nistor"]
    firstnames = ["Andreea", "Mihai", "Elena", "Alexandru", "Ioana", "Victor", "Ana", "Răzvan"]
    for count, size, accessible in [(18, 4, False), (12, 3, False), (16, 2, False), (38, 1, False), (4, 2, True)]:
        for _ in range(count):
            index = len(groups)
            surname = surnames[index % len(surnames)]
            members = [{"id": f"P{sum(g['size'] for g in groups)+i+1:03}",
                        "name": f"{firstnames[(index+i) % len(firstnames)]} {surname}",
                        "accessible": accessible and i == 0} for i in range(size)]
            groups.append({"id": f"G{index+1:03}", "label": f"Familia {surname}" if size > 1 else members[0]["name"],
                           "size": size, "members": members, "eligible": True, "bags": size,
                           "accessible": int(accessible), "room_needs": [{"occupants": size, "accessible": accessible}],
                           "pickup_start": iso(min(begin + timedelta(minutes=30), latest)),
                           "pickup_end": iso(min(begin + timedelta(minutes=150), latest)), "token": secrets.token_urlsafe(24)})
    return groups


def new_state(incident: dict | None = None, load_demo: bool = True) -> dict:
    incident = incident or {"flight": "RO 384", "airport": "OTP · Henri Coandă", "destination": "Paris · CDG",
                            "passenger_count": 186, "start": "2026-10-30T20:00:00+02:00",
                            "end": "2026-10-31T08:00:00+02:00", "budget": 45000}
    hotels, vehicles = catalog(incident["start"], incident["end"])
    return {"incident": {**incident, "id": "INC-2026-0042", "status": "open"},
            "groups": demo_groups(incident["start"], incident["end"]) if load_demo else [],
            "hotels": hotels, "vehicles": vehicles, "assignments": [], "trips": [],
            "exceptions": [], "plan_version": 0, "approved_version": None,
            "audit": [], "revision": 0, "last_change": "Incident deschis. Catalogul contractual este disponibil.",
            "simulation": True, "meal_rate": 65}
