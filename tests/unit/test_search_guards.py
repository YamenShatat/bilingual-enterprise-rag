"""The guards `search()` checks before it ever touches the connection or the embedder."""

import pytest

from bilingual_rag.retrieval.search import search


class DummyEmbedder:
    """If `search()` reaches into this, the test using it should have failed already."""

    key = "should-not-be-used"
    model_name = "should-not-be-used"
    dimension = 1

    def embed_query(self, text):
        raise AssertionError("embed_query should not be called")

    def embed_documents(self, texts):
        raise AssertionError("embed_documents should not be called")


class DummyConnection:
    def execute(self, *args, **kwargs):
        raise AssertionError("execute should not be called")


@pytest.fixture
def conn():
    return DummyConnection()


@pytest.fixture
def embedder():
    return DummyEmbedder()


class TestAccessLevelGuard:
    def test_a_bare_string_is_refused_not_read_as_letters(self, conn, embedder):
        with pytest.raises(TypeError, match="not one string"):
            search(conn, embedder, "annual leave", "employee")

    def test_an_empty_collection_returns_nothing_without_touching_the_database_or_the_model(
        self, conn, embedder
    ):
        for empty in ([], (), set(), frozenset()):
            assert search(conn, embedder, "annual leave", empty) == []


class TestKGuard:
    @pytest.mark.parametrize("k", [0, -1, -100])
    def test_a_non_positive_k_is_rejected(self, conn, embedder, k):
        with pytest.raises(ValueError, match="k must be positive"):
            search(conn, embedder, "annual leave", {"employee"}, k=k)

    def test_the_k_check_runs_before_touching_the_database_or_the_model(self, conn, embedder):
        # If either dummy were used, its AssertionError would surface instead of ValueError.
        with pytest.raises(ValueError):
            search(conn, embedder, "annual leave", {"employee"}, k=0)
