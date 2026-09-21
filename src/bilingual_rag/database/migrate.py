"""Versioned SQL migrations: a small forward-only runner with no dependencies.

Migrations are files named ``NNNN_description.sql`` (four digits, lowercase words joined by
underscores) in ``bilingual_rag/database/migrations``. Versions must run 1, 2, 3 without gaps.
Each file runs in its own transaction together with the row that records it in
``schema_migrations``, so a failure leaves nothing half-applied. The recorded checksum makes
editing an applied migration an error: change the schema with a new file instead.

There are no down-migrations, and a migration cannot contain statements PostgreSQL refuses to
run inside a transaction (such as ``CREATE INDEX CONCURRENTLY``).
"""

import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable

import psycopg
from psycopg.pq import TransactionStatus

_FILENAME = re.compile(r"(\d{4})_([a-z0-9]+(?:_[a-z0-9]+)*)\.sql")
# Serializes concurrent runners. Any constant would do ("bragmigr" as ASCII).
_LOCK_KEY = 0x6272_6167_6D69_6772


class MigrationError(Exception):
    """A migration is invalid, was changed after being applied, or failed to run."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str  # sha256 of the SQL with line endings normalized to LF


def default_migrations_directory() -> Traversable:
    """The migrations that ship inside the package."""
    return files("bilingual_rag.database") / "migrations"


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def load_migrations(directory: Traversable | None = None) -> list[Migration]:
    """Read and validate the migration files, ordered by version.

    Line endings are normalized before hashing so a checkout with CRLF does not look like a
    changed migration. Files that are not ``.sql`` are ignored; a ``.sql`` file with a wrong
    name is an error, so a typo cannot silently skip a migration.

    Raises:
        MigrationError: a bad file name, a duplicate or missing version, invalid UTF-8, or an
            empty file.
    """
    directory = default_migrations_directory() if directory is None else directory
    found: dict[int, Migration] = {}
    for entry in directory.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _FILENAME.fullmatch(entry.name)
        if match is None:
            raise MigrationError(f"{entry.name}: expected a name like 0001_create_tables.sql")
        version = int(match.group(1))
        if version == 0:
            raise MigrationError(f"{entry.name}: versions start at 0001")
        try:
            sql = entry.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
        except UnicodeDecodeError as exc:
            raise MigrationError(f"{entry.name} is not valid UTF-8") from exc
        if not sql.strip():
            raise MigrationError(f"{entry.name} is empty")
        if version in found:
            raise MigrationError(f"version {version:04d} appears twice ({entry.name})")
        found[version] = Migration(version, match.group(2), sql, _checksum(sql))

    migrations = [found[version] for version in sorted(found)]
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise MigrationError(
                f"version {expected:04d} is missing (found {migration.version:04d})"
            )
    return migrations


def _ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version integer PRIMARY KEY,"
        " name text NOT NULL,"
        " checksum text NOT NULL,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )


def applied_versions(conn: psycopg.Connection) -> dict[int, str]:
    """Map each applied version to its recorded checksum ({} if none was ever applied)."""
    exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
    if not exists:
        return {}
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return dict(rows)


def _verify(applied: dict[int, str], migrations: list[Migration]) -> None:
    by_version = {migration.version: migration for migration in migrations}
    for version, checksum in sorted(applied.items()):
        migration = by_version.get(version)
        if migration is None:
            raise MigrationError(
                f"the database has migration {version:04d}, which this code does not know: "
                "the code is older than the database"
            )
        if migration.checksum != checksum:
            raise MigrationError(
                f"migration {version:04d}_{migration.name}.sql was changed after it was applied; "
                "restore it and put the change in a new migration"
            )


def migrate(conn: psycopg.Connection, migrations: list[Migration] | None = None) -> list[Migration]:
    """Apply every pending migration in order and return the ones applied by this call.

    Safe to call repeatedly and from several processes at once (an advisory lock makes them
    take turns). The connection must not be inside a transaction.

    Raises:
        MigrationError: a recorded migration was changed or is unknown, or a migration failed.
            A failed migration is rolled back; earlier ones stay applied.
    """
    migrations = load_migrations() if migrations is None else migrations
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise MigrationError("migrate() needs a connection that is not inside a transaction")

    try:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        _ensure_table(conn)
        _verify(applied_versions(conn), migrations)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise

    applied_now: list[Migration] = []
    for migration in migrations:
        try:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            if migration.version in applied_versions(conn):
                conn.rollback()  # another process applied it while we waited
                continue
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            raise MigrationError(
                f"migration {migration.version:04d}_{migration.name}.sql failed: {exc}"
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        applied_now.append(migration)
    return applied_now
