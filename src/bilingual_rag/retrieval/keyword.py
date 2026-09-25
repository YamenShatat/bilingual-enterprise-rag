"""Keyword search: PostgreSQL full-text search over the stemmed ``search_vector`` column (D-021).

The question's language is not known, so it is stemmed with both the Arabic and the English
stemmer and the resulting words are joined with OR: a chunk matches if it shares any stemmed
word with the question (each chunk was indexed with its own document's language, migration
0003). Ranking is ``ts_rank_cd``, which rewards more and closer matches but, unlike BM25, has no
IDF: a common word counts as much as a rare one. Hybrid search uses only the rank order.

Deny by default, exactly like ``search()``: ``allowed_access_levels`` is required, an empty
collection returns nothing, and the filter is part of the SQL statement.

Keyword search cannot match across languages; that is the vector search's job.
"""

from collections.abc import Collection

import psycopg

from bilingual_rag.database.repository import StoredChunk
from bilingual_rag.ingestion.manifest import DocumentMetadata
from bilingual_rag.retrieval.search import DEFAULT_K, SearchResult

# The question's stemmed words (both stemmers, duplicates removed), joined with OR. A question
# with no words left (only stopwords or punctuation) gives NULL, which matches nothing. Quoting
# each word is defence in depth: the parser already splits on every tsquery operator character
# (measured: "localhost:8000", "it's", URLs and paths build the same query quoted or not), but
# the question is untrusted, and a future dictionary could emit a word containing one.
_QUERY = """
WITH q AS (
    SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
    FROM (
        SELECT DISTINCT lexeme
        FROM unnest(to_tsvector('arabic', %(q)s) || to_tsvector('english', %(q)s))
    ) AS words
)
SELECT c.id, c.page, c.chunk_index, c.text, c.start_offset, c.end_offset,
       d.id, d.path, d.title, d.department, d.language, d.format, d.access_level,
       d.topic, d.pair, d.digits,
       ts_rank_cd(c.search_vector, q.query) AS score
FROM q
JOIN chunks c ON c.search_vector @@ q.query
JOIN documents d ON d.id = c.document_id
WHERE d.access_level = ANY(%(levels)s) {language_clause}
ORDER BY score DESC, c.id
LIMIT %(k)s
"""


def keyword_search(
    conn: psycopg.Connection,
    query: str,
    allowed_access_levels: Collection[str],
    *,
    k: int = DEFAULT_K,
    language: str | None = None,
) -> list[SearchResult]:
    """The ``k`` permitted chunks that best match ``query``'s words, best first.

    ``SearchResult.score`` here is ``ts_rank_cd``: higher is better, but it is not a cosine
    similarity and must not be compared with ``search()``'s scores or its refusal floor.

    Raises:
        TypeError: ``query`` is not a string, or ``allowed_access_levels`` is a single string.
        ValueError: ``query`` is blank, or ``k`` is not positive.
    """
    if not isinstance(query, str):
        raise TypeError(f"query must be a string, got {type(query).__name__}")
    if not query.strip():
        raise ValueError("query must not be blank")
    if isinstance(allowed_access_levels, str):
        raise TypeError("allowed_access_levels must be a collection of levels, not one string")
    if k < 1:
        raise ValueError(f"k must be positive, got {k}")
    levels = sorted(set(allowed_access_levels))
    if not levels:
        return []

    params: dict[str, object] = {"q": query, "levels": levels, "k": k}
    language_clause = ""
    if language is not None:
        language_clause = "AND d.language = %(language)s"
        params["language"] = language
    rows = conn.execute(_QUERY.format(language_clause=language_clause), params).fetchall()
    return [
        SearchResult(
            chunk=StoredChunk(
                id=row[0],
                page=row[1],
                index=row[2],
                text=row[3],
                start=row[4],
                end=row[5],
                document=DocumentMetadata(*row[6:16]),
            ),
            score=row[16],
        )
        for row in rows
    ]
