import pytest

from bilingual_rag.retrieval.hybrid import RRF_K, reciprocal_rank_fusion
from support.samples import make_result


def ranking(*indexes):
    return [make_result(f"text {i}", index=i) for i in indexes]


def ids(fused):
    return [chunk_id.rsplit("-c", 1)[1] for chunk_id, _ in fused]


def test_a_single_ranking_keeps_its_order():
    assert ids(reciprocal_rank_fusion([ranking(3, 1, 2)])) == ["3", "1", "2"]


def test_scores_are_one_over_k_plus_rank():
    fused = dict(reciprocal_rank_fusion([ranking(0, 1)]))
    assert fused["hr-leave-en-p1-c0"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["hr-leave-en-p1-c1"] == pytest.approx(1 / (RRF_K + 2))


def test_a_chunk_in_both_rankings_sums_its_contributions():
    fused = dict(reciprocal_rank_fusion([ranking(0, 1), ranking(1)]))
    assert fused["hr-leave-en-p1-c1"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))


def test_found_by_both_beats_first_in_one():
    # The property that hurts cross-lingual questions (D-022): 2nd + 1st beats 1st alone.
    assert ids(reciprocal_rank_fusion([ranking(0, 1), ranking(1)])) == ["1", "0"]


def test_ties_keep_the_first_rankings_order():
    # 5 and 0 are both first in one list: the earlier list (vector search) wins the tie, even
    # though "...-c0" would sort first by id.
    assert ids(reciprocal_rank_fusion([ranking(5), ranking(0)])) == ["5", "0"]


def test_a_duplicate_within_one_ranking_counts_once_at_its_best_position():
    fused = dict(reciprocal_rank_fusion([ranking(0, 1, 0)]))
    assert fused["hr-leave-en-p1-c0"] == pytest.approx(1 / (RRF_K + 1))


def test_empty_rankings_give_nothing():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_the_constant_is_the_papers():
    assert RRF_K == 60


def test_a_custom_constant_is_used():
    fused = dict(reciprocal_rank_fusion([ranking(0)], rrf_k=0))
    assert fused["hr-leave-en-p1-c0"] == pytest.approx(1.0)
