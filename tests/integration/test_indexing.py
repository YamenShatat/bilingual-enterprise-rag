from dataclasses import replace
from pathlib import Path

import pytest

from bilingual_rag.database.embedding_store import (
    EmbeddingModelError,
    chunk_ids_without_embeddings,
    get_embedding,
    get_embedding_model,
)
from bilingual_rag.database.repository import chunk_id, store_document
from bilingual_rag.database.vectors import format_vector
from bilingual_rag.embeddings.base import EmbeddingError
from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS, load_manifest
from bilingual_rag.ingestion.pipeline import ingest_directory
from support.arabic import ARABIC_TRUTH
from support.samples import make_chunks, make_metadata

pytestmark = pytest.mark.database

DATA = Path(__file__).resolve().parents[2] / "data"


class Recording:
    """Wraps an embedder and remembers every batch of texts it was asked to embed."""

    def __init__(self, inner):
        self.inner = inner
        self.batches: list[list[str]] = []
        self.key, self.model_name, self.dimension = inner.key, inner.model_name, inner.dimension

    def embed_documents(self, texts):
        self.batches.append(list(texts))
        return self.inner.embed_documents(texts)

    def embed_query(self, text):
        return self.inner.embed_query(text)


class Broken:
    """An embedder whose output is deliberately wrong."""

    def __init__(self, inner, mutate):
        self.inner, self.mutate = inner, mutate
        self.key, self.model_name, self.dimension = inner.key, inner.model_name, inner.dimension

    def embed_documents(self, texts):
        return self.mutate(self.inner.embed_documents(texts))

    def embed_query(self, text):
        return self.inner.embed_query(text)


def _add(conn, number, texts, **overrides):
    metadata = make_metadata(id=f"doc-{number}", path=f"doc{number}.md", **overrides)
    chunks = make_chunks(metadata.path, list(texts))
    store_document(conn, metadata, chunks)
    return [chunk_id(metadata.id, c.page, c.index) for c in chunks]


@pytest.fixture
def fake():
    return HashingEmbedder(16)


