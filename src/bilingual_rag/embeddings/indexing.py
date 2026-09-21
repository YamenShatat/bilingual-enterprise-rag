"""Embed the chunks in the database that have no vector yet for a model."""

from collections.abc import Callable

import psycopg

from bilingual_rag.database.embedding_store import (
    chunk_ids_without_embeddings,
    ensure_embedding_model,
    store_embeddings,
)
from bilingual_rag.database.repository import list_chunks
from bilingual_rag.embeddings.base import Embedder, validate_vectors
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS

DEFAULT_BATCH_SIZE = 32


def embed_missing(
    conn: psycopg.Connection,
    embedder: Embedder,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Callable[[int, int], None] | None = None,
) -> int:
    """Embed every chunk that has no vector for this embedder's model, in batches.

    Registers the model if needed, so the first call creates its table. Only missing chunks
    are embedded, so it is safe to call again after adding documents, and after a failure it
    resumes where it stopped. Chunk text is embedded exactly as stored.

    Nothing is committed here. ``on_batch(done, total)`` runs after each stored batch: pass a
    function that commits to keep progress across a crash, or one that prints progress.

    This reads chunks of every access level on purpose. Embedding is a trusted ingestion step
    that must cover restricted documents too; keeping their vectors away from unauthorized
    callers is the search query's job (it joins each vector to its document's access level).

    Returns:
        How many chunks were embedded.

    Raises:
        ValueError: ``batch_size`` is not positive.
        EmbeddingError: the embedder returned the wrong number or size of vectors.
        EmbeddingModelError: the embedder's key is registered for a different model.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be positive, got {batch_size}")
    model = ensure_embedding_model(conn, embedder.key, embedder.model_name, embedder.dimension)
    missing = set(chunk_ids_without_embeddings(conn, model.key))
    pending = [chunk for chunk in list_chunks(conn, set(ACCESS_LEVELS)) if chunk.id in missing]

    done = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = embedder.embed_documents([chunk.text for chunk in batch])
        validate_vectors(vectors, expected_count=len(batch), dimension=model.dimension)
        store_embeddings(conn, model.key, {c.id: v for c, v in zip(batch, vectors, strict=True)})
        done += len(batch)
        if on_batch is not None:
            on_batch(done, len(pending))
    return done
