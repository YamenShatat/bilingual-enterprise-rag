"""Open PostgreSQL connections from validated settings."""

import psycopg

from bilingual_rag.config import DatabaseSettings

DEFAULT_CONNECT_TIMEOUT = 5  # seconds


def connect(
    settings: DatabaseSettings,
    *,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    autocommit: bool = False,
) -> psycopg.Connection:
    """Open a connection whose client encoding is always UTF-8.

    The encoding is set explicitly so Arabic text never depends on the Windows locale.
    Unless ``autocommit`` is set, transactions are the psycopg default: nothing is saved
    until ``commit()``. Autocommit exists for statements PostgreSQL refuses to run inside a
    transaction, such as ``CREATE DATABASE``.

    Raises:
        psycopg.OperationalError: the server is unreachable or rejects the credentials.
    """
    return psycopg.connect(
        **settings.connect_kwargs(),
        client_encoding="UTF8",
        connect_timeout=connect_timeout,
        autocommit=autocommit,
    )
