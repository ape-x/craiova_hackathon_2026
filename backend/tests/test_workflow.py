from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import random

import pytest
from fastapi.testclient import TestClient

from backend.demo import new_state
from backend.engine import ACTIVE, allocate, metrics, recommend, validate
from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDED_DB", str(tmp_path / "test.sqlite3"))
    with TestClient(app) as c:
        yield c


def state(client):
    return client.get("/api/workspace").json()


def post(client, path, body=None, expected=200):
    response = client.post("/api" + path, json={"revision": state(client)["revision"], **(body or {})})
    assert response.status_code == expected, response.text
    return response.json()


def test_demo_has_186_passengers_and_repeatable_plan():
    source = new_state()
    first, second = recommend(source), recommend(source)
    assert sum(g["size"] for g in source["groups"]) == 186
    assert metrics(first)["covered"] == 186
    assert metrics(first)["confirmed"] == 0
    assert [(a["group_id"], a["hotel_id"], a["room_ids"], a["trip_id"]) for a in first["assignments"]] == [(a["group_id"], a["hotel_id"], a["room_ids"], a["trip_id"]) for a in second["assignments"]]


def test_no_overallocation_and_families_share_hotel_and_transfer():
    s = recommend(new_state())
    validate(s)
    room_ids = [rid for a in s["assignments"] for rid in a["room_ids"]]
    assert len(room_ids) == len(set(room_ids))
    for group in s["groups"]:
        assignments = [a for a in s["assignments"] if a["group_id"] == group["id"]]
        assert len(assignments) == 1
        trips = [t for t in s["trips"] if group["id"] in t["group_ids"]]
        assert len(trips) == 1
        assert trips[0]["hotel_id"] == assignments[0]["hotel_id"]


@pytest.mark.parametrize("resource", ["rooms", "vehicles", "bags", "wheelchairs", "pickup"])
def test_infeasible_resources_create_explicit_interventions(resource):
    s = new_state()
    if resource == "rooms":
        for h in s["hotels"]:
            h["rooms"] = []
    if resource == "vehicles":
        s["vehicles"] = []
    if resource in {"bags", "wheelchairs"}:
        for v in s["vehicles"]:
            v[resource] = 0
    if resource == "pickup":
        for g in s["groups"]:
            g["pickup_start"] = g["pickup_end"] = "2026-11-01T12:00:00+02:00"
    result = recommend(s)
    validate(result)
    assert metrics(result)["exceptions"] > 0
    assert sum(e["passengers"] for e in result["exceptions"]) == metrics(result)["exceptions"]


def test_varied_limited_inventory_never_breaks_constraints():
    rng = random.Random(42)
    for _ in range(12):
        s = new_state()
        for h in s["hotels"]:
            h["rooms"] = rng.sample(h["rooms"], rng.randrange(len(h["rooms"])+1))
        s["vehicles"] = rng.sample(s["vehicles"], rng.randrange(len(s["vehicles"])+1))
        result = recommend(s)
        validate(result)
        assert metrics(result)["covered"] >= metrics(allocate(s, "baseline"))["covered"]


def test_validator_detects_duplicate_room_and_vehicle():
    s = recommend(new_state())
    broken = deepcopy(s)
    broken["assignments"].append(deepcopy(broken["assignments"][0]))
    with pytest.raises(AssertionError):
        validate(broken)
    broken = deepcopy(s)
    broken["trips"].append(deepcopy(broken["trips"][0]))
    with pytest.raises(AssertionError):
        validate(broken)


def test_short_assistance_window_bounds_demo_pickups():
    incident = new_state()["incident"]
    incident["end"] = "2026-10-30T21:00:00+02:00"
    s = new_state(incident)
    result = recommend(s)
    validate(result)
    from backend.engine import dt
    assert all(dt(g["pickup_end"]) <= dt(incident["end"]) for g in s["groups"])
    assert all(dt(v["available_until"]) <= dt(incident["end"]) for v in s["vehicles"])


def test_refusing_additional_rooms_keeps_existing_hotel_confirmations():
    from backend.main import respond
    s = recommend(new_state())
    s["plan_version"] = s["approved_version"] = 1
    retained = next(a for a in s["assignments"] if a["hotel_id"] == "h1")
    retained["hotel_status"] = "confirmed"
    saved_rooms = list(retained["room_ids"])
    respond(s, "hotel", "h1", "refused", "Refuz de capacitate suplimentară")
    validate(s)
    updated = recommend(s)
    current = next(a for a in updated["assignments"] if a["group_id"] == retained["group_id"])
    assert current["hotel_status"] == "confirmed"
    assert current["room_ids"] == saved_rooms
    validate(updated)


