"""Compose the ingestion stages: load -> clean -> chunk.

Chunk offsets (``start``/``end``) refer to the *cleaned* page text, not the raw file,
because cleaning can change line endings and whitespace.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from bilingual_rag.ingestion.chunker import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_document
from bilingual_rag.ingestion.cleaner import clean_document
from bilingual_rag.ingestion.loaders import (
    DocumentLoadError,
    UnsupportedFileTypeError,
    load_document,
)
from bilingual_rag.ingestion.models import Chunk


@dataclass(frozen=True, slots=True)
class SkippedFile:
    """A file that produced no chunks, and why."""

    filename: str  # relative to the ingested directory, with "/" separators
    reason: str


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Chunks from every readable file, plus the files that were skipped."""

    chunks: tuple[Chunk, ...]
    skipped: tuple[SkippedFile, ...]


def _ingest(path: Path, filename: str, chunk_size: int, overlap: int) -> list[Chunk]:
    document = replace(load_document(path), filename=filename)
    return chunk_document(clean_document(document), chunk_size=chunk_size, overlap=overlap)


def ingest_file(
    path: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Load, clean and chunk one file. Loader errors propagate to the caller."""
    return _ingest(path, path.name, chunk_size, overlap)


def ingest_directory(
    root: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> IngestionResult:
    """Ingest every file under ``root`` (recursively) in a deterministic order.

    Chunk filenames are paths relative to ``root`` so that files with the same name in
    different folders stay distinct in citations. A file that cannot be ingested is
    recorded in ``skipped`` instead of aborting the run.

    Raises:
        NotADirectoryError: ``root`` is not a directory.
    """
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")

    chunks: list[Chunk] = []
    skipped: list[SkippedFile] = []
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in files:
        filename = path.relative_to(root).as_posix()
        try:
            file_chunks = _ingest(path, filename, chunk_size, overlap)
        except (UnsupportedFileTypeError, DocumentLoadError) as exc:
            skipped.append(SkippedFile(filename, str(exc)))
            continue
        if not file_chunks:
            skipped.append(SkippedFile(filename, "no text after cleaning"))
            continue
        chunks.extend(file_chunks)
    return IngestionResult(chunks=tuple(chunks), skipped=tuple(skipped))
