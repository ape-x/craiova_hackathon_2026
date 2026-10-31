"""Offline tests, isolated before app modules are imported during collection.

app.main creates its default ASGI app at import time. Never let collection load
real provider credentials or open the operator's configured SQLite database.
MockTransport, ASGI TestClient and fake voice sockets remain usable.
"""
import socket
import tempfile
from pathlib import Path

import pytest


_patch = pytest.MonkeyPatch()
_temporary = None


def _deny_network(*args, **kwargs):
    raise AssertionError("Live network access is disabled in backend tests; use a mock transport")


def pytest_configure(config):
    global _temporary
    _temporary = tempfile.TemporaryDirectory(prefix="airport-recovery-tests-")
    isolated = {
        "OPENAI_API_KEY": "", "FR24_API_KEY": "", "GOOGLE_MAPS_API_KEY": "",
        "GOOGLE_MAPS_SERVER_API_KEY": "", "BOOKING_DEMAND_API_TOKEN": "", "BOOKING_AFFILIATE_ID": "",
        "LITEAPI_API_KEY": "", "LITEAPI_ENVIRONMENT": "sandbox", "HOTEL_OFFERS_PROVIDER": "auto",
        "OPERATOR_TOKEN": "", "ENVIRONMENT": "test", "OPERATION_MODE": "simulation",
        "DATABASE_PATH": str(Path(_temporary.name) / "import-only.sqlite3"),
        "PUBLIC_BASE_URL": "http://localhost:5173",
        "CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
        "ALLOWED_HOSTS": "localhost,127.0.0.1,testserver",
    }
    for name, value in isolated.items():
        _patch.setenv(name, value)
    # Block real transports before collection, not merely after fixture setup.
    # AF_UNIX socket pairs used by event loops are not network connections.
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            return _deny_network()
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            return _deny_network()
        return original_connect_ex(sock, address)

    _patch.setattr(socket.socket, "connect", connect)
    _patch.setattr(socket.socket, "connect_ex", connect_ex)
    _patch.setattr(socket, "getaddrinfo", _deny_network)
    _patch.setattr(socket, "create_connection", _deny_network)


def pytest_unconfigure(config):
    _patch.undo()
    if _temporary is not None:
        _temporary.cleanup()
