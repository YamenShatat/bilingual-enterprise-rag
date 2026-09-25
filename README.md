# Bilingual Enterprise RAG

A bilingual (Arabic / English) knowledge assistant for enterprise documents, built from first principles:
hybrid retrieval, reranking, grounded answers with citations, document-level access control and a
measured evaluation, served through a FastAPI backend.

> **Status: early development (Week 2 of 8 — PostgreSQL + pgvector, embeddings).** Nothing below is implemented yet
> unless it is listed under [Current progress](#current-progress).

## Goals

- Ingest Arabic and English documents and preserve Arabic text correctly end to end.
- Retrieve across languages: Arabic question → English document, and English question → Arabic document.
- Combine vector and keyword search, then rerank the candidates.
- Answer only from retrieved evidence, cite document and page, and refuse unsupported questions.
- Enforce document permissions **before** any text reaches the LLM.
- Report measured retrieval and generation results — no numbers without a benchmark run.

## Planned architecture

```text
Upload → Parser → Cleaner → Chunker → Metadata → Embeddings → PostgreSQL + pgvector

Question → Query processing → Hybrid retrieval (vector + keyword)
         → Metadata & permission filtering → Reranker → Context builder
         → LLM → Citation validation → Grounded answer + sources
```

## Technology (planned)

Python 3.12+, FastAPI, PostgreSQL + pgvector, multilingual embedding models (to be benchmarked),
Ollama for local LLMs, Streamlit, pytest, Docker, GitHub Actions.
Each choice will be recorded, with the alternatives considered, in `docs/decisions.md`.

## Dataset

**Synthetic dataset created for demonstration purposes. No confidential company data is included.**
The corpus describes a fictional company, *Acme MENA Technology*. See [`data/README.md`](data/README.md).

## Current progress

- [x] Repository, license, Python project configuration, test and lint tooling
- [x] TXT / Markdown loading, Arabic-safe cleaning, chunking and an ingestion pipeline
- [x] PDF loader (Arabic extraction has known limits, see [`docs/pdf-extraction.md`](docs/pdf-extraction.md))
- [x] DOCX loader (exact Arabic text; page numbers are approximate, see [`docs/decisions.md`](docs/decisions.md) D-009)
- [x] Synthetic corpus: 32 bilingual documents in four formats with a manifest (see [`docs/dataset.md`](docs/dataset.md))
- [x] PostgreSQL + pgvector in Docker (database only), settings from environment variables, health check (see [`docs/decisions.md`](docs/decisions.md) D-011)
- [ ] Database schema, embeddings and vector search
- [ ] Retrieval evaluation
- [ ] RAG with citations
- [ ] FastAPI backend
- [ ] Hybrid search and reranking
- [ ] UI, authentication and permissions
- [ ] Docker and CI/CD

## Benchmark results

TBD — benchmark not run yet.

## Development setup (Windows / PowerShell)

Requires Python 3.12 or newer. Keep the path to the virtual environment short: the PDF
dependency (`pypdfium2`) ships deeply nested files and fails to install when the total path
exceeds Windows' 260-character limit.

```powershell
git clone https://github.com/YamenShatat/bilingual-enterprise-rag.git
cd bilingual-enterprise-rag
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Run the checks:

```powershell
pytest
ruff check .
ruff format --check .
```

### Database

PostgreSQL with pgvector runs in Docker (the app itself does not, yet). Docker Desktop is
proprietary software, so check that its license terms cover your use; any Docker engine works.

```powershell
Copy-Item .env.example .env        # then edit POSTGRES_PASSWORD (letters and digits only)
docker compose up -d --wait db     # starts pgvector/pgvector:0.8.6-pg17 on 127.0.0.1:5432
python scripts/check_database.py   # connects, checks UTF-8, runs a pgvector distance query
```

`.env` is ignored by Git. PostgreSQL reads the password only when it first creates the data
volume, so editing it afterwards has no effect on an existing database. To start over with a new
password run `docker compose down -v`, which **deletes the data**.

Tests that need the database are skipped when it is not running, with the reason shown
(`pytest -rs`). Set `RAG_REQUIRE_DATABASE=1` to make them fail instead, as CI should.

## License

[MIT](LICENSE)
