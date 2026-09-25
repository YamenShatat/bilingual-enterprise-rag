"""Store embeddings, one table per model.

A pgvector column has a fixed dimension and Week 3 compares models with different ones, so
each model gets its own table ``embeddings_<key>`` with one vector per chunk, registered in
``embedding_models``. Deleting or re-chunking a document deletes its embeddings with it
(``ON DELETE CASCADE``), so a stored vector always belongs to the text that produced it.

Like the repository, nothing here commits. The tables are created by code, not by a migration,
because the set of models is not known in advance; that needs a database role allowed to run
``CREATE TABLE``, which matters if the application is ever given a narrower role.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import psycopg
from psycopg import sql

from bilingual_rag.database.vectors import format_vector, parse_vector

_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
MAX_DIMENSION = 16000  # pgvector's limit for the vector type


class EmbeddingModelError(Exception):
    """An embedding model is unknown, or its key was reused with different settings."""


@dataclass(frozen=True, slots=True)
class EmbeddingModel:
    key: str  # short identifier used in the table name, for example "bge_m3"
    model_name: str  # the full model name, for example "BAAI/bge-m3"
    dimension: int
    table_name: str


def _table(model: EmbeddingModel) -> sql.Identifier:
    return sql.Identifier(model.table_name)


def get_embedding_model(conn: psycopg.Connection, key: str) -> EmbeddingModel | None:
    """The registered model with this key, or None."""
    row = conn.execute(
        "SELECT key, model_name, dimension, table_name FROM embedding_models WHERE key = %s",
        (key,),
    ).fetchone()
    return None if row is None else EmbeddingModel(*row)


def _require(conn: psycopg.Connection, key: str) -> EmbeddingModel:
    model = get_embedding_model(conn, key)
    if model is None:
        raise EmbeddingModelError(f"no embedding model is registered under the key {key!r}")
    return model


def ensure_embedding_model(
    conn: psycopg.Connection, key: str, model_name: str, dimension: int
) -> EmbeddingModel:
    """Register a model and create its table if needed. Safe to call repeatedly.

    Raises:
        ValueError: the key, name or dimension is invalid.
        EmbeddingModelError: the key is already registered for a different model name or
            dimension, which would mix incompatible vectors.
    """
    if not _KEY.fullmatch(key):
        raise ValueError(
            f"key must be 1-40 lowercase letters, digits or underscores, starting with a letter: "
            f"{key!r}"
        )
    if not model_name.strip():
        raise ValueError("model_name must not be empty")
    if isinstance(dimension, bool) or not isinstance(dimension, int):
        raise ValueError(f"dimension must be an integer, got {dimension!r}")
    if not 1 <= dimension <= MAX_DIMENSION:
        raise ValueError(f"dimension must be between 1 and {MAX_DIMENSION}, got {dimension}")

    wanted = EmbeddingModel(key, model_name, dimension, f"embeddings_{key}")
    conn.execute(
        "INSERT INTO embedding_models (key, model_name, dimension, table_name)"
        " VALUES (%s, %s, %s, %s) ON CONFLICT (key) DO NOTHING",
        (wanted.key, wanted.model_name, wanted.dimension, wanted.table_name),
    )
    registered = _require(conn, key)
    if registered != wanted:
        raise EmbeddingModelError(
            f"key {key!r} is already registered for {registered.model_name!r} with "
            f"{registered.dimension} dimensions, not {model_name!r} with {dimension}"
        )
    conn.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {table} ("
            " chunk_id text PRIMARY KEY REFERENCES chunks (id) ON DELETE CASCADE,"
            " embedding vector({dimension}) NOT NULL,"
            " created_at timestamptz NOT NULL DEFAULT now())"
        ).format(table=_table(registered), dimension=sql.Literal(dimension))
    )
    return registered


def store_embeddings(
    conn: psycopg.Connection, key: str, embeddings: Mapping[str, Sequence[float]]
) -> int:
    """Insert or replace the embedding of each chunk id. Returns how many were written.

    Raises:
        EmbeddingModelError: the key is not registered.
        ValueError: a vector has the wrong length or holds a NaN or infinity.
        psycopg.errors.ForeignKeyViolation: a chunk id does not exist.
    """
    model = _require(conn, key)
    rows = []
    for chunk_id, values in embeddings.items():
        if len(values) != model.dimension:
            raise ValueError(
                f"{chunk_id}: expected {model.dimension} values for {key!r}, got {len(values)}"
            )
        rows.append((chunk_id, format_vector(values)))
    query = sql.SQL(
        "INSERT INTO {table} (chunk_id, embedding) VALUES (%s, %s::vector)"
        " ON CONFLICT (chunk_id) DO UPDATE"
        " SET embedding = EXCLUDED.embedding, created_at = now()"
    ).format(table=_table(model))
    with conn.cursor() as cursor:
        cursor.executemany(query, rows)
    return len(rows)


def get_embedding(conn: psycopg.Connection, key: str, chunk_id: str) -> list[float] | None:
    """The stored vector of one chunk (single precision), or None if it has none."""
    model = _require(conn, key)
    row = conn.execute(
        sql.SQL("SELECT embedding::text FROM {table} WHERE chunk_id = %s").format(
            table=_table(model)
        ),
        (chunk_id,),
    ).fetchone()
    return None if row is None else parse_vector(row[0])


def chunk_ids_without_embeddings(conn: psycopg.Connection, key: str) -> list[str]:
    """Ids of chunks that have no embedding for this model, so only new text is embedded."""
    model = _require(conn, key)
    rows = conn.execute(
        sql.SQL(
            "SELECT c.id FROM chunks c LEFT JOIN {table} e ON e.chunk_id = c.id"
            ' WHERE e.chunk_id IS NULL ORDER BY c.id COLLATE "C"'
        ).format(table=_table(model))
    ).fetchall()
    return [row[0] for row in rows]
