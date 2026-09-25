"""Small builders for database tests: valid metadata and chunks with consistent offsets."""

from dataclasses import replace

from bilingual_rag.ingestion.manifest import DocumentMetadata
from bilingual_rag.ingestion.models import Chunk


def make_metadata(**overrides) -> DocumentMetadata:
    fields = {
        "id": "hr-leave-en",
        "path": "hr/leave_en.md",
        "title": "Leave Policy",
        "department": "hr",
        "language": "en",
        "format": "md",
        "access_level": "employee",
        "topic": "leave",
    }
    return DocumentMetadata(**(fields | overrides))


def make_chunks(path: str, texts: list[str], *, page: int = 1) -> list[Chunk]:
    """Chunks of one page laid end to end, so text == page_text[start:end] holds."""
    chunks, position = [], 0
    for index, text in enumerate(texts):
        chunks.append(Chunk(path, page, index, text, position, position + len(text)))
        position += len(text)
    return chunks


def with_text(chunk: Chunk, text: str) -> Chunk:
    """The same chunk with different text and a matching end offset."""
    return replace(chunk, text=text, end=chunk.start + len(text))