class TestEmbedMissing:
    def test_embeds_every_chunk_and_registers_the_model(self, store_connection, fake):
        ids = _add(store_connection, 1, ["alpha beta", "gamma delta", "epsilon"])
        assert embed_missing(store_connection, fake) == 3
        model = get_embedding_model(store_connection, fake.key)
        assert (model.model_name, model.dimension) == (fake.model_name, 16)
        assert chunk_ids_without_embeddings(store_connection, fake.key) == []
        assert get_embedding(store_connection, fake.key, ids[0]) == pytest.approx(
            fake.embed_query("alpha beta"), rel=1e-6
        )

    def test_running_again_embeds_nothing(self, store_connection, fake):
        _add(store_connection, 1, ["alpha", "beta"])
        embed_missing(store_connection, fake)
        recording = Recording(fake)
        assert embed_missing(store_connection, recording) == 0
        assert recording.batches == []  # the embedder was not even called

    def test_only_new_chunks_are_embedded_after_adding_a_document(self, store_connection, fake):
        _add(store_connection, 1, ["alpha", "beta"])
        embed_missing(store_connection, fake)
        _add(store_connection, 2, ["gamma"])
        recording = Recording(fake)
        assert embed_missing(store_connection, recording) == 1
        assert recording.batches == [["gamma"]]

    def test_a_document_with_changed_text_is_embedded_again(self, store_connection, fake):
        _add(store_connection, 1, ["alpha", "beta"])
        embed_missing(store_connection, fake)
        metadata = make_metadata(id="doc-1", path="doc1.md")
        store_document(store_connection, metadata, make_chunks(metadata.path, ["alpha", "CHANGED"]))
        recording = Recording(fake)
        assert embed_missing(store_connection, recording) == 2  # the whole document was replaced
        assert sorted(sum(recording.batches, [])) == ["CHANGED", "alpha"]

    def test_nothing_to_embed_still_registers_the_model(self, store_connection, fake):
        assert embed_missing(store_connection, fake) == 0
        assert get_embedding_model(store_connection, fake.key) is not None

    def test_restricted_documents_are_embedded_too(self, store_connection, fake):
        for number, level in enumerate(ACCESS_LEVELS):
            _add(store_connection, number, [f"text of {level}"], access_level=level)
        assert embed_missing(store_connection, fake) == len(ACCESS_LEVELS)
        assert chunk_ids_without_embeddings(store_connection, fake.key) == []

    def test_the_embedder_receives_the_stored_text_exactly(self, store_connection, fake):
        text = (
            "\N{NO-BREAK SPACE}  "  # leading whitespace that a strip() would remove
            + ARABIC_TRUTH[1]
            + " \N{ARABIC TATWEEL}\N{ARABIC FATHA}\N{RIGHT-TO-LEFT MARK}\N{NO-BREAK SPACE}"
            + "\N{ARABIC-INDIC DIGIT TWO}\N{ARABIC-INDIC DIGIT ONE}  two  spaces\n\n"
            + "MiXeD Case and a break\n"  # upper case that lower() would change; trailing newline
        )
        _add(store_connection, 1, [text])
        recording = Recording(fake)
        embed_missing(store_connection, recording)
        (batch,) = recording.batches
        assert batch == [text]  # no strip, no normalization, no prefix added by the caller

    def test_batches_have_the_requested_size(self, store_connection, fake):
        _add(store_connection, 1, [f"word{n}" for n in range(7)])
        recording = Recording(fake)
        embed_missing(store_connection, recording, batch_size=3)
        assert [len(batch) for batch in recording.batches] == [3, 3, 1]

    @pytest.mark.parametrize("batch_size", [1, 2, 3, 100])
    def test_the_batch_size_does_not_change_what_is_stored(
        self, store_connection, fake, batch_size
    ):
        ids = _add(store_connection, 1, [f"word{n} shared" for n in range(5)])
        embed_missing(store_connection, fake, batch_size=batch_size)
        stored = [get_embedding(store_connection, fake.key, i) for i in ids]
        expected = fake.embed_documents([f"word{n} shared" for n in range(5)])
        assert stored == [pytest.approx(v, rel=1e-6) for v in expected]

    @pytest.mark.parametrize("batch_size", [0, -1])
    def test_a_non_positive_batch_size_is_rejected(self, store_connection, fake, batch_size):
        with pytest.raises(ValueError, match="batch_size"):
            embed_missing(store_connection, fake, batch_size=batch_size)

    def test_progress_is_reported_after_each_batch(self, store_connection, fake):
        _add(store_connection, 1, [f"w{n}" for n in range(5)])
        seen = []
        embed_missing(
            store_connection, fake, batch_size=2, on_batch=lambda d, t: seen.append((d, t))
        )
        assert seen == [(2, 5), (4, 5), (5, 5)]

    def test_progress_stored_before_a_failure_is_kept_and_resumed(self, store_connection, fake):
        _add(store_connection, 1, [f"w{n}" for n in range(6)])
        calls = []

        class FailsOnSecondBatch(Recording):
            def embed_documents(self, texts):
                calls.append(len(texts))
                if len(calls) == 2:
                    raise RuntimeError("model crashed")
                return super().embed_documents(texts)

        with pytest.raises(RuntimeError, match="crashed"):
            embed_missing(store_connection, FailsOnSecondBatch(fake), batch_size=2)
        assert (
            len(chunk_ids_without_embeddings(store_connection, fake.key)) == 4
        )  # first batch kept
        assert embed_missing(store_connection, fake, batch_size=2) == 4  # resumes with the rest
        assert chunk_ids_without_embeddings(store_connection, fake.key) == []


class TestRejectsBadEmbedders:
    def test_too_few_vectors_are_an_error_and_nothing_is_stored(self, store_connection, fake):
        _add(store_connection, 1, ["a", "b", "c"])
        broken = Broken(fake, lambda vectors: vectors[:-1])
        with pytest.raises(EmbeddingError, match="expected 3 vectors, got 2"):
            embed_missing(store_connection, broken)
        assert len(chunk_ids_without_embeddings(store_connection, fake.key)) == 3

    def test_too_many_vectors_are_an_error(self, store_connection, fake):
        _add(store_connection, 1, ["a", "b"])
        broken = Broken(fake, lambda vectors: vectors + vectors[:1])
        with pytest.raises(EmbeddingError, match="expected 2 vectors, got 3"):
            embed_missing(store_connection, broken)

    def test_vectors_of_the_wrong_size_are_an_error(self, store_connection, fake):
        _add(store_connection, 1, ["a", "b"])
        broken = Broken(fake, lambda vectors: [v[:-1] for v in vectors])
        with pytest.raises(EmbeddingError, match="expected 16"):
            embed_missing(store_connection, broken)

    def test_non_finite_values_are_an_error(self, store_connection, fake):
        _add(store_connection, 1, ["a"])
        broken = Broken(fake, lambda vectors: [[float("nan")] * 16 for _ in vectors])
        with pytest.raises(EmbeddingError, match="NaN"):
            embed_missing(store_connection, broken)

    def test_a_model_key_cannot_be_reused_with_another_dimension(self, store_connection):
        _add(store_connection, 1, ["a"])
        embed_missing(store_connection, HashingEmbedder(16))

        class Impostor(HashingEmbedder):
            def __init__(self):
                super().__init__(32)
                self.key = "hashing_16"  # same key, different dimension

        with pytest.raises(EmbeddingModelError, match="already registered"):
            embed_missing(store_connection, Impostor())


