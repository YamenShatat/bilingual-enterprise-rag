import math
from dataclasses import replace

import psycopg
import pytest

from bilingual_rag.database.embedding_store import (
    EmbeddingModel,
    EmbeddingModelError,
    chunk_ids_without_embeddings,
    ensure_embedding_model,
    get_embedding,
    get_embedding_model,
    store_embeddings,
)
from bilingual_rag.database.repository import (
    chunk_id,
    delete_document,
    list_chunks,
    store_document,
)
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from support.samples import make_chunks, make_metadata, with_text

pytestmark = pytest.mark.database


@pytest.fixture
def stored(store_connection):
    """A document with three chunks (one Arabic) and its chunk ids."""
    metadata = make_metadata()
    chunks = make_chunks(metadata.path, ["alpha", "beta", "سياسة الإجازة"])
    store_document(store_connection, metadata, chunks)
    ids = [chunk_id(metadata.id, c.page, c.index) for c in chunks]
    return store_connection, metadata, chunks, ids


def _column_type(conn, table):
    return conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
        " WHERE attrelid = %s::regclass AND attname = 'embedding'",
        (table,),
    ).fetchone()[0]


class TestEnsureEmbeddingModel:
    def test_registers_the_model_and_creates_a_table_of_the_right_dimension(self, store_connection):
        model = ensure_embedding_model(store_connection, "bge_m3", "BAAI/bge-m3", 1024)
        assert model == EmbeddingModel("bge_m3", "BAAI/bge-m3", 1024, "embeddings_bge_m3")
        assert get_embedding_model(store_connection, "bge_m3") == model
        assert _column_type(store_connection, "embeddings_bge_m3") == "vector(1024)"

    def test_is_idempotent(self, store_connection):
        first = ensure_embedding_model(store_connection, "fake_4", "fake", 4)
        assert ensure_embedding_model(store_connection, "fake_4", "fake", 4) == first
        count = store_connection.execute("SELECT count(*) FROM embedding_models").fetchone()
        assert count == (1,)

    def test_models_with_different_dimensions_coexist(self, stored):
        conn, _, _, ids = stored
        ensure_embedding_model(conn, "small", "small-model", 2)
        ensure_embedding_model(conn, "large", "large-model", 5)
        store_embeddings(conn, "small", {ids[0]: [1, 2]})
        store_embeddings(conn, "large", {ids[0]: [1, 2, 3, 4, 5]})
        assert get_embedding(conn, "small", ids[0]) == [1.0, 2.0]
        assert get_embedding(conn, "large", ids[0]) == [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _column_type(conn, "embeddings_small") == "vector(2)"
        assert _column_type(conn, "embeddings_large") == "vector(5)"

    def test_a_key_cannot_be_reused_with_another_dimension(self, store_connection):
        ensure_embedding_model(store_connection, "m", "model", 4)
        with pytest.raises(EmbeddingModelError, match="already registered"):
            ensure_embedding_model(store_connection, "m", "model", 8)
        assert get_embedding_model(store_connection, "m").dimension == 4
        assert _column_type(store_connection, "embeddings_m") == "vector(4)"

    def test_a_key_cannot_be_reused_for_another_model(self, store_connection):
        ensure_embedding_model(store_connection, "m", "model-a", 4)
        with pytest.raises(EmbeddingModelError, match="model-a"):
            ensure_embedding_model(store_connection, "m", "model-b", 4)

    def test_an_unknown_key_is_none(self, store_connection):
        assert get_embedding_model(store_connection, "absent") is None

    @pytest.mark.parametrize(
        "key", ["", "Bad", "1a", "a-b", "a b", "a" * 41, "é", "bge.m3", 'a"b', "a;b"]
    )
    def test_an_invalid_key_is_rejected_before_any_sql_runs(self, store_connection, key):
        with pytest.raises(ValueError, match="key must be"):
            ensure_embedding_model(store_connection, key, "model", 4)

    def test_an_injection_attempt_in_the_key_does_nothing(self, stored):
        conn, _, _, _ = stored
        with pytest.raises(ValueError):
            ensure_embedding_model(conn, "x; DROP TABLE chunks; --", "model", 4)
        assert len(list_chunks(conn, set(ACCESS_LEVELS))) == 3

    @pytest.mark.parametrize("dimension", [0, -1, 16001, 4.0, "4", None, True])
    def test_an_invalid_dimension_is_rejected(self, store_connection, dimension):
        with pytest.raises(ValueError, match="dimension"):
            ensure_embedding_model(store_connection, "m", "model", dimension)

    def test_the_dimension_limits_are_accepted(self, store_connection):
        ensure_embedding_model(store_connection, "one", "model", 1)
        ensure_embedding_model(store_connection, "max", "model", 16000)
        assert _column_type(store_connection, "embeddings_max") == "vector(16000)"

    def test_an_empty_model_name_is_rejected(self, store_connection):
        with pytest.raises(ValueError, match="model_name"):
            ensure_embedding_model(store_connection, "m", "  ", 4)


class TestStoreEmbeddings:
    @pytest.fixture(autouse=True)
    def _model(self, stored):
        ensure_embedding_model(stored[0], "fake_3", "fake", 3)

    def test_stores_and_reads_back_vectors(self, stored):
        conn, _, _, ids = stored
        written = store_embeddings(conn, "fake_3", {ids[0]: [1, 0, 0], ids[1]: [0, 0.5, -2]})
        assert written == 2
        assert get_embedding(conn, "fake_3", ids[0]) == [1.0, 0.0, 0.0]
        assert get_embedding(conn, "fake_3", ids[1]) == [0.0, 0.5, -2.0]

    def test_values_are_kept_to_single_precision(self, stored):
        conn, _, _, ids = stored
        store_embeddings(conn, "fake_3", {ids[0]: [0.1, 1 / 3, 1e-7]})
        got = get_embedding(conn, "fake_3", ids[0])
        assert got == pytest.approx([0.1, 1 / 3, 1e-7], rel=1e-6)

    def test_storing_again_replaces_the_vector(self, stored):
        conn, _, _, ids = stored
        store_embeddings(conn, "fake_3", {ids[0]: [1, 1, 1]})
        store_embeddings(conn, "fake_3", {ids[0]: [2, 2, 2]})
        assert get_embedding(conn, "fake_3", ids[0]) == [2.0, 2.0, 2.0]
        assert conn.execute("SELECT count(*) FROM embeddings_fake_3").fetchone() == (1,)

    def test_a_chunk_without_a_vector_is_none(self, stored):
        conn, _, _, ids = stored
        assert get_embedding(conn, "fake_3", ids[0]) is None

    def test_nothing_to_store_is_fine(self, stored):
        assert store_embeddings(stored[0], "fake_3", {}) == 0

    def test_the_arabic_chunk_keeps_its_text_next_to_its_vector(self, stored):
        conn, metadata, chunks, ids = stored
        store_embeddings(conn, "fake_3", {ids[2]: [0.25, 0.5, 0.75]})
        row = conn.execute(
            "SELECT c.text, e.embedding::text FROM chunks c JOIN embeddings_fake_3 e"
            " ON e.chunk_id = c.id"
        ).fetchone()
        assert row == ("سياسة الإجازة", "[0.25,0.5,0.75]")

    @pytest.mark.parametrize("values", [[1, 2], [1, 2, 3, 4], []])
    def test_a_vector_of_the_wrong_length_is_rejected_and_nothing_is_written(self, stored, values):
        conn, _, _, ids = stored
        with pytest.raises(ValueError, match="expected 3 values"):
            store_embeddings(conn, "fake_3", {ids[0]: [1, 1, 1], ids[1]: values})
        assert conn.execute("SELECT count(*) FROM embeddings_fake_3").fetchone() == (0,)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_a_non_finite_value_is_rejected(self, stored, bad):
        conn, _, _, ids = stored
        with pytest.raises(ValueError, match="finite"):
            store_embeddings(conn, "fake_3", {ids[0]: [1, bad, 3]})

    def test_an_unknown_model_key_is_an_error(self, stored):
        conn, _, _, ids = stored
        with pytest.raises(EmbeddingModelError, match="no embedding model"):
            store_embeddings(conn, "absent", {ids[0]: [1, 1, 1]})
        with pytest.raises(EmbeddingModelError):
            get_embedding(conn, "absent", ids[0])
        with pytest.raises(EmbeddingModelError):
            chunk_ids_without_embeddings(conn, "absent")

    def test_a_vector_for_a_chunk_that_does_not_exist_is_rejected(self, stored):
        conn, _, _, _ = stored
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            store_embeddings(conn, "fake_3", {"no-such-chunk-p1-c0": [1, 1, 1]})


class TestChunksWithoutEmbeddings:
    @pytest.fixture(autouse=True)
    def _model(self, stored):
        ensure_embedding_model(stored[0], "fake_3", "fake", 3)

    def test_starts_with_every_chunk_in_order(self, stored):
        conn, _, _, ids = stored
        assert chunk_ids_without_embeddings(conn, "fake_3") == sorted(ids)

    def test_shrinks_as_vectors_are_stored(self, stored):
        conn, _, _, ids = stored
        store_embeddings(conn, "fake_3", {ids[0]: [1, 1, 1]})
        assert chunk_ids_without_embeddings(conn, "fake_3") == sorted(ids[1:])
        store_embeddings(conn, "fake_3", {ids[1]: [1, 1, 1], ids[2]: [1, 1, 1]})
        assert chunk_ids_without_embeddings(conn, "fake_3") == []

    def test_is_tracked_per_model(self, stored):
        conn, _, _, ids = stored
        ensure_embedding_model(conn, "other", "other-model", 2)
        store_embeddings(conn, "fake_3", {ids[0]: [1, 1, 1]})
        assert chunk_ids_without_embeddings(conn, "other") == sorted(ids)


class TestEmbeddingsFollowTheirChunks:
    @pytest.fixture(autouse=True)
    def _embedded(self, stored):
        conn, _, _, ids = stored
        ensure_embedding_model(conn, "fake_3", "fake", 3)
        store_embeddings(conn, "fake_3", {i: [1, 2, 3] for i in ids})

    def _count(self, conn):
        return conn.execute("SELECT count(*) FROM embeddings_fake_3").fetchone()[0]

    def test_re_ingesting_unchanged_content_keeps_the_embeddings(self, stored):
        conn, metadata, chunks, _ = stored
        assert store_document(conn, metadata, chunks) == "unchanged"
        assert self._count(conn) == 3

    def test_a_metadata_only_change_keeps_the_embeddings(self, stored):
        conn, metadata, chunks, _ = stored
        store_document(conn, replace(metadata, access_level="hr"), chunks)
        assert self._count(conn) == 3

    def test_changed_text_drops_the_stale_embeddings_so_they_are_recomputed(self, stored):
        conn, metadata, chunks, ids = stored
        changed = [chunks[0], with_text(chunks[1], "changed"), chunks[2]]
        assert store_document(conn, metadata, changed) == "updated"
        assert self._count(conn) == 0  # all of the document's vectors go with its old chunks
        assert chunk_ids_without_embeddings(conn, "fake_3") == sorted(ids)

    def test_deleting_the_document_deletes_its_embeddings(self, stored):
        conn, metadata, _, _ = stored
        delete_document(conn, metadata.id)
        assert self._count(conn) == 0

    def test_only_the_changed_documents_embeddings_are_dropped(self, stored):
        conn, metadata, chunks, _ = stored
        other = make_metadata(id="other-doc", path="other.md")
        other_chunks = make_chunks(other.path, ["keep me"])
        store_document(conn, other, other_chunks)
        store_embeddings(conn, "fake_3", {chunk_id(other.id, 1, 0): [9, 9, 9]})
        store_document(conn, metadata, [with_text(chunks[0], "new")])
        assert self._count(conn) == 1
        assert get_embedding(conn, "fake_3", chunk_id(other.id, 1, 0)) == [9.0, 9.0, 9.0]


class TestVectorSearchOverStoredEmbeddings:
    def test_cosine_distance_orders_the_stored_chunks(self, stored):
        conn, _, _, ids = stored
        ensure_embedding_model(conn, "fake_3", "fake", 3)
        store_embeddings(
            conn, "fake_3", {ids[0]: [1, 0, 0], ids[1]: [0, 1, 0], ids[2]: [0.9, 0.1, 0]}
        )
        # Angles from the x axis: query 6.84, ids[2] 6.34, ids[0] 0, ids[1] 90 degrees.
        rows = conn.execute(
            "SELECT chunk_id FROM embeddings_fake_3 ORDER BY embedding <=> '[1,0.12,0]'::vector"
        ).fetchall()
        assert [row[0] for row in rows] == [ids[2], ids[0], ids[1]]
