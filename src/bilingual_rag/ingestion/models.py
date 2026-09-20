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
class Document:
    """A loaded source document, before cleaning or chunking."""

    filename: str
    pages: tuple[Page, ...]