class TestModelsCoexist:
    def test_two_models_of_different_sizes_embed_the_same_chunks(self, store_connection):
        ids = _add(store_connection, 1, ["annual leave", "vpn guide"])
        small, large = HashingEmbedder(8), HashingEmbedder(32)
        assert embed_missing(store_connection, small) == 2
        assert embed_missing(store_connection, large) == 2
        assert len(get_embedding(store_connection, small.key, ids[0])) == 8
        assert len(get_embedding(store_connection, large.key, ids[0])) == 32


class TestTheRealCorpus:
    """Plumbing over the 32-document corpus with the hashing stand-in (lexical, not semantic)."""

    @pytest.fixture
    def indexed(self, store_connection):
        documents = load_manifest(DATA / "manifest.json")
        by_path: dict[str, list] = {}
        for chunk in ingest_directory(DATA / "synthetic").chunks:
            by_path.setdefault(chunk.filename, []).append(chunk)
        for document in documents:
            store_document(store_connection, document, by_path[document.path])
        embedder = HashingEmbedder(256)
        total = embed_missing(store_connection, embedder)
        return store_connection, embedder, total, sum(len(c) for c in by_path.values())

    def _search(self, conn, embedder, query, levels, k=3):
        rows = conn.execute(
            "SELECT d.id FROM embeddings_hashing_256 e"
            " JOIN chunks c ON c.id = e.chunk_id JOIN documents d ON d.id = c.document_id"
            " WHERE d.access_level = ANY(%s)"
            " ORDER BY e.embedding <=> %s::vector, c.id LIMIT %s",
            (sorted(levels), format_vector(embedder.embed_query(query)), k),
        ).fetchall()
        return [row[0] for row in rows]

    def test_every_chunk_of_every_document_is_embedded(self, indexed):
        conn, embedder, total, chunk_count = indexed
        assert total == chunk_count
        assert chunk_ids_without_embeddings(conn, embedder.key) == []

    def test_an_english_query_finds_the_english_policy_that_shares_its_words(self, indexed):
        conn, embedder, _, _ = indexed
        top = self._search(
            conn, embedder, "how many days of annual leave do employees get", ACCESS_LEVELS
        )
        assert top[0] == "hr-annual-leave-policy-en"

    def test_an_arabic_query_finds_the_arabic_policy_that_shares_its_words(self, indexed):
        conn, embedder, _, _ = indexed
        assert self._search(conn, embedder, "الإجازة السنوية", ACCESS_LEVELS)[0] == (
            "hr-annual-leave-policy-ar"
        )
        top = self._search(conn, embedder, "ساعات العمل الإضافية", ACCESS_LEVELS)
        assert top[0] == "hr-working-hours-and-overtime-ar"

    def test_the_stand_in_cannot_cross_languages_which_is_why_it_proves_nothing_about_quality(
        self, indexed
    ):
        conn, embedder, _, _ = indexed
        top = self._search(conn, embedder, "overtime pay rates", ACCESS_LEVELS, k=5)
        assert "hr-working-hours-and-overtime-ar" not in top  # the answer is only in Arabic

    def test_restricted_documents_are_embedded_but_hidden_by_the_access_filter(self, indexed):
        conn, embedder, _, _ = indexed
        everyone = self._search(conn, embedder, "compensation salary bands", ACCESS_LEVELS, k=1)
        assert everyone == ["hr-compensation-bands-en"]
        employee = self._search(
            conn, embedder, "compensation salary bands", {"public", "employee"}, k=40
        )
        assert "hr-compensation-bands-en" not in employee
        assert employee  # other documents are still returned

    def test_the_hashing_vectors_are_what_is_stored(self, indexed):
        conn, embedder, _, _ = indexed
        chunk = conn.execute("SELECT id, text FROM chunks ORDER BY id LIMIT 1").fetchone()
        stored = get_embedding(conn, embedder.key, chunk[0])
        assert stored == pytest.approx(embedder.embed_documents([chunk[1]])[0], rel=1e-6)

    def test_metadata_changes_do_not_trigger_re_embedding(self, indexed):
        conn, embedder, _, _ = indexed
        document = load_manifest(DATA / "manifest.json")[0]
        chunks = ingest_directory(DATA / "synthetic").chunks
        mine = [c for c in chunks if c.filename == document.path]
        store_document(conn, replace(document, title=document.title + " (renamed)"), mine)
        assert embed_missing(conn, embedder) == 0
