import pytest

from bilingual_rag.evaluation.answers import AnswerOutcome, reply_language, summarize_answers
from bilingual_rag.evaluation.questions import EvaluationQuestion
from bilingual_rag.generation.answer import LOW_SCORE, MODEL_REFUSED, Answer, Citation
from support.arabic import ARABIC_TRUTH


class TestReplyLanguage:
    def test_english(self):
        assert reply_language("Employees get 21 days [1].") == "en"

    def test_arabic(self):
        assert reply_language(f"{ARABIC_TRUTH[1]} [1]") == "ar"

    def test_arabic_with_a_few_latin_words_is_arabic(self):
        assert reply_language(ARABIC_TRUTH[4]) == "ar"  # contains "VPN" and "Cisco AnyConnect"

    def test_digits_and_markers_are_ignored(self):
        assert reply_language("21 [1][2].") is None
        assert reply_language("") is None

    def test_exactly_half_arabic_letters_counts_as_arabic(self):
        assert reply_language("ab" + ARABIC_TRUTH[0][:2]) == "ar"


def question(qid, language="en", category="same_language", expected="doc-a"):
    if category == "absent_fact":
        expected = None
    return EvaluationQuestion(qid, language, category, f"question {qid}", expected)


def answered(text, *document_ids):
    citations = tuple(Citation(i + 1, d, "T", 1, f"{d}-p1-c0") for i, d in enumerate(document_ids))
    return Answer(text, citations, None, 0.6)


def refused(reason):
    return Answer("", (), reason, 0.4)


@pytest.fixture
def outcomes():
    return [
        AnswerOutcome(question("q1"), answered("21 days [1].", "doc-a")),
        AnswerOutcome(question("q2"), answered("21 days [1].", "doc-b")),  # wrong document
        AnswerOutcome(question("q3", "ar"), answered("Answered in English [1].", "doc-a")),
        AnswerOutcome(
            question("q4", "ar", "cross_lingual"),
            answered(f"{ARABIC_TRUTH[1]} [1]", "doc-b", "doc-a"),
        ),
        AnswerOutcome(question("q5", category="near_miss"), refused(MODEL_REFUSED)),
        AnswerOutcome(question("q6", category="absent_fact"), refused(MODEL_REFUSED)),
        AnswerOutcome(question("q7", "ar", "absent_fact"), refused(LOW_SCORE)),
        AnswerOutcome(question("q8", category="absent_fact"), answered("A guess [1].", "doc-a")),
    ]


def test_overall_counts_only_answerable_questions(outcomes):
    assert summarize_answers(outcomes)["overall"] == {
        "count": 5,
        "answered": 4,
        "cites_expected": 3,  # q1, q3, q4 (q4 cites it second)
        "right_language": 3,  # q3 is Arabic answered in English
        "refusals": {MODEL_REFUSED: 1},
    }


def test_absent_fact_questions_are_summarized_separately(outcomes):
    absent = summarize_answers(outcomes)["absent_fact"]
    assert absent["count"] == 3
    assert absent["answered"] == 1
    assert absent["refusals"] == {MODEL_REFUSED: 1, LOW_SCORE: 1}


def test_breakdowns_by_language_and_category(outcomes):
    summary = summarize_answers(outcomes)
    assert summary["language:ar"]["count"] == 2
    assert summary["language:ar"]["right_language"] == 1
    assert summary["language:en"]["count"] == 3
    assert summary["category:cross_lingual"]["cites_expected"] == 1
    assert summary["category:near_miss"]["answered"] == 0
    assert "category:absent_fact" not in summary


def test_a_refused_question_never_counts_as_citing_the_expected_document():
    outcome = AnswerOutcome(question("q1"), Answer("21 days [1]", (), "uncited", 0.6))
    assert summarize_answers([outcome])["overall"]["cites_expected"] == 0


def test_no_absent_questions_means_no_absent_block(outcomes):
    assert "absent_fact" not in summarize_answers(outcomes[:5])
