"""Data structures shared across the ingestion pipeline."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Page:
    """The text of one page of a source document.

    ``number`` is 1-based so it can be shown to users in citations as-is.
    Formats without real pages (TXT, Markdown) are represented as a single page.
    """

    number: int
    text: str


@dataclass(frozen=True, slots=True)
class Chunk:
    """A contiguous slice of one page, small enough to embed and retrieve.

    ``text`` is always exactly ``page_text[start:end]``, so offsets can be used to
    highlight the passage in its source. ``index`` is the 0-based position of the
    chunk within its page. A chunk never spans more than one page, so ``page`` is
    always a valid citation.
    """

    filename: str
    page: int
    index: int
    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Document:
    """A loaded source document, before cleaning or chunking."""

    filename: str
    pages: tuple[Page, ...]
