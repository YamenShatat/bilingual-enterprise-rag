"""Run the question set through `ask()` and summarize refusal, citation and language behaviour.

This measures what can be checked without a human grader: whether an answerable question is
answered, whether the answer cites the document known to hold the fact, whether it is in the
question's language, and whether an absent-fact question is refused. It does not measure whether
the answer's content is correct; the questions have no reference answers (D-019).
"""

import unicodedata
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass

import psycopg

from bilingual_rag.embeddings.base import Embedder
from bilingual_rag.evaluation.questions import EvaluationQuestion
from bilingual_rag.generation.answer import Answer, ask
from bilingual_rag.generation.llm import LLM
from bilingual_rag.retrieval.rerank import Reranker


@dataclass(frozen=True, slots=True)
class AnswerOutcome:
    question: EvaluationQuestion
    answer: Answer


def reply_language(text: str) -> str | None:
    """``"ar"`` if at least half the letters are Arabic, ``"en"`` otherwise, None with no letters.

    Letters only: digits, citation markers and punctuation say nothing about the language.
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    arabic = sum("ARABIC" in unicodedata.name(ch, "") for ch in letters)
    return "ar" if arabic * 2 >= len(letters) else "en"


def run_answer_questions(
    conn: psycopg.Connection,
    embedder: Embedder,
    llm: LLM,
    questions: Sequence[EvaluationQuestion],
    allowed_access_levels: Collection[str],
    *,
    reranker: Reranker | None = None,
) -> list[AnswerOutcome]:
    return [
        AnswerOutcome(
            q, ask(conn, embedder, llm, q.question, allowed_access_levels, reranker=reranker)
        )
        for q in questions
    ]


def _cites_expected(outcome: AnswerOutcome) -> bool:
    # A refusal never carries citations (answer_from_results), so it never counts.
    expected = outcome.question.expected_document_id
    return any(c.document_id == expected for c in outcome.answer.citations)


def _block(outcomes: Sequence[AnswerOutcome]) -> dict:
    answered = [o for o in outcomes if o.answer.answered]
    right_language = [o for o in answered if reply_language(o.answer.text) == o.question.language]
    return {
        "count": len(outcomes),
        "answered": len(answered),
        "cites_expected": sum(_cites_expected(o) for o in outcomes),
        "right_language": len(right_language),
        "refusals": dict(Counter(o.answer.refusal for o in outcomes if not o.answer.answered)),
    }


def summarize_answers(outcomes: Sequence[AnswerOutcome]) -> dict:
    """Counts overall, by language and by category for answerable questions, and separately for
    absent-fact questions (where a refusal is the right outcome). Counts, not rates: with this
    few questions, "3 of 4" says more than 0.75."""
    answerable = [o for o in outcomes if o.question.expected_document_id is not None]
    absent = [o for o in outcomes if o.question.expected_document_id is None]
    summary = {"overall": _block(answerable)}
    for language in sorted({o.question.language for o in answerable}):
        summary[f"language:{language}"] = _block(
            [o for o in answerable if o.question.language == language]
        )
    for category in sorted({o.question.category for o in answerable}):
        summary[f"category:{category}"] = _block(
            [o for o in answerable if o.question.category == category]
        )
    if absent:
        summary["absent_fact"] = _block(absent)
    return summary
