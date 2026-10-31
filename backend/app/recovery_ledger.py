"""Durable job and dispatch ledger. A lost dispatch is never automatically retried."""
from __future__ import annotations

import json
import secrets

from .store import DomainError, Store, utcnow


TERMINAL = {"completed", "blocked", "failed", "cancelled", "needs_review", "unknown_outcome"}


class RecoveryLedger:
    def __init__(self, store: Store, lease_seconds: int):
        self.store, self.lease_seconds = store, lease_seconds
        with store.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS recovery_jobs (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                    owner TEXT, lease_until REAL NOT NULL DEFAULT 0, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recovery_steps (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, query_hash TEXT NOT NULL,
                    data TEXT NOT NULL, result TEXT, retain_until REAL NOT NULL,
                    query TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(job_id,query_hash));
                CREATE INDEX IF NOT EXISTS recovery_job_steps ON recovery_steps(job_id);
                CREATE TABLE IF NOT EXISTS recovery_cancellations (
                    request_key TEXT PRIMARY KEY, job_id TEXT NOT NULL);
            """)
            conn.execute("BEGIN IMMEDIATE")
            if "query" not in {row[1] for row in conn.execute("PRAGMA table_info(recovery_steps)")}:
                conn.execute("ALTER TABLE recovery_steps ADD COLUMN query TEXT NOT NULL DEFAULT '{}'")

    @staticmethod
    def _load(conn, job_id):
        row = conn.execute("SELECT data FROM recovery_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise DomainError(404, "Recovery job not found")
        return json.loads(row[0])

    @staticmethod
    def _save(conn, job):
        job["updated_at"] = utcnow().isoformat()
        conn.execute("UPDATE recovery_jobs SET status=?,data=? WHERE id=?", (job["status"], json.dumps(job), job["id"]))

    def _public(self, conn, job):
        result = {key:value for key,value in job.items() if not key.startswith("_")}
        result["steps"] = [json.loads(row[0]) for row in conn.execute("SELECT data FROM recovery_steps WHERE job_id=? ORDER BY rowid", (job["id"],))]
        return result

    def replay(self, key, payload):
        with self.store.connect() as conn:
            row = conn.execute("SELECT fingerprint,data FROM recovery_jobs WHERE request_key=?", (key,)).fetchone()
            if not row:
                return None
            if row[0] != Store.fingerprint(payload):
                raise DomainError(409, "This recovery-job key already identifies a different request")
            return {"job": self._public(conn, json.loads(row[1])), "replayed": True}

    def create(self, incident_id, request, scope_hash, scenario_id, *, on_created=None):
        payload = {**request.model_dump(mode="json"), "incident_id": incident_id}
        key = "recovery-job:" + request.idempotency_key
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT fingerprint,data FROM recovery_jobs WHERE request_key=?", (key,)).fetchone()
            if row:
                if row[0] != Store.fingerprint(payload):
                    raise DomainError(409, "This recovery-job key already identifies a different request")
                result = {"job": self._public(conn,json.loads(row[1])), "replayed": True}
                if on_created is None:
                    return result
                world = self.store._load(conn)
                if world.version != request.expected_version or world.scenario_id != scenario_id:
                    raise DomainError(409, "Operation changed before the command receipt could be recorded; inspect the existing job")
                return on_created(conn, world, result)
            world = self.store._load(conn)
            if world.version != request.expected_version or world.scenario_id != scenario_id:
                raise DomainError(409, "Operation changed before this recovery job was queued")
            active = [json.loads(row[0]) for row in conn.execute("SELECT data FROM recovery_jobs WHERE status IN ('queued','running')")]
            if len(active) >= 20:
                raise DomainError(429, "Recovery queue is full; wait for an existing job to finish")
            if any(set(request.group_ids).intersection(job["group_ids"]) for job in active):
                raise DomainError(409, "A recovery job already owns one of these passenger groups; inspect or cancel it first")
            now = utcnow().isoformat()
            job = {"id": "JOB-"+secrets.token_hex(10).upper(), "incident_id":incident_id,
                   "group_ids":request.group_ids, "objective":request.objective, "modes":request.modes,
                   "max_provider_calls":request.max_provider_calls, "calls_dispatched":0,
                   "status":"queued", "stage":"queued", "message":"Recovery job queued; no supplier request has been dispatched.",
                   "created_at":now, "updated_at":now, "source_version":world.version, "applied_version":None,
                   "cancel_requested":False, "gaps":[], "result":None,
                   "_request":payload, "_scope_hash":scope_hash, "_scenario_id":scenario_id}
            conn.execute("INSERT INTO recovery_jobs VALUES(?,?,?,?,?,?,?)", (job["id"],key,Store.fingerprint(payload),"queued",None,0,json.dumps(job)))
            result = {"job":self._public(conn,job), "replayed":False}
            # The command receipt and queued job commit together. In particular,
            # a process failure cannot leave a paid job without its replay key.
            return on_created(conn, world, result) if on_created else result

    def _recover_expired(self, conn):
        now = utcnow().timestamp()
        conn.execute("UPDATE recovery_steps SET result=NULL WHERE retain_until<=?", (now,))
        for job_id, in conn.execute("SELECT id FROM recovery_jobs WHERE status='running' AND lease_until<=?", (now,)).fetchall():
            job = self._load(conn,job_id)
            unknown = False
            for step_id, encoded in conn.execute("SELECT id,data FROM recovery_steps WHERE job_id=?",(job_id,)).fetchall():
                step = json.loads(encoded)
                if step["status"] == "dispatched":
                    unknown = True
                    step.update(status="unknown", finished_at=utcnow().isoformat(), message="Worker lease ended after dispatch; provider outcome and charges are unconfirmed. No retry was issued.")
                    conn.execute("UPDATE recovery_steps SET data=? WHERE id=?", (json.dumps(step),step_id))
            job.update(status="unknown_outcome" if unknown else "needs_review", stage="finished",
                       message="Worker stopped before completing this job. Review its recorded steps; starting a new job is an explicit new attempt.")
            self._save(conn,job)

    def claim_next(self):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._recover_expired(conn)
            # A single shared worker slot bounds orchestration across processes.
            if conn.execute("SELECT 1 FROM recovery_jobs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = conn.execute("SELECT id FROM recovery_jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            job = self._load(conn,row[0])
            owner = secrets.token_hex(16)
            job.update(status="running",stage="validating",message="Checking current passenger scope, provider access and physical journey inputs.")
            self._save(conn,job)
            conn.execute("UPDATE recovery_jobs SET owner=?,lease_until=? WHERE id=?",(owner,utcnow().timestamp()+self.lease_seconds,job["id"]))
            return job,owner

    def get(self, job_id, *, private=False):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._recover_expired(conn)
            job=self._load(conn,job_id)
            return job if private else self._public(conn,job)

    def list(self, incident_id):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._recover_expired(conn)
            return [self._public(conn,json.loads(row[0])) for row in conn.execute("SELECT data FROM recovery_jobs WHERE json_extract(data,'$.incident_id')=? ORDER BY rowid DESC LIMIT 40",(incident_id,))]

    def _owned(self, conn, job_id, owner):
        row=conn.execute("SELECT owner,lease_until FROM recovery_jobs WHERE id=?",(job_id,)).fetchone()
        job=self._load(conn,job_id)
        if row[0]!=owner or job["status"]!="running" or row[1]<=utcnow().timestamp():
            raise DomainError(409,"Recovery worker no longer owns this job")
        return job

    def update(self,job_id,owner,**changes):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job=self._owned(conn,job_id,owner)
            job.update(changes)
            self._save(conn,job)
            conn.execute("UPDATE recovery_jobs SET lease_until=? WHERE id=?",(utcnow().timestamp()+self.lease_seconds,job_id))

    def dispatch(self,job_id,owner,stage,provider,query,group_ids):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job=self._owned(conn,job_id,owner)
            if job["cancel_requested"] or job.get("_halt_dispatch"):
                raise DomainError(409,"Recovery cancellation requested")
            if job["calls_dispatched"]>=job["max_provider_calls"]:
                raise DomainError(409,"Recovery provider-call allowance exhausted")
            query_hash=Store.fingerprint({"stage":stage,"provider":provider,"query":query})
            if conn.execute("SELECT 1 FROM recovery_steps WHERE job_id=? AND query_hash=?",(job_id,query_hash)).fetchone():
                raise DomainError(409,"This provider query was already dispatched; no automatic retry is allowed")
            step={"id":"STEP-"+secrets.token_hex(10),"stage":stage,"provider":provider,"status":"dispatched",
                  "started_at":utcnow().isoformat(),"finished_at":None,"group_ids":group_ids,"message":"Provider request dispatched; outcome pending.","quote_count":0}
            conn.execute("INSERT INTO recovery_steps(id,job_id,query_hash,data,result,retain_until,query) VALUES(?,?,?,?,?,?,?)",(step["id"],job_id,query_hash,json.dumps(step),None,utcnow().timestamp()+3600,json.dumps(query,allow_nan=False)))
            job.update(stage=stage,message=f"Awaiting {provider}.",calls_dispatched=job["calls_dispatched"]+1)
            self._save(conn,job)
            conn.execute("UPDATE recovery_jobs SET lease_until=? WHERE id=?",(utcnow().timestamp()+self.lease_seconds,job_id))
            return step["id"]

    def outcome(self,job_id,owner,step_id,status,message,result=None,quote_count=0):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._owned(conn,job_id,owner)
            row=conn.execute("SELECT data FROM recovery_steps WHERE id=? AND job_id=?",(step_id,job_id)).fetchone()
            step=json.loads(row[0])
            if step["status"]!="dispatched":
                raise DomainError(409,"Provider outcome already recorded")
            step.update(status=status,finished_at=utcnow().isoformat(),message=message,quote_count=quote_count)
            conn.execute("UPDATE recovery_steps SET data=?,result=? WHERE id=?",(json.dumps(step),json.dumps(result) if result is not None else None,step_id))

    def halt(self,job_id,owner,status,message):
        """Stop later dispatches while existing independent calls are drained."""
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job=self._owned(conn,job_id,owner)
            job["_halt_dispatch"]=True
            job["_failure_status"]="unknown_outcome" if "unknown_outcome" in (status,job.get("_failure_status")) else status
            job["message"]=message
            self._save(conn,job)

    def finish(self,job_id,owner,status,message):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job=self._owned(conn,job_id,owner)
            for step_id,encoded in conn.execute("SELECT id,data FROM recovery_steps WHERE job_id=?",(job_id,)).fetchall():
                step=json.loads(encoded)
                if step["status"]=="dispatched":
                    status="unknown_outcome"
                    step.update(status="unknown",finished_at=utcnow().isoformat(),message="Dispatch outcome was not established before worker termination; no automatic retry was issued.")
                    conn.execute("UPDATE recovery_steps SET data=? WHERE id=?",(json.dumps(step),step_id))
            job.update(status=status,stage="finished",message=message)
            self._save(conn,job)

    def cancel(self,job_id,key):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job=self._load(conn,job_id)
            old=conn.execute("SELECT job_id FROM recovery_cancellations WHERE request_key=?",(key,)).fetchone()
            if old and old[0]!=job_id:
                raise DomainError(409,"Cancellation key already identifies a different job")
            if not old:
                conn.execute("INSERT INTO recovery_cancellations VALUES(?,?)",(key,job_id))
                if job["status"] not in TERMINAL:
                    job["cancel_requested"]=True
                    job["message"]="Cancellation requested. A dispatched call may still finish or incur charges; no later call or plan will be applied."
                    if job["status"]=="queued":
                        job.update(status="cancelled",stage="finished")
                    self._save(conn,job)
            return {"job":self._public(conn,job),"replayed":bool(old)}

    def complete_in_transaction(self,conn,job_id,owner,response,result):
        job=self._owned(conn,job_id,owner)
        if job["cancel_requested"]:
            raise DomainError(409,"Recovery was cancelled before commit; no plan was applied")
        job.update(status="completed",stage="finished",message="Sourced recovery proposals applied to the selected groups. No supplier hold or booking was made.",
                   applied_version=response["world"]["version"],result=result)
        self._save(conn,job)
