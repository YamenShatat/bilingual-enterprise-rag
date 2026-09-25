"""hybrid_search and retrieve on a real database, with the hashing and a hand-built embedder."""

import pytest

from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from bilingual_rag.retrieval.hybrid import MODES, hybrid_search, retrieve
from bilingual_rag.retrieval.keyword import keyword_search
from bilingual_rag.retrieval.search import search
from support.samples import make_chunks, make_metadata

pytestmark = pytest.mark.database

EVERYONE = set(ACCESS_LEVELS)
EMBEDDER = HashingEmbedder(64)


@pytest.fixture
def conn(store_connection):
    metadata = make_metadata()
    store_document(
        store_connection,
        metadata,
        make_chunks(
            metadata.path,
            [
                "annual leave policy details",
                "vpn setup guide for laptops",
                "parking permits and garage access",
            ],
        ),
    )
    embed_missing(store_connection, EMBEDDER)
    return store_connection


A, B, C = (
    "annual leave policy details",
    "vpn setup guide for laptops",
    "parking permits and garage access",
)
QUERY = "garage parking"  # its words match only C


class TableEmbedder:
    """Vectors chosen by the test, so the vector ranking can disagree with word overlap (the
    hashing embedder is lexical, so with it the two searches mostly agree). For QUERY the
    vector ranking is A, B, C, while the keyword search finds only C."""

    key = "table_3"
    model_name = "table-embedder"
    dimension = 3
    vectors = {
        A: [1.0, 0.0, 0.0],
        B: [0.8, 0.6, 0.0],
        C: [0.0, 0.0, 1.0],
        QUERY: [0.98, 0.1, 0.17],
    }

    def embed_documents(self, texts):
        return [self.vectors[text] for text in texts]

    def embed_query(self, text):
        return self.vectors[text]


@pytest.fixture
def table(store_connection):
    metadata = make_metadata()
    store_document(store_connection, metadata, make_chunks(metadata.path, [A, B, C]))
    embed_missing(store_connection, TableEmbedder())
    return store_connection


def texts(results):
    return [r.chunk.text for r in results]


class TestFusionWithDisagreeingRankings:
    def test_the_fixture_disagrees_as_intended(self, table):
        assert texts(search(table, TableEmbedder(), QUERY, EVERYONE, k=3)) == [A, B, C]
        assert texts(keyword_search(table, QUERY, EVERYONE)) == [C]

    def test_a_chunk_found_by_both_overtakes_the_vector_top_result(self, table):
        # C: 3rd by vector + 1st by keyword beats A: 1st by vector only.
        assert texts(hybrid_search(table, TableEmbedder(), QUERY, EVERYONE, k=3)) == [C, A, B]

    def test_a_keyword_only_match_is_included_and_scored_by_cosine(self, table):
        # depth 2: the vector list is A, B, so C comes from the keyword search alone.
        results = hybrid_search(table, TableEmbedder(), QUERY, EVERYONE, k=2, candidates=1)
        assert texts(results) == [A, C]
        (expected,) = search(
            table, TableEmbedder(), QUERY, EVERYONE, chunk_ids=[results[1].chunk.id]
        )
        assert results[1].score == pytest.approx(expected.score)
        assert results[1].score < 0.2  # its real cosine, not a borrowed score

    def test_on_a_tie_the_vector_result_comes_first(self, table):
        # A (vector 1st) and C (keyword 1st) both score 1/61: vector search, which also covers
        # the other language, wins the tie.
        results = hybrid_search(table, TableEmbedder(), QUERY, EVERYONE, k=2, candidates=1)
        assert texts(results)[0] == A

    def test_each_search_looks_at_least_k_deep(self, table):
        assert len(hybrid_search(table, TableEmbedder(), QUERY, EVERYONE, k=3, candidates=1)) == 3

    def test_retrieve_defaults_to_vector_not_hybrid(self, table):
        # D-022: hybrid fusion hurt cross-lingual questions, so it is not the default.
        assert texts(retrieve(table, TableEmbedder(), QUERY, EVERYONE, k=3)) == [A, B, C]


class TestHybridSearch:
    def test_every_result_carries_its_cosine_similarity(self, conn):
        hybrid = hybrid_search(conn, EMBEDDER, "annual leave", EVERYONE, k=3)
        cosine = {
            r.chunk.id: r.score for r in search(conn, EMBEDDER, "annual leave", EVERYONE, k=3)
        }
        assert {r.chunk.id: r.score for r in hybrid} == pytest.approx(
            {r.chunk.id: cosine[r.chunk.id] for r in hybrid}
        )

    def test_found_by_both_ranks_first(self, conn):
        assert hybrid_search(conn, EMBEDDER, "annual leave policy", EVERYONE)[0].chunk.text == (
            "annual leave policy details"
        )

    def test_k_limits_the_results(self, conn):
        assert len(hybrid_search(conn, EMBEDDER, "leave vpn parking", EVERYONE, k=2)) == 2

    def test_a_chunk_with_no_embedding_yet_is_left_out(self, conn):
        extra = make_metadata(id="new-doc", path="x/new.md")
        store_document(conn, extra, make_chunks(extra.path, ["garage parking garage parking"]))
        results = hybrid_search(conn, EMBEDDER, "garage parking", EVERYONE, k=10)
        assert "new-doc-p1-c0" not in [r.chunk.id for r in results]
        assert "new-doc-p1-c0" in [r.chunk.id for r in keyword_search(conn, "garage", EVERYONE)]

    @pytest.mark.parametrize("k, candidates", [(0, 20), (5, 0)])
    def test_k_and_candidates_must_be_positive(self, conn, k, candidates):
        with pytest.raises(ValueError, match="positive"):
            hybrid_search(conn, EMBEDDER, "leave", EVERYONE, k=k, candidates=candidates)


