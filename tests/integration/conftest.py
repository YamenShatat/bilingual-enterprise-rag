"""Fixtures for tests that need the PostgreSQL container (`docker compose up -d --wait db`).

Without a reachable database these tests are skipped, so the suite stays green on a machine
that has no Docker. Set ``RAG_REQUIRE_DATABASE=1`` (as CI should) to make them fail instead:
a skipped test proves nothing, and a database test that silently never runs is worse than none.

Tests that commit (migrations) or need the real schema run in throwaway databases named
``rag_test_<random>``, created and dropped here, so the development database is never touched.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from bilingual_rag.config import ConfigError, DatabaseSettings, load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.database.migrate import migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_PREFIX = "rag_test_"


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


@contextmanager
def scratch_database(settings: DatabaseSettings) -> Iterator[DatabaseSettings]:
    """Create an empty database with a random name and drop it afterwards."""
    name = f"{SCRATCH_PREFIX}{uuid.uuid4().hex[:12]}"
    assert name.startswith(SCRATCH_PREFIX)  # the only kind of database this helper may drop
    admin_settings = replace(settings, dbname="postgres")
    with connect(admin_settings, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield replace(settings, dbname=name)
    finally:
        with connect(admin_settings, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def empty_database(database_settings) -> Iterator[DatabaseSettings]:
    """A brand-new database with nothing in it (not even the vector extension)."""
    with scratch_database(database_settings) as settings:
        yield settings


@pytest.fixture(scope="session")
def migrated_database(database_settings) -> Iterator[DatabaseSettings]:
    """One database with every migration applied, shared by the whole test session."""
    with scratch_database(database_settings) as settings:
        with connect(settings) as conn:
            migrate(conn)
        yield settings


@pytest.fixture
def fresh_migrated_database(database_settings) -> Iterator[DatabaseSettings]:
    """A migrated database of its own, for code that commits (the HTTP API opens and commits
    its own connections, so the shared ``migrated_database`` would keep its writes)."""
    with scratch_database(database_settings) as settings:
        with connect(settings) as conn:
            migrate(conn)
        yield settings


@pytest.fixture
def store_connection(migrated_database):
    """A connection to the migrated database that is always rolled back.

    Repository functions never commit, so a test can write freely and leave no trace.
    """
    conn = connect(migrated_database)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
