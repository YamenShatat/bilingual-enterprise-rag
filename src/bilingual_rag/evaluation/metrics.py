"""Recall@k and MRR, computed from the rank of the expected document in a result list."""

from collections.abc import Sequence


def document_rank(retrieved_document_ids: Sequence[str], expected_document_id: str) -> int | None:
    """1-based rank of the first occurrence of ``expected_document_id``, or None if absent."""
    for position, document_id in enumerate(retrieved_document_ids, start=1):
        if document_id == expected_document_id:
            return position
    return None


def recall_at_k(ranks: Sequence[int | None], k: int) -> float:
    """Share of ``ranks`` that are within the top ``k`` (rank is not None and <= k).

    Raises:
        ValueError: ``ranks`` is empty, or ``k`` is not positive.
    """
    if not ranks:
        raise ValueError("no ranks to average")
    if k < 1:
        raise ValueError(f"k must be positive, got {k}")
    hits = sum(1 for rank in ranks if rank is not None and rank <= k)
    return hits / len(ranks)


def mean_reciprocal_rank(ranks: Sequence[int | None]) -> float:
    """Average of 1/rank (0 for a question whose rank is None: not found).

    Raises:
        ValueError: ``ranks`` is empty.
    """
    if not ranks:
        raise ValueError("no ranks to average")
    return sum(0.0 if rank is None else 1.0 / rank for rank in ranks) / len(ranks)
