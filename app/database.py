"""Lazy, process-wide Mongo pools; short-lived PostgreSQL sessions."""

from contextlib import contextmanager
from threading import Lock

import psycopg
from pymongo import MongoClient

from app.core.config import get_settings
from app.runtime import CLUSTERS, COLLECTIONS

_clients = {}
_lock = Lock()


def mongo(cluster="mongo_1"):
    if cluster not in CLUSTERS:
        raise ValueError("Unknown cluster")
    settings = get_settings()
    with _lock:
        if cluster not in _clients:
            _clients[cluster] = MongoClient(
                getattr(settings, f"{cluster}_uri").get_secret_value(),
                appname="cloud-db",
                tz_aware=True,
                serverSelectionTimeoutMS=settings.database_timeout_ms,
                connectTimeoutMS=settings.database_timeout_ms,
                socketTimeoutMS=settings.database_timeout_ms,
            )
        return _clients[cluster][settings.mongo_database]


def collection(cluster, name):
    if name not in COLLECTIONS:
        raise ValueError("Collection outside the project allowlist")
    return mongo(cluster)[name]


def close_clients():
    with _lock:
        for client in _clients.values():
            client.close()
        _clients.clear()


@contextmanager
def postgres(*, read_only=False, row_factory=None):
    settings = get_settings()
    with psycopg.connect(
        settings.postgres_direct_uri.get_secret_value(),
        autocommit=True,
        connect_timeout=max(2, settings.database_timeout_ms // 1000),
        options="-c statement_timeout=15000 -c lock_timeout=5000",
        application_name="cloud-db",
        row_factory=row_factory,
    ) as conn:
        conn.read_only = read_only
        yield conn
