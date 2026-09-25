"""Fixtures for tests that need the PostgreSQL container (`docker compose up -d --wait db`).

Without a reachable database these tests are skipped, so the suite stays green on a machine
that has no Docker. Set ``RAG_REQUIRE_DATABASE=1`` (as CI should) to make them fail instead:
a skipped test proves nothing, and a database test that silently never runs is worse than none.
"""

import os
from pathlib import Path

import psycopg
import pytest

from bilingual_rag.config import ConfigError, DatabaseSettings, load_database_settings
from bilingual_rag.database.connection import connect

REPO_ROOT = Path(__file__).resolve().parents[2]


def _unavailable(reason: str) -> None:
    if os.environ.get("RAG_REQUIRE_DATABASE") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def database_settings() -> DatabaseSettings:
    try:
        settings = load_database_settings(REPO_ROOT / ".env")
    except ConfigError as exc:
        _unavailable(f"database not configured: {exc}")
    try:
        connect(settings, connect_timeout=2).close()
    except psycopg.OperationalError as exc:
        _unavailable(f"database unreachable at {settings.host}:{settings.port}: {exc}")
    return settings


@pytest.fixture
def db_connection(database_settings):
    """A connection that is always rolled back, so a test cannot leave data behind."""
    conn = connect(database_settings)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
