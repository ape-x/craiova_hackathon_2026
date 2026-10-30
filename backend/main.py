import csv
import io
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from .demo import demo_groups, new_state
from .engine import ACTIVE, allocate, dt, metrics, recommend, validate
from .integrations import SimulatedNotificationGateway, SimulatedProviderGateway
from .store import audit, transaction

app = FastAPI(title="StrandedOS · API simulare", version="0.1.0")
providers, notifications = SimulatedProviderGateway(), SimulatedNotificationGateway()


class Mutation(BaseModel):
    revision: int


class Incident(Mutation):
    flight: str = Field(min_length=2, max_length=20)
    airport: str = Field(min_length=2, max_length=80)
    destination: str = Field(min_length=2, max_length=80)
    passenger_count: int = Field(ge=1, le=1000)
    start: str
    end: str
    budget: int = Field(ge=1, le=10000000)
    load_demo: bool = False

    @model_validator(mode="after")
    def dates(self):
        try:
            start, end = dt(self.start), dt(self.end)
            if start.tzinfo is None or end.tzinfo is None or not timedelta(hours=1) <= end-start <= timedelta(hours=72):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Intervalul trebuie să fie între 1 și 72 ore, cu fus orar explicit.")
        if self.load_demo and self.passenger_count != 186:
            raise ValueError("Scenariul demonstrativ conține exact 186 de pasageri.")
        return self


class Approval(Mutation):
    allow_over_budget: bool = False


class Response(Mutation):
    response: Literal["confirmed", "refused"]
    reason: str = Field(min_length=3, max_length=300)


class Adjustment(Mutation):
    hotel_id: str
    reason: str = Field(min_length=3, max_length=300)


class CsvImport(Mutation):
    csv_text: str = Field(max_length=1000000)


def expire(state: dict):
    now = datetime.now(timezone.utc)
    count = 0
    for a in state["assignments"]:
        if a["hotel_status"] == "held" and dt(a["expires_at"]) <= now:
            a["hotel_status"] = "expired"
            count += 1
    for t in state["trips"]:
        if t["status"] == "held" and dt(t["expires_at"]) <= now:
            t["status"] = "expired"
            count += 1
    if count:
        valid_hotels = {a["group_id"] for a in state["assignments"] if a["hotel_status"] in ACTIVE}
        for t in state["trips"]:
            t["group_ids"] = [gid for gid in t["group_ids"] if gid in valid_hotels]
        state["trips"] = [t for t in state["trips"] if t["group_ids"]]
        valid_trips = {t["id"] for t in state["trips"] if t["status"] in ACTIVE}
        for a in state["assignments"]:
            if a.get("trip_id") not in valid_trips:
                a["trip_id"] = None
        state["approved_version"] = None
        state["revision"] += 1
        refresh_exceptions(state)
        audit(state, "Rezervări expirate", f"{count} rezervări temporare au depășit cele 20 de minute. Resursele sunt eliberate.", "Sistem")
        state["last_change"] = "Rezervările temporare au expirat. Recalculează planul; confirmările valabile se păstrează."


def refresh_exceptions(state: dict):
    trips = {t["id"] for t in state["trips"] if t["status"] in ACTIVE}
    covered = {a["group_id"] for a in state["assignments"] if a["hotel_status"] in ACTIVE and a.get("trip_id") in trips}
    state["exceptions"] = [{"group_id": g["id"], "passengers": g["size"], "reason": "Serviciu refuzat, expirat sau fără capacitate. Este necesară recalcularea ori intervenția operatorului."} for g in state["groups"] if g["eligible"] and g["id"] not in covered]


def check(state: dict, revision: int):
    expire(state)
    if revision != state["revision"]:
        raise HTTPException(409, "Datele s-au schimbat. Pagina a fost reîncărcată; repetă acțiunea.")


def changed(state: dict):
    state["revision"] += 1


def view(state: dict):
    return {**state, "metrics": metrics(state), "approval_required": bool(state["plan_version"] and state["approved_version"] != state["plan_version"])}


