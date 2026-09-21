"""Split page text into overlapping, size-bounded chunks.

The splitter is recursive: it breaks text at the coarsest boundary that keeps every
piece within ``chunk_size`` (paragraph, then line, then sentence, then word) and only
falls back to a hard character cut for a single unbroken run that is too long. Pieces
are then packed greedily into chunks, and consecutive chunks share whole trailing
pieces as overlap.

Sizes are measured in characters, not tokens. The defaults are starting hypotheses,
not tuned values: they are meant to be compared against alternatives using the
retrieval evaluation, and must not be described as optimal before that.

Every chunk is an exact slice of the input, so no text is ever altered, and Arabic
letters are never separated from their diacritics by a hard cut.
"""

import re
import unicodedata

from bilingual_rag.ingestion.models import Chunk, Document

Span = tuple[int, int]  # (start, end) offsets into the text, end exclusive

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_OVERLAP = 200

# Coarse to fine. The sentence pattern recognises the Arabic question mark as well as
# the ASCII terminators; the last level splits on any whitespace, i.e. between words.
_SEPARATORS = (
    re.compile(r"\n\s*\n"),
    re.compile(r"\n"),
    re.compile("(?<=[.!?\N{ARABIC QUESTION MARK}])\\s+"),
    re.compile(r"\s+"),
)


def _trim(text: str, start: int, end: int) -> Span | None:
    """Shrink ``[start, end)`` to exclude surrounding whitespace; ``None`` if empty."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def _pieces(text: str, span: Span, separator: re.Pattern[str]) -> list[Span]:
    """Split ``span`` on ``separator``, dropping the separators and empty pieces."""
    start, end = span
    pieces: list[Span] = []
    cursor = start
    for match in separator.finditer(text, start, end):
        piece = _trim(text, cursor, match.start())
        if piece:
            pieces.append(piece)
        cursor = match.end()
    last = _trim(text, cursor, end)
    if last:
        pieces.append(last)
    return pieces


def _hard_cut(text: str, span: Span, chunk_size: int) -> list[Span]:
    """Cut an unbroken run into ``chunk_size`` pieces without orphaning combining marks."""
    start, end = span
    cuts: list[Span] = []
    while start < end:
        stop = min(start + chunk_size, end)
        # Step back so the next piece starts on a base character, not a diacritic.
        while start + 1 < stop < end and unicodedata.category(text[stop]) == "Mn":
            stop -= 1
        cuts.append((start, stop))
        start = stop
    return cuts


def _split(text: str, span: Span, chunk_size: int, level: int) -> list[Span]:
    """Break ``span`` into units of at most ``chunk_size`` using the coarsest separator."""
    start, end = span
    if end - start <= chunk_size:
        return [span]
    if level == len(_SEPARATORS):
        return _hard_cut(text, span, chunk_size)
    pieces = _pieces(text, span, _SEPARATORS[level])
    if len(pieces) <= 1:  # this separator does not occur; try a finer one
        return _split(text, span, chunk_size, level + 1)
    return [unit for piece in pieces for unit in _split(text, piece, chunk_size, level + 1)]


def _pack(units: list[Span], chunk_size: int, overlap: int) -> list[Span]:
    """Greedily merge consecutive units into chunks, repeating trailing units as overlap."""
    chunks: list[Span] = []
    first = 0
    while first < len(units):
        last = first
        while last + 1 < len(units) and units[last + 1][1] - units[first][0] <= chunk_size:
            last += 1
        chunks.append((units[first][0], units[last][1]))
        if last + 1 >= len(units):
            break
        # The next chunk starts with as many trailing units as fit in ``overlap`` while
        # still leaving room for the next new unit. Always at least one unit of progress.
        next_first = last + 1
        while (
            next_first - 1 > first
            and units[last][1] - units[next_first - 1][0] <= overlap
            and units[last + 1][1] - units[next_first - 1][0] <= chunk_size
        ):
            next_first -= 1
        first = next_first
    return chunks


def chunk_spans(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Span]:
    """Return ``(start, end)`` offsets of the chunks of ``text``.

    Raises:
        ValueError: ``chunk_size`` is not positive, or ``overlap`` is negative or
            not smaller than ``chunk_size``.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    if not 0 <= overlap < chunk_size:
        raise ValueError(f"overlap must be in [0, chunk_size), got {overlap}")
    whole = _trim(text, 0, len(text))
    if whole is None:
        return []
    return _pack(_split(text, whole, chunk_size, 0), chunk_size, overlap)


def chunk_document(
    document: Document,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Chunk every page of ``document``. Chunks never cross a page boundary."""
    chunks: list[Chunk] = []
    for page in document.pages:
        spans = chunk_spans(page.text, chunk_size=chunk_size, overlap=overlap)
        for index, (start, end) in enumerate(spans):
            chunks.append(
                Chunk(
                    filename=document.filename,
                    page=page.number,
                    index=index,
                    text=page.text[start:end],
                    start=start,
                    end=end,
                )
            )
    return chunks
