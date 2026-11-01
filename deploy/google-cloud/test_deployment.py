"""Offline infrastructure guards: temp SQLite only, no cloud or application calls."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from persistence_guard import DURABLE_TABLES, database, environment
from restore import restore


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "snapshot.sqlite3"
        with sqlite3.connect(self.source) as conn:
            conn.executescript("""
                CREATE TABLE world(id INTEGER, data TEXT);
                CREATE TABLE controls(key TEXT,value TEXT);
                CREATE TABLE leases(name TEXT,count INTEGER);
                CREATE TABLE recovery_jobs(status TEXT);
                CREATE TABLE recovery_steps(id TEXT);
                CREATE TABLE audit(id TEXT);
                CREATE TABLE idempotency(key TEXT,result TEXT);
                CREATE TABLE airport_cab_receipts(status TEXT,http_requests INTEGER);
                CREATE TABLE transfer_quote_receipts(status TEXT);
                CREATE TABLE live_command_intents(state TEXT);
                CREATE TABLE romanian_hotel_profiles(data TEXT);
                CREATE TABLE future_durable_records(data TEXT);
            """)
            existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in sorted(DURABLE_TABLES - existing):
                conn.execute(f"CREATE TABLE {table}(data TEXT)")
            conn.execute("INSERT INTO world VALUES(1,?)", (json.dumps({"mode": "live", "version": 52, "groups": [], "resources": []}),))
            conn.executemany("INSERT INTO controls VALUES(?,?)", [("traffic_running", "false"), ("fr24_credits_reserved", "3215")])
            conn.execute("INSERT INTO leases VALUES('fr24',81)")
            conn.execute("INSERT INTO recovery_jobs VALUES('unknown_outcome')")
            conn.execute("INSERT INTO recovery_steps VALUES('consumed')")
            conn.execute("INSERT INTO airport_cab_receipts VALUES('unknown',2)")
            conn.execute("INSERT INTO transfer_quote_receipts VALUES('unknown')")
            conn.execute("INSERT INTO live_command_intents VALUES('reserved')")
            conn.execute("INSERT INTO future_durable_records VALUES('unknown')")
            conn.execute("INSERT INTO recovery_cancellations VALUES('do-not-repeat')")
            conn.execute("INSERT INTO idempotency VALUES('do-not-retry','unknown')")
            conn.execute("INSERT INTO romanian_hotel_profiles VALUES('private-input-fixture')")
        self.target = self.root / "data" / "recovery.sqlite3"

    def checksum(self):
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def install(self, checksum=None):
        return restore(self.source, self.target, checksum or self.checksum(), uid=os.getuid(), gid=os.getgid())

    def test_exact_restore_retains_budgets_profiles_unknown_outcomes_and_idempotency(self):
        result = self.install()
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertEqual(result["fr24_requests"], 81)
        self.assertEqual(result["fr24_credits"], 3215)
        self.assertEqual(result["romanian_hotel_profiles"], 1)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.target) as conn:
            self.assertEqual(conn.execute("SELECT status FROM recovery_jobs").fetchone()[0], "unknown_outcome")
            self.assertEqual(conn.execute("SELECT result FROM idempotency").fetchone()[0], "unknown")
            self.assertEqual(conn.execute("SELECT status,http_requests FROM airport_cab_receipts").fetchone(), ("unknown", 2))
            self.assertEqual(conn.execute("SELECT status FROM transfer_quote_receipts").fetchone()[0], "unknown")
            self.assertEqual(conn.execute("SELECT state FROM live_command_intents").fetchone()[0], "reserved")
            self.assertEqual(conn.execute("SELECT data FROM future_durable_records").fetchone()[0], "unknown")
            self.assertEqual(conn.execute("SELECT data FROM recovery_cancellations").fetchone()[0], "do-not-repeat")

    def test_every_missing_durable_table_refuses_restore_without_creating_target(self):
        for table in sorted(DURABLE_TABLES):
            with self.subTest(table=table):
                damaged = self.root / (table + ".sqlite3")
                damaged.write_bytes(self.source.read_bytes())
                with sqlite3.connect(damaged) as conn:
                    conn.execute(f"DROP TABLE {table}")
                with self.assertRaisesRegex(ValueError, "authoritative application and request ledgers"):
                    restore(damaged, self.target, hashlib.sha256(damaged.read_bytes()).hexdigest(),
                            uid=os.getuid(), gid=os.getgid())
                self.assertFalse(self.target.exists())

    def test_committed_application_schema_is_accepted_without_transient_cache_tables(self):
        # Read SQL literals from the reviewed base, not another task's dirty files.
        # Never import/start the application or adapters.
        repository = Path(__file__).resolve().parents[2]
        revision = "69b252a9209c43f97de5b418e62a6074f66d280e"
        paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", revision, "backend/app"],
                                        cwd=repository, text=True).splitlines()
        current = self.root / "current-schema.sqlite3"
        with sqlite3.connect(current) as conn:
            for path in paths:
                if not path.endswith(".py"):
                    continue
                source = subprocess.check_output(["git", "show", revision + ":" + path], cwd=repository, text=True)
                for node in ast.walk(ast.parse(source)):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and "CREATE TABLE IF NOT EXISTS" in node.value:
                        conn.executescript(node.value)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            transient = {"observations", "flight_lookups", "transfer_quote_cache", "airport_cab_cache"}
            self.assertEqual(tables, DURABLE_TABLES | transient)
            for table in sorted(transient):
                conn.execute(f"DROP TABLE {table}")
            conn.execute("INSERT INTO world VALUES(1,?)", (json.dumps({"mode": "live", "version": 52, "groups": [], "resources": []}),))
            conn.executemany("INSERT INTO controls VALUES(?,?)", [("traffic_running", "false"), ("fr24_credits_reserved", "3215")])
            conn.execute("INSERT INTO leases VALUES('fr24',0,81)")
        result = database(current, restoring=True)
        self.assertEqual(set(result["durable_rows"]), DURABLE_TABLES)
        self.assertEqual(result["fr24_credits"], 3215)

    def test_repeated_restore_never_overwrites_existing_database(self):
        self.install()
        original = self.target.read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.install()
        self.assertEqual(self.target.read_bytes(), original)

    def test_wrong_checksum_does_not_install(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.install("0" * 64)
        self.assertFalse(self.target.exists())

    def test_active_job_or_polling_requires_quiescence(self):
        for statement in ("INSERT INTO recovery_jobs VALUES('running')", "UPDATE controls SET value='true' WHERE key='traffic_running'"):
            with self.subTest(statement=statement), sqlite3.connect(self.source) as conn:
                conn.execute(statement)
                conn.commit()
                with self.assertRaisesRegex(ValueError, "quiescent"):
                    self.install()
                self.assertFalse(self.target.exists())
                conn.execute("DELETE FROM recovery_jobs WHERE status='running'")
                conn.execute("UPDATE controls SET value='false' WHERE key='traffic_running'")

    def test_missing_usage_or_synthetic_world_refuses_restore(self):
        with sqlite3.connect(self.source) as conn:
            conn.execute("DELETE FROM leases")
        with self.assertRaisesRegex(ValueError, "usage records"):
            self.install()
        with sqlite3.connect(self.source) as conn:
            conn.execute("INSERT INTO leases VALUES('fr24',81)")
            conn.execute("UPDATE world SET data=?", (json.dumps({"mode": "simulation", "version": 1, "groups": [], "resources": []}),))
        with self.assertRaisesRegex(ValueError, "existing live world"):
            self.install()

    def test_missing_database_never_created(self):
        with self.assertRaises(ValueError):
            database(self.target)
        self.assertFalse(self.target.exists())

    def test_env_requires_private_literal_strong_operator_token(self):
        path = self.root / "backend.env"
        path.write_text("OPERATOR_TOKEN=" + "x" * 64 + "\n")
        path.chmod(0o600)
        environment(path)
        for value in ("", "short", "${TOKEN_FROM_SHELL}", '"' + "x" * 64 + '"'):
            path.write_text("OPERATOR_TOKEN=" + value + "\n")
            with self.subTest(value=value[:5]), self.assertRaises(ValueError):
                environment(path)
        path.write_text("OPERATOR_TOKEN=" + "x" * 64 + "\n")
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "0600"):
            environment(path)

    def test_wal_source_and_target_sidecar_are_not_silently_discarded(self):
        Path(str(self.source) + "-wal").write_bytes(b"uncheckpointed")
        with self.assertRaisesRegex(ValueError, "standalone"):
            self.install()
        Path(str(self.source) + "-wal").unlink()
        self.target.parent.mkdir()
        Path(str(self.target) + "-wal").write_bytes(b"existing-ledger")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.install()


if __name__ == "__main__":
    unittest.main()
