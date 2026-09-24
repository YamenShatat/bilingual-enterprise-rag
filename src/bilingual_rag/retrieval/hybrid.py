"""Hybrid retrieval: vector and keyword search fused with Reciprocal Rank Fusion (D-022).

RRF scores each chunk by its positions alone, ``1 / (RRF_K + rank)`` summed over the lists it
appears in, so the two incompatible score scales (cosine similarity, ``ts_rank_cd``) are never
mixed. A chunk found by both searches beats one found by either.

Every hybrid result carries its cosine similarity as ``score``, including chunks only the
keyword search found, so the answer step's refusal floor (D-019) keeps its meaning; only the
order comes from RRF.

``retrieve`` is the one entry point for all three modes, used by the evaluation and by
``ask()``. Both searches filter by access level in SQL; fusion only reorders what they return.
"""

from collections.abc import Collection, Sequence

import psycopg

from bilingual_rag.embeddings.base import Embedder
from bilingual_rag.retrieval.keyword import keyword_search
from bilingual_rag.retrieval.search import DEFAULT_K, SearchResult, search

MODES = ("vector", "keyword", "hybrid")
RRF_K = 60  # the constant from the original RRF paper (Cormack et al., 2009); not tuned here
DEFAULT_CANDIDATES = 20  # how deep each search looks before fusion


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[SearchResult]], *, rrf_k: int = RRF_K
) -> list[tuple[str, float]]:
    """Chunk ids with their fused scores, best first; ties go to the earlier ranking's order.

    A chunk listed twice in one ranking counts once, at its best position.
    """
    fused: dict[str, float] = {}
    first_seen: dict[str, tuple[int, int]] = {}
    for list_index, ranking in enumerate(rankings):
        seen_here: set[str] = set()
        for position, result in enumerate(ranking):
            chunk = result.chunk.id
            if chunk in seen_here:
                continue
            seen_here.add(chunk)
            fused[chunk] = fused.get(chunk, 0.0) + 1.0 / (rrf_k + position + 1)
            first_seen.setdefault(chunk, (list_index, position))
    return sorted(fused.items(), key=lambda item: (-item[1], first_seen[item[0]]))


def hybrid_search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    allowed_access_levels: Collection[str],
    *,
    k: int = DEFAULT_K,
    candidates: int = DEFAULT_CANDIDATES,
    language: str | None = None,
) -> list[SearchResult]:
    """The top ``k`` chunks by RRF over the top ``candidates`` of each search.

    Raises:
        ValueError: ``k`` or ``candidates`` is not positive; see also ``search`` and
            ``keyword_search``.
    """
    if k < 1 or candidates < 1:
        raise ValueError(f"k and candidates must be positive, got {k} and {candidates}")
    depth = max(k, candidates)
    vector = search(conn, embedder, query, allowed_access_levels, k=depth, language=language)
    keyword = keyword_search(conn, query, allowed_access_levels, k=depth, language=language)

    by_id = {result.chunk.id: result for result in vector}
    missing = [r.chunk.id for r in keyword if r.chunk.id not in by_id]
    if missing:
        # Keyword-only matches get their real cosine similarity. This embeds the query a second
        # time; ponytail: fine at this scale, pass the vector through if embedding gets slow.
        for result in search(
            conn,
            embedder,
            query,
            allowed_access_levels,
            k=len(missing),
            language=language,
            chunk_ids=missing,
        ):
            by_id[result.chunk.id] = result
    fused = reciprocal_rank_fusion([vector, keyword])
    return [by_id[chunk_id] for chunk_id, _ in fused[:k] if chunk_id in by_id]


def retrieve(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    allowed_access_levels: Collection[str],
    *,
    mode: str = "vector",
    k: int = DEFAULT_K,
    language: str | None = None,
) -> list[SearchResult]:
    """Search in one of ``MODES``. In ``keyword`` mode the embedder is not used, and ``score``
    is ``ts_rank_cd``, not a cosine similarity.

    Raises:
        ValueError: ``mode`` is not one of ``MODES``.
    """
    if mode == "vector":
        return search(conn, embedder, query, allowed_access_levels, k=k, language=language)
    if mode == "keyword":
        return keyword_search(conn, query, allowed_access_levels, k=k, language=language)
    if mode == "hybrid":
        return hybrid_search(conn, embedder, query, allowed_access_levels, k=k, language=language)
    raise ValueError(f"unknown mode {mode!r}, expected one of {MODES}")