def test_budget_approval_is_distinct_and_required(client):
    s = post(client, "/plan/generate")
    assert s["metrics"]["confirmed"] == 0
    post(client, "/demo/confirm-all", expected=409)
    approved = post(client, "/plan/approve")
    assert approved["metrics"]["confirmed"] == 0
    confirmed = post(client, "/demo/confirm-all")
    assert confirmed["metrics"]["confirmed"] == 186
    validate(confirmed)


def test_over_budget_requires_explicit_authorization(client):
    incident = state(client)["incident"]
    post(client, "/incidents", {**{k: v for k,v in incident.items() if k not in {"id", "status"}}, "budget": 100, "load_demo": True})
    post(client, "/plan/generate")
    post(client, "/plan/approve", expected=409)
    approved = post(client, "/plan/approve", {"allow_over_budget": True})
    assert approved["approved_version"] == approved["plan_version"]
    assert "Depășire explicit aprobată" in approved["audit"][-1]["reason"]


def test_refusal_replans_only_affected_and_preserves_confirmations(client):
    post(client, "/plan/generate")
    post(client, "/plan/approve")
    refused = post(client, "/demo/responses")
    assert next(h for h in refused["hotels"] if h["id"] == "h2")["status"] == "refused"
    assert refused["metrics"]["exceptions"] > 0
    retained = {a["group_id"]: a for a in refused["assignments"] if a["hotel_status"] == "confirmed"}
    confirmed_trips = {t["id"]: t for t in refused["trips"] if t["status"] == "confirmed"}
    updated = post(client, "/plan/generate")
    validate(updated)
    assert updated["approval_required"]
    for a in updated["assignments"]:
        assert a["hotel_id"] != "h2"
        if a["group_id"] in retained:
            old = retained[a["group_id"]]
            assert a["hotel_id"] == old["hotel_id"]
            assert a["room_ids"] == old["room_ids"]
            assert a["hotel_status"] == "confirmed"
            assert a["trip_id"] == old["trip_id"]
    for trip in updated["trips"]:
        if trip["id"] in confirmed_trips:
            assert trip["status"] == "confirmed"
            assert trip["departure_at"] == confirmed_trips[trip["id"]]["departure_at"]
    post(client, "/demo/confirm-all", expected=409)
    post(client, "/plan/approve")
    final = post(client, "/demo/confirm-all")
    assert final["metrics"]["confirmed"] == final["metrics"]["covered"]
    assert final["metrics"]["confirmed"] + final["metrics"]["exceptions"] == 186


def test_transport_refusal_keeps_confirmed_rooms(client):
    post(client, "/plan/generate")
    post(client, "/plan/approve")
    s = state(client)
    for h in s["hotels"]:
        post(client, f"/providers/hotel/{h['id']}/respond", {"response":"confirmed", "reason":"Acceptare cazare simulată"})
    s = state(client)
    tid = s["trips"][0]["id"]
    refused = post(client, f"/providers/transport/{tid}/respond", {"response":"refused", "reason":"Vehicul indisponibil"})
    validate(refused)
    updated = post(client, "/plan/generate")
    assert all(a["hotel_status"] == "confirmed" for a in updated["assignments"])
    assert not any(t["id"] == tid and t["status"] in ACTIVE for t in updated["trips"])
    validate(updated)


def test_expiry_releases_holds_but_keeps_confirmed_services(client):
    post(client, "/plan/generate")
    post(client, "/plan/approve")
    confirmed = post(client, "/providers/hotel/h1/respond", {"response":"confirmed", "reason":"Disponibilitate confirmată"})
    preserved = {a["group_id"] for a in confirmed["assignments"] if a["hotel_status"] == "confirmed"}
    expired = post(client, "/demo/expire")
    assert any(a["hotel_status"] == "expired" for a in expired["assignments"])
    assert all(a["hotel_status"] == "confirmed" for a in expired["assignments"] if a["group_id"] in preserved)
    assert not any(t["status"] == "held" for t in expired["trips"])
    post(client, "/plan/approve", expected=409)
    updated = post(client, "/plan/generate")
    validate(updated)
    assert metrics(updated)["covered"] == 186


def test_csv_roundtrip_and_invalid_input_is_atomic(client):
    text = Path("public/demo-passengers.csv").read_text()
    result = post(client, "/passengers/import", {"csv_text": text})
    assert result["metrics"]["passengers"] == 186
    before = deepcopy(result)
    post(client, "/passengers/import", {"csv_text":"passenger_id,name\nP1,Ana"}, expected=422)
    assert state(client)["revision"] == before["revision"]
    post(client, "/passengers/import", {"csv_text":text.replace("G001,4", "G001,0",1)}, expected=422)
    post(client, "/plan/generate")
    post(client, "/passengers/import", {"csv_text":text}, expected=409)


