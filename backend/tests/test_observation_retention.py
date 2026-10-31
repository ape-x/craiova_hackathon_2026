import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta

from fastapi.testclient import TestClient

import app.store as store_module
from app.config import Settings
from app.main import create_app
from app.store import Store, utcnow


def observation(identifier,stamp):
    return {"id":identifier,"callsign":"TEST ONLY","lat":44.32,"lon":23.88,"source":"Flightradar24","mode":"LIVE","observed_at":stamp.isoformat(),"received_at":stamp.isoformat()}


def test_expired_cache_is_removed_on_read_without_world_version_change(tmp_path,monkeypatch):
    store=Store(str(tmp_path/"cache.sqlite3"),60);start=utcnow()
    store.set_observations([observation("expired-observation",start)],{"state":"connected","unbounded_raw_payload":{"secret":"not-a-status-field"}})
    version=store.read().version
    monkeypatch.setattr(store_module,"utcnow",lambda:start+timedelta(seconds=61))
    world=store.read()
    assert not world.observations and world.version==version
    assert world.provider_status["flights"]["state"]=="expired"
    assert "unbounded_raw_payload" not in world.provider_status["flights"]
    with store.connect() as conn:
        raw=conn.execute("SELECT data FROM observations").fetchone()[0]
    assert json.loads(raw)==[]


def test_idempotency_and_world_do_not_archive_feed_and_replays_overlay_current_cache(tmp_path):
    store=Store(str(tmp_path/"snapshots.sqlite3"));start=utcnow()
    store.set_observations([observation("old-sensitive-observation",start)],{"state":"connected"})
    payload={"type":"test"}
    original=store.mutate("operation-key",payload,1,lambda world:None)
    store.remember("readonly-key",{},2,{"explanation":"No operational change"})
    assert original["world"]["observations"][0]["id"]=="old-sensitive-observation"
    with store.connect() as conn:
        persisted=[r[0] for r in conn.execute("SELECT result FROM idempotency")]
        persisted.append(conn.execute("SELECT data FROM world").fetchone()[0])
    assert all("old-sensitive-observation" not in raw for raw in persisted)
    store.set_observations([observation("current-sensitive-observation",start)],{"state":"connected"})
    replay=store.replay("operation-key",payload)
    assert replay["replayed"] and replay["world"]["version"]==2
    assert replay["world"]["observations"][0]["id"]=="current-sensitive-observation"
    replay_via_mutate=store.mutate("operation-key",payload,1,lambda world:None)
    assert replay_via_mutate["world"]["observations"]==replay["world"]["observations"]


def test_legacy_observation_snapshots_are_scrubbed_once_without_changing_scenario(tmp_path):
    path=str(tmp_path/"legacy.sqlite3");store=Store(path)
    result=store.mutate("legacy-operation",{},1,lambda world:None)
    result["world"]["observations"]=[observation("legacy-sensitive-observation",utcnow())]
    result["world"]["provider_status"]["flights"]={"state":"connected"}
    with store.connect() as conn:
        conn.execute("UPDATE idempotency SET result=?",(json.dumps(result),))
        conn.execute("UPDATE world SET data=?",(json.dumps(result["world"]),))
        conn.execute("DELETE FROM controls WHERE key='observation_redaction_v1'")
    reopened=Store(path)
    assert reopened.read().version==2
    with reopened.connect() as conn:
        rows=[row[0] for row in conn.execute("SELECT result FROM idempotency")]
        rows.append(conn.execute("SELECT data FROM world").fetchone()[0])
    assert all("legacy-sensitive-observation" not in row for row in rows)


def test_background_cleanup_runs_while_feed_is_paused_without_provider_calls(tmp_path,monkeypatch):
    settings=Settings(database_path=str(tmp_path/"paused.sqlite3"),observation_retention_seconds=60,openai_api_key="",fr24_api_key="",_env_file=None)
    app=create_app(settings);store=app.state.store;start=utcnow()
    store.set_observations([observation("paused-sensitive-observation",start)],{"state":"connected"})
    store.set_control("traffic_running",False)
    monkeypatch.setattr(store_module,"utcnow",lambda:start+timedelta(seconds=61))
    with TestClient(app):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            # Read the raw cache table: calling Store.read would itself expire it.
            with store.connect() as conn:
                rows=json.loads(conn.execute("SELECT data FROM observations").fetchone()[0])
            if not rows:
                break
            time.sleep(.05)
        assert not rows
        assert not store.control("traffic_running") and store.poll_usage()==0


def test_backup_omits_observation_cache_preserves_state_and_refuses_overwrite(tmp_path):
    path=tmp_path/"source?special#name.sqlite3";store=Store(str(path))
    store.set_observations([observation("do-not-archive-this-aircraft",utcnow())],{"state":"connected"})
    destination=tmp_path/"snapshot.sqlite3"
    script=Path(__file__).resolve().parents[2]/"scripts"/"backup.py"
    result=subprocess.run([sys.executable,str(script),str(path),str(destination)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT count(*) FROM observations").fetchone()[0]==0
        assert json.loads(conn.execute("SELECT data FROM world").fetchone()[0])["version"]==1
    assert "do-not-archive-this-aircraft" not in destination.read_bytes().decode("utf-8",errors="ignore")
    assert destination.stat().st_mode&0o777==0o600
    before=destination.read_bytes()
    retry=subprocess.run([sys.executable,str(script),str(path),str(destination)],capture_output=True,text=True,timeout=10)
    assert retry.returncode!=0 and destination.read_bytes()==before


def test_dev_script_stops_backend_when_frontend_fails(tmp_path):
    root=tmp_path/"test project";scripts=root/"scripts";scripts.mkdir(parents=True)
    source=Path(__file__).resolve().parents[2]/"scripts"/"dev.sh"
    target=scripts/"dev.sh";target.write_text(source.read_text())
    api=root/"backend"/".venv"/"bin"/"python";api.parent.mkdir(parents=True)
    pidfile=root/"api.pid"
    api.write_text("#!"+sys.executable+"\nimport os,signal,time\nfrom pathlib import Path\nPath(os.environ['DEV_TEST_PID']).write_text(str(os.getpid()))\nsignal.signal(signal.SIGTERM,lambda *args:exit(0))\nwhile True: time.sleep(1)\n")
    api.chmod(0o755)
    web=root/"frontend"/"node_modules"/".bin"/"vite";web.parent.mkdir(parents=True)
    web.write_text("#!/bin/sh\nsleep 0.2\nexit 17\n");web.chmod(0o755)
    result=subprocess.run(["/bin/bash",str(target)],env={**os.environ,"DEV_TEST_PID":str(pidfile)},capture_output=True,text=True,timeout=8)
    assert result.returncode==17,result.stderr
    pid=int(pidfile.read_text())
    try:
        os.kill(pid,0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("Backend remained alive after frontend startup failed")
