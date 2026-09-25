"""bge-m3 embedding stored chunks and answering a handful of real queries over pgvector.

Qualitative evidence for D-014 that the pipeline works end to end with a real model, on a
small curated subset of the corpus (kept small so this stays reasonably fast). This is NOT a
benchmark: Recall@k and MRR over the full evaluation set come from the Week 3 experiment.
"""

from pathlib import Path

import pytest

from bilingual_rag.database.connection import connect
from bilingual_rag.database.embedding_store import chunk_ids_without_embeddings
from bilingual_rag.database.repository import store_document
from bilingual_rag.database.vectors import format_vector
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.embeddings.sentence_transformer import bge_m3
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS, load_manifest
from bilingual_rag.ingestion.pipeline import ingest_directory

pytestmark = [pytest.mark.slow, pytest.mark.database]

DATA = Path(__file__).resolve().parents[2] / "data"

# A small, deliberately mixed subset: an English/Arabic pair on the same topic, another on a
# different topic, an Arabic-only topic (tests true cross-language retrieval, not just a
# translated pair), and a restricted document (tests the access filter still applies).
SUBSET_IDS = {
    "hr-annual-leave-policy-en",
    "hr-annual-leave-policy-ar",
    "it-vpn-troubleshooting-guide-en",
    "it-vpn-troubleshooting-guide-ar",
    "hr-working-hours-and-overtime-ar",  # Arabic-only: overtime rates exist nowhere in English
    "hr-compensation-bands-en",  # access_level "hr"
}


@pytest.fixture(scope="module")
def embedded(migrated_database):
    """Its own connection, kept in one uncommitted transaction and rolled back at the end.

    ``migrated_database`` is session-scoped and shared with every other integration test
    file, so nothing here may be committed: a permanent write would make this file's subset
    look pre-existing to, for example, test_corpus_in_database.py's "every document is
    inserted the first time" assertion.
    """
    conn = connect(migrated_database)
    try:
        documents = {d.id: d for d in load_manifest(DATA / "manifest.json") if d.id in SUBSET_IDS}
        assert set(documents) == SUBSET_IDS, sorted(SUBSET_IDS - set(documents))
        chunks_by_path: dict[str, list] = {}
        for chunk in ingest_directory(DATA / "synthetic").chunks:
            chunks_by_path.setdefault(chunk.filename, []).append(chunk)
        for document in documents.values():
            store_document(conn, document, chunks_by_path[document.path])

        embedder = bge_m3()
        embed_missing(conn, embedder)
        yield conn, embedder
    finally:
        conn.rollback()
        conn.close()


def _search(conn, embedder, query, levels, k=3):
    rows = conn.execute(
        "SELECT d.id FROM embeddings_bge_m3 e"
        " JOIN chunks c ON c.id = e.chunk_id JOIN documents d ON d.id = c.document_id"
        " WHERE d.access_level = ANY(%s)"
        " ORDER BY e.embedding <=> %s::vector, c.id LIMIT %s",
        (sorted(levels), format_vector(embedder.embed_query(query)), k),
    ).fetchall()
    return [row[0] for row in rows]


def test_every_chunk_in_the_subset_is_embedded(embedded):
    conn, embedder = embedded
    assert chunk_ids_without_embeddings(conn, embedder.key) == []


def test_an_english_query_finds_the_english_policy(embedded):
    conn, embedder = embedded
    top = _search(conn, embedder, "how many days of annual leave do employees get", ACCESS_LEVELS)
    assert top[0] == "hr-annual-leave-policy-en"


def test_an_arabic_query_finds_the_arabic_policy(embedded):
    conn, embedder = embedded
    top = _search(conn, embedder, "كم عدد أيام الإجازة السنوية", ACCESS_LEVELS)
    assert top[0] == "hr-annual-leave-policy-ar"


def test_an_arabic_query_finds_the_english_only_document_cross_lingually(embedded):
    """The property the hashing stand-in could not offer (D-013): meaning, not shared words."""
    conn, embedder = embedded
    top = _search(conn, embedder, "دليل حل مشاكل الشبكة الافتراضية VPN", ACCESS_LEVELS, k=2)
    assert "it-vpn-troubleshooting-guide-en" in top


def test_an_english_query_finds_the_arabic_only_document_cross_lingually(embedded):
    conn, embedder = embedded
    top = _search(conn, embedder, "overtime pay rates for extra hours worked", ACCESS_LEVELS, k=2)
    assert "hr-working-hours-and-overtime-ar" in top


def test_the_restricted_document_is_hidden_by_the_access_filter(embedded):
    conn, embedder = embedded
    everyone = _search(conn, embedder, "compensation salary bands", ACCESS_LEVELS, k=1)
    assert everyone == ["hr-compensation-bands-en"]
    employee_only = _search(
        conn, embedder, "compensation salary bands", {"public", "employee"}, k=6
    )
    assert "hr-compensation-bands-en" not in employee_only
