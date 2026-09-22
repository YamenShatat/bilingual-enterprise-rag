"""search() itself, with the real bge-m3 model, over a small curated subset of the corpus.

Qualitative evidence, not a benchmark (see D-014). Complements test_real_embedder_search.py,
which checked the same property with a hand-written SQL query; this checks the actual shipped
`search()` function callers will use.
"""

from pathlib import Path

import pytest

from bilingual_rag.database.connection import connect
from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.embeddings.sentence_transformer import bge_m3
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS, load_manifest
from bilingual_rag.ingestion.pipeline import ingest_directory
from bilingual_rag.retrieval.search import search

pytestmark = [pytest.mark.slow, pytest.mark.database]

DATA = Path(__file__).resolve().parents[2] / "data"
EVERYONE = set(ACCESS_LEVELS)

SUBSET_IDS = {
    "hr-annual-leave-policy-en",
    "hr-annual-leave-policy-ar",
    "it-vpn-troubleshooting-guide-en",
    "it-vpn-troubleshooting-guide-ar",
    "hr-working-hours-and-overtime-ar",
    "hr-compensation-bands-en",  # access_level "hr"
}


@pytest.fixture(scope="module")
def embedded(migrated_database):
    """Its own connection, kept in one uncommitted transaction and rolled back at the end
    (see D-014: the shared session database must never see a permanent write here)."""
    conn = connect(migrated_database)
    try:
        documents = {d.id: d for d in load_manifest(DATA / "manifest.json") if d.id in SUBSET_IDS}
        assert set(documents) == SUBSET_IDS
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


def test_an_english_query_finds_the_english_policy(embedded):
    conn, embedder = embedded
    (top,) = search(conn, embedder, "how many days of annual leave do employees get", EVERYONE, k=1)
    assert top.chunk.document.id == "hr-annual-leave-policy-en"


def test_an_arabic_query_finds_the_arabic_policy(embedded):
    conn, embedder = embedded
    (top,) = search(conn, embedder, "كم عدد أيام الإجازة السنوية", EVERYONE, k=1)
    assert top.chunk.document.id == "hr-annual-leave-policy-ar"


def test_cross_lingual_retrieval_both_directions(embedded):
    conn, embedder = embedded
    ar_to_en = search(conn, embedder, "دليل حل مشاكل الشبكة الافتراضية VPN", EVERYONE, k=2)
    assert "it-vpn-troubleshooting-guide-en" in {r.chunk.document.id for r in ar_to_en}

    en_to_ar = search(conn, embedder, "overtime pay rates for extra hours worked", EVERYONE, k=2)
    assert "hr-working-hours-and-overtime-ar" in {r.chunk.document.id for r in en_to_ar}


def test_the_access_filter_hides_the_restricted_document(embedded):
    conn, embedder = embedded
    everyone = search(conn, embedder, "compensation salary bands", EVERYONE, k=1)
    assert everyone[0].chunk.document.id == "hr-compensation-bands-en"

    employee_only = search(conn, embedder, "compensation salary bands", {"public", "employee"}, k=6)
    assert "hr-compensation-bands-en" not in {r.chunk.document.id for r in employee_only}


def test_results_carry_a_similarity_score_that_ranks_correctly(embedded):
    conn, embedder = embedded
    results = search(conn, embedder, "annual leave policy", EVERYONE, k=3)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert all(-1.0 <= s <= 1.0 for s in scores)