def test_csv_multiple_rooms_and_accessibility(client):
    inc = state(client)["incident"]
    post(client, "/incidents", {**{k:v for k,v in inc.items() if k not in {"id", "status"}}, "passenger_count":4, "load_demo":False})
    text = "passenger_id,name,group_id,room_capacity,accessible,bags,eligible\nP1,Ana,F1,2,true,1,true\nP2,Dan,F1,2,false,1,true\nP3,Mara,F1,2,false,1,true\nP4,Ion,F1,2,false,1,true"
    s = post(client,"/passengers/import",{"csv_text":text})
    assert len(s["groups"][0]["room_needs"]) == 2
    s = post(client,"/plan/generate")
    assert len(s["assignments"][0]["room_ids"]) == 2
    validate(s)


def test_invalid_interval_rejected(client):
    inc=state(client)["incident"]
    post(client, "/incidents", {**{k:v for k,v in inc.items() if k not in {"id", "status"}}, "start":"2026-10-30T22:00:00", "end":"2026-10-30T21:00:00"}, expected=422)


def test_stale_and_concurrent_mutations_are_rejected(client):
    revision = state(client)["revision"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post("/api/plan/generate",json={"revision":revision}).status_code, range(2)))
    assert sorted(results) == [200,409]
    validate(state(client))


def test_invalid_adjustment_keeps_plan_atomic(client):
    s = post(client, "/plan/generate")
    post(client, "/plan/groups/G001", {"hotel_id":"missing", "reason":"Solicitare familie"}, expected=404)
    assert state(client)["assignments"] == s["assignments"]


def test_feasible_manual_move_invalidates_approval_and_changed_confirmations(client):
    inc = state(client)["incident"]
    post(client, "/incidents", {**{k:v for k,v in inc.items() if k not in {"id", "status"}}, "passenger_count":2, "load_demo":False})
    csv_text = "passenger_id,name,group_id,room_capacity,accessible,bags,eligible\nP1,Ana,F1,2,false,1,true\nP2,Dan,F1,2,false,1,true"
    post(client, "/passengers/import", {"csv_text":csv_text})
    post(client, "/plan/generate")
    post(client, "/plan/approve")
    original = post(client, "/demo/confirm-all")
    hotel_id = "h1" if original["assignments"][0]["hotel_id"] != "h1" else "h4"
    moved = post(client, "/plan/groups/F1", {"hotel_id":hotel_id, "reason":"Preferință pentru o altă locație"})
    assert moved["assignments"][0]["hotel_id"] == hotel_id
    assert moved["assignments"][0]["hotel_status"] == "held"
    assert moved["approval_required"]
    assert moved["metrics"]["confirmed"] == 0
    assert moved["plan_version"] == original["plan_version"] + 1
    validate(moved)


def test_passenger_only_sees_own_group_and_confirmed_services(client):
    s = post(client, "/plan/generate")
    token = s["groups"][0]["token"]
    before = client.get(f"/api/passenger/{token}").json()
    assert before["hotel"] is None and before["transfer"] is None and before["voucher"] is None
    post(client, "/plan/approve")
    post(client, "/demo/confirm-all")
    passenger = client.get(f"/api/passenger/{token}").json()
    assert passenger["hotel"] and passenger["transfer"] and passenger["voucher"]
    assert len(passenger["group"]["members"]) == 4
    assert not any(key in passenger for key in ("audit", "budget", "groups", "assignments"))
    assert passenger["notification"]["delivered"] is False
    assert client.get("/api/passenger/unknown").status_code == 404


def test_comparison_uses_equal_constraints_and_is_labelled_simulation(client):
    c = client.get("/api/comparison").json()
    assert c["simulation"]
    assert c["recommended"]["covered"] == 186
    assert c["baseline"]["covered"] == 184
    assert c["recommended"]["confirmed"] == 0
    assert "acoperire egală" in c["assumption"]


def test_reset_persists_and_invalidates_passenger_tokens(client):
    before = post(client, "/plan/generate")
    old_token = before["groups"][0]["token"]
    s = post(client, "/reset")
    assert not s["assignments"] and not s["plan_version"]
    assert s["revision"] > before["revision"]
    assert state(client)["revision"] == s["revision"]
    assert client.get(f"/api/passenger/{old_token}").status_code == 404
