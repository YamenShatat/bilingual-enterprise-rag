import numpy as np
import pytest

from bilingual_rag.embeddings.sentence_transformer import (
    SentenceTransformerEmbedder,
    bge_m3,
    e5_large,
)
from support.arabic import ARABIC_TRUTH


class FakeModel:
    """Stands in for `sentence_transformers.SentenceTransformer` and records every call."""

    def __init__(self, dimension: int = 3):
        self.dimension = dimension
        self.calls: list[dict] = []

    def encode(self, texts, *, batch_size, normalize_embeddings, convert_to_numpy):
        self.calls.append(
            {
                "texts": list(texts),
                "batch_size": batch_size,
                "normalize_embeddings": normalize_embeddings,
                "convert_to_numpy": convert_to_numpy,
            }
        )
        # A vector that differs by text so distinct inputs are distinguishable in assertions.
        return np.array([[float(sum(map(ord, t))), 0.0, 1.0] for t in texts])


@pytest.fixture
def fake():
    return FakeModel()


def make(fake, **kwargs):
    return SentenceTransformerEmbedder("org/fake-model", _model=fake, **kwargs)


class TestConstruction:
    def test_the_dimension_is_measured_not_assumed_from_a_method_name(self, fake):
        embedder = make(fake)
        assert embedder.dimension == 3
        assert len(fake.calls) == 1  # the one probe call, made during construction

    def test_the_probe_call_asks_for_a_normalized_numpy_result(self, fake):
        make(fake)
        assert fake.calls[0]["normalize_embeddings"] is True
        assert fake.calls[0]["convert_to_numpy"] is True

    def test_the_key_defaults_to_the_derived_key(self, fake):
        assert make(fake).key == "fake_model"

    def test_an_explicit_key_overrides_the_derived_one(self, fake):
        assert make(fake, key="custom").key == "custom"

    def test_model_name_is_kept_exactly(self, fake):
        assert make(fake).model_name == "org/fake-model"

    @pytest.mark.parametrize("batch_size", [0, -1])
    def test_a_non_positive_batch_size_is_rejected(self, fake, batch_size):
        with pytest.raises(ValueError, match="batch_size"):
            make(fake, batch_size=batch_size)

    def test_the_batch_size_is_forwarded_to_encode(self, fake):
        make(fake, batch_size=7).embed_documents(["a", "b"])
        assert all(call["batch_size"] == 7 for call in fake.calls)


class TestDevice:
    def test_an_explicit_device_is_kept(self, fake):
        assert make(fake, device="cpu").device == "cpu"

    def test_defaults_to_cuda_when_available(self, fake, monkeypatch):
        monkeypatch.setattr("torch.cuda.is_available", lambda: True)
        assert make(fake).device == "cuda"

    def test_falls_back_to_cpu_when_cuda_is_not_available(self, fake, monkeypatch):
        monkeypatch.setattr("torch.cuda.is_available", lambda: False)
        assert make(fake).device == "cpu"


