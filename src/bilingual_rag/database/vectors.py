"""Convert between Python number sequences and pgvector's text form, ``[1,2,3]``.

Vectors are sent as text and cast in SQL (``%s::vector``), which needs no driver adapter and
works on a connection opened before the extension exists. pgvector stores single precision,
so values are rounded to float32 on the way in.
"""

import math
from collections.abc import Sequence


def format_vector(values: Sequence[float]) -> str:
    """Render numbers as a pgvector literal.

    Raises:
        ValueError: the vector is empty or holds a NaN or infinity, which pgvector rejects.
    """
    numbers = [float(value) for value in values]
    if not numbers:
        raise ValueError("a vector needs at least one value")
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError("vector values must be finite (no NaN or infinity)")
    return "[" + ",".join(repr(number) for number in numbers) + "]"


def parse_vector(text: str) -> list[float]:
    """Read a pgvector literal such as ``[0.5,1,-2]``.

    Raises:
        ValueError: the text is not a bracketed, comma-separated list of numbers.
    """
    stripped = text.strip()
    if len(stripped) < 3 or stripped[0] != "[" or stripped[-1] != "]":
        raise ValueError(f"not a vector literal: {text!r}")
    return [float(part) for part in stripped[1:-1].split(",")]
