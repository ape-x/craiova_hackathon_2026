from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import time
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from .models import AuditEvent, FlightSelection, World
from .seed import seed_world, empty_live_world


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DomainError(Exception):
    def __init__(self, status: int, message: str, details=None, *, applied: bool | None=False):
        self.status, self.message, self.details = status,message,details
        self.applied=applied
        super().__init__(message)


def audit(world: World, kind: str, description: str, target=None, data=None):
    world.events.append(AuditEvent(id=secrets.token_hex(12),type=kind,timestamp=utcnow().isoformat(),version=world.version+1,
        target_id=target,description=description,data=data or {}))


def refresh_stats(world: World):
    groups = {g.id:g for g in world.groups}
    active = [o for o in world.offers if o.status in ("held","proposed","accepted","confirmed")]
    total = sum(g.size for g in world.groups)
    world.stats = {
        "total_passengers":total,"total_groups":len(groups),
        **{f"{status}_passengers":sum(g.size for g in world.groups if g.status==status) for status in ("waiting","offered","confirmed","affected","exception","scheduled")},
        "recovered_passengers":sum(groups[o.group_id].size for o in active if world.mode=="simulation" or o.status=="confirmed"),
        "proposed_passengers":sum(groups[o.group_id].size for o in active if o.status in ("proposed","accepted")),
        "committed_cost":round(sum(o.itinerary.total_cost for o in active),2),
        "confirmed_cost":round(sum(o.itinerary.total_cost for o in active if o.status=="confirmed"),2),
        "budget_remaining":round(world.budget-sum(o.itinerary.total_cost for o in active),2),
        "resource_usage":{r.id:sum(x.quantity for x in world.reservations if x.resource_id==r.id and x.status in ("held","proposed","accepted","confirmed")) for r in world.resources},
        "currency":world.currency,"data_source":"synthetic" if world.mode=="simulation" else "real_resources_test_passengers" if any(g.source=="synthetic" for g in world.groups) else "real", "last_updated":utcnow().isoformat(),
    }


def expire_holds(world: World) -> bool:
    changed = False
    for offer in world.offers:
        if offer.status in ("held","proposed","accepted") and datetime.fromisoformat(offer.expires_at)<=utcnow():
            offer.status="expired"
            offer.reason="Temporary all-resource hold expired. Request a new offer." if world.mode=="simulation" else "Proposal freshness window expired. Request current supplier quotes."
            for reservation in world.reservations:
                if reservation.offer_id==offer.id:
                    reservation.status="released"
            group = next(g for g in world.groups if g.id==offer.group_id)
            if group.status=="offered":
                group.status="waiting"
            audit(world,"hold_expired",f"All resources for {group.name} released after offer expiry." if world.mode=="simulation" else f"Proposal for {group.name} expired; no supplier cancellation was issued.",offer.id)
            changed=True
    return changed


