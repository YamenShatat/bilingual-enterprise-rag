"""Diagnose a PostgreSQL connection: server, encoding, and whether pgvector works."""

from dataclasses import dataclass

import psycopg

# Cosine distance between two orthogonal vectors is exactly 1. Other operators disagree:
# L2 gives 1.414..., negative inner product gives -0, L1 gives 2.
_SMOKE_QUERY = "SELECT '[1,0,0]'::vector <=> '[0,1,0]'::vector"
_SMOKE_EXPECTED = 1.0


@dataclass(frozen=True, slots=True)
class HealthReport:
    """What ``check_health`` found. ``vector_error`` explains a failed pgvector check."""

    server_version: str
    server_encoding: str
    vector_available: str | None  # version shipped with the server, None if not installed on it
    vector_installed: str | None  # version enabled in this database, None if not enabled yet
    cosine_distance: float | None
    vector_error: str | None

    @property
    def ok(self) -> bool:
        """UTF-8 database and a pgvector cosine distance that came out right."""
        return (
            self.server_encoding.upper() == "UTF8"
            and self.cosine_distance is not None
            and abs(self.cosine_distance - _SMOKE_EXPECTED) < 1e-9
        )


def check_health(conn: psycopg.Connection) -> HealthReport:
    """Inspect the server and run a pgvector distance query.

    The pgvector check enables the extension inside a savepoint and rolls it back, so a
    database where it is not enabled yet is left exactly as it was.
    """
    with conn.cursor() as cursor:
        cursor.execute("SHOW server_version")
        (server_version,) = cursor.fetchone()
        cursor.execute("SHOW server_encoding")
        (server_encoding,) = cursor.fetchone()
        cursor.execute(
            "SELECT default_version, installed_version FROM pg_available_extensions "
            "WHERE name = 'vector'"
        )
        row = cursor.fetchone()
    available, installed = row if row is not None else (None, None)

    distance: float | None = None
    error: str | None = None
    if available is None:
        error = "the vector extension is not installed on this PostgreSQL server"
    else:
        try:
            with conn.transaction():
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                distance = conn.execute(_SMOKE_QUERY).fetchone()[0]
                raise psycopg.Rollback
        except psycopg.Error as exc:
            error = str(exc).strip()
    return HealthReport(
        server_version=server_version,
        server_encoding=server_encoding,
        vector_available=available,
        vector_installed=installed,
        cosine_distance=distance,
        vector_error=error,
    )
