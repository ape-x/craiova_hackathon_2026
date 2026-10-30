"""Deterministic, constraint-aware recommendations; no global optimality claim.

One vehicle = one airport-to-hotel trip in the incident pickup window. Rooms are
held for the whole assistance interval. Prices are integer RON.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import ceil

ACTIVE = {"held", "confirmed"}


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def nights(state: dict) -> int:
    return max(1, ceil((dt(state["incident"]["end"]) - dt(state["incident"]["start"])).total_seconds() / 86400))


def expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()


def room_bundle(hotel: dict, group: dict, used: set, night_count: int):
    """Cheapest feasible bundle for the group's explicit room requirements."""
    free = [r for r in hotel["rooms"] if r["id"] not in used]
    selected = []
    for need in sorted(group["room_needs"], key=lambda n: (-n["accessible"], -n["occupants"])):
        eligible = [r for r in free if r["capacity"] >= need["occupants"] and (not need["accessible"] or r["accessible"])]
        if not eligible:
            return None
        room = min(eligible, key=lambda r: (r["rate"], r["accessible"] != need["accessible"], r["capacity"], r["id"]))
        free.remove(room)
        selected.append(room)
    return {"room_ids": [r["id"] for r in selected], "cost": sum(r["rate"] for r in selected) * night_count}


def transport_options(state: dict, hotel: dict, group: dict, trips: list, strategy: str):
    options = []
    groups = {g["id"]: g for g in state["groups"]}
    used = {t["vehicle_id"] for t in trips if t["status"] in ACTIVE}
    for trip in trips:
        if trip["hotel_id"] != hotel["id"] or trip["status"] not in ACTIVE:
            continue
        v = next(v for v in state["vehicles"] if v["id"] == trip["vehicle_id"])
        occupants = [groups[gid] for gid in trip["group_ids"]] + [group]
        if sum(g["size"] for g in occupants) > v["seats"] or sum(g["bags"] for g in occupants) > v["bags"] or sum(g["accessible"] for g in occupants) > v["wheelchairs"]:
            continue
        low = max([dt(v["available_from"])] + [dt(g["pickup_start"]) for g in occupants])
        high = min([dt(v["available_until"]) - timedelta(minutes=hotel["travel_minutes"])] + [dt(g["pickup_end"]) for g in occupants])
        departure = dt(trip["departure_at"]) if trip["status"] == "confirmed" else low
        if low <= departure <= high:
            options.append({"trip": trip, "vehicle": v, "departure_at": departure.isoformat(), "cost": 0, "rank": (0, 0, v["id"])})
    for index, v in enumerate(state["vehicles"]):
        if v["id"] in used or v["status"] != "available" or v["seats"] < group["size"] or v["bags"] < group["bags"] or v["wheelchairs"] < group["accessible"]:
            continue
        low = max(dt(group["pickup_start"]), dt(v["available_from"]))
        high = min(dt(group["pickup_end"]), dt(v["available_until"]) - timedelta(minutes=hotel["travel_minutes"]))
        if low <= high:
            # Cost per usable seat avoids opening many tiny vehicles first.
            rank = (1, v["price"] / v["seats"], v["id"]) if strategy == "recommended" else (1, index, v["id"])
            options.append({"trip": None, "vehicle": v, "departure_at": low.isoformat(), "cost": v["price"], "rank": rank})
    return sorted(options, key=lambda x: x["rank"])


