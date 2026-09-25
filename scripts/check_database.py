"""Check that PostgreSQL is reachable and that pgvector works. Changes nothing.

Reads .env at the repository root (real environment variables win). Exit code 0 means the
database is ready, 1 that it is unreachable or unhealthy, 2 that the configuration is wrong.

Usage (from the repository root, with the project environment active):

    docker compose up -d --wait db
    python scripts/check_database.py
"""

import sys
from pathlib import Path

import psycopg

from bilingual_rag.config import ConfigError, load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.database.health import check_health

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def main() -> int:
    try:
        settings = load_database_settings(ENV_FILE)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    print(f"connecting to {settings.host}:{settings.port} database={settings.dbname}")
    try:
        with connect(settings) as conn:
            report = check_health(conn)
    except psycopg.OperationalError as exc:
        print(f"cannot connect: {str(exc).strip()}", file=sys.stderr)
        print("is it running? try: docker compose up -d --wait db", file=sys.stderr)
        return 1

    print(f"server version   {report.server_version}")
    print(f"server encoding  {report.server_encoding}")
    print(
        f"pgvector         available={report.vector_available}, enabled={report.vector_installed}"
    )
    if report.vector_error:
        print(f"pgvector check   FAILED: {report.vector_error}")
    else:
        print(f"pgvector check   cosine distance of orthogonal vectors = {report.cosine_distance}")
    print("OK" if report.ok else "NOT OK")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
