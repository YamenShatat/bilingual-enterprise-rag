import pytest

from bilingual_rag.database.connection import connect
from bilingual_rag.database.health import check_health
from support.arabic import ARABIC_TRUTH

pytestmark = pytest.mark.database


def _extension_versions(conn):
    return conn.execute("SELECT extname, extversion FROM pg_extension ORDER BY extname").fetchall()


def test_connection_uses_utf8_on_both_sides(db_connection):
    assert db_connection.info.encoding == "utf-8"
    assert db_connection.execute("SHOW server_encoding").fetchone() == ("UTF8",)
    assert db_connection.execute("SHOW client_encoding").fetchone() == ("UTF8",)


def test_utf8_is_forced_even_if_the_environment_asks_for_another_encoding(
    database_settings, monkeypatch
):
    monkeypatch.setenv("PGCLIENTENCODING", "LATIN1")
    conn = connect(database_settings)
    try:
        assert conn.info.encoding == "utf-8"
        (returned,) = conn.execute("SELECT %s::text", (ARABIC_TRUTH[1],)).fetchone()
        assert returned == ARABIC_TRUTH[1]
    finally:
        conn.close()


@pytest.mark.parametrize("text", ARABIC_TRUTH)
def test_arabic_text_round_trips_exactly(db_connection, text):
    (returned,) = db_connection.execute("SELECT %s::text", (text,)).fetchone()
    assert returned == text


def test_arabic_text_round_trips_through_a_table(db_connection):
    db_connection.execute("CREATE TEMP TABLE round_trip (id int, body text)")
    with db_connection.cursor() as cursor:
        cursor.executemany("INSERT INTO round_trip VALUES (%s, %s)", list(enumerate(ARABIC_TRUTH)))
    rows = db_connection.execute("SELECT body FROM round_trip ORDER BY id").fetchall()
    assert [body for (body,) in rows] == ARABIC_TRUTH


def test_the_health_check_passes_and_reports_pgvector(db_connection):
    report = check_health(db_connection)
    assert report.ok, report
    assert report.vector_error is None
    assert report.cosine_distance == pytest.approx(1.0)
    assert report.server_encoding == "UTF8"
    assert report.vector_available is not None


def test_the_health_check_leaves_the_database_unchanged(db_connection):
    before = _extension_versions(db_connection)
    check_health(db_connection)
    assert _extension_versions(db_connection) == before


def test_pgvector_orders_by_cosine_distance(db_connection):
    db_connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    db_connection.execute("CREATE TEMP TABLE v (name text, embedding vector(3))")
    db_connection.execute(
        "INSERT INTO v VALUES ('x', '[1,0,0]'), ('near_x', '[1,0.1,0]'), ('y', '[0,1,0]')"
    )
    # The query is 4.6 degrees from x and 1.1 degrees from near_x, so the order is unambiguous.
    rows = db_connection.execute(
        "SELECT name FROM v ORDER BY embedding <=> '[1,0.08,0]' LIMIT 3"
    ).fetchall()
    assert [name for (name,) in rows] == ["near_x", "x", "y"]
