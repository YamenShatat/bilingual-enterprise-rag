"""Reranking: reorder retrieved chunks by a model that reads the question and each chunk together.

A cross-encoder scores a (question, chunk) pair jointly, instead of comparing two separately
made vectors, so it can judge whether the chunk actually answers the question. It is too slow to
run over a whole corpus, so it reorders the candidates a retriever returns (D-023).

The model lives in ``retrieval.cross_encoder`` (it needs the "embeddings" extra); this module has
only the interface and the reordering, so it can be tested with a stub.
"""

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from bilingual_rag.retrieval.search import SearchResult


@runtime_checkable
class Reranker(Protocol):
    model_name: str

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """One relevance score per text, in the same order; higher is more relevant."""
        ...


def rerank(
    reranker: Reranker, query: str, results: Sequence[SearchResult], *, k: int | None = None
) -> list[SearchResult]:
    """``results`` ordered by the reranker, best first, each with ``rerank_score`` set; the
    top ``k`` if given. Equal scores keep the retriever's order.

    Raises:
        ValueError: the reranker returned the wrong number of scores, or ``k`` is not positive.
    """
    if k is not None and k < 1:
        raise ValueError(f"k must be positive, got {k}")
    if not results:
        return []
    scores = reranker.score(query, [result.chunk.text for result in results])
    if len(scores) != len(results):
        raise ValueError(f"the reranker returned {len(scores)} scores for {len(results)} texts")
    order = sorted(range(len(results)), key=lambda i: -scores[i])  # sorted() is stable
    reranked = [replace(results[i], rerank_score=float(scores[i])) for i in order]
    return reranked if k is None else reranked[:k]
