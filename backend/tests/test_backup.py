"""Backup isolation and no-repeat checks using temporary SQLite state only."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.store as store_module
from app.airport_catalog import lookup_airport
from app.airport_cab_planning import AirportCabImport, AirportCabPlanning, AirportCabPlanningRequest
from app.models import Airport, PassengerGroup
from app.providers.bucharest_cab import normalize_quote
from app.store import DomainError, Store


NOW = datetime(2030, 1, 4, tzinfo=timezone.utc)
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backup.py"


class NoSourceCalls:
    configured = True

    def __init__(self):
        self.quote_calls = 0

    def validate_request(self, request):
        return request

    async def quote(self, *args, **kwargs):
        self.quote_calls += 1
        raise AssertionError("Restoration must not repeat a supplier request")


def initialize(store):
    def populate(world):
        world.scenario_id = "CAB-BACKUP-TEST"
        world.epoch = NOW.isoformat()
        world.sim_time = 0
        world.airports = []
        for code in ("OTP", "CRA"):
            reference = lookup_airport(code)
            world.airports.append(Airport(id=code, code=code, name=reference["name"],
                lat=reference["lat"], lon=reference["lon"]))
        world.groups = [PassengerGroup(id="G1", name="Reported adults", size=2, adult_count=2,
            origin="OTP", destination="CRA", ready_at=600, deadline=1200, budget=500,
            original_flight_id="ARRIVED", token="offline", source="operator_input")]
        world.budget = 500
        return {"applied": True, "operational_record": "retained-after-restore"}
    store.mutate("cab-backup-operational-record", {"initialization": "offline"}, store.read().version, populate)


def request(store, key):
    return AirportCabPlanningRequest(group_id="G1", destination_airport="CRA", pickup_at=NOW+timedelta(hours=10),
        suitcases=2, handbags=2, expected_version=store.read().version, idempotency_key=key)


def rows(connection):
    return connection.execute("SELECT rowid,id,request_key,fingerprint,scenario_id,status,lease_until,http_requests "
        "FROM airport_cab_receipts ORDER BY rowid").fetchall()


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatched_http_requests", [0, 1, 2])
async def test_airport_cab_backup_omits_cache_preserves_counters_and_never_requeries_same_key(
        tmp_path, monkeypatch, dispatched_http_requests):
    monkeypatch.setattr(store_module, "utcnow", lambda: NOW)
    store = Store(str(tmp_path / "source.sqlite3"), operation_mode="live")
    initialize(store)
    provider = NoSourceCalls()
    service = AirportCabPlanning(SimpleNamespace(), store, provider=provider, now=lambda: NOW)

    completed = request(store, "cab-backup-completed")
    saved = service._reserve(completed)
    for _ in range(2):
        service._dispatch(saved["quote_id"], saved["context"], completed)
    payload = json.loads((Path(__file__).parent / "fixtures/bucharest_cab_otp_cra.json").read_text())
    payload.update(datetime_param="2030-01-04 12:00:00", datetime="2030-01-04 10:00:00")
    quote = normalize_quote(payload, saved["query"], received_at=NOW)
    cache_marker = "CAB-TEMPORARY-SOURCE-BODY-MUST-NOT-SURVIVE-BACKUP"
    response = {"quote_id":saved["quote_id"], "quote_sequence":saved["sequence"], "group_id":"G1",
        "source_version":store.read().version, "created_at":NOW.isoformat(),
        "usable_until":(NOW+timedelta(seconds=120)).isoformat(), "state":"ready", "transfer":quote,
        "options":[], "blockers":[], "replayed":False, "cache_marker":cache_marker}
    service._finish(saved["quote_id"], saved["context"], response)
    import_request = AirportCabImport(option_id=quote["options"][0]["id"],
        expected_version=store.read().version, idempotency_key="cab-backup-import")
    imported = service.import_quote(saved["quote_id"], import_request)
    imported_resources = [item.model_dump(mode="json") for item in store.read().resources]
    assert len(imported_resources) == 1

    unknown = request(store, "cab-backup-already-unknown")
    uncertain = service._reserve(unknown)
    service._dispatch(uncertain["quote_id"], uncertain["context"], unknown)
    with store.connect() as connection:
        connection.execute("UPDATE airport_cab_receipts SET status='unknown' WHERE id=?", (uncertain["quote_id"],))

    dispatched = request(store, "cab-backup-dispatched")
    pending = service._reserve(dispatched)
    for _ in range(dispatched_http_requests):
        service._dispatch(pending["quote_id"], pending["context"], dispatched)
    with store.connect() as connection:
        # Sparse rowids also protect the durable revision sequence through VACUUM.
        connection.execute("UPDATE airport_cab_receipts SET rowid=77 WHERE id=?", (pending["quote_id"],))

    destination = tmp_path / "restored.sqlite3"
    with store.connect() as live:
        before_receipts = rows(live)
        before_cache = live.execute("SELECT * FROM airport_cab_cache").fetchall()
        before_idempotency = live.execute("SELECT key,fingerprint,result FROM idempotency ORDER BY key").fetchall()
        before_world = live.execute("SELECT data FROM world").fetchone()[0]
        assert len(before_cache) == 1 and cache_marker in before_cache[0][2]
        assert [row[-1] for row in before_receipts] == [2, 1, dispatched_http_requests]
        result = subprocess.run([sys.executable, str(SCRIPT), store.path, str(destination)],
            check=True, capture_output=True, text=True, timeout=10)
        assert "Verified snapshot saved" in result.stdout
        assert rows(live) == before_receipts
        assert live.execute("SELECT * FROM airport_cab_cache").fetchall() == before_cache
        assert live.execute("SELECT key,fingerprint,result FROM idempotency ORDER BY key").fetchall() == before_idempotency
        assert live.execute("SELECT data FROM world").fetchone()[0] == before_world

    assert cache_marker.encode() not in destination.read_bytes()
    assert destination.stat().st_mode & 0o777 == 0o600
    restored = Store(str(destination), operation_mode="live")
    restored_service = AirportCabPlanning(SimpleNamespace(), restored, provider=provider, now=lambda: NOW)
    assert [item.model_dump(mode="json") for item in restored.read().resources] == imported_resources
    replay = restored_service.import_quote(saved["quote_id"], import_request)
    assert replay["replayed"] is True and replay["resource_ids"] == imported["resource_ids"]
    with restored.connect() as connection:
        expected = [row[:5] + ("unknown" if row[5] == "dispatched" else row[5],) + row[6:] for row in before_receipts]
        assert rows(connection) == expected
        assert connection.execute("SELECT COUNT(*) FROM airport_cab_cache").fetchone()[0] == 0
        restored_idempotency = connection.execute("SELECT key,fingerprint,result FROM idempotency ORDER BY key").fetchall()
        assert [(key, fingerprint, json.loads(data)) for key, fingerprint, data in restored_idempotency] == [
            (key, fingerprint, json.loads(data)) for key, fingerprint, data in before_idempotency]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    for body, expected_status in ((completed, 409), (unknown, 503), (dispatched, 503)):
        with pytest.raises(DomainError) as caught:
            await restored_service.quote(body)
        assert caught.value.status == expected_status
        if expected_status == 503:
            assert caught.value.applied is None
            assert caught.value.details == {"in_progress":False, "unknown_outcome":True}
    with restored.connect() as connection:
        assert rows(connection) == expected
    assert provider.quote_calls == 0
