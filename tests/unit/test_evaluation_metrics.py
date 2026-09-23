import pytest

from bilingual_rag.evaluation.metrics import document_rank, mean_reciprocal_rank, recall_at_k


class TestDocumentRank:
    def test_finds_the_first_occurrence_one_based(self):
        assert document_rank(["a", "b", "c"], "b") == 2

    def test_a_repeated_document_uses_its_first_position(self):
        assert document_rank(["a", "b", "a"], "a") == 1

    def test_not_found_is_none(self):
        assert document_rank(["a", "b"], "z") is None

    def test_an_empty_list_is_none(self):
        assert document_rank([], "a") is None


class TestRecallAtK:
    def test_within_k_counts_as_a_hit(self):
        assert recall_at_k([1, 2, 3], k=3) == 1.0

    def test_a_rank_past_k_is_a_miss(self):
        assert recall_at_k([1, 5], k=3) == 0.5

    def test_none_is_always_a_miss(self):
        assert recall_at_k([1, None, None, None], k=5) == 0.25

    def test_a_larger_k_never_lowers_recall(self):
        ranks = [1, 4, None, 9]
        assert recall_at_k(ranks, k=10) >= recall_at_k(ranks, k=1)

    def test_rank_exactly_k_counts(self):
        assert recall_at_k([5], k=5) == 1.0

    def test_rank_one_past_k_does_not_count(self):
        assert recall_at_k([6], k=5) == 0.0

    def test_an_empty_list_is_an_error(self):
        with pytest.raises(ValueError, match="no ranks"):
            recall_at_k([], k=1)

    @pytest.mark.parametrize("k", [0, -1])
    def test_a_non_positive_k_is_an_error(self, k):
        with pytest.raises(ValueError, match="k must be positive"):
            recall_at_k([1], k=k)


class TestMeanReciprocalRank:
    def test_rank_one_gives_one(self):
        assert mean_reciprocal_rank([1]) == 1.0

    def test_averages_reciprocals(self):
        assert mean_reciprocal_rank([1, 4]) == pytest.approx((1 / 1 + 1 / 4) / 2)

    def test_none_contributes_zero(self):
        assert mean_reciprocal_rank([1, None]) == pytest.approx(0.5)

    def test_all_none_is_zero(self):
        assert mean_reciprocal_rank([None, None]) == 0.0

    def test_an_empty_list_is_an_error(self):
        with pytest.raises(ValueError, match="no ranks"):
            mean_reciprocal_rank([])
