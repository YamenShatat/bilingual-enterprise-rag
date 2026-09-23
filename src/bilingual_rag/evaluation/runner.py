"""Run the question set through `search()` and summarize Recall@k and MRR."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

import psycopg

from bilingual_rag.embeddings.base import Embedder
from bilingual_rag.evaluation.metrics import document_rank, mean_reciprocal_rank, recall_at_k
from bilingual_rag.evaluation.questions import EvaluationQuestion
from bilingual_rag.retrieval.search import search

DEFAULT_K_VALUES = (1, 3, 5)
# Chunks are fetched at document granularity (a question is "answered" by a document, not a
# specific chunk), so this must exceed max(DEFAULT_K_VALUES) by enough that a document with
# several high-scoring chunks cannot crowd out other documents before Recall@5 is measured.
# The corpus has 46 chunks across 32 documents, so 20 is generous, not a real cost.
CHUNK_FETCH_K = 20


@dataclass(frozen=True, slots=True)
class QuestionOutcome:
    """One question's result. ``rank`` is the expected document's 1-based rank, or None if
    it was not found in the top ``CHUNK_FETCH_K`` chunks, or the question has no expected
    document (``absent_fact``). ``top_score`` is the top result's score, or None if search
    returned nothing."""

    question: EvaluationQuestion
    rank: int | None
    top_score: float | None


def run_question(
    conn: psycopg.Connection,
    embedder: Embedder,
    question: EvaluationQuestion,
    allowed_access_levels: Collection[str],
) -> QuestionOutcome:
    results = search(conn, embedder, question.question, allowed_access_levels, k=CHUNK_FETCH_K)
    ranked_document_ids: list[str] = []
    for result in results:
        if result.chunk.document.id not in ranked_document_ids:
            ranked_document_ids.append(result.chunk.document.id)
    rank = (
        None
        if question.expected_document_id is None
        else document_rank(ranked_document_ids, question.expected_document_id)
    )
    return QuestionOutcome(
        question=question, rank=rank, top_score=results[0].score if results else None
    )


def run_questions(
    conn: psycopg.Connection,
    embedder: Embedder,
    questions: Sequence[EvaluationQuestion],
    allowed_access_levels: Collection[str],
) -> list[QuestionOutcome]:
    return [run_question(conn, embedder, q, allowed_access_levels) for q in questions]


def _block(outcomes: Sequence[QuestionOutcome], k_values: Sequence[int]) -> dict:
    ranks = [o.rank for o in outcomes]
    return {
        "count": len(outcomes),
        **{f"recall@{k}": recall_at_k(ranks, k) for k in k_values},
        "mrr": mean_reciprocal_rank(ranks),
    }


def summarize(
    outcomes: Sequence[QuestionOutcome], k_values: Sequence[int] = DEFAULT_K_VALUES
) -> dict:
    """Recall@k and MRR overall and broken down by language and category, over the
    questions that have an expected document. ``absent_fact`` questions (no expected
    document) are summarized separately, by their top score alone."""
    answerable = [o for o in outcomes if o.question.expected_document_id is not None]
    absent = [o for o in outcomes if o.question.expected_document_id is None]
    if not answerable:
        raise ValueError("no answerable questions (every question is absent_fact)")

    summary = {"overall": _block(answerable, k_values)}
    for language in sorted({o.question.language for o in answerable}):
        group = [o for o in answerable if o.question.language == language]
        summary[f"language:{language}"] = _block(group, k_values)
    for category in sorted({o.question.category for o in answerable}):
        group = [o for o in answerable if o.question.category == category]
        summary[f"category:{category}"] = _block(group, k_values)

    if absent:
        scores = [o.top_score for o in absent if o.top_score is not None]
        summary["absent_fact"] = {
            "count": len(absent),
            "mean_top_score": sum(scores) / len(scores) if scores else None,
        }
    return summary
