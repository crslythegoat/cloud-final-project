import os
from pathlib import Path

import psycopg
import pytest


@pytest.fixture(scope="session")
def pg():
    uri = os.environ.get("CLOUD_DB_TEST_POSTGRES_URI")
    if not uri:
        pytest.skip("Set CLOUD_DB_TEST_POSTGRES_URI to an EMPTY disposable test database")
    conn = psycopg.connect(uri, autocommit=True, connect_timeout=5)
    if conn.execute("SELECT to_regnamespace('cloud_db')").fetchone()[0] is not None:
        conn.close()
        pytest.fail("Test database must be empty; never drop an existing cloud_db schema")
    root = Path(__file__).resolve().parents[1]
    for file in ["001_business_schema.sql", "002_sync_and_logs.sql"]:
        conn.execute((root / "migrations" / file).read_text(encoding="utf-8"))
    yield conn
    conn.close()
