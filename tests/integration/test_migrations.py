import hashlib
import threading

import psycopg
import pytest

from bilingual_rag.database.connection import connect
from bilingual_rag.database.migrate import (
    Migration,
    MigrationError,
    applied_versions,
    load_migrations,
    migrate,
)

pytestmark = pytest.mark.database


def _migration(version, name, sql):
    return Migration(version, name, sql, hashlib.sha256(sql.encode()).hexdigest())


def _tables(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    return {name for (name,) in rows}


@pytest.fixture
def conn(empty_database):
    connection = connect(empty_database)
    try:
        yield connection
    finally:
        connection.close()


def test_applies_every_shipped_migration_to_an_empty_database(conn):
    applied = migrate(conn)
    assert [m.version for m in applied] == [m.version for m in load_migrations()]
    assert {"documents", "chunks", "embedding_models", "schema_migrations"} <= _tables(conn)
    assert conn.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone()


def test_records_each_migration_with_its_checksum(conn):
    migrate(conn)
    expected = {m.version: m.checksum for m in load_migrations()}
    assert applied_versions(conn) == expected


def test_running_again_applies_nothing(conn):
    migrate(conn)
    assert migrate(conn) == []
    assert len(applied_versions(conn)) == len(load_migrations())


def test_a_database_with_no_history_reports_no_applied_versions(conn):
    assert applied_versions(conn) == {}


def test_picks_up_only_the_new_migration(conn):
    first = _migration(1, "one", "CREATE TABLE a (x int);")
    second = _migration(2, "two", "CREATE TABLE b (x int);")
    assert migrate(conn, [first]) == [first]
    assert migrate(conn, [first, second]) == [second]
    assert {"a", "b"} <= _tables(conn)


def test_editing_an_applied_migration_is_an_error(conn):
    migrate(conn, [_migration(1, "one", "CREATE TABLE a (x int);")])
    edited = _migration(1, "one", "CREATE TABLE a (x int, y int);")
    with pytest.raises(MigrationError, match="was changed after it was applied"):
        migrate(conn, [edited])


def test_a_database_ahead_of_the_code_is_an_error(conn):
    one = _migration(1, "one", "CREATE TABLE a (x int);")
    two = _migration(2, "two", "CREATE TABLE b (x int);")
    migrate(conn, [one, two])
    with pytest.raises(MigrationError, match="code is older than the database"):
        migrate(conn, [one])


def test_a_failing_migration_is_rolled_back_and_later_ones_do_not_run(conn):
    good = _migration(1, "good", "CREATE TABLE a (x int);")
    bad = _migration(2, "bad", "CREATE TABLE b (x int); SELECT 1/0;")
    never = _migration(3, "never", "CREATE TABLE c (x int);")
    with pytest.raises(MigrationError, match="0002_bad.sql failed"):
        migrate(conn, [good, bad, never])
    assert "a" in _tables(conn)
    assert not {"b", "c"} & _tables(conn)  # the half-run migration left nothing behind
    assert set(applied_versions(conn)) == {1}


def test_a_migration_can_be_fixed_and_retried_after_a_failure(conn):
    good = _migration(1, "good", "CREATE TABLE a (x int);")
    with pytest.raises(MigrationError):
        migrate(conn, [good, _migration(2, "bad", "SELECT 1/0;")])
    fixed = _migration(2, "bad", "CREATE TABLE b (x int);")
    assert migrate(conn, [good, fixed]) == [fixed]


def test_the_connection_is_usable_after_a_failure(conn):
    with pytest.raises(MigrationError):
        migrate(conn, [_migration(1, "bad", "SELECT 1/0;")])
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_refuses_a_connection_that_is_inside_a_transaction(conn):
    conn.execute("SELECT 1")  # opens a transaction
    with pytest.raises(MigrationError, match="not inside a transaction"):
        migrate(conn, [_migration(1, "one", "CREATE TABLE a (x int);")])


def test_a_migration_with_percent_signs_is_sent_unchanged(conn):
    sql = "CREATE TABLE p (x text DEFAULT '100%'); INSERT INTO p VALUES ('%s');"
    migrate(conn, [_migration(1, "percent", sql)])
    assert conn.execute("SELECT x FROM p").fetchone() == ("%s",)


def test_arabic_in_a_migration_round_trips(conn):
    sql = "CREATE TABLE t (x text); INSERT INTO t VALUES ('سياسة الإجازة السنوية');"
    migrate(conn, [_migration(1, "arabic", sql)])
    assert conn.execute("SELECT x FROM t").fetchone() == ("سياسة الإجازة السنوية",)


def test_two_runners_at_once_apply_each_migration_exactly_once(empty_database):
    errors: list[BaseException] = []
    applied_counts: list[int] = []
    barrier = threading.Barrier(4)

    def run():
        try:
            with connect(empty_database) as connection:
                barrier.wait(timeout=10)
                applied_counts.append(len(migrate(connection)))
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sum(applied_counts) == len(load_migrations())  # every migration applied once in total
    with connect(empty_database) as check:
        assert len(applied_versions(check)) == len(load_migrations())


def test_a_migration_that_violates_a_constraint_reports_the_database_error(conn):
    bad = _migration(1, "dup", "CREATE TABLE a (x int PRIMARY KEY); INSERT INTO a VALUES (1), (1);")
    with pytest.raises(MigrationError, match="duplicate key") as excinfo:
        migrate(conn, [bad])
    assert isinstance(excinfo.value.__cause__, psycopg.errors.UniqueViolation)
