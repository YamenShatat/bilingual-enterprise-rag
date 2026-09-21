"""Apply pending SQL migrations to the database. Safe to run repeatedly.

Reads .env at the repository root (real environment variables win). Exit code 0 means the
schema is up to date, 1 that a migration failed or the database is unreachable, 2 that the
configuration or the migration files are wrong.

Usage (from the repository root, with the project environment active):

    docker compose up -d --wait db
    python scripts/migrate_database.py
"""

import sys
from pathlib import Path

import psycopg

from bilingual_rag.config import ConfigError, load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.database.migrate import MigrationError, load_migrations, migrate

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def main() -> int:
    try:
        settings = load_database_settings(ENV_FILE)
        migrations = load_migrations()
    except (ConfigError, MigrationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"connecting to {settings.host}:{settings.port} database={settings.dbname}")
    try:
        with connect(settings) as conn:
            applied = migrate(conn, migrations)
    except psycopg.OperationalError as exc:
        print(f"cannot connect: {str(exc).strip()}", file=sys.stderr)
        print("is it running? try: docker compose up -d --wait db", file=sys.stderr)
        return 1
    except MigrationError as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        return 1

    for migration in applied:
        print(f"applied  {migration.version:04d}_{migration.name}.sql")
    if not applied:
        print("nothing to apply")
    print(f"schema is at version {migrations[-1].version:04d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
