"""Vector search: embed a query and rank the stored chunks by cosine similarity.

Deny by default: ``allowed_access_levels`` is required and has no default, exactly like
``bilingual_rag.database.repository.list_chunks``, whose access-level filter this shares (an
empty collection returns nothing, a bare string is refused). The filter is part of the SQL
statement, so restricted text never leaves the database (D-012).
"""

from collections.abc import Collection
from dataclasses import dataclass

import psycopg
from psycopg import sql

from bilingual_rag.database.embedding_store import EmbeddingModelError, get_embedding_model
from bilingual_rag.database.repository import StoredChunk
from bilingual_rag.database.vectors import format_vector
from bilingual_rag.embeddings.base import Embedder
from bilingual_rag.ingestion.manifest import DocumentMetadata

DEFAULT_K = 5


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One matching chunk. From ``search()``, ``score`` is cosine similarity (1 minus
    pgvector's ``<=>`` distance): 1.0 is identical direction, higher is more similar. It is a
    ranking signal, not a probability, and by itself says nothing about whether the match is
    correct. Other retrievers (``keyword_search``) document their own scale.

    ``rerank_score`` is set only after reranking (``retrieval.rerank``): the cross-encoder's
    relevance, 0 to 1. It never replaces ``score``, so code reading ``score`` keeps its meaning."""

    chunk: StoredChunk
    score: float
    rerank_score: float | None = None


def search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    allowed_access_levels: Collection[str],
    *,
    k: int = DEFAULT_K,
    language: str | None = None,
    chunk_ids: Collection[str] | None = None,
) -> list[SearchResult]:
    """The ``k`` stored chunks whose ``embedder`` vectors are most similar to ``query``.

    Only chunks of documents whose access level is in ``allowed_access_levels`` are
    considered; an empty collection returns nothing without embedding the query at all.
    ``chunk_ids`` narrows the search to those chunks (hybrid search uses it to score keyword
    matches); the access filter still applies to them.
    ``query`` is embedded exactly as given by ``embedder.embed_query`` — its own validation
    (a non-string or blank query) applies here too.

    Raises:
        TypeError: ``allowed_access_levels`` is a single string (it would be read as a
            collection of letters and silently match nothing, or the wrong thing).
        ValueError: ``k`` is not positive.
        EmbeddingModelError: no embeddings are stored yet for ``embedder.key`` (run
            ``scripts/ingest_documents.py`` first).
    """
    if isinstance(allowed_access_levels, str):
        raise TypeError("allowed_access_levels must be a collection of levels, not one string")
    if k < 1:
        raise ValueError(f"k must be positive, got {k}")
    levels = sorted(set(allowed_access_levels))
    if not levels:
        return []

    model = get_embedding_model(conn, embedder.key)
    if model is None:
        raise EmbeddingModelError(
            f"no embeddings are stored for {embedder.key!r} yet; run "
            "scripts/ingest_documents.py first"
        )
    vector = format_vector(embedder.embed_query(query))

    filters = " AND d.language = %s" if language is not None else ""
    if chunk_ids is not None:
        filters += " AND c.id = ANY(%s)"
    query_sql = sql.SQL(
        "SELECT c.id, c.page, c.chunk_index, c.text, c.start_offset, c.end_offset,"
        " d.id, d.path, d.title, d.department, d.language, d.format, d.access_level,"
        " d.topic, d.pair, d.digits,"
        " 1 - (e.embedding <=> %s::vector) AS score"
        " FROM {table} e"
        " JOIN chunks c ON c.id = e.chunk_id"
        " JOIN documents d ON d.id = c.document_id"
        " WHERE d.access_level = ANY(%s)" + filters + " "
        "ORDER BY e.embedding <=> %s::vector"
        " LIMIT %s"
    ).format(table=sql.Identifier(model.table_name))

    params: list[object] = [vector, levels]
    if language is not None:
        params.append(language)
    if chunk_ids is not None:
        params.append(sorted(set(chunk_ids)))
    params += [vector, k]

    return [
        SearchResult(
            chunk=StoredChunk(
                id=row[0],
                page=row[1],
                index=row[2],
                text=row[3],
                start=row[4],
                end=row[5],
                document=DocumentMetadata(*row[6:16]),
            ),
            score=row[16],
        )
        for row in conn.execute(query_sql, params).fetchall()
    ]
