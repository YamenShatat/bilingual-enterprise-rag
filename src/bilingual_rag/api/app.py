"""FastAPI application: POST /auth/token, GET /auth/me, POST /query, GET /documents,
POST /documents, GET /health.

Run it (from the repository root, database and Ollama running)::

    uvicorn bilingual_rag.api.app:create_app --factory

Every endpoint but /health and /auth/token needs a bearer token from /auth/token (D-024). The
token only names the user: their role and access levels are read from the database on every
request, and the access levels go straight to the search, which filters in SQL (D-015). A request
can never choose its own levels. Only admins may upload.

Endpoints are plain ``def`` functions: FastAPI runs them in a thread pool, which suits the
blocking database driver and the multi-second LLM call.
"""

import tempfile
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field

from bilingual_rag.auth.tokens import TokenError, issue_token, read_token
from bilingual_rag.auth.users import User, authenticate, get_user
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
from bilingual_rag.embeddings.registry import make_reranker
from bilingual_rag.generation.answer import ask
from bilingual_rag.generation.llm import LLM, GenerationError, OllamaLLM
from bilingual_rag.ingestion.loaders import DocumentLoadError
from bilingual_rag.ingestion.manifest import FORMATS, DocumentMetadata, ManifestError
from bilingual_rag.ingestion.pipeline import ingest_file
from bilingual_rag.retrieval.rerank import Reranker

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_QUESTION_CHARS = 2000


@dataclass(frozen=True, slots=True)
class Services:
    """Everything the endpoints need, built once at startup."""

    database: DatabaseSettings
    api: ApiSettings
    embedder: Embedder
    llm: LLM
    reranker: Reranker | None = None


def default_services() -> Services:
    """The real configuration: ``.env`` plus the environment, bge-m3 (16-bit on CUDA),
    bge-reranker-v2-m3 and qwen3:8b. 16-bit keeps all three inside 8 GB of GPU memory, which
    measured 39% faster per question than with bge-m3 in 32-bit (D-023)."""
    from bilingual_rag.embeddings.sentence_transformer import bge_m3

    return Services(
        database=load_database_settings(),
        api=load_api_settings(),
        embedder=bge_m3(half=True),
        llm=OllamaLLM(),
        reranker=make_reranker("bge-reranker-v2-m3"),
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


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class MeResponse(BaseModel):
    username: str
    role: str
    access_levels: list[str]


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
    bearer = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)
    unauthorized = HTTPException(
        401, "a valid bearer token is required", headers={"WWW-Authenticate": "Bearer"}
    )

    def current_user(svc: Svc, conn: Conn, token: Annotated[str | None, Depends(bearer)]) -> User:
        """The token's user, read from the database now: a deactivated or deleted user is
        refused even with a token that has not expired."""
        try:  # no token at all fails here too: read_token(None) raises TokenError
            user_id = read_token(token, svc.api.jwt_secret)
        except TokenError:
            raise unauthorized from None
        user = get_user(conn, user_id)
        if user is None or not user.active:
            raise unauthorized
        return user

    def current_admin(user: Annotated[User, Depends(current_user)]) -> User:
        if not user.is_admin:
            raise HTTPException(403, "only an admin may do this")
        return user

    CurrentUser = Annotated[User, Depends(current_user)]

    @app.post("/auth/token", response_model=TokenResponse)
    def login(
        svc: Svc, conn: Conn, form: Annotated[OAuth2PasswordRequestForm, Depends()]
    ) -> TokenResponse:
        """Exchange a username and password for a bearer token. A wrong password and an
        unknown username get the same answer."""
        user = authenticate(conn, form.username, form.password)
        if user is None:
            raise HTTPException(
                401, "wrong username or password", headers={"WWW-Authenticate": "Bearer"}
            )
        ttl = svc.api.token_ttl_seconds
        token = issue_token(user.id, svc.api.jwt_secret, ttl_seconds=ttl)
        return TokenResponse(access_token=token, token_type="bearer", expires_in=ttl)

    @app.get("/auth/me", response_model=MeResponse)
    def me(user: CurrentUser) -> MeResponse:
        return MeResponse(
            username=user.username, role=user.role, access_levels=list(user.access_levels)
        )

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
        reranker = svc.reranker.model_name if svc.reranker is not None else None
        try:
            svc.llm.check()
        except GenerationError as exc:
            llm.update(ok=False, detail=str(exc))
        body = {
            "status": "ok" if database["ok"] and llm["ok"] else "degraded",
            "database": database,
            "llm": llm,
            "reranker": reranker,
        }
        if not database["ok"]:
            return JSONResponse(body, status_code=503)
        return body

    @app.post("/query", response_model=QueryResponse)
    def query(body: QueryRequest, svc: Svc, conn: Conn, user: CurrentUser) -> QueryResponse:
        try:
            answer = ask(
                conn,
                svc.embedder,
                svc.llm,
                body.question,
                user.access_levels,
                k=body.k,
                reranker=svc.reranker,
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
    def documents(conn: Conn, user: CurrentUser) -> list[DocumentOut]:
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
            for d in list_documents(conn, user.access_levels)
        ]

    @app.post(
        "/documents",
        status_code=201,
        response_model=UploadResponse,
        dependencies=[Depends(current_admin)],
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
