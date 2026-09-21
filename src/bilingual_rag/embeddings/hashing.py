"""A deterministic stand-in embedder for tests and offline demos. It is NOT semantic.

Each word is hashed to a position in the vector (feature hashing), so two texts that share
words have similar vectors and unrelated texts do not. That is enough to test storage, ranking
and access control without a model, and it is reproducible across machines and processes
(SHA-256, not Python's per-process ``hash``). It cannot match a question in one language to a
document in another and knows nothing about meaning, so it must never be used to judge
retrieval quality.

Text is lowercased and split on whitespace, and leading and trailing punctuation and symbols are
stripped. Nothing else is normalized: an Arabic word with and without a diacritic hashes to two
different positions, exactly as the pipeline keeps them apart (D-004).
"""

import hashlib
import unicodedata
from collections.abc import Sequence

from bilingual_rag.embeddings.base import normalize

MAX_DIMENSION = 16000  # the same limit as the database column


def _is_edge(character: str) -> bool:
    return unicodedata.category(character)[0] in "PS"


def _words(text: str) -> list[str]:
    words = []
    for raw in text.casefold().split():
        start, end = 0, len(raw)
        while start < end and _is_edge(raw[start]):
            start += 1
        while end > start and _is_edge(raw[end - 1]):
            end -= 1
        if start < end:
            words.append(raw[start:end])
    return words


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


class HashingEmbedder:
    """Feature-hashing embedder of a fixed dimension (default 256)."""

    def __init__(self, dimension: int = 256):
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise ValueError(f"dimension must be an integer, got {dimension!r}")
        if not 1 <= dimension <= MAX_DIMENSION:
            raise ValueError(f"dimension must be between 1 and {MAX_DIMENSION}, got {dimension}")
        self.dimension = dimension
        self.key = f"hashing_{dimension}"
        self.model_name = f"hashing-bag-of-words-{dimension}"

    def _embed(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise TypeError(f"text must be a string, got {type(text).__name__}")
        if not text.strip():
            raise ValueError("text must not be blank")
        vector = [0.0] * self.dimension
        # A text of only punctuation has no words; hash the text itself so it still gets a vector.
        for word in _words(text) or [text.strip()]:
            digest = _digest(word)
            position = int.from_bytes(digest[:8], "big") % self.dimension
            vector[position] += 1.0 if digest[8] & 1 else -1.0
        if not any(vector):  # colliding words with opposite signs cancelled out exactly
            vector[int.from_bytes(_digest(text)[:8], "big") % self.dimension] = 1.0
        return normalize(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, str):
            raise TypeError("texts must be a sequence of strings, not one string")
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
