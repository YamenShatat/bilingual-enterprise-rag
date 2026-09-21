"""The embedding interface and the small helpers every implementation shares.

An embedder must embed exactly the text it is given. Any model-specific prefix (for example
``query: `` and ``passage: `` for the E5 models) is added inside the embedder, never by the
caller, and no Arabic normalization happens here: that is a Week 3 experiment (D-004).
"""

import math
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

_KEY_LENGTH = 40  # the longest key the database accepts


class EmbeddingError(Exception):
    """An embedder returned something unusable (wrong count, wrong size, non-finite values)."""


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into fixed-size vectors.

    ``key`` names the model's table in the database (see ``model_key``), ``model_name`` is the
    full model name, and ``dimension`` is the length of every vector. Vectors have unit length,
    so cosine distance and inner product order results identically.
    """

    key: str
    model_name: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the same order. Texts are embedded exactly as given."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """The vector of a search query (models with a query prefix add it here)."""
        ...


def normalize(values: Sequence[float]) -> list[float]:
    """Scale a vector to unit length.

    Raises:
        ValueError: the vector is empty, holds a NaN or infinity, or has zero length.
    """
    numbers = [float(value) for value in values]
    if not numbers:
        raise ValueError("cannot normalize an empty vector")
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError("cannot normalize a vector with NaN or infinite values")
    length = math.hypot(*numbers)  # hypot rescales internally, so huge values do not overflow
    if length == 0.0:
        raise ValueError("cannot normalize a zero vector")
    return [number / length for number in numbers]


def validate_vectors(
    vectors: Sequence[Sequence[float]], *, expected_count: int, dimension: int
) -> None:
    """Check what an embedder returned before it is stored.

    Without the count check, a model that returned too few vectors would silently leave chunks
    unembedded (or attach a vector to the wrong chunk).

    Raises:
        EmbeddingError: the count or a length is wrong, or a value is not finite.
    """
    if len(vectors) != expected_count:
        raise EmbeddingError(f"expected {expected_count} vectors, got {len(vectors)}")
    for position, vector in enumerate(vectors):
        if len(vector) != dimension:
            raise EmbeddingError(
                f"vector {position} has {len(vector)} values, expected {dimension}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingError(f"vector {position} contains NaN or infinite values")


def model_key(model_name: str) -> str:
    """A database-safe key for a model name: ``BAAI/bge-m3`` becomes ``bge_m3``.

    Uses the part after the last ``/``, lowercased, with runs of other characters turned into
    one underscore. Two models whose names end the same way would collide, so implementations
    accept an explicit key.

    Raises:
        ValueError: nothing usable is left of the name.
    """
    tail = model_name.rsplit("/", 1)[-1].lower()
    key = re.sub(r"[^a-z0-9]+", "_", tail).strip("_")
    if not key:
        raise ValueError(f"cannot derive a key from {model_name!r}")
    if not key[0].isalpha():
        key = f"m_{key}"
    return key[:_KEY_LENGTH].rstrip("_")
