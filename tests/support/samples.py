"""Small builders for tests: valid metadata, chunks with consistent offsets, search results."""

from dataclasses import replace

from bilingual_rag.database.repository import StoredChunk, chunk_id
from bilingual_rag.ingestion.manifest import DocumentMetadata
from bilingual_rag.ingestion.models import Chunk
from bilingual_rag.retrieval.search import SearchResult


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


def make_result(text: str, *, document=None, page: int = 1, index: int = 0, score: float = 0.7):
    """A search result for one chunk of ``document`` (default: ``make_metadata()``)."""
    document = document or make_metadata()
    chunk = StoredChunk(
        id=chunk_id(document.id, page, index),
        document=document,
        page=page,
        index=index,
        text=text,
        start=0,
        end=len(text),
    )
    return SearchResult(chunk=chunk, score=score)