def allocate(source: dict, strategy: str = "recommended", preserve: bool = True, forced: dict | None = None) -> dict:
    state = deepcopy(source)
    forced = forced or {}
    by_group = {g["id"]: g for g in state["groups"]}
    hotels = {h["id"]: h for h in state["hotels"]}
    assignments = []
    old_trips = {t["id"]: t for t in state["trips"]}
    trips = []
    if preserve:
        for old in state["assignments"]:
            if old["group_id"] not in by_group or old["hotel_status"] not in ACTIVE or (hotels[old["hotel_id"]]["status"] != "available" and old["hotel_status"] != "confirmed"):
                continue
            a = deepcopy(old)
            old_trip = old_trips.get(a.get("trip_id"))
            if old_trip and old_trip["status"] in ACTIVE:
                trip = next((t for t in trips if t["id"] == old_trip["id"]), None)
                if trip is None:
                    trip = deepcopy(old_trip)
                    trip["group_ids"] = []
                    trips.append(trip)
                trip["group_ids"].append(a["group_id"])
            else:
                a["trip_id"] = None
            assignments.append(a)
    used_rooms = {rid for a in assignments for rid in a["room_ids"]}
    order = [g for g in state["groups"] if g["eligible"]]
    if strategy == "recommended":
        order.sort(key=lambda g: (-g["accessible"], -max(n["occupants"] for n in g["room_needs"]), -g["size"], g["id"]))
    exceptions = []
    for group in order:
        existing = next((a for a in assignments if a["group_id"] == group["id"]), None)
        if existing and existing.get("trip_id"):
            continue
        candidates = []
        for index, hotel in enumerate(state["hotels"]):
            if (hotel["status"] != "available" and not (existing and existing["hotel_status"] == "confirmed")) or dt(hotel["available_from"]) > dt(state["incident"]["start"]) or dt(hotel["available_until"]) < dt(state["incident"]["end"]):
                continue
            if existing and existing["hotel_id"] != hotel["id"]:
                continue
            if group["id"] in forced and hotel["id"] != forced[group["id"]]:
                continue
            bundle = {"room_ids": existing["room_ids"], "cost": 0} if existing else room_bundle(hotel, group, used_rooms, nights(state))
            if bundle is None:
                continue
            transport = transport_options(state, hotel, group, trips, strategy)
            if not transport:
                continue
            option = transport[0]
            rank = (bundle["cost"] + option["cost"] / option["vehicle"]["seats"] * group["size"], index) if strategy == "recommended" else (index,)
            candidates.append((rank, hotel, bundle, option))
        if not candidates:
            exceptions.append({"group_id": group["id"], "passengers": group["size"],
                               "reason": "Cazare păstrată; niciun transfer compatibil cu locurile, bagajele, accesibilitatea și intervalul." if existing else "Nicio combinație fezabilă de camere și transfer. Verifică disponibilitatea, accesibilitatea și intervalul de preluare."})
            continue
        _, hotel, bundle, option = min(candidates, key=lambda item: item[0])
        trip = option["trip"]
        if trip is None:
            trip = {"id": f"trip-{option['vehicle']['id']}", "vehicle_id": option["vehicle"]["id"],
                    "hotel_id": hotel["id"], "group_ids": [], "departure_at": option["departure_at"],
                    "status": "held", "expires_at": expiry()}
            trips.append(trip)
        elif trip["status"] != "confirmed":
            trip["departure_at"] = option["departure_at"]
        trip["group_ids"].append(group["id"])
        if existing:
            existing["trip_id"] = trip["id"]
            existing["explanation"] = "Cazarea existentă a fost păstrată; transferul a fost recalculat respectând intervalul grupului."
        else:
            assignments.append({"group_id": group["id"], "hotel_id": hotel["id"], "room_ids": bundle["room_ids"],
                                "trip_id": trip["id"], "hotel_status": "held", "expires_at": expiry(),
                                "explanation": f"{group['size']} persoane împreună; {len(bundle['room_ids'])} cameră/camere compatibile" + (", accesibilitate verificată" if group["accessible"] else "") + ". Tarif și transport evaluate; rezervare temporară 20 min."})
            used_rooms.update(bundle["room_ids"])
    state["assignments"], state["trips"], state["exceptions"] = assignments, trips, exceptions
    validate(state)
    return state


def recommend(state: dict, preserve: bool = True, forced: dict | None = None) -> dict:
    candidates = [allocate(state, strategy, preserve, forced) for strategy in ("recommended", "baseline")]
    return max(candidates, key=lambda s: (metrics(s)["covered"], -metrics(s)["total_cost"]))


