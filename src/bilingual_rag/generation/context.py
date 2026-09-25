"""Turn search results into numbered evidence for the LLM, within a character budget.

Each result becomes one source, numbered from 1 in rank order::

    [1] hr-annual-leave-policy-en | page 1 | Annual Leave Policy
    <the chunk text, exactly as stored>

The model cites a source by its number; the answer step maps the numbers back to documents and
pages through ``Context.sources``. A number is easier for a model to copy correctly than a long
document id, and one that is not in the list is plainly invalid rather than a plausible id.

Access control happened before this point: ``search()`` returns only chunks the caller may read
(D-015), and this module never adds a chunk, only drops ones that do not fit. Chunk text is not
trusted: a chunk could contain a line that looks like a source header. That can at worst make
the model attribute text to the wrong one of the caller's own sources; it cannot reveal
anything the caller may not read.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from bilingual_rag.retrieval.search import SearchResult

# qwen3:8b measured 0.466 tokens/char at worst on the corpus (Arabic; English at most 0.348).
# 12,000 chars is at most about 5,600 tokens, leaving about 2,500 of the 8,192-token window
# (DEFAULT_NUM_CTX) for the instructions, the question and the answer. See D-018.
DEFAULT_MAX_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class Source:
    """One numbered piece of evidence and where it came from."""

    number: int
    chunk_id: str
    document_id: str
    title: str
    page: int
    score: float
    text: str


@dataclass(frozen=True, slots=True)
class Context:
    """The evidence block for the prompt and the sources it numbers, in the same order."""

    text: str
    sources: tuple[Source, ...]


def _format(source: Source) -> str:
    header = f"[{source.number}] {source.document_id} | page {source.page} | {source.title}"
    return f"{header}\n{source.text}"


def build_context(
    results: Sequence[SearchResult], *, max_chars: int = DEFAULT_MAX_CHARS
) -> Context:
    """Number ``results`` in the order given and join them into one evidence block.

    Sources are added whole, in rank order, until the next one would take the block past
    ``max_chars``; it and everything after it are left out. A chunk is never cut short, because
    half a sentence of evidence can mean something the document does not say. No results give
    an empty context, which the answer step treats as no evidence.

    Raises:
        ValueError: ``max_chars`` is not positive, or is too small for even the top result.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError(f"max_chars must be a positive integer, got {max_chars!r}")
    blocks: list[str] = []
    sources: list[Source] = []
    used = 0
    for result in results:
        chunk = result.chunk
        source = Source(
            number=len(sources) + 1,
            chunk_id=chunk.id,
            document_id=chunk.document.id,
            title=chunk.document.title,
            page=chunk.page,
            score=result.score,
            text=chunk.text,
        )
        block = _format(source)
        cost = len(block) + (2 if blocks else 0)  # the blank line between blocks
        if used + cost > max_chars:
            if not blocks:
                raise ValueError(
                    f"max_chars={max_chars} cannot hold the top result ({len(block)} chars)"
                )
            break
        blocks.append(block)
        sources.append(source)
        used += cost
    return Context(text="\n\n".join(blocks), sources=tuple(sources))