def replace_plan(state: dict, updated: dict, reason: str):
    for key in ("assignments", "trips", "exceptions"):
        state[key] = updated[key]
    state["plan_version"] += 1
    state["approved_version"] = None
    state["last_change"] = reason
    changed(state)
    audit(state, f"Plan v{state['plan_version']} generat", reason, "Motor de alocare · simulare")


@app.get("/api/health")
def health():
    return {"status": "ok", "simulation": True}


@app.get("/api/workspace")
def workspace():
    with transaction() as state:
        expire(state)
        return view(state)


@app.post("/api/reset")
def reset(body: Mutation):
    with transaction() as state:
        check(state, body.revision)
        revision = state["revision"] + 1
        state.clear()
        state.update(new_state())
        state["revision"] = revision
        audit(state, "Scenariu resetat", "Incident demonstrativ nou, catalog complet și 186 de pasageri fictivi.")
        return view(state)


@app.post("/api/incidents")
def create_incident(body: Incident):
    with transaction() as state:
        check(state, body.revision)
        revision = state["revision"] + 1
        state.clear()
        state.update(new_state(body.model_dump(exclude={"revision", "load_demo"}), body.load_demo))
        state["revision"] = revision
        audit(state, "Incident deschis", f"Zbor {body.flight}; {body.passenger_count} pasageri declarați; buget {body.budget} RON. Catalog fictiv pentru acest interval.")
        return view(state)


@app.post("/api/passengers/demo")
def load_demo(body: Mutation):
    with transaction() as state:
        check(state, body.revision)
        if state["plan_version"]:
            raise HTTPException(409, "Pasagerii nu pot fi înlocuiți după generarea planului. Deschide un incident nou.")
        if state["incident"]["passenger_count"] != 186:
            raise HTTPException(422, "Demo-ul are 186 de pasageri. Creează un incident cu acest număr.")
        state["groups"] = demo_groups(state["incident"]["start"], state["incident"]["end"])
        changed(state)
        audit(state, "Pasageri încărcați", "186 de pasageri fictivi, 88 de grupuri, 4 persoane cu cerințe de accesibilitate.")
        return view(state)