class Store:
    """SQLite serializes writes across workers; version CAS prevents stale model plans.

    Every itinerary's resource holds and idempotency result commit in one transaction.
    No model/network call is ever performed under this lock.
    """
    def __init__(self, path: str, observation_retention_seconds: int=3600, operation_mode: str | None=None, legacy_fr24_credits_per_request: int=40):
        if operation_mode not in (None,"live","simulation"):
            raise ValueError("Unknown operation mode")
        self.path = path
        self.observation_retention_seconds=observation_retention_seconds
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS world (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS leases (name TEXT PRIMARY KEY, expires REAL NOT NULL, count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS controls (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS request_claims (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, owner TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS world_archives (id TEXT PRIMARY KEY, archived_at TEXT NOT NULL, mode TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS flight_lookups (id TEXT PRIMARY KEY, query_key TEXT NOT NULL, select_until REAL NOT NULL, retain_until REAL NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS flight_lookup_query ON flight_lookups(query_key);
            """)
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM controls WHERE key='fr24_credits_reserved'").fetchone():
                previous_usage=conn.execute("SELECT count FROM leases WHERE name='fr24'").fetchone()
                credits=(previous_usage[0] if previous_usage else 0)*legacy_fr24_credits_per_request
                conn.execute("INSERT INTO controls VALUES('fr24_credits_reserved',?)",(json.dumps(credits),))
            existing=conn.execute("SELECT data FROM world WHERE id=1").fetchone()
            if not existing:
                world=empty_live_world() if operation_mode=="live" else seed_world()
                refresh_stats(world)
                conn.execute("INSERT INTO world VALUES(1,?)",(world.model_dump_json(),))
                self._save_audit(conn,world)
            elif operation_mode is not None and json.loads(existing[0]).get("mode","simulation") != operation_mode:
                previous=World.model_validate_json(existing[0])
                archived=self._without_observations({"world":json.loads(existing[0])})["world"]
                conn.execute("INSERT INTO world_archives VALUES(?,?,?,?)",(secrets.token_hex(16),utcnow().isoformat(),previous.mode,json.dumps(archived)))
                world=empty_live_world() if operation_mode=="live" else seed_world()
                world.version=previous.version+1
                refresh_stats(world)
                conn.execute("UPDATE world SET data=? WHERE id=1",(world.model_dump_json(),))
                conn.execute("DELETE FROM idempotency")
                conn.execute("DELETE FROM request_claims")
                self._save_audit(conn,world)
            # One-time cleanup of development snapshots created before the
            # separate observation-cache retention policy was introduced.
            if not conn.execute("SELECT 1 FROM controls WHERE key='observation_redaction_v1'").fetchone():
                old=json.loads(conn.execute("SELECT data FROM world WHERE id=1").fetchone()[0])
                cleaned=self._without_observations({"world":old})["world"]
                conn.execute("UPDATE world SET data=? WHERE id=1",(json.dumps(cleaned),))
                for key,result in conn.execute("SELECT key,result FROM idempotency").fetchall():
                    conn.execute("UPDATE idempotency SET result=? WHERE key=?",(json.dumps(self._without_observations(json.loads(result))),key))
                conn.execute("INSERT INTO controls VALUES('observation_redaction_v1','true')")
            if not conn.execute("SELECT 1 FROM controls WHERE key='archive_observation_redaction_v1'").fetchone():
                for archive_id,data in conn.execute("SELECT id,data FROM world_archives").fetchall():
                    cleaned=self._without_observations({"world":json.loads(data)})["world"]
                    conn.execute("UPDATE world_archives SET data=? WHERE id=?",(json.dumps(cleaned),archive_id))
                conn.execute("INSERT INTO controls VALUES('archive_observation_redaction_v1','true')")
        os.chmod(self.path,0o600)

    @contextmanager
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def fingerprint(payload) -> str:
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

    def _load(self,conn):
        conn.execute("DELETE FROM flight_lookups WHERE retain_until<=?",(utcnow().timestamp(),))
        world=World.model_validate_json(conn.execute("SELECT data FROM world WHERE id=1").fetchone()[0])
        if world.mode=="live":
            world.sim_time=max(0,int((utcnow()-datetime.fromisoformat(world.epoch)).total_seconds()//60))
        for incident in world.incidents:
            match=self._incident_flight(conn,incident.flight_ref.lookup_id,incident.flight_ref.selection_id)
            incident.flight=FlightSelection.model_validate(match) if match else None
            incident.flight_context_state="available" if match else "expired"
        return world

    def _save_audit(self,conn,world):
        conn.executemany("INSERT OR IGNORE INTO audit VALUES(?,?,?,?)",[(e.id,world.scenario_id,e.version,e.model_dump_json()) for e in world.events])

    def _save(self,conn,world):
        refresh_stats(world)
        self._save_audit(conn,world)
        world.events=world.events[-200:]
        world.decisions=world.decisions[-6:]
        persisted=self._without_observations({"world":world.model_dump(mode="json")})["world"]
        conn.execute("UPDATE world SET data=? WHERE id=1",(json.dumps(persisted),))

    @staticmethod
    def _without_observations(response):
        result={**response}
        if isinstance(result.get("world"),dict):
            world={**result["world"],"observations":[]}
            world["provider_status"]={k:v for k,v in world.get("provider_status",{}).items() if k!="flights"}
            if "incidents" in world:
                world["incidents"]=[{**case,"flight":None,"flight_context_state":"expired"} for case in world["incidents"]]
            result["world"]=world
        result.pop("observations",None)
        return result

    def _retained_observations(self,observations,status):
        now=utcnow()
        cutoff=now.timestamp()-self.observation_retention_seconds
        retained=[]
        for observation in observations[:20]:
            try:
                stamps=[datetime.fromisoformat(observation[key].replace("Z","+00:00")) for key in ("observed_at","received_at")]
                if any(stamp.tzinfo is None for stamp in stamps):
                    continue
                oldest=min(stamp.timestamp() for stamp in stamps)
                if cutoff<oldest<=now.timestamp()+60:
                    retained.append(observation)
            except (KeyError,ValueError,TypeError,AttributeError):
                continue
        allowed={"state","source","updated_at","environment","message","code","last_observed_at","last_received_at","expired_count"}
        bounded={key:(value[:400] if isinstance(value,str) else value) for key,value in status.items() if key in allowed and isinstance(value,(str,bool,int,float))}
        if retained:
            bounded["last_observed_at"]=max(row["observed_at"] for row in retained)
            bounded["last_received_at"]=max(row["received_at"] for row in retained)
        if len(retained)<len(observations):
            bounded["expired_count"]=len(observations)-len(retained)
            if not retained:
                bounded.update({"state":"expired","source":"Flightradar24","message":"Observation cache expired; waiting for a fresh observation.","updated_at":now.isoformat()})
        return retained,bounded

    def _current_observations(self,conn):
        row=conn.execute("SELECT data,status FROM observations WHERE id=1").fetchone()
        if not row:
            return [],{}
        observations,status=json.loads(row[0]),json.loads(row[1])
        retained,bounded=self._retained_observations(observations,status)
        # Only write under an explicit transaction. Replay responses still filter
        # immediately; the background cleanup removes expired cache rows on disk.
        if conn.in_transaction and (retained!=observations or bounded!=status):
            conn.execute("UPDATE observations SET data=?,status=? WHERE id=1",(json.dumps(retained),json.dumps(bounded)))
        return retained,bounded

    def _with_observations(self,conn,world):
        world.observations,status=self._current_observations(conn)
        if status:
            world.provider_status["flights"]=status
        else:
            world.provider_status.pop("flights",None)
        for decision in world.decisions:
            timing=conn.execute("SELECT value FROM controls WHERE key=?",("decision_timings:"+decision.id,)).fetchone()
            if timing:
                decision.timings_ms=json.loads(timing[0])
        return world

    def _with_response_timings(self,conn,response):
        if isinstance(response.get("world"),dict):
            observations,status=self._current_observations(conn)
            response["world"]["observations"]=observations
            response["world"].setdefault("provider_status",{}).pop("flights",None)
            if status:
                response["world"]["provider_status"]["flights"]=status
            for incident in response["world"].get("incidents",[]):
                ref=incident["flight_ref"]
                match=self._incident_flight(conn,ref["lookup_id"],ref["selection_id"])
                incident.update(flight=match,flight_context_state="available" if match else "expired")
        decisions=response.get("world",{}).get("decisions",[])+([response["decision"]] if response.get("decision") else [])
        for decision in decisions:
            timing=conn.execute("SELECT value FROM controls WHERE key=?",("decision_timings:"+decision["id"],)).fetchone()
            if timing:
                decision["timings_ms"]=json.loads(timing[0])
        return response

    def read(self) -> World:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            world=self._load(conn)
            if expire_holds(world):
                world.version+=1
                self._save(conn,world)
            self._with_observations(conn,world)
            conn.commit()
            return world

    def replay(self,key: str,payload):
        with self.connect() as conn:
            row=conn.execute("SELECT fingerprint,result FROM idempotency WHERE key=?",(key,)).fetchone()
            if row:
                if row[0]!=self.fingerprint(payload):
                    raise DomainError(409,"Idempotency key was already used for a different request")
                return self._with_response_timings(conn,{**json.loads(row[1]),"replayed":True})
        return None

    def claim_request(self,key,payload,lease_seconds):
        """Coalesce live provider work across workers without holding a DB lock."""
        fingerprint=self.fingerprint(payload)
        now=utcnow().timestamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            completed=conn.execute("SELECT fingerprint,result FROM idempotency WHERE key=?",(key,)).fetchone()
            if completed:
                if completed[0]!=fingerprint:
                    raise DomainError(409,"Idempotency key was already used for a different request")
                return {"replay":self._with_response_timings(conn,{**json.loads(completed[1]),"replayed":True})}
            pending=conn.execute("SELECT fingerprint,expires FROM request_claims WHERE key=?",(key,)).fetchone()
            if pending:
                if pending[0]!=fingerprint:
                    raise DomainError(409,"Idempotency key is already processing a different request")
                if pending[1]>now:
                    raise DomainError(503,"This request is still processing. Retry the same request ID after it finishes; do not create a new key.",{"in_progress":True,"retry_after_seconds":2},applied=None)
            owner=secrets.token_hex(16)
            conn.execute("INSERT INTO request_claims VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET fingerprint=excluded.fingerprint,owner=excluded.owner,expires=excluded.expires",(key,fingerprint,owner,now+lease_seconds))
            return {"owner":owner}

    def release_request(self,key,owner):
        with self.connect() as conn:
            conn.execute("DELETE FROM request_claims WHERE key=? AND owner=?",(key,owner))

    def remember(self,key,payload,expected_version,result):
        """Record a non-mutating AI reply without manufacturing a world version."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row=conn.execute("SELECT fingerprint,result FROM idempotency WHERE key=?",(key,)).fetchone()
            if row:
                if row[0]!=self.fingerprint(payload):
                    raise DomainError(409,"Idempotency key was already used for a different request")
                conn.commit()
                return self._with_response_timings(conn,{**json.loads(row[1]),"replayed":True})
            world=self._load(conn)
            if world.version!=expected_version:
                raise DomainError(409,"World changed while interpreting the request",{"current_version":world.version})
            response={"world":self._with_observations(conn,world).model_dump(mode="json"),**result,"replayed":False}
            conn.execute("INSERT INTO idempotency VALUES(?,?,?,?)",(key,self.fingerprint(payload),json.dumps(self._without_observations(response)),utcnow().isoformat()))
            conn.commit()
            return response

    def mutate(self,key: str,payload,expected_version: int | None,operation: Callable[[World],dict | None],*,request_started: float | None=None,authorize: Callable[[World],None] | None=None,on_commit: Callable | None=None):
        transaction_started=time.perf_counter()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            world=self._load(conn) if authorize else None
            if authorize:
                authorize(world)
            row=conn.execute("SELECT fingerprint,result FROM idempotency WHERE key=?",(key,)).fetchone()
            if row:
                if row[0]!=self.fingerprint(payload):
                    raise DomainError(409,"Idempotency key was already used for a different request")
                conn.commit()
                return self._with_response_timings(conn,{**json.loads(row[1]),"replayed":True})
            world=world if world is not None else self._load(conn)
            if expire_holds(world):
                world.version+=1
                self._save(conn,world)
                # Expiry must commit even when it makes the client's plan stale.
                conn.commit()
                conn.execute("BEGIN IMMEDIATE")
                world=self._load(conn)
                if authorize:
                    authorize(world)
                # Another same-key request may have committed while expiry was
                # committed independently. Recheck before applying or inserting.
                row=conn.execute("SELECT fingerprint,result FROM idempotency WHERE key=?",(key,)).fetchone()
                if row:
                    if row[0]!=self.fingerprint(payload):
                        raise DomainError(409,"Idempotency key was already used for a different request")
                    return self._with_response_timings(conn,{**json.loads(row[1]),"replayed":True})
            if expected_version is not None and world.version!=expected_version:
                raise DomainError(409,"World changed; refresh and retry with the new version",{"current_version":world.version})
            result=operation(world) or {}
            world.version+=1
            self._save(conn,world)
            response={"world":self._with_observations(conn,world).model_dump(mode="json"),**result,"replayed":False}
            conn.execute("INSERT INTO idempotency VALUES(?,?,?,?)",(key,self.fingerprint(payload),json.dumps(self._without_observations(response)),utcnow().isoformat()))
            if on_commit is not None:
                on_commit(conn,response)
            conn.commit()
            if request_started is not None and response.get("decision"):
                finished=time.perf_counter()
                decision=response["decision"]
                timings={**decision.get("timings_ms",{}),"transaction_ms":round((finished-transaction_started)*1000,2),"server_total_ms":round((finished-request_started)*1000,2)}
                # Timing telemetry is independent of the operational version. The
                # measured total includes the durable commit, not this telemetry write.
                decision["timings_ms"]=timings
                for item in response["world"]["decisions"]:
                    if item["id"]==decision["id"]:
                        item["timings_ms"]=timings
                try:
                    conn.execute("INSERT INTO controls VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",("decision_timings:"+decision["id"],json.dumps(timings)))
                except sqlite3.Error:
                    # Operational commit already succeeded. A telemetry failure
                    # must not turn that successful mutation into a failed action.
                    logging.getLogger(__name__).warning("Decision committed, but timing telemetry could not be persisted",exc_info=True)
            return response

    @staticmethod
    def _freshen_flight(match):
        match={**match}
        if match.get("flight_ended") is True:
            match["freshness"]="historical"
        elif match.get("last_seen"):
            age=(utcnow()-datetime.fromisoformat(match["last_seen"].replace("Z","+00:00"))).total_seconds()
            match["freshness"]="current" if -60<=age<=120 else "stale"
        else:
            match["freshness"]="unknown"
        return match

    def _incident_flight(self,conn,lookup_id,selection_id):
        row=conn.execute("SELECT data FROM flight_lookups WHERE id=? AND retain_until>?",(lookup_id,utcnow().timestamp())).fetchone()
        if not row:
            return None
        match=next((item for item in json.loads(row[0])["matches"] if item["selection_id"]==selection_id),None)
        return self._freshen_flight(match) if match else None

    def cached_flight_lookup(self,query_key):
        with self.connect() as conn:
            row=conn.execute("SELECT data FROM flight_lookups WHERE query_key=? AND select_until>? AND retain_until>? ORDER BY select_until DESC LIMIT 1",(query_key,utcnow().timestamp(),utcnow().timestamp())).fetchone()
            if not row:
                return None
            result=json.loads(row[0])
            result["matches"]=[self._freshen_flight(item) for item in result["matches"]]
            return {**result,"cached":True}

    def save_flight_lookup(self,query_key,result):
        now=utcnow().timestamp()
        select_until=datetime.fromisoformat(result["expires_at"]).timestamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM flight_lookups WHERE retain_until<=?",(now,))
            conn.execute("INSERT INTO flight_lookups VALUES(?,?,?,?,?)",(result["lookup_id"],query_key,select_until,now+self.observation_retention_seconds,json.dumps(result)))
            conn.execute("DELETE FROM flight_lookups WHERE id IN (SELECT id FROM flight_lookups ORDER BY retain_until DESC LIMIT -1 OFFSET 128)")

    def selected_flight(self,lookup_id,selection_id,*,fresh=True):
        # Read-only: safe to use while mutate holds the writer transaction.
        with self.connect() as conn:
            row=conn.execute("SELECT data,select_until,retain_until FROM flight_lookups WHERE id=?",(lookup_id,)).fetchone()
            if not row or row[2]<=utcnow().timestamp() or (fresh and row[1]<=utcnow().timestamp()):
                raise DomainError(409,"Flight context expired. Look up the flight again before selecting or refreshing it.")
            result=json.loads(row[0])
            match=next((item for item in result["matches"] if item["selection_id"]==selection_id),None)
            if not match:
                raise DomainError(422,"Selected flight does not belong to this lookup")
            return result,self._freshen_flight(match)

    def reserve_fr24_request(self,interval,max_requests,max_credits,credits):
        """Reserve worst-case credits before I/O; unknown outcomes keep the charge."""
        now=utcnow().timestamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row=conn.execute("SELECT expires,count FROM leases WHERE name='fr24'").fetchone()
            count=row[1] if row else 0
            used=json.loads(conn.execute("SELECT value FROM controls WHERE key='fr24_credits_reserved'").fetchone()[0])
            if count>=max_requests or used+credits>max_credits:
                return "budget_exhausted"
            if row and row[0]>now:
                return "poll_interval"
            conn.execute("INSERT INTO leases VALUES('fr24',?,?) ON CONFLICT(name) DO UPDATE SET expires=excluded.expires,count=excluded.count",(now+interval,count+1))
            conn.execute("UPDATE controls SET value=? WHERE key='fr24_credits_reserved'",(json.dumps(used+credits),))
            return None

    def fr24_credits_reserved(self):
        return self.control("fr24_credits_reserved",0)

    def set_observations(self,observations,status):
        retained,bounded=self._retained_observations(observations,status)
        with self.connect() as conn:
            conn.execute("INSERT INTO observations VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,status=excluded.status",(json.dumps(retained),json.dumps(bounded)))

    def purge_observations(self):
        """A local cache policy, not a representation of provider license terms."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            observations,_=self._current_observations(conn)
            conn.execute("DELETE FROM flight_lookups WHERE retain_until<=?",(utcnow().timestamp(),))
            return len(observations)

    def acquire_poll(self,interval: int,max_requests: int) -> bool:
        if max_requests<=0:
            return False
        now=utcnow().timestamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row=conn.execute("SELECT expires,count FROM leases WHERE name='fr24'").fetchone()
            if row and (row[0]>now or row[1]>=max_requests):
                conn.commit(); return False
            count=row[1]+1 if row else 1
            conn.execute("INSERT INTO leases VALUES('fr24',?,?) ON CONFLICT(name) DO UPDATE SET expires=excluded.expires,count=excluded.count",(now+interval,count))
            conn.commit(); return True

    def control(self,key,default=None):
        with self.connect() as conn:
            row=conn.execute("SELECT value FROM controls WHERE key=?",(key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_control(self,key,value):
        with self.connect() as conn:
            conn.execute("INSERT INTO controls VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,json.dumps(value)))

    def poll_usage(self):
        with self.connect() as conn:
            row=conn.execute("SELECT count FROM leases WHERE name='fr24'").fetchone()
            return row[0] if row else 0

    def audit_history(self,limit=200):
        with self.connect() as conn:
            scenario=self._load(conn).scenario_id
            return [json.loads(row[0]) for row in conn.execute("SELECT data FROM audit WHERE scenario_id=? ORDER BY rowid DESC LIMIT ?",(scenario,limit))]