class TestEmbedDocuments:
    def test_returns_one_vector_per_text_in_order(self, fake):
        vectors = make(fake).embed_documents(["alpha", "beta"])
        assert len(vectors) == 2
        assert vectors[0] != vectors[1]

    def test_vectors_are_plain_python_floats(self, fake):
        (vector,) = make(fake).embed_documents(["alpha"])
        assert all(isinstance(value, float) for value in vector)

    def test_no_texts_gives_no_vectors_and_calls_nothing(self, fake):
        embedder = make(fake)
        assert len(fake.calls) == 1  # only the construction-time probe so far
        assert embedder.embed_documents([]) == []
        assert len(fake.calls) == 1  # embed_documents([]) made no additional call

    def test_a_bare_string_is_refused_not_read_as_characters(self, fake):
        with pytest.raises(TypeError, match="not one string"):
            make(fake).embed_documents("annual leave")

    @pytest.mark.parametrize("bad", [None, 5, b"bytes", ["x"]])
    def test_a_non_string_entry_is_rejected(self, fake, bad):
        with pytest.raises(TypeError):
            make(fake).embed_documents(["fine", bad])

    @pytest.mark.parametrize("blank", ["", " ", "\n\t  "])
    def test_a_blank_entry_is_rejected(self, fake, blank):
        with pytest.raises(ValueError, match="blank"):
            make(fake).embed_documents(["fine", blank])

    def test_the_document_prefix_is_prepended_exactly_once(self, fake):
        make(fake, document_prefix="passage: ").embed_documents(["leave policy"])
        assert fake.calls[-1]["texts"] == ["passage: leave policy"]

    def test_no_prefix_means_the_text_is_sent_unchanged(self, fake):
        text = "  leave policy with spaces  "
        make(fake).embed_documents([text])
        assert fake.calls[-1]["texts"] == [text]  # not stripped

    def test_arabic_text_reaches_the_model_exactly(self, fake):
        make(fake).embed_documents([ARABIC_TRUTH[1]])
        assert fake.calls[-1]["texts"] == [ARABIC_TRUTH[1]]

    def test_normalize_embeddings_is_always_requested(self, fake):
        make(fake).embed_documents(["a"])
        assert fake.calls[-1]["normalize_embeddings"] is True


class TestEmbedQuery:
    def test_returns_a_single_vector(self, fake):
        vector = make(fake).embed_query("annual leave")
        assert isinstance(vector, list)
        assert all(isinstance(value, float) for value in vector)

    def test_the_query_prefix_is_prepended_exactly_once(self, fake):
        make(fake, query_prefix="query: ").embed_query("leave policy")
        assert fake.calls[-1]["texts"] == ["query: leave policy"]

    def test_query_and_document_prefixes_are_independent(self, fake):
        embedder = make(fake, query_prefix="q: ", document_prefix="p: ")
        embedder.embed_query("x")
        embedder.embed_documents(["x"])
        assert [c["texts"] for c in fake.calls[1:]] == [["q: x"], ["p: x"]]

    @pytest.mark.parametrize("blank", ["", "  "])
    def test_a_blank_query_is_rejected(self, fake, blank):
        with pytest.raises(ValueError, match="blank"):
            make(fake).embed_query(blank)

    def test_a_non_string_query_is_rejected(self, fake):
        with pytest.raises(TypeError):
            make(fake).embed_query(5)


def _capture_init(monkeypatch, captured):
    """Monkeypatch __init__ to record its arguments instead of loading a real model."""

    def fake_init(
        self,
        model_name,
        *,
        key=None,
        device=None,
        query_prefix="",
        document_prefix="",
        batch_size=32,
        _model=None,
    ):
        captured["model_name"] = model_name
        captured["query_prefix"] = query_prefix
        captured["document_prefix"] = document_prefix
        self.key = key if key is not None else "captured"
        self.model_name = model_name
        self.query_prefix, self.document_prefix = query_prefix, document_prefix
        self.device, self.batch_size, self.dimension = device or "cpu", batch_size, 1024
        self._model = FakeModel(1024)

    monkeypatch.setattr(SentenceTransformerEmbedder, "__init__", fake_init)


class TestBgeM3Factory:
    def test_uses_the_expected_model_name_and_no_prefixes(self, monkeypatch):
        captured = {}
        _capture_init(monkeypatch, captured)
        bge_m3(device="cpu")
        assert captured == {"model_name": "BAAI/bge-m3", "query_prefix": "", "document_prefix": ""}


class TestE5LargeFactory:
    def test_uses_the_expected_model_name_and_both_prefixes(self, monkeypatch):
        captured = {}
        _capture_init(monkeypatch, captured)
        e5_large(device="cpu")
        assert captured == {
            "model_name": "intfloat/multilingual-e5-large",
            "query_prefix": "query: ",
            "document_prefix": "passage: ",
        }
