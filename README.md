# Bilingual Enterprise RAG

A bilingual (Arabic / English) knowledge assistant for enterprise documents, built from first principles:
hybrid retrieval, reranking, grounded answers with citations, document-level access control and a
measured evaluation, served through a FastAPI backend.

> **Status: early development (Week 3 of 8 — retrieval evaluation).**
> Nothing below is implemented yet unless it is listed under [Current progress](#current-progress).

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

Python 3.12+, FastAPI, PostgreSQL + pgvector, multilingual embedding models (bge-m3, chosen by
measurement in D-016), Ollama for local LLMs (qwen3:8b, D-017), Streamlit, pytest, Docker,
GitHub Actions.
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
- [x] Database schema: versioned migrations, documents and chunks with manifest metadata, one embedding table per model, access-level-filtered reads (see [`docs/decisions.md`](docs/decisions.md) D-012)
- [x] Embedder interface, a deterministic stand-in embedder and batched embedding of stored chunks (see D-013; the stand-in is lexical and says nothing about retrieval quality)
- [x] Chunk sizes measured in each candidate model's real tokenizer (see D-006 addendum): `mpnet` truncates 78% of chunks at 128 tokens, `bge-m3` and `multilingual-e5-large` do not
- [x] Real embedder (BAAI/bge-m3 via `sentence-transformers`, CUDA), gated behind `pytest --slow`; a qualitative cross-lingual check over the real corpus (see [`docs/decisions.md`](docs/decisions.md) D-014 — not a benchmark)
- [x] `scripts/ingest_documents.py` and `search()`: question → embedding → pgvector → relevant chunks, access-level filtering in SQL — **Week 2's goal** (see D-015; the cross-lingual retrieval example is qualitative, not a benchmark)
- [x] Retrieval evaluation: 50 questions (25 EN, 25 AR), Recall@k, MRR, bge-m3 vs multilingual-e5-large, two chunk sizes (see [`docs/decisions.md`](docs/decisions.md) D-016 — **Week 3's goal**)
- [x] Local LLM client for Ollama (`qwen3:8b`), standard library only, context window set explicitly (see [`docs/decisions.md`](docs/decisions.md) D-017)
- [x] Context builder: numbered, whole-chunk evidence within a character budget measured in the LLM's own tokenizer (see [`docs/decisions.md`](docs/decisions.md) D-018)
- [x] Grounded answers with citations, or a refusal the system (not the model) enforces at four gates; measured on the 50 questions, including a prompt-injection comparison (see [`docs/decisions.md`](docs/decisions.md) D-019 — **Week 4's goal**)
- [x] FastAPI backend: `POST /query`, `GET /documents`, `POST /documents` (admin key), `GET /health`; access levels come from the server, never the request (see [`docs/decisions.md`](docs/decisions.md) D-020 — **Week 5's goal**)
- [ ] Hybrid search and reranking
- [ ] UI, authentication and permissions
- [ ] Docker and CI/CD

## Benchmark results

Week 3 retrieval evaluation, 50 questions (25 EN, 25 AR), 1200/200 chunking, full detail
and caveats in [`docs/decisions.md`](docs/decisions.md) D-016:

| | bge-m3 | multilingual-e5-large |
| --- | --- | --- |
| Overall Recall@1 / @3 / @5 | 0.826 / 1.000 / 1.000 | 0.804 / 0.891 / 0.913 |
| Overall MRR | 0.913 | 0.863 |
| Cross-lingual Recall@1 / MRR | 0.917 / 0.958 | 0.333 / 0.517 |

`multilingual-e5-large` is perfect within one language on this corpus but far weaker
cross-lingually, the retrieval direction the corpus was built to test. **This is 50
questions over 46 chunks — a small, directional result, not a claim about either model in
general.** `paraphrase-multilingual-mpnet-base-v2` was not evaluated: it truncates 78% of
this corpus's chunks (D-006 addendum).

Week 4 answer evaluation, the same 50 questions through `ask()` (bge-m3 retrieval, qwen3:8b
answers), full detail in D-019:

| | Count |
| --- | --- |
| Answerable questions answered | 44 of 46 |
| Answers citing the document known to hold the fact | 42 of 46 |
| Answers in the question's language | 44 of 44 |
| Deliberately unanswerable questions refused | 4 of 4 |

**Answer correctness is not scored** (the questions have no reference answers); every miss and
nine answers were checked by hand against the source text. One answer stated a correct fact
with the wrong citation, and a planted document that cites itself can still mislead the model
(a known limitation, D-019).

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
python scripts/migrate_database.py # creates or updates the tables; safe to run repeatedly
```

The schema lives in versioned SQL files under `src/bilingual_rag/database/migrations/`. Never
edit a migration that has been applied (the runner refuses); add a new numbered file instead.

`.env` is ignored by Git. PostgreSQL reads the password only when it first creates the data
volume, so editing it afterwards has no effect on an existing database. To start over with a new
password run `docker compose down -v`, which **deletes the data**.

Tests that need the database are skipped when it is not running, with the reason shown
(`pytest -rs`). Set `RAG_REQUIRE_DATABASE=1` to make them fail instead, as CI should. They
create and drop their own `rag_test_*` databases, so your development data is never touched.

### The real embedding model

`HashingEmbedder` (lexical, deterministic, no download) is enough for the fast test suite. The
real model, `BAAI/bge-m3` through `sentence-transformers`, needs a multi-gigabyte download and
is gated behind `pytest --slow`. On Windows with an NVIDIA GPU, install PyTorch's CUDA build
**before** the `embeddings` extra, or pip may fetch a CPU-only wheel from the default index:

```powershell
python -m pip install torch --index-url https://download.pytorch.org/whl/cu130
python -m pip install -e ".[dev,embeddings]"
pytest --slow -m slow    # downloads the bge-m3 weights (MIT) on first run, then reuses them
```

Without a CUDA GPU, skip the `--index-url` line; `torch` will install a CPU build and the
wrapper falls back to it automatically. Slow tests are excluded from a plain `pytest` run.

### Ingesting the corpus and searching it

```powershell
python scripts/ingest_documents.py                     # bge-m3: stores and embeds all 32 documents
python scripts/ingest_documents.py --embedder e5-large  # a second model, same 46 chunks
python scripts/ingest_documents.py --embedder hashing   # the deterministic stand-in, no download
```

Safe to run again: an unchanged document is skipped, and only chunks with no embedding yet
for the chosen model are embedded. Two models can be embedded over the same chunks and kept
side by side (each gets its own table; see [`docs/decisions.md`](docs/decisions.md) D-012).

```python
from bilingual_rag.config import load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.embeddings.sentence_transformer import bge_m3
from bilingual_rag.retrieval.search import search

settings = load_database_settings()
with connect(settings) as conn:
    results = search(
        conn,
        bge_m3(),
        "How many days of annual leave do I get?",
        allowed_access_levels={"public", "employee"},
        k=3,
    )
    for r in results:
        print(r.score, r.chunk.document.id, r.chunk.text[:80])
```

`allowed_access_levels` is required, with no default: an empty collection returns nothing,
and the filter runs inside the SQL query, so a caller can never see a chunk from a document
above their access level (D-012, D-015).

### Evaluating retrieval

`data/evaluation_questions.json` holds 50 fixed questions with an expected document each (or
none, for four deliberately unanswerable ones). One embedder and one chunking per run; run it
again to compare another model or chunk size (results: D-016).

```powershell
python scripts/evaluate_retrieval.py --embedder bge-m3
python scripts/evaluate_retrieval.py --embedder e5-large --chunk-size 600 --overlap 100
```

### Asking questions (needs Ollama)

Install [Ollama](https://ollama.com) and pull the model (`ollama pull qwen3:8b`, about 5.2 GB),
then ingest the corpus with bge-m3 as above.

```python
from bilingual_rag.config import load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.embeddings.sentence_transformer import bge_m3
from bilingual_rag.generation.answer import ask
from bilingual_rag.generation.llm import OllamaLLM

with connect(load_database_settings()) as conn:
    answer = ask(
        conn,
        bge_m3(),
        OllamaLLM(),
        "كم يوم إجازة سنوية مدفوعة يحصل عليها الموظف؟",
        allowed_access_levels={"public", "employee"},
    )
    print(answer.refusal or answer.text)
    for c in answer.citations:
        print(c.number, c.document_id, "page", c.page)
```

`answer.refusal` is None for an answer, or says which gate refused: `no_results`,
`low_score`, `model_refused` or `uncited` (D-019). To measure answering over the 50
questions and save every answer for reading:

```powershell
python scripts/evaluate_answers.py --output answers.json
```

### Running the API (needs Ollama)

```powershell
pip install -e ".[embeddings,api]"
uvicorn bilingual_rag.api.app:create_app --factory --port 8000
```

Interactive documentation is served at `http://127.0.0.1:8000/docs`. Two settings in `.env`
(see `.env.example`) control what callers may do until real users arrive in Week 7:

- `API_ACCESS_LEVELS` (default `public`): the access levels every caller gets. A request cannot
  send its own; an unknown field such as `access_levels` is rejected with 422.
- `ADMIN_API_KEY` (at least 16 characters): required as the `X-Admin-Key` header to upload.
  Leave it empty and uploads are disabled.

```powershell
# ask (PowerShell 5.1: pass UTF-8 bytes so Arabic survives)
$body = [Text.Encoding]::UTF8.GetBytes('{"question": "How many days of annual leave do I get?"}')
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/query -ContentType "application/json" -Body $body

# list the documents you may read
Invoke-RestMethod http://127.0.0.1:8000/documents
```

Uploads take a multipart form with the file (md, txt, docx or pdf, up to 10 MB) and the
metadata fields `id`, `title`, `department`, `language`, `access_level`, `topic` and, for
Arabic, `digits`. The client's filename is never stored; the document is chunked, stored and
embedded at once, and an existing id is refused (409).

## License

[MIT](LICENSE)
