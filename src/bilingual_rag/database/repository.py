"""Store and read documents and chunks.

Nothing here commits: the caller owns the transaction, so one ingestion run is all-or-nothing
and tests can roll back. Chunk text is only ever read through ``list_chunks``, and document
lists through ``list_documents``; both require the caller's allowed access levels, so
unauthorized content cannot be fetched by forgetting a filter. Deny by default: an empty
collection of levels returns nothing. ``get_document`` is unfiltered and meant for trusted
ingestion code (checking whether an id exists), never for a response to a caller.
"""

import hashlib
import json
from collections.abc import Collection, Iterable
from dataclasses import astuple, dataclass
from typing import Literal

import psycopg

from bilingual_rag.ingestion.manifest import DocumentMetadata
from bilingual_rag.ingestion.models import Chunk

StoreOutcome = Literal["inserted", "unchanged", "updated"]

_DOCUMENT_COLUMNS = (
    "id, path, title, department, language, format, access_level, topic, pair, digits"
)


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """A chunk as read back, with the metadata of the document it belongs to."""

    id: str
    document: DocumentMetadata
    page: int
    index: int
    text: str
    start: int
    end: int


def chunk_id(document_id: str, page: int, index: int) -> str:
    """The stable id of a chunk, for example ``hr-annual-leave-policy-ar-p1-c0``."""
    return f"{document_id}-p{page}-c{index}"


def chunks_hash(chunks: Iterable[Chunk]) -> str:
    """A digest of what a document's chunks say, independent of the order they are given in.

    It covers page, index, offsets and text, so a change to the chunker's settings or to the
    document changes it, while a metadata-only edit does not.
    """
    ordered = sorted(chunks, key=lambda chunk: (chunk.page, chunk.index))
    payload = json.dumps(
        [[c.page, c.index, c.start, c.end, c.text] for c in ordered],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _insert_chunks(conn: psycopg.Connection, document_id: str, chunks: list[Chunk]) -> None:
    rows = [
        (
            chunk_id(document_id, chunk.page, chunk.index),
            document_id,
            chunk.page,
            chunk.index,
            chunk.text,
            chunk.start,
            chunk.end,
        )
        for chunk in chunks
    ]
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO chunks (id, document_id, page, chunk_index, text, start_offset,"
            " end_offset) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )


def store_document(
    conn: psycopg.Connection, metadata: DocumentMetadata, chunks: Iterable[Chunk]
) -> StoreOutcome:
    """Insert or update one document and its chunks.

    Metadata is always brought up to date, so a changed access level takes effect on the next
    ingestion. Chunks are replaced (and their embeddings with them) only when their hash
    changed. Returns ``"inserted"``, ``"unchanged"`` (nothing was written) or ``"updated"``.

    Raises:
        ValueError: there are no chunks, or a chunk's filename is not the document's path.
        psycopg.errors.IntegrityError: the path belongs to another document, or a chunk breaks
            a database constraint (for example a duplicate page and index).
    """
    chunk_list = list(chunks)
    if not chunk_list:
        raise ValueError(f"{metadata.id}: a document needs at least one chunk")
    for chunk in chunk_list:
        if chunk.filename != metadata.path:
            raise ValueError(
                f"chunk filename {chunk.filename!r} does not match the document path "
                f"{metadata.path!r}"
            )
    content_hash = chunks_hash(chunk_list)

    row = conn.execute(
        f"SELECT {_DOCUMENT_COLUMNS}, content_hash FROM documents WHERE id = %s FOR UPDATE",
        (metadata.id,),
    ).fetchone()
    if row is None:
        conn.execute(
            f"INSERT INTO documents ({_DOCUMENT_COLUMNS}, content_hash)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (*astuple(metadata), content_hash),
        )
        _insert_chunks(conn, metadata.id, chunk_list)
        return "inserted"

    *stored_metadata, stored_hash = row
    metadata_changed = tuple(stored_metadata) != astuple(metadata)
    chunks_changed = stored_hash != content_hash
    if not metadata_changed and not chunks_changed:
        return "unchanged"

    conn.execute(
        "UPDATE documents SET path = %s, title = %s, department = %s, language = %s,"
        " format = %s, access_level = %s, topic = %s, pair = %s, digits = %s,"
        " content_hash = %s, updated_at = now() WHERE id = %s",
        (*astuple(metadata)[1:], content_hash, metadata.id),
    )
    if chunks_changed:
        conn.execute("DELETE FROM chunks WHERE document_id = %s", (metadata.id,))
        _insert_chunks(conn, metadata.id, chunk_list)
    return "updated"


def delete_document(conn: psycopg.Connection, document_id: str) -> bool:
    """Delete a document with its chunks and embeddings. True if it existed."""
    cursor = conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))
    return cursor.rowcount == 1


def get_document(conn: psycopg.Connection, document_id: str) -> DocumentMetadata | None:
    """The metadata of one document (never its text), or None."""
    row = conn.execute(
        f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE id = %s", (document_id,)
    ).fetchone()
    return None if row is None else DocumentMetadata(*row)


def _levels(allowed_access_levels: Collection[str]) -> list[str]:
    if isinstance(allowed_access_levels, str):
        raise TypeError("allowed_access_levels must be a collection of levels, not one string")
    return sorted(set(allowed_access_levels))


def list_documents(
    conn: psycopg.Connection, allowed_access_levels: Collection[str]
) -> list[DocumentMetadata]:
    """The metadata (never the text) of the documents the caller may read, ordered by path.

    Titles can be sensitive too ("2027 layoffs plan"), so this is filtered exactly like
    ``list_chunks``: ``allowed_access_levels`` is required, an empty collection returns nothing,
    and the filter is part of the SQL statement.

    Raises:
        TypeError: ``allowed_access_levels`` is a single string.
    """
    levels = _levels(allowed_access_levels)
    if not levels:
        return []
    rows = conn.execute(
        f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE access_level = ANY(%s)"
        ' ORDER BY path COLLATE "C"',
        (levels,),
    ).fetchall()
    return [DocumentMetadata(*row) for row in rows]


def list_chunks(
    conn: psycopg.Connection,
    allowed_access_levels: Collection[str],
    *,
    document_id: str | None = None,
    language: str | None = None,
) -> list[StoredChunk]:
    """Chunks the caller may read, in document path, page and index order.

    ``allowed_access_levels`` is required and has no default: only chunks of documents whose
    access level is in it are returned, and an empty collection returns nothing. The filter is
    part of the SQL statement, so restricted text never leaves the database.

    Raises:
        TypeError: ``allowed_access_levels`` is a single string (which would be read as a
            collection of letters and silently match nothing, or the wrong thing).
    """
    levels = _levels(allowed_access_levels)
    if not levels:
        return []

    query = (
        "SELECT c.id, c.page, c.chunk_index, c.text, c.start_offset, c.end_offset,"
        " d.id, d.path, d.title, d.department, d.language, d.format, d.access_level,"
        " d.topic, d.pair, d.digits"
        " FROM chunks c JOIN documents d ON d.id = c.document_id"
        " WHERE d.access_level = ANY(%s)"
    )
    params: list[object] = [levels]
    if document_id is not None:
        query += " AND d.id = %s"
        params.append(document_id)
    if language is not None:
        query += " AND d.language = %s"
        params.append(language)
    query += ' ORDER BY d.path COLLATE "C", c.page, c.chunk_index'

    return [
        StoredChunk(
            id=row[0],
            page=row[1],
            index=row[2],
            text=row[3],
            start=row[4],
            end=row[5],
            document=DocumentMetadata(*row[6:]),
        )
        for row in conn.execute(query, params).fetchall()
    ]
