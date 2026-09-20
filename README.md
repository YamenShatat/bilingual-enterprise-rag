# Bilingual Enterprise RAG

A bilingual (Arabic / English) knowledge assistant for enterprise documents, built from first principles:
hybrid retrieval, reranking, grounded answers with citations, document-level access control and a
measured evaluation, served through a FastAPI backend.

> **Status: early development (Week 1 of 8 — project foundation).** Nothing below is implemented yet
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
- [ ] Document loading, cleaning and chunking (Arabic + English)
- [ ] Embeddings and vector search
- [ ] Retrieval evaluation
- [ ] RAG with citations
- [ ] FastAPI backend
- [ ] Hybrid search and reranking
- [ ] UI, authentication and permissions
- [ ] Docker and CI/CD

## Benchmark results

TBD — benchmark not run yet.

## Development setup (Windows / PowerShell)

Requires Python 3.12 or newer.

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

## License

[MIT](LICENSE)