def metrics(state: dict) -> dict:
    groups = {g["id"]: g for g in state["groups"]}
    rooms = {r["id"]: r for h in state["hotels"] for r in h["rooms"]}
    vehicles = {v["id"]: v for v in state["vehicles"]}
    active_trips = {t["id"]: t for t in state["trips"] if t["status"] in ACTIVE}
    active = [a for a in state["assignments"] if a["hotel_status"] in ACTIVE]
    hotel_cost = sum(rooms[r]["rate"] * nights(state) for a in active for r in a["room_ids"])
    transport_cost = sum(vehicles[t["vehicle_id"]]["price"] for t in active_trips.values())
    meal_cost = sum(groups[a["group_id"]]["size"] * state["meal_rate"] for a in active)
    covered = sum(groups[a["group_id"]]["size"] for a in active if a.get("trip_id") in active_trips)
    confirmed = sum(groups[a["group_id"]]["size"] for a in active if a["hotel_status"] == "confirmed" and a.get("trip_id") in active_trips and active_trips[a["trip_id"]]["status"] == "confirmed")
    eligible = sum(g["size"] for g in groups.values() if g["eligible"])
    return {"passengers": sum(g["size"] for g in groups.values()), "eligible": eligible,
            "covered": covered, "confirmed": confirmed, "exceptions": eligible-covered,
            "hotel_cost": hotel_cost, "transport_cost": transport_cost, "meal_cost": meal_cost,
            "total_cost": hotel_cost+transport_cost+meal_cost, "room_count": sum(len(a["room_ids"]) for a in active),
            "trip_count": len(active_trips), "nights": nights(state)}


def validate(state: dict) -> None:
    """Fail atomically on duplicate use, split groups, capacity or time violations."""
    groups = {g["id"]: g for g in state["groups"]}
    hotels = {h["id"]: h for h in state["hotels"]}
    seen_groups, seen_rooms, seen_vehicles = set(), set(), set()
    active = [a for a in state["assignments"] if a["hotel_status"] in ACTIVE]
    assignments = {a["group_id"]: a for a in active}
    for a in active:
        gid = a["group_id"]
        assert gid not in seen_groups, "Grup alocat de două ori"
        assert groups[gid]["eligible"], "Grup neeligibil alocat"
        seen_groups.add(gid)
        hotel = hotels[a["hotel_id"]]
        assert hotel["status"] == "available" or a["hotel_status"] == "confirmed", "Hotel indisponibil"
        assert dt(hotel["available_from"]) <= dt(state["incident"]["start"]) and dt(hotel["available_until"]) >= dt(state["incident"]["end"]), "Hotel în afara intervalului"
        room_map = {r["id"]: r for r in hotel["rooms"]}
        selected = []
        for rid in a["room_ids"]:
            assert rid not in seen_rooms and rid in room_map, "Supraalocare camere"
            seen_rooms.add(rid)
            selected.append(room_map[rid])
        needs = sorted(groups[gid]["room_needs"], key=lambda n: (-n["accessible"], -n["occupants"]))
        assert len(needs) == len(selected), "Necesar camere invalid"
        for need in needs:
            matches = [r for r in selected if r["capacity"] >= need["occupants"] and (not need["accessible"] or r["accessible"])]
            assert matches, "Capacitate sau accesibilitate cameră insuficientă"
            selected.remove(min(matches, key=lambda r: (r["capacity"], r["id"])))
    seen_trip_groups = set()
    for trip in state["trips"]:
        if trip["status"] not in ACTIVE:
            continue
        assert trip["group_ids"], "Transfer gol"
        assert trip["vehicle_id"] not in seen_vehicles, "Vehicul folosit simultan"
        seen_vehicles.add(trip["vehicle_id"])
        vehicle = next(v for v in state["vehicles"] if v["id"] == trip["vehicle_id"])
        assert vehicle["status"] == "available", "Vehicul indisponibil"
        occupants = [groups[gid] for gid in trip["group_ids"]]
        assert sum(g["size"] for g in occupants) <= vehicle["seats"], "Locuri insuficiente"
        assert sum(g["bags"] for g in occupants) <= vehicle["bags"], "Bagaje peste capacitate"
        assert sum(g["accessible"] for g in occupants) <= vehicle["wheelchairs"], "Accesibilitate vehicul insuficientă"
        departure = dt(trip["departure_at"])
        assert dt(vehicle["available_from"]) <= departure and departure + timedelta(minutes=hotels[trip["hotel_id"]]["travel_minutes"]) <= dt(vehicle["available_until"]), "Interval vehicul invalid"
        for group in occupants:
            assert group["id"] not in seen_trip_groups, "Familie împărțită în transferuri"
            seen_trip_groups.add(group["id"])
            assert dt(group["pickup_start"]) <= departure <= dt(group["pickup_end"]), "Interval pasager invalid"
            a = assignments.get(group["id"])
            assert a and a["trip_id"] == trip["id"] and a["hotel_id"] == trip["hotel_id"], "Familie sau destinație transfer inconsistentă"
    for a in active:
        assert not a.get("trip_id") or a["group_id"] in seen_trip_groups, "Transfer lipsă"