def parse_csv(text: str, state: dict):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    required = {"passenger_id", "name", "group_id", "room_capacity", "accessible", "bags", "eligible"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("Coloane obligatorii: " + ", ".join(sorted(required)))
    groups, seen = {}, set()
    start = dt(state["incident"]["start"])
    for row in reader:
        pid, gid, name = row["passenger_id"].strip(), row["group_id"].strip(), row["name"].strip()
        if not pid or pid in seen or not gid or not name or len(name) > 100 or len(gid) > 30:
            raise ValueError("Identificatori unici, nume și grupuri valide sunt obligatorii.")
        seen.add(pid)
        if len(seen) > 1000:
            raise ValueError("Maximum 1.000 de pasageri.")
        if any(row[k].strip().lower() not in {"true", "false", "1", "0"} for k in ("accessible", "eligible")):
            raise ValueError("accessible și eligible acceptă true/false sau 1/0.")
        accessible, eligible = [row[k].strip().lower() in {"true", "1"} for k in ("accessible", "eligible")]
        capacity, bags = int(row["room_capacity"]), int(row["bags"])
        if not 1 <= capacity <= 4 or not 0 <= bags <= 5:
            raise ValueError("room_capacity: 1–4; bags: 0–5.")
        pickup_start = row.get("pickup_start") or min(start+timedelta(minutes=30), dt(state["incident"]["end"])-timedelta(minutes=35)).isoformat()
        pickup_end = row.get("pickup_end") or min(start+timedelta(minutes=150), dt(state["incident"]["end"])-timedelta(minutes=35)).isoformat()
        low, high = dt(pickup_start), dt(pickup_end)
        if low.tzinfo is None or high.tzinfo is None or not start <= low <= high <= dt(state["incident"]["end"]):
            raise ValueError("Intervalul de preluare trebuie să fie în intervalul incidentului, cu fus orar.")
        if gid not in groups:
            groups[gid] = {"id": gid, "label": name, "members": [], "size": 0, "bags": 0, "accessible": 0,
                           "eligible": eligible, "capacity": capacity, "pickup_start": pickup_start, "pickup_end": pickup_end}
        g = groups[gid]
        if g["eligible"] != eligible or g["capacity"] != capacity:
            raise ValueError("Membrii unui grup trebuie să aibă aceeași eligibilitate și room_capacity.")
        g["pickup_start"] = max(dt(g["pickup_start"]), low).isoformat()
        g["pickup_end"] = min(dt(g["pickup_end"]), high).isoformat()
        if dt(g["pickup_start"]) > dt(g["pickup_end"]):
            raise ValueError("Membrii grupului nu au un interval comun de preluare.")
        g["members"].append({"id": pid, "name": name, "accessible": accessible})
        g["size"] += 1
        g["bags"] += bags
        g["accessible"] += int(accessible)
        if g["size"] > 12:
            raise ValueError("MVP: maximum 12 persoane într-un grup.")
    import secrets
    result = []
    for g in groups.values():
        g["room_needs"] = []
        members = sorted(g["members"], key=lambda m: not m["accessible"])
        capacity = g.pop("capacity")
        for i in range(0, len(members), capacity):
            chunk = members[i:i+capacity]
            g["room_needs"].append({"occupants": len(chunk), "accessible": any(m["accessible"] for m in chunk)})
        g["token"] = secrets.token_urlsafe(24)
        result.append(g)
    if len(seen) != state["incident"]["passenger_count"]:
        raise ValueError(f"CSV-ul trebuie să conțină exact {state['incident']['passenger_count']} pasageri, conform incidentului.")
    return result


@app.post("/api/passengers/import")
def import_csv(body: CsvImport):
    with transaction() as state:
        check(state, body.revision)
        if state["plan_version"]:
            raise HTTPException(409, "Importul este permis înaintea generării planului. Deschide un incident nou.")
        try:
            state["groups"] = parse_csv(body.csv_text, state)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise HTTPException(422, str(error))
        changed(state)
        audit(state, "CSV importat", f"{len(state['groups'])} grupuri validate; fără separarea familiilor.")
        return view(state)


@app.post("/api/plan/generate")
def generate(body: Mutation):
    with transaction() as state:
        check(state, body.revision)
        if not state["groups"]:
            raise HTTPException(422, "Importă pasagerii sau încarcă scenariul demonstrativ.")
        before = metrics(state)
        updated = recommend(state)
        after = metrics(updated)
        preserved = sum(1 for a in updated["assignments"] if a["hotel_status"] == "confirmed")
        reason = f"Recomandare deterministă: {after['covered']}/{after['eligible']} pasageri acoperiți, {after['total_cost']} RON. "
        if state["plan_version"]:
            reason += f"{preserved} grupuri cu cazare confirmată păstrate. Diferență de cost: {after['total_cost']-before['total_cost']:+} RON față de serviciile încă valabile. Hotelurile și vehiculele refuzate sunt excluse."
        else:
            reason += "Grupurile rămân împreună. Camerele, bagajele, accesibilitatea și orele au fost verificate."
        replace_plan(state, updated, reason)
        return view(state)


@app.post("/api/plan/approve")
def approve(body: Approval):
    with transaction() as state:
        check(state, body.revision)
        if not state["assignments"]:
            raise HTTPException(422, "Generează mai întâi un plan.")
        if any(a["hotel_status"] not in ACTIVE for a in state["assignments"]) or any(t["status"] not in ACTIVE for t in state["trips"]):
            raise HTTPException(409, "Recalculează rezervările refuzate sau expirate înainte de aprobare.")
        cost = metrics(state)["total_cost"]
        over = cost > state["incident"]["budget"]
        if over and not body.allow_over_budget:
            raise HTTPException(409, "Costul depășește bugetul. Bifează aprobarea explicită a depășirii.")
        state["approved_version"] = state["plan_version"]
        changed(state)
        audit(state, "Buget aprobat intern", f"Plan v{state['plan_version']}: {cost} RON." + (f" Depășire explicit aprobată: {cost-state['incident']['budget']} RON." if over else " În limita bugetului.") + " Furnizorii nu sunt încă confirmați.")
        return view(state)


@app.post("/api/plan/groups/{group_id}")
def adjust(group_id: str, body: Adjustment):
    with transaction() as state:
        check(state, body.revision)
        if not any(g["id"] == group_id and g["eligible"] for g in state["groups"]) or not any(h["id"] == body.hotel_id for h in state["hotels"]):
            raise HTTPException(404, "Grup sau hotel inexistent.")
        candidate = deepcopy(state)
        candidate["assignments"] = [a for a in candidate["assignments"] if a["group_id"] != group_id]
        for t in candidate["trips"]:
            t["group_ids"] = [g for g in t["group_ids"] if g != group_id]
        candidate["trips"] = [t for t in candidate["trips"] if t["group_ids"]]
        updated = recommend(candidate, forced={group_id: body.hotel_id})
        moved = next((a for a in updated["assignments"] if a["group_id"] == group_id), None)
        if not moved or moved["hotel_id"] != body.hotel_id or not moved["trip_id"]:
            raise HTTPException(422, "Mutarea nu este fezabilă: camere, accesibilitate sau transfer indisponibil. Planul existent a fost păstrat.")
        replace_plan(state, updated, f"Ajustare manuală pentru {group_id}. Motiv: {body.reason}. Serviciile modificate necesită aprobare și confirmare nouă.")
        return view(state)


def respond(state: dict, kind: str, resource_id: str, response: str, reason: str):
    if state["approved_version"] != state["plan_version"] or not state["plan_version"]:
        raise HTTPException(409, "Aprobă intern planul curent înainte de răspunsurile furnizorilor.")
    if kind == "hotel":
        resource = next((h for h in state["hotels"] if h["id"] == resource_id), None)
        pending = [a for a in state["assignments"] if a["hotel_id"] == resource_id and a["hotel_status"] == "held"]
        if not resource or not pending:
            raise HTTPException(409, "Hotelul nu are solicitări în așteptare.")
        for a in pending:
            a["hotel_status"] = response
        if response == "refused":
            resource["status"] = "refused"
            affected = {a["group_id"] for a in pending}
            for t in state["trips"]:
                t["group_ids"] = [gid for gid in t["group_ids"] if gid not in affected]
            state["trips"] = [t for t in state["trips"] if t["group_ids"]]
        name = resource["name"]
    elif kind == "transport":
        trip = next((t for t in state["trips"] if t["id"] == resource_id and t["status"] == "held"), None)
        if not trip:
            raise HTTPException(409, "Transferul nu are o solicitare în așteptare.")
        trip["status"] = response
        vehicle = next(v for v in state["vehicles"] if v["id"] == trip["vehicle_id"])
        if response == "refused":
            vehicle["status"] = "refused"
            for a in state["assignments"]:
                if a.get("trip_id") == trip["id"]:
                    a["trip_id"] = None
        name = vehicle["provider"] + " · " + vehicle["id"]
    else:
        raise HTTPException(404, "Tip de furnizor necunoscut.")
    providers.record_response(resource_id, response)
    refresh_exceptions(state)
    changed(state)
    label = "Confirmare furnizor" if response == "confirmed" else "Refuz furnizor"
    audit(state, label, reason, name + " · simulat")
    if response == "refused":
        state["last_change"] = f"{name} a refuzat: {reason}. Recalculează grupurile afectate; confirmările utilizabile se păstrează."


@app.post("/api/providers/{kind}/{resource_id}/respond")
def provider_response(kind: str, resource_id: str, body: Response):
    with transaction() as state:
        check(state, body.revision)
        respond(state, kind, resource_id, body.response, body.reason)
        return view(state)


@app.post("/api/demo/responses")
def demo_responses(body: Mutation):
    with transaction() as state:
        check(state, body.revision)
        # Accept other requests first, then refuse h2 so useful confirmations survive.
        for hotel in state["hotels"]:
            if hotel["id"] != "h2" and any(a["hotel_id"] == hotel["id"] and a["hotel_status"] == "held" for a in state["assignments"]):
                respond(state, "hotel", hotel["id"], "confirmed", "Disponibilitate confirmată în scenariul demonstrativ.")
        for trip in list(state["trips"]):
            if trip["hotel_id"] != "h2" and trip["status"] == "held":
                respond(state, "transport", trip["id"], "confirmed", "Vehicul și preluare confirmate în simulare.")
        if any(a["hotel_id"] == "h2" and a["hotel_status"] == "held" for a in state["assignments"]):
            respond(state, "hotel", "h2", "refused", "Northline Hotel raportează o indisponibilitate de ultim moment.")
        return view(state)


@app.post("/api/demo/confirm-all")
def confirm_all(body: Mutation):
    with transaction() as state:
        check(state, body.revision)
        for hotel in state["hotels"]:
            if any(a["hotel_id"] == hotel["id"] and a["hotel_status"] == "held" for a in state["assignments"]):
                respond(state, "hotel", hotel["id"], "confirmed", "Solicitare acceptată în simulare; cazare și masă confirmate.")
        for trip in state["trips"]:
            if trip["status"] == "held":
                respond(state, "transport", trip["id"], "confirmed", "Transfer acceptat în simulare.")
        return view(state)


@app.post("/api/demo/expire")
def simulate_expiry(body: Mutation):
    with transaction() as state:
        check(state, body.revision)
        for item in state["assignments"] + state["trips"]:
            if item.get("hotel_status", item.get("status")) == "held":
                item["expires_at"] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        expire(state)
        return view(state)


@app.get("/api/comparison")
def comparison():
    with transaction() as state:
        expire(state)
        # Both strategies receive the SAME current catalog and locked confirmations.
        source = deepcopy(state)
        source["assignments"] = [a for a in source["assignments"] if a["hotel_status"] == "confirmed"]
        locked_groups = {a["group_id"] for a in source["assignments"]}
        source["trips"] = [t for t in source["trips"] if t["status"] == "confirmed"]
        for t in source["trips"]:
            t["group_ids"] = [gid for gid in t["group_ids"] if gid in locked_groups]
        source["trips"] = [t for t in source["trips"] if t["group_ids"]]
        rec, base = metrics(recommend(source)), metrics(allocate(source, "baseline"))
        return {"recommended": rec, "baseline": base, "simulation": True,
                "assumption": "Simulare pe același catalog și aceleași confirmări blocate. Confirmări proiectate dacă toți furnizorii acceptă. Regula simplă: ordinea CSV și primul hotel/vehicul fezabil. Costurile includ cazare, transfer și masă. Compară costul direct numai la acoperire egală."}


@app.get("/api/passenger/{token}")
def passenger(token: str):
    with transaction() as state:
        expire(state)
        group = next((g for g in state["groups"] if g["token"] == token), None)
        if not group:
            raise HTTPException(404, "Linkul de asistență nu este valid.")
        a = next((a for a in state["assignments"] if a["group_id"] == group["id"]), None)
        hotel = next((h for h in state["hotels"] if a and h["id"] == a["hotel_id"] and a["hotel_status"] == "confirmed"), None)
        trip = next((t for t in state["trips"] if a and t["id"] == a.get("trip_id") and t["status"] == "confirmed" and hotel), None)
        vehicle = next((v for v in state["vehicles"] if trip and v["id"] == trip["vehicle_id"]), None)
        return {"simulation": True, "group": {k: group[k] for k in ("id", "label", "size", "members")},
                "flight": state["incident"]["flight"], "end": state["incident"]["end"],
                "hotel": {"name": hotel["name"], "address": hotel["address"], "rooms": len(a["room_ids"])} if hotel else None,
                "transfer": {"provider": vehicle["provider"], "label": vehicle["label"], "departure_at": trip["departure_at"], "meeting_point": "Terminal Sosiri · parter · punctul de întâlnire B"} if trip else None,
                "voucher": {"code": f"DEMO-{group['id']}-MASA", "value": group["size"]*state["meal_rate"], "per_person": state["meal_rate"], "venue": hotel["name"], "expires_at": state["incident"]["end"]} if hotel else None,
                "notification": notifications.passenger_link(token)}
