"""search() into build_context() on a real database: the evidence the LLM would see."""

import pytest

from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.generation.context import build_context
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from bilingual_rag.retrieval.search import search
from support.samples import make_chunks, make_metadata

pytestmark = pytest.mark.database

SECRET = "executive bonus pool salary figures leave"


@pytest.fixture
def conn(store_connection):
    # One document per access level, all matching the query, each carrying a marker word.
    for level in ACCESS_LEVELS:
        metadata = make_metadata(id=f"doc-{level}", path=f"x/{level}.md", access_level=level)
        store_document(
            store_connection, metadata, make_chunks(metadata.path, [f"{SECRET} {level}"])
        )
    embed_missing(store_connection, HashingEmbedder(64))
    return store_connection


@pytest.mark.parametrize("level", ACCESS_LEVELS)
def test_the_evidence_holds_only_what_the_caller_may_read(conn, level):
    results = search(conn, HashingEmbedder(64), SECRET, {level}, k=10)
    context = build_context(results)
    assert [s.document_id for s in context.sources] == [f"doc-{level}"]
    for other in set(ACCESS_LEVELS) - {level}:
        assert f"doc-{other}" not in context.text
        assert f"{SECRET} {other}" not in context.text


def test_sources_follow_search_rank_and_carry_the_real_chunk_ids(conn):
    results = search(conn, HashingEmbedder(64), SECRET, set(ACCESS_LEVELS), k=10)
    context = build_context(results)
    assert [s.chunk_id for s in context.sources] == [r.chunk.id for r in results]
    assert [s.score for s in context.sources] == [r.score for r in results]
    assert len(context.sources) == len(ACCESS_LEVELS)
