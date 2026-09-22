"""search() against a real database: ranking, access control, and unregistered models."""

from pathlib import Path

import pytest

from bilingual_rag.database.embedding_store import EmbeddingModelError
from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS, load_manifest
from bilingual_rag.ingestion.pipeline import ingest_directory
from bilingual_rag.retrieval.search import search
from support.samples import make_chunks, make_metadata

pytestmark = pytest.mark.database

DATA = Path(__file__).resolve().parents[2] / "data"
EVERYONE = set(ACCESS_LEVELS)


@pytest.fixture
def fake():
    return HashingEmbedder(64)


class TestUnregisteredModel:
    def test_a_model_with_no_embeddings_yet_is_a_clear_error(self, store_connection, fake):
        with pytest.raises(EmbeddingModelError, match="ingest_documents"):
            search(store_connection, fake, "annual leave", EVERYONE)


class TestRankingWithSyntheticData:
    @pytest.fixture(autouse=True)
    def _seeded(self, store_connection, fake):
        metadata = make_metadata()
        chunks = make_chunks(metadata.path, ["annual leave policy details", "vpn setup guide"])
        store_document(store_connection, metadata, chunks)
        embed_missing(store_connection, fake)
        self.conn = store_connection
        self.fake = fake

    def test_the_closer_chunk_ranks_first(self):
        results = search(self.conn, self.fake, "leave policy", EVERYONE)
        assert results[0].chunk.text == "annual leave policy details"

    def test_scores_are_sorted_descending(self):
        results = search(self.conn, self.fake, "leave policy", EVERYONE, k=2)
        assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)

    def test_an_identical_query_scores_close_to_one(self):
        (top,) = search(self.conn, self.fake, "annual leave policy details", EVERYONE, k=1)
        assert top.score == pytest.approx(1.0, abs=1e-6)

    def test_k_limits_the_result_count(self):
        assert len(search(self.conn, self.fake, "leave policy", EVERYONE, k=1)) == 1
        assert len(search(self.conn, self.fake, "leave policy", EVERYONE, k=100)) == 2

    def test_the_result_carries_full_chunk_and_document_metadata(self):
        (top,) = search(self.conn, self.fake, "annual leave policy details", EVERYONE, k=1)
        assert top.chunk.document.id == "hr-leave-en"
        assert top.chunk.page == 1
        assert top.chunk.index == 0

    def test_a_blank_query_is_rejected_by_the_embedder_not_silently_returned_empty(self):
        with pytest.raises(ValueError, match="blank"):
            search(self.conn, self.fake, "", EVERYONE)

    def test_a_language_filter_that_matches_nothing_returns_nothing(self):
        assert search(self.conn, self.fake, "leave policy", EVERYONE, language="ar") == []

    def test_a_language_filter_that_matches_returns_results(self):
        results = search(self.conn, self.fake, "leave policy", EVERYONE, language="en")
        assert results


class TestAccessControl:
    """The core promise of D-012: unauthorized chunks never leave the database."""

    @pytest.fixture(autouse=True)
    def _seeded(self, store_connection, fake):
        for level in ACCESS_LEVELS:
            metadata = make_metadata(id=f"doc-{level}", path=f"{level}.md", access_level=level)
            store_document(
                store_connection, metadata, make_chunks(metadata.path, [f"secret {level} content"])
            )
        embed_missing(store_connection, fake)
        self.conn = store_connection
        self.fake = fake

    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_a_single_level_only_returns_that_levels_documents(self, level):
        results = search(self.conn, self.fake, "secret content", {level}, k=10)
        assert {r.chunk.document.access_level for r in results} == {level}

    def test_every_level_returns_every_document(self):
        assert len(search(self.conn, self.fake, "secret content", EVERYONE, k=10)) == len(
            ACCESS_LEVELS
        )

    def test_restricted_text_never_appears_in_a_lower_privilege_result(self):
        results = search(self.conn, self.fake, "secret content", {"public", "employee"}, k=10)
        blob = repr(results)
        for hidden in ("hr", "management", "engineering"):
            assert f"secret {hidden} content" not in blob
            assert f"{hidden}.md" not in blob

    def test_an_unknown_level_matches_nothing(self):
        assert search(self.conn, self.fake, "secret content", {"admin", "Employee", ""}, k=10) == []

    def test_a_sql_looking_string_in_the_levels_is_only_data(self):
        attack = {"employee' OR '1'='1", "x') OR true --"}
        assert search(self.conn, self.fake, "secret content", attack, k=10) == []
        # the table is intact: a normal query still returns everything
        assert len(search(self.conn, self.fake, "secret content", EVERYONE, k=10)) == len(
            ACCESS_LEVELS
        )


class TestOverTheRealCorpus:
    """Lexical stand-in over the real 32-document corpus (see D-013: not a quality claim).

    Uses a wider dimension than the other classes in this file: 64 has enough hash
    collisions over 46 real chunks that the ranking below is no longer clean, which is a
    property of the stand-in's dimension, not of search() (test_indexing.py's own real-corpus
    tests already established 256 as ambiguity-free for this exact query).
    """

    @pytest.fixture(autouse=True)
    def _seeded(self, store_connection):
        documents = load_manifest(DATA / "manifest.json")
        by_path: dict[str, list] = {}
        for chunk in ingest_directory(DATA / "synthetic").chunks:
            by_path.setdefault(chunk.filename, []).append(chunk)
        for document in documents:
            store_document(store_connection, document, by_path[document.path])
        self.fake = HashingEmbedder(256)
        embed_missing(store_connection, self.fake)
        self.conn = store_connection

    def test_an_english_query_finds_the_english_policy_that_shares_its_words(self):
        results = search(
            self.conn, self.fake, "how many days of annual leave do employees get", EVERYONE
        )
        assert results[0].chunk.document.id == "hr-annual-leave-policy-en"

    def test_a_restricted_document_never_reaches_an_employee_level_search(self):
        results = search(
            self.conn, self.fake, "compensation salary bands", {"public", "employee"}, k=40
        )
        assert "hr-compensation-bands-en" not in {r.chunk.document.id for r in results}
