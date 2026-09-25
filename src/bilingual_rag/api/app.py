"""FastAPI application: POST /query, GET /documents, POST /documents, GET /health.

Run it (from the repository root, database and Ollama running)::

    uvicorn bilingual_rag.api.app:create_app --factory

Permissions come from the server, never the request (D-020): every caller gets
``API_ACCESS_LEVELS`` (default ``public``) until Week 7 adds real users. Uploads need the
``X-Admin-Key`` header to match ``ADMIN_API_KEY``, and are disabled when it is unset.

Endpoints are plain ``def`` functions: FastAPI runs them in a thread pool, which suits the
blocking database driver and the multi-second LLM call.
"""

import secrets
import tempfile
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from bilingual_rag.config import (
    ApiSettings,
    DatabaseSettings,
    load_api_settings,
    load_database_settings,
)
from bilingual_rag.database.connection import connect
from bilingual_rag.database.embedding_store import EmbeddingModelError
from bilingual_rag.database.health import check_health
from bilingual_rag.database.repository import get_document, list_documents, store_document
from bilingual_rag.embeddings.base import Embedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.embeddings.registry import make_embedder
from bilingual_rag.generation.answer import ask
from bilingual_rag.generation.llm import LLM, GenerationError, OllamaLLM
from bilingual_rag.ingestion.loaders import DocumentLoadError
from bilingual_rag.ingestion.manifest import FORMATS, DocumentMetadata, ManifestError
from bilingual_rag.ingestion.pipeline import ingest_file

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_QUESTION_CHARS = 2000


@dataclass(frozen=True, slots=True)
class Services:
    """Everything the endpoints need, built once at startup."""

    database: DatabaseSettings
    api: ApiSettings
    embedder: Embedder
    llm: LLM


def default_services() -> Services:
    """The real configuration: ``.env`` plus the environment, bge-m3 and qwen3:8b."""
    return Services(
        database=load_database_settings(),
        api=load_api_settings(),
        embedder=make_embedder("bge-m3"),
        llm=OllamaLLM(),
    )


class QueryRequest(BaseModel):
    # Unknown fields are an error, not ignored: a client sending "access_levels" learns at once
    # that permissions are not the request's to choose.
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    k: int = Field(default=5, ge=1, le=20)


class CitationOut(BaseModel):
    number: int
    document_id: str
    title: str
    page: int


class QueryResponse(BaseModel):
    """``answer`` is null exactly when ``refusal`` is set (D-019's four reasons)."""

    answer: str | None
    refusal: str | None
    citations: list[CitationOut]
    top_score: float | None


class DocumentOut(BaseModel):
    """A document's metadata. The storage path is left out: it says nothing a caller needs."""

    id: str
    title: str
    department: str
    language: str
    format: str
    access_level: str
    topic: str


class UploadResponse(BaseModel):
    id: str
    chunks: int
    embedded: int


