"""SQLite aggregate with serialized writes and optimistic UI revisions."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .demo import new_state


def audit(state: dict, action: str, reason: str, actor: str = "Alexandra Ionescu · operațiuni"):
    state["audit"].append({"id": len(state["audit"])+1, "action": action, "reason": reason,
                           "actor": actor, "at": datetime.now(timezone.utc).isoformat()})


@contextmanager
def transaction():
    path = os.environ.get("STRANDED_DB", "data/stranded.sqlite3")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT payload FROM workspace WHERE id=1").fetchone()
        if row:
            state = json.loads(row[0])
        else:
            state = new_state()
            audit(state, "Incident deschis", "Scenariu demonstrativ: 186 de pasageri fictivi la Otopeni.")
        yield state
        connection.execute("INSERT OR REPLACE INTO workspace VALUES (1, ?)", (json.dumps(state, ensure_ascii=False),))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
