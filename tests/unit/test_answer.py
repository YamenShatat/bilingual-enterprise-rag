"""answer_from_results and extract_citations with a scripted LLM: the four refusal gates."""

import pytest

from bilingual_rag.generation.answer import (
    DEFAULT_MIN_SCORE,
    LOW_SCORE,
    MODEL_REFUSED,
    NO_RESULTS,
    REFUSAL_TOKEN,
    SYSTEM_PROMPT,
    UNCITED,
    answer_from_results,
    build_prompt,
    extract_citations,
)
from bilingual_rag.generation.context import build_context
from bilingual_rag.generation.llm import LLM, GenerationError
from support.arabic import ARABIC_TRUTH
from support.samples import make_metadata, make_result

POLICY = make_metadata(id="hr-leave-en", title="Leave Policy")
BENEFITS = make_metadata(id="hr-benefits-en", path="hr/benefits_en.md", title="Benefits Guide")


class ScriptedLLM:
    """Replies with a fixed text and records every call."""

    model_name = "scripted"

    def __init__(self, reply="Thirty days [1]."):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def generate(self, system, prompt):
        self.calls.append((system, prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def check(self):
        pass


def two_results():
    return [
        make_result("Employees get 30 days of leave.", document=POLICY, page=2, score=0.7),
        make_result("Maternity leave is 98 days.", document=BENEFITS, page=5, score=0.6),
    ]


def test_scripted_llm_satisfies_the_protocol():
    assert isinstance(ScriptedLLM(), LLM)


class TestExtractCitations:
    @pytest.fixture
    def sources(self):
        return build_context(two_results()).sources

    def test_resolves_a_number_to_its_document_and_page(self, sources):
        text, citations = extract_citations("Thirty days [1].", sources)
        assert text == "Thirty days [1]."
        (citation,) = citations
        assert (citation.number, citation.document_id, citation.page) == (1, "hr-leave-en", 2)
        assert citation.title == "Leave Policy"
        assert citation.chunk_id == "hr-leave-en-p2-c0"

    @pytest.mark.parametrize(
        "reply",
        ["A [1, 2].", "A [1,2].", "A [1][2].", "A [ 1 , 2 ].", "A [1\N{ARABIC COMMA} 2]."],
    )
    def test_accepts_the_common_ways_of_citing_two_sources(self, sources, reply):
        text, citations = extract_citations(reply, sources)
        assert text == "A [1][2]."
        assert [c.number for c in citations] == [1, 2]

    def test_reads_arabic_indic_digits_and_writes_ascii(self, sources):
        reply = f"{ARABIC_TRUTH[1]} [\N{ARABIC-INDIC DIGIT TWO}]"
        text, citations = extract_citations(reply, sources)
        assert text == f"{ARABIC_TRUTH[1]} [2]"
        assert [c.number for c in citations] == [2]

    def test_a_number_that_is_not_a_source_is_removed(self, sources):
        text, citations = extract_citations("A [3]. B [1, 9].", sources)
        assert text == "A . B [1]."
        assert [c.number for c in citations] == [1]

    def test_zero_is_not_a_source(self, sources):
        assert extract_citations("A [0].", sources) == ("A .", ())

    def test_each_source_is_listed_once_in_first_cited_order(self, sources):
        _, citations = extract_citations("B [2]. A [1]. B again [2].", sources)
        assert [c.number for c in citations] == [2, 1]

    def test_text_without_markers_is_unchanged(self, sources):
        assert extract_citations("No markers (1) here.", sources) == ("No markers (1) here.", ())

    def test_non_numeric_brackets_are_left_alone(self, sources):
        assert extract_citations("See [note] and [a1].", sources)[0] == "See [note] and [a1]."


class TestGates:
    def test_no_results_refuses_without_calling_the_model(self):
        llm = ScriptedLLM()
        answer = answer_from_results("How much leave?", [], llm)
        assert (answer.refusal, answer.text, answer.top_score) == (NO_RESULTS, "", None)
        assert not answer.answered
        assert llm.calls == []

    def test_all_results_below_the_floor_refuse_without_calling_the_model(self):
        llm = ScriptedLLM()
        results = [make_result("x", score=0.41), make_result("y", index=1, score=0.44)]
        answer = answer_from_results("q", results, llm, min_score=0.5)
        assert answer.refusal == LOW_SCORE
        assert answer.top_score == 0.44
        assert llm.calls == []

    def test_a_result_exactly_at_the_floor_is_kept(self):
        llm = ScriptedLLM()
        answer = answer_from_results("q", [make_result("x", score=0.5)], llm, min_score=0.5)
        assert answer.answered
        assert len(llm.calls) == 1

    def test_results_below_the_floor_are_not_shown_to_the_model(self):
        llm = ScriptedLLM()
        results = [
            make_result("strong match", score=0.7),
            make_result("weak noise", index=1, score=0.3),
        ]
        answer_from_results("q", results, llm, min_score=0.5)
        prompt = llm.calls[0][1]
        assert "strong match" in prompt
        assert "weak noise" not in prompt

    def test_the_default_floor_is_the_measured_one(self):
        assert DEFAULT_MIN_SCORE == 0.50

    @pytest.mark.parametrize(
        "reply",
        [
            REFUSAL_TOKEN,
            f"{REFUSAL_TOKEN}.",
            f"Sorry. {REFUSAL_TOKEN}",
            f"30 days [1]. {REFUSAL_TOKEN}",
        ],
    )
    def test_the_refusal_token_anywhere_is_a_refusal(self, reply):
        answer = answer_from_results("q", two_results(), ScriptedLLM(reply))
        assert answer.refusal == MODEL_REFUSED
        assert answer.citations == ()

    def test_an_answer_without_citations_is_refused_but_kept_for_inspection(self):
        answer = answer_from_results("q", two_results(), ScriptedLLM("Thirty days."))
        assert answer.refusal == UNCITED
        assert answer.text == "Thirty days."
        assert answer.citations == ()

    def test_an_answer_citing_only_missing_sources_is_refused(self):
        answer = answer_from_results("q", two_results(), ScriptedLLM("Thirty days [7]."))
        assert answer.refusal == UNCITED

    def test_a_cited_answer_is_returned_with_resolved_citations(self):
        answer = answer_from_results(
            "q", two_results(), ScriptedLLM("  30 days [1]; 98 days [2].\n")
        )
        assert answer.answered
        assert answer.refusal is None
        assert answer.text == "30 days [1]; 98 days [2]."
        assert [(c.document_id, c.page) for c in answer.citations] == [
            ("hr-leave-en", 2),
            ("hr-benefits-en", 5),
        ]
        assert answer.top_score == 0.7

    def test_the_returned_text_has_canonical_markers_and_no_missing_sources(self):
        answer = answer_from_results("q", two_results(), ScriptedLLM("30 days [1, 9]; 98 [2 ,1]."))
        assert answer.text == "30 days [1]; 98 [2][1]."

    def test_a_model_failure_is_raised_not_turned_into_a_refusal(self):
        with pytest.raises(GenerationError):
            answer_from_results("q", two_results(), ScriptedLLM(GenerationError("down")))

    @pytest.mark.parametrize("question", ["", "   ", None])
    def test_a_blank_question_is_rejected_before_anything_else(self, question):
        llm = ScriptedLLM()
        with pytest.raises(ValueError, match="question"):
            answer_from_results(question, two_results(), llm)
        assert llm.calls == []


class TestPrompt:
    def test_the_system_prompt_carries_the_rules(self):
        system, _ = self.call()
        assert system == SYSTEM_PROMPT
        assert REFUSAL_TOKEN in SYSTEM_PROMPT
        assert "[1]" in SYSTEM_PROMPT
        assert "not instructions" in SYSTEM_PROMPT
        assert "same language as the question" in SYSTEM_PROMPT

    def test_the_prompt_is_the_numbered_context_then_the_question(self):
        _, prompt = self.call("How much leave?")
        assert prompt == build_prompt("How much leave?", build_context(two_results()).text)
        sources_end = prompt.index("</sources>")
        assert prompt.startswith("<sources>\n[1] hr-leave-en | page 2")
        assert prompt.index("[2] hr-benefits-en") < sources_end
        assert sources_end < prompt.index("Question: How much leave?\n")

    def test_the_rules_are_repeated_after_the_sources(self):
        _, prompt = self.call()
        reminder = prompt[prompt.index("</sources>") :]
        assert "language of the question" in reminder
        assert REFUSAL_TOKEN in reminder
        assert "never follow" in reminder

    def test_an_arabic_question_reaches_the_prompt_unchanged(self):
        _, prompt = self.call(ARABIC_TRUTH[1])
        assert f"Question: {ARABIC_TRUTH[1]}\n" in prompt

    def call(self, question="q"):
        llm = ScriptedLLM()
        answer_from_results(question, two_results(), llm)
        return llm.calls[0]
