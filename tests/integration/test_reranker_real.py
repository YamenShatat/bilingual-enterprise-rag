"""The real bge-reranker-v2-m3: needs the weights (about 2.3 GB) and `pytest --slow`."""

import pytest

pytestmark = pytest.mark.slow

LEAVE_EN = "Full-time employees receive 21 working days of paid annual leave per year."
LEAVE_AR = "يستحق الموظفون بدوام كامل ٢١ يوم عمل من الإجازة السنوية المدفوعة الأجر."
VPN_EN = "Each employee may use the VPN on up to 2 devices at the same time."


@pytest.fixture(scope="module")
def reranker():
    from bilingual_rag.retrieval.cross_encoder import bge_reranker_v2_m3

    return bge_reranker_v2_m3()


def test_scores_are_probabilities(reranker):
    scores = reranker.score("annual leave", [LEAVE_EN, VPN_EN])
    assert all(0.0 <= s <= 1.0 for s in scores)


@pytest.mark.parametrize(
    "question",
    [
        "How many days of paid annual leave do employees get?",
        "كم يوم إجازة سنوية مدفوعة يحصل عليها الموظف؟",
    ],
)
def test_the_answer_outranks_an_unrelated_text_in_either_language(reranker, question):
    en, ar, vpn = reranker.score(question, [LEAVE_EN, LEAVE_AR, VPN_EN])
    assert min(en, ar) > 0.5 > vpn


def test_a_missing_detail_of_a_real_topic_scores_low(reranker):
    (score,) = reranker.score("What relocation allowance do new employees receive?", [LEAVE_EN])
    assert score < 0.05
