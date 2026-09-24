import math

import pytest

from bilingual_rag.embeddings.base import (
    Embedder,
    EmbeddingError,
    model_key,
    normalize,
    validate_vectors,
)
from bilingual_rag.embeddings.hashing import HashingEmbedder


class TestNormalize:
    def test_scales_to_unit_length_and_keeps_the_direction(self):
        assert normalize([3, 4]) == pytest.approx([0.6, 0.8])

    def test_a_unit_vector_is_unchanged(self):
        assert normalize([0.0, 1.0, 0.0]) == [0.0, 1.0, 0.0]

    def test_the_result_has_length_one(self):
        result = normalize([1, -2, 3, 0.5, 7])
        assert math.hypot(*result) == pytest.approx(1.0)

    def test_a_negative_direction_is_kept(self):
        assert normalize([-2, 0]) == [-1.0, 0.0]

    def test_huge_values_do_not_overflow(self):
        assert normalize([1e200, 1e200]) == pytest.approx([2**-0.5, 2**-0.5])

    def test_tiny_values_do_not_underflow_to_zero(self):
        assert normalize([1e-200, 1e-200]) == pytest.approx([2**-0.5, 2**-0.5])

    def test_accepts_any_sequence_of_numbers(self):
        assert normalize((0, 5)) == [0.0, 1.0]
        assert normalize(range(1, 2)) == [1.0]

    @pytest.mark.parametrize("bad", [[], [0, 0, 0]])
    def test_an_empty_or_zero_vector_is_rejected(self, bad):
        with pytest.raises(ValueError):
            normalize(bad)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_values_are_rejected(self, bad):
        with pytest.raises(ValueError, match="NaN or infinite"):
            normalize([1.0, bad])


class TestValidateVectors:
    def test_accepts_the_right_count_and_size(self):
        validate_vectors([[1.0, 2.0], [3.0, 4.0]], expected_count=2, dimension=2)

    def test_accepts_nothing_when_nothing_was_asked_for(self):
        validate_vectors([], expected_count=0, dimension=5)

    @pytest.mark.parametrize("count", [0, 1, 3])
    def test_a_wrong_count_is_rejected_so_no_chunk_is_silently_skipped(self, count):
        vectors = [[1.0, 2.0]] * count
        with pytest.raises(EmbeddingError, match="expected 2 vectors"):
            validate_vectors(vectors, expected_count=2, dimension=2)

    def test_a_wrong_size_is_rejected_and_named_by_position(self):
        with pytest.raises(EmbeddingError, match="vector 1 has 3 values, expected 2"):
            validate_vectors([[1.0, 2.0], [1.0, 2.0, 3.0]], expected_count=2, dimension=2)

    @pytest.mark.parametrize("bad", [math.nan, math.inf])
    def test_non_finite_values_are_rejected(self, bad):
        with pytest.raises(EmbeddingError, match="vector 0"):
            validate_vectors([[1.0, bad]], expected_count=1, dimension=2)


class TestModelKey:
    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("BAAI/bge-m3", "bge_m3"),
            ("intfloat/multilingual-e5-large", "multilingual_e5_large"),
            (
                "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
                "paraphrase_multilingual_mpnet_base_v2",
            ),
            ("all-MiniLM-L6-v2", "all_minilm_l6_v2"),
            ("Some  Odd..Name!!", "some_odd_name"),
        ],
    )
    def test_derives_a_readable_key(self, name, key):
        assert model_key(name) == key

    def test_a_name_starting_with_a_digit_gets_a_letter_prefix(self):
        assert model_key("org/3d-model") == "m_3d_model"

    def test_a_long_name_is_cut_to_the_database_limit_without_a_trailing_underscore(self):
        key = model_key("org/" + "a" * 39 + "-tail")
        assert len(key) <= 40 and not key.endswith("_")

    @pytest.mark.parametrize("name", ["", "///", "!!!", "عربي"])
    def test_a_name_with_nothing_usable_is_rejected(self, name):
        with pytest.raises(ValueError, match="cannot derive a key"):
            model_key(name)

    def test_every_derived_key_is_accepted_by_the_database_pattern(self):
        import re

        for name in ("BAAI/bge-m3", "a/9", "x" * 100, "org/-x-", "A/B/C d"):
            assert re.fullmatch(r"[a-z][a-z0-9_]{0,39}", model_key(name)), name


class TestProtocol:
    def test_the_hashing_embedder_satisfies_the_interface(self):
        assert isinstance(HashingEmbedder(), Embedder)

    def test_an_object_missing_a_method_does_not(self):
        class Half:
            key = "k"
            model_name = "m"
            dimension = 1

            def embed_documents(self, texts):
                return []

        assert not isinstance(Half(), Embedder)


def test_an_unknown_reranker_is_an_error():
    from bilingual_rag.embeddings.registry import make_reranker

    with pytest.raises(ValueError, match="unknown reranker"):
        make_reranker("bge-reranker-large")