def create_app(services_factory: Callable[[], Services] = default_services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services_factory()
        yield

    app = FastAPI(title="Bilingual Enterprise RAG", version="0.1.0", lifespan=lifespan)

    def services(request: Request) -> Services:
        return request.app.state.services

    def connection(svc: Annotated[Services, Depends(services)]) -> Iterator[psycopg.Connection]:
        try:
            conn = connect(svc.database)
        except psycopg.OperationalError as exc:
            raise HTTPException(503, "the database is unavailable") from exc
        with conn:  # commits on success, rolls back on an exception
            yield conn

    Svc = Annotated[Services, Depends(services)]
    Conn = Annotated[psycopg.Connection, Depends(connection)]

    @app.get("/health")
    def health(svc: Svc):
        """200 while the database works (documents can be listed); 503 when it does not. The
        LLM is reported but does not fail the check: without it only /query is affected."""
        database = {"ok": False, "detail": None}
        try:
            with connect(svc.database, connect_timeout=2) as conn:
                report = check_health(conn)
            database = {"ok": report.ok, "detail": report.vector_error}
        except psycopg.OperationalError:
            database["detail"] = "unreachable"
        llm = {"ok": True, "model": svc.llm.model_name, "detail": None}
        try:
            svc.llm.check()
        except GenerationError as exc:
            llm.update(ok=False, detail=str(exc))
        body = {
            "status": "ok" if database["ok"] and llm["ok"] else "degraded",
            "database": database,
            "llm": llm,
        }
        if not database["ok"]:
            return JSONResponse(body, status_code=503)
        return body

    @app.post("/query", response_model=QueryResponse)
    def query(body: QueryRequest, svc: Svc, conn: Conn) -> QueryResponse:
        try:
            answer = ask(
                conn, svc.embedder, svc.llm, body.question, svc.api.access_levels, k=body.k
            )
        except EmbeddingModelError as exc:
            raise HTTPException(503, "no documents have been embedded yet") from exc
        except GenerationError as exc:
            raise HTTPException(503, f"the language model is unavailable: {exc}") from exc
        except ValueError as exc:  # a blank question after trimming, for example
            raise HTTPException(422, str(exc)) from exc
        return QueryResponse(
            answer=answer.text if answer.answered else None,
            refusal=answer.refusal,
            citations=[
                CitationOut(number=c.number, document_id=c.document_id, title=c.title, page=c.page)
                for c in answer.citations
            ],
            top_score=answer.top_score,
        )

    @app.get("/documents", response_model=list[DocumentOut])
    def documents(svc: Svc, conn: Conn) -> list[DocumentOut]:
        return [
            DocumentOut(
                id=d.id,
                title=d.title,
                department=d.department,
                language=d.language,
                format=d.format,
                access_level=d.access_level,
                topic=d.topic,
            )
            for d in list_documents(conn, svc.api.access_levels)
        ]

    def require_admin(svc: Svc, x_admin_key: Annotated[str | None, Header()] = None) -> None:
        expected = svc.api.admin_api_key
        if expected is None:
            raise HTTPException(403, "uploads are disabled (ADMIN_API_KEY is not set)")
        if x_admin_key is None or not secrets.compare_digest(
            x_admin_key.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(401, "a valid X-Admin-Key header is required")

    @app.post(
        "/documents",
        status_code=201,
        response_model=UploadResponse,
        dependencies=[Depends(require_admin)],
    )
    def upload(
        svc: Svc,
        conn: Conn,
        file: Annotated[UploadFile, File()],
        id: Annotated[str, Form()],
        title: Annotated[str, Form()],
        department: Annotated[str, Form()],
        language: Annotated[str, Form()],
        access_level: Annotated[str, Form()],
        topic: Annotated[str, Form()],
        digits: Annotated[str | None, Form()] = None,
    ) -> UploadResponse:
        # Only the extension of the client's filename is used; the name itself never touches
        # the filesystem or the database, so it cannot traverse paths.
        extension = Path(file.filename or "").suffix.lower().lstrip(".")
        if extension not in FORMATS:
            raise HTTPException(415, f"unsupported file type; expected one of {FORMATS}")
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"file is larger than {MAX_UPLOAD_BYTES} bytes")
        try:
            metadata = DocumentMetadata(
                id=id,
                path=f"uploads/{id}.{extension}",
                title=title,
                department=department,
                language=language,
                format=extension,
                access_level=access_level,
                topic=topic,
                digits=digits or None,
            )
        except ManifestError as exc:
            raise HTTPException(422, str(exc)) from exc
        if get_document(conn, metadata.id) is not None:
            raise HTTPException(409, f"a document with id {metadata.id!r} already exists")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / f"upload.{extension}"
            path.write_bytes(data)
            try:
                chunks = ingest_file(path)
            except DocumentLoadError as exc:
                raise HTTPException(422, f"the file could not be read: {exc}") from exc
        if not chunks:
            raise HTTPException(422, "the file contains no text")
        chunks = [replace(chunk, filename=metadata.path) for chunk in chunks]
        try:
            store_document(conn, metadata, chunks)
        except psycopg.errors.UniqueViolation as exc:  # a concurrent upload of the same id
            raise HTTPException(409, f"a document with id {metadata.id!r} already exists") from exc
        embedded = embed_missing(conn, svc.embedder)
        return UploadResponse(id=metadata.id, chunks=len(chunks), embedded=embedded)

    return app