class TestAccessControl:
    @pytest.fixture(autouse=True)
    def _seeded(self, store_connection):
        for level in ACCESS_LEVELS:
            metadata = make_metadata(id=f"doc-{level}", path=f"x/{level}.md", access_level=level)
            store_document(
                store_connection, metadata, make_chunks(metadata.path, [f"bonus secret {level}"])
            )
        embed_missing(store_connection, EMBEDDER)
        self.conn = store_connection

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_every_mode_returns_only_the_callers_levels(self, mode, level):
        results = retrieve(self.conn, EMBEDDER, "bonus secret", {level}, mode=mode, k=10)
        assert [r.chunk.document.access_level for r in results] == [level]

    def test_scoring_chosen_chunk_ids_still_applies_the_access_filter(self):
        results = search(self.conn, EMBEDDER, "bonus", {"public"}, k=10, chunk_ids=["doc-hr-p1-c0"])
        assert results == []


class TestRetrieve:
    def test_each_mode_calls_its_search(self, conn):
        query = "annual leave"
        assert retrieve(conn, EMBEDDER, query, EVERYONE, mode="vector") == search(
            conn, EMBEDDER, query, EVERYONE
        )
        assert retrieve(conn, EMBEDDER, query, EVERYONE, mode="keyword") == keyword_search(
            conn, query, EVERYONE
        )
        assert retrieve(conn, EMBEDDER, query, EVERYONE, mode="hybrid") == hybrid_search(
            conn, EMBEDDER, query, EVERYONE
        )

    def test_k_and_language_are_passed_on(self, conn):
        for mode in MODES:
            assert len(retrieve(conn, EMBEDDER, "leave vpn parking", EVERYONE, mode=mode, k=1)) == 1
            assert retrieve(conn, EMBEDDER, "leave", EVERYONE, mode=mode, language="ar") == []

    def test_an_unknown_mode_is_an_error(self, conn):
        with pytest.raises(ValueError, match="unknown mode"):
            retrieve(conn, EMBEDDER, "leave", EVERYONE, mode="bm25")


class TestSearchChunkIds:
    def test_narrows_the_search_to_the_given_chunks(self, conn):
        results = search(conn, EMBEDDER, "leave", EVERYONE, k=10, chunk_ids=["hr-leave-en-p1-c2"])
        assert [r.chunk.id for r in results] == ["hr-leave-en-p1-c2"]

    def test_an_empty_list_matches_nothing(self, conn):
        assert search(conn, EMBEDDER, "leave", EVERYONE, chunk_ids=[]) == []


class ReverseReranker:
    """Scores the retriever's last candidate highest, so reranking visibly changes the order."""

    model_name = "reverse"

    def __init__(self):
        self.seen: list[list[str]] = []

    def score(self, query, texts):
        self.seen.append(list(texts))
        return [float(i) for i in range(len(texts))]


class TestRetrieveWithReranker:
    def test_the_reranker_orders_the_results_and_sets_rerank_score(self, table):
        results = retrieve(table, TableEmbedder(), QUERY, EVERYONE, k=3, reranker=ReverseReranker())
        assert texts(results) == [C, B, A]
        assert [r.rerank_score for r in results] == [2.0, 1.0, 0.0]

    def test_the_reranker_sees_rerank_candidates_not_just_k(self, table):
        reranker = ReverseReranker()
        results = retrieve(table, TableEmbedder(), QUERY, EVERYONE, k=1, reranker=reranker)
        assert reranker.seen == [[A, B, C]]  # three candidates for one result
        assert texts(results) == [C]

    def test_rerank_candidates_limits_what_the_reranker_sees(self, table):
        reranker = ReverseReranker()
        retrieve(
            table, TableEmbedder(), QUERY, EVERYONE, k=1, reranker=reranker, rerank_candidates=2
        )
        assert reranker.seen == [[A, B]]

    def test_without_a_reranker_nothing_is_reranked(self, table):
        results = retrieve(table, TableEmbedder(), QUERY, EVERYONE, k=3)
        assert [r.rerank_score for r in results] == [None, None, None]

    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_the_reranker_only_ever_sees_permitted_chunks(self, store_connection, level):
        for lvl in ACCESS_LEVELS:
            metadata = make_metadata(id=f"doc-{lvl}", path=f"x/{lvl}.md", access_level=lvl)
            store_document(
                store_connection, metadata, make_chunks(metadata.path, [f"bonus secret {lvl}"])
            )
        embed_missing(store_connection, EMBEDDER)
        reranker = ReverseReranker()
        retrieve(store_connection, EMBEDDER, "bonus secret", {level}, k=5, reranker=reranker)
        assert reranker.seen == [[f"bonus secret {level}"]]
