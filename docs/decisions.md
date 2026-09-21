# Architecture decisions

A running log of meaningful design choices, the alternatives considered, and how each
decision will be validated. Newest entries go at the bottom.

Status values: **Accepted** (in use), **Provisional** (in use, to be validated by measurement).

## D-001: `src` layout with `pyproject.toml`; no `requirements.txt`

- **Decision:** the package lives in `src/bilingual_rag/` and dependencies are declared in
  `pyproject.toml` (runtime dependencies plus a `dev` extra).
- **Alternatives:** a flat `src/` of loose modules, or `requirements.txt` files.
- **Why:** the `src` layout forces tests to import the installed package, which avoids
  accidentally testing code that is not importable after installation. A single
  `pyproject.toml` replaces separate dependency, tool and packaging files that drift apart.
- **Status:** Accepted.

## D-002: LF line endings enforced by `.gitattributes`

- **Decision:** `* text=auto eol=lf`.
- **Why:** chunking tests depend on exact character offsets. CRLF versus LF would change
  them between Windows and the Linux CI runners.
- **Status:** Accepted.

## D-003: Loaders decode strict UTF-8 and never guess encodings

- **Decision:** files are decoded as `utf-8-sig` (which drops a leading BOM) with strict
  error handling. Undecodable input raises `DocumentLoadError`.
- **Alternatives:** `errors="replace"` (silently turns Arabic into U+FFFD), or encoding
  detection such as `chardet` (guesses wrong on short Arabic text and adds a dependency).
- **Why:** silent corruption of Arabic is the worst failure mode for this project. A legacy
  `windows-1256` file fails loudly and can be converted deliberately.
- **Status:** Accepted.

## D-004: Arabic text is not normalized by default

- **Decision:** the cleaner does no NFC/NFKC normalization, no folding of alef/ya/ta-marbuta
  variants, and no removal of tatweel or diacritics. ZWNJ, ZWJ and direction marks are kept.
- **Alternatives:** the common Arabic preprocessing recipe (fold alef variants, strip
  diacritics and tatweel, normalize ya/alef maqsura).
- **Why:** each of these changes meaning or citation fidelity for some words, and whether
  they help retrieval is an empirical question for the chosen embedding model. They will be
  tested as options in the Week 3 evaluation, not applied on assumption.
- **Resolved (see D-008):** Arabic presentation forms (U+FB50-FEFF) appear in the output of
  some PDF extractors. The chosen extractor, PDFium, already returns base letters on both
  test producers, so no NFKC step is needed. NFKC would also rewrite unrelated characters,
  so revisit only if a future extractor emits presentation forms.
- **Status:** Accepted for now; revisit with measurements.

## D-005: Invisible characters are removed from an explicit list

- **Decision:** remove soft hyphen (U+00AD), zero-width space (U+200B), word joiner
  (U+2060) and BOM/zero-width no-break space (U+FEFF), not the whole Unicode "format"
  category.
- **Why:** the format category also contains ZWNJ/ZWJ, which are meaningful in Arabic-script
  orthography.
- **Related:** source files must write special characters as `\N{NAME}` escapes; a test
  fails if a raw invisible character appears in any `.py` file.
- **Status:** Accepted.

## D-006: Baseline chunker is recursive, character-based, with whole-unit overlap

- **Decision:** split at the coarsest boundary that fits (paragraph, line, sentence, word),
  hard-cut only unbroken runs, pack greedily, and repeat whole trailing units as overlap.
  Every chunk is an exact slice of the cleaned page; chunks never cross pages.
  Defaults are 1200 characters with 200 overlap.
- **Alternatives:** fixed-size sliding window, heading-aware chunks, semantic chunking,
  token-based sizes.
- **Why:** it keeps sentences intact, is simple enough to reason about and test, and gives
  an honest baseline to compare the other strategies against.
- **Provisional.** The defaults are unevaluated hypotheses, measured in characters because
  no tokenizer exists yet (it arrives with the embedding model).
- **Observed limitation (real corpus, default settings):** some chunks end with a bare
  Markdown heading whose body starts the next chunk, for example `## 5. Public Holidays`
  in `hr/annual_leave_policy_en.md`. Overlap partly hides this. It is a concrete reason to
  evaluate heading-aware chunking in Week 3 instead of assuming it is better.
- **Validation:** Week 3 retrieval benchmark (Recall@k, MRR) across chunking strategies,
  sizes and overlaps.

## D-007: Directory ingestion names documents by relative path and reports skips

- **Decision:** chunk filenames are paths relative to the ingested root, with `/`
  separators, in a deterministic case-sensitive order. Files that cannot be ingested
  (unsupported type, invalid UTF-8, no text after cleaning) are returned in `skipped` with a
  reason and never abort the run.
- **Why:** `hr/policy.md` and `it/policy.md` must stay distinguishable in citations, results
  must be identical on Windows and Linux, and one bad file must not hide the rest of a
  corpus. "No text after cleaning" will matter for scanned PDFs.
- **Status:** Accepted.

## D-008: PDFs are read with PDFium (pypdfium2)

- **Decision:** `.pdf` files are loaded with `pypdfium2`, one `Page` per PDF page. Blank
  pages are kept as empty pages so page numbers stay correct, and files with no text at all
  are reported as skipped by the pipeline.
- **Alternatives:** `pypdf` (pure Python, no dependencies), `pdfminer.six`, and PyMuPDF
  (AGPL-3.0, so not compatible with an MIT project).
- **Why:** measured on Arabic/English PDFs from Word and Chromium, only PDFium kept
  Arabic-Indic numbers correct on both. `pypdf` and PyMuPDF turned `٢١` into `١٢`, which
  silently changes facts such as "21 days". PDFium also had the best word recall on the Word
  PDF (90%). Full method and numbers: `docs/pdf-extraction.md`.
- **Cost:** the project's first runtime dependency (8 MB binary). On Windows the virtual
  environment path must be short, because `pypdfium2` fails to install past the 260-character
  path limit.
- **Known limits:** Arabic word order within a line, lam-alef ligature swaps, displaced
  tanween, and poor results on Chromium-generated PDFs. Prefer DOCX or native text for Arabic
  source documents; OCR or a layout model is a later option.
- **Status:** Provisional. The evidence is two producers and a few sentences, and PDF-derived
  questions should be part of the evaluation set so any loss shows up as a measured drop.

## D-009: DOCX is read with the standard library, not python-docx

- **Decision:** `.docx` files are read by `ingestion/docx_reader.py` using `zipfile` and
  `xml.etree`. No dependency is added.
- **Alternatives:** `python-docx` (pulls in `lxml`), or converting through Word/LibreOffice.
- **Why:** the format is a ZIP of XML with text in reading order, so a reader under 200 lines
  (docstring included) covers what a policy corpus needs, and gives control over things a
  generic library does not handle for us: tracked deletions, text boxes that Word writes twice, table rows kept as
  `cell | cell` lines, and page markers. A DOCX read this way returned 100% of the Arabic
  words and both numbers on the Word fixture, against 90% for the PDF of the same content
  (see `docs/pdf-extraction.md`).
- **Untrusted input:** the archive is size-capped, any document containing a DTD is rejected
  (this blocks entity-expansion attacks; Word never writes one), and password-protected or
  damaged files raise `DocumentLoadError` instead of crashing a directory run.
- **Pages are approximate.** A DOCX has no fixed pages. They are derived from explicit page
  breaks and from the `lastRenderedPageBreak` markers Word saves. Files not saved by Word may
  come out as one page, so a DOCX citation's page number is a best guess, not a guarantee.
- **Not extracted:** headers, footers, footnotes, endnotes, comments, and list numbers or
  bullets (Word does not store them in the text). Legacy `.doc` and macro-enabled `.docm` are
  not supported.
- **Validation:** unit tests over hand-built XML for each construct, an exact-text test on a
  real Word document, and 13 injected bugs, all caught (including removal of the DTD check and
  of the size cap).
- **Status:** Accepted. Revisit if the corpus needs footnotes or list numbering.

## D-010: The corpus is generated from sources and guarded by a manifest and tests

- **Decision:** `data/manifest.json` records every document (language, format, department,
  access level, bilingual pair, digit style, source). Markdown and text documents are written
  directly; DOCX documents are generated from Markdown sources in `data/sources/` by a
  standard-library writer; PDFs are exported by Microsoft Word from those sources. Tests keep
  the manifest, files, sources and pairs consistent.
- **Alternatives:** hand-authored DOCX and PDF files, or a corpus of loose files with no
  metadata, or machine translation of every document from one language.
- **Why:** the evaluation depends on the corpus being *right*: a bilingual pair that disagrees
  on a number would make a correct retrieval look wrong. Generating DOCX from readable sources
  keeps reviews meaningful (a diff of Markdown, not of a ZIP) and makes drift detectable.
  The manifest carries exactly the metadata later stages need (department, access level,
  language, pair) and lets results be split by language, format and digit style.
- **Designed in:** facts that exist in only one language (for the two cross-lingual
  directions), near-identical numbers across policies (for confusion), deliberately absent
  facts (for refusal tests), restricted-access documents (for Week 7), both Arabic digit
  systems, and one Arabic PDF so the known PDF loss is measured on a whole document.
- **Guarantees:** paired documents state the same numbers with the same structure; digit
  style, language and titles match the manifest; absent facts stay absent; every DOCX equals
  its source. Twelve deliberate corruptions of the corpus were each caught by a test.
- **Limits, stated openly:** the corpus is small (about 6,000 words, 46 chunks), uniformly
  styled, AI-written, and its Arabic has not been reviewed by a native-speaker editor. See
  `docs/dataset.md`.
- **Status:** Accepted. Grow it (and add harder distractors) before drawing conclusions from
  the Week 3 benchmark.

## D-011: PostgreSQL with pgvector, run in Docker, database only

- **Decision:** chunks, their metadata and their embeddings will live in one PostgreSQL
  database with the pgvector extension. Locally it runs from `docker-compose.yml` as a single
  `db` service using the image `pgvector/pgvector:0.8.6-pg17` (pgvector 0.8.6 on PostgreSQL 17).
  The application is not containerized yet (Week 8). Python talks to it with `psycopg` 3
  directly, with no ORM, and reads its settings from `POSTGRES_*` environment variables.
- **Alternatives:** a dedicated vector database (Qdrant, Weaviate, Chroma), an in-process index
  such as FAISS, or PostgreSQL installed natively on Windows.
- **Why one PostgreSQL:** the permission rule of this project is that unauthorized text must
  never reach the language model. With vectors and metadata in the same database, the
  access-level filter and the similarity ordering happen in a single SQL statement, inside one
  transaction, instead of being reconciled across two systems. It also makes the hybrid search
  planned for Week 6 possible without a second service (whether PostgreSQL's built-in full-text
  search handles Arabic well enough is still to be tested).
  The corpus is 46 chunks, so any of the alternatives would be fast enough; the choice is about
  the architecture, and no speed or scale claim is made for it.
- **Why Docker:** pgvector has no official Windows installer, and the container is what the
  final `docker compose up` will use. Docker Desktop is proprietary software under Docker's
  subscription agreement, so anyone reusing this setup should check that their use qualifies;
  any Docker engine runs the same compose file.
- **Choices made and why:**
  - the image tag is pinned to an exact version and PostgreSQL 17 was chosen over 18, whose
    images moved both `PGDATA` and the volume path (checked against the upstream Dockerfiles),
    so the mount stays conventional;
  - the port is published on `127.0.0.1` only, so the database is not reachable from the
    network;
  - `POSTGRES_PASSWORD` is required and has no default anywhere, and `.env` is ignored by Git;
  - the client encoding is set to UTF-8 explicitly, because Arabic must not depend on the
    Windows locale. A test sets `PGCLIENTENCODING=LATIN1` in the environment and checks the
    connection still uses UTF-8;
  - `.env` is read by a small standard-library parser, not `python-dotenv`, and real
    environment variables override it. Docker Compose reads the same file and treats quotes
    and `#` slightly differently, so secrets should be plain letters and digits.
- **Dependency:** `psycopg[binary]` (LGPL-3.0, used unmodified as an installed library, bundles
  libpq so nothing else needs installing). The standard library has no PostgreSQL driver.
- **Tests that need the database** are marked `database` and skip with a clear reason when it
  is unreachable. With `RAG_REQUIRE_DATABASE=1` (CI should set this) they fail instead, because
  a test that silently never runs proves nothing. They run inside a rolled-back transaction.
- **Validation:** `scripts/check_database.py` connected to PostgreSQL 17.11 with pgvector
  0.8.6, and a cosine-distance query returned exactly 1 for orthogonal vectors (the L2, inner
  product and L1 operators return different values, so the check cannot pass by accident). The
  health check enables the extension inside a savepoint and rolls it back; the database was
  inspected afterwards and contained no extra extension or table. Thirteen deliberate
  breakages of the code and of the compose file were each caught by a test.
- **Not decided yet:** the schema, migrations, embedding storage and any vector index. With
  46 chunks an exact scan is enough, and no index will be added until the corpus justifies it.
- **Status:** Accepted.

## D-012: Versioned SQL migrations, a schema that enforces the data model, one embedding table per model

- **Decision:**
  - **Migrations** are plain `NNNN_name.sql` files shipped inside the package
    (`bilingual_rag/database/migrations/`) and applied by a small runner
    (`database/migrate.py`, `scripts/migrate_database.py`). The runner is forward-only, runs each
    file in its own transaction together with the row that records it, stores a checksum of every
    applied file (editing an applied migration is an error), and takes an advisory lock so two
    runners take turns.
  - **Tables:** `documents` carries the manifest metadata plus a content hash; `chunks` carries
    page, index, text and character offsets, and is keyed by a readable id,
    `<document id>-p<page>-c<index>`. The database itself enforces the rules of the data model:
    the allowed languages, formats and access levels, the derived chunk id, and
    `end_offset - start_offset = char_length(text)` (the chunk text is exactly a slice of its
    page). `ON DELETE CASCADE` removes a document's chunks and embeddings with it.
  - **Embeddings:** an `embedding_models` registry plus one table per model,
    `embeddings_<key>`, created by code (`database/embedding_store.py`). No vector index exists.
  - **Access:** repository functions never commit, and chunk text can only be read through
    `list_chunks`, whose `allowed_access_levels` argument is required, has no default, and returns
    nothing when empty. The filter is part of the SQL statement.
  - **Vectors** cross the driver as text and are cast in SQL (`%s::vector`).
- **Alternatives:** Alembic; an ORM; one embeddings table with a dimensionless vector column and a
  model column; serial integer chunk ids; the `pgvector` Python adapter.
- **Why:**
  - *Plain SQL and a 140-line runner* instead of Alembic: two tables need no migration DSL, the
    SQL is reviewable as written, and it adds no dependency. Alembic remains the answer if the
    schema grows branches or needs autogeneration.
  - *Constraints in the database*, because access level is what the search filters on: a typo such
    as `Employee` must fail at the write and not silently hide or expose a document. A test keeps
    the SQL value sets and the Python constants in step.
  - *A readable natural chunk id* so evaluation data (Week 3) and citations can name a chunk and
    still find it after re-ingestion, which a serial id would not survive.
  - *One table per model*, because a pgvector column has a fixed dimension and Week 3 compares
    models with different ones. The registry refuses to reuse a key for another model or
    dimension.
  - *Idempotent ingestion* through a hash of the document's chunks: an unchanged document writes
    nothing, changed text replaces the chunks (and drops their now-stale embeddings through the
    cascade), and a metadata-only change, such as a new access level, takes effect at once without
    touching the chunks.
  - *Required access levels, deny by default:* a caller cannot fetch text by forgetting a filter,
    an empty list yields nothing, and a bare string (which would be read as letters) is refused.
  - *Text vectors and not the adapter (a change from the default agreed earlier):* the `pgvector`
    package is small, MIT licensed and has no required dependencies, but registering it fails on a
    connection opened before the extension exists, which couples connection setup to migration
    order. A short helper with strict validation is enough for writing vectors; distances are
    computed in SQL. Revisit when a query needs to read vectors as arrays.
- **Limits, stated openly:**
  - One chunking configuration is stored at a time, and chunk ids do not include it. Week 3's
    chunk-size experiments must re-ingest into a separate database or replace the chunks.
  - Embedding tables are created by application code, which needs a role allowed to run
    `CREATE TABLE`. Revisit if the application is given a narrower role.
  - The access levels are listed in both SQL and Python. Adding one needs a new migration and a
    change to `ACCESS_LEVELS`; the sync test fails until both agree.
  - There are no down-migrations, and a migration cannot use statements PostgreSQL refuses inside
    a transaction (such as `CREATE INDEX CONCURRENTLY`).
  - Tests create throwaway `rag_test_*` databases; a hard-killed test run can leave one behind.
- **Validation:** the suite passes with the database (710 tests, 1 expected failure) and, on a machine without
  Docker, passes with 201 database tests skipped. The migrations were applied twice to the
  development database (PostgreSQL 17.11); the second run applied nothing. The real 32-document
  corpus was stored and read back exactly, Arabic included, and every access-level filter returned
  precisely the documents an independent count from the manifest predicts. Forty-two deliberate
  breakages (10 in the migration runner, 11 in the repository, 6 in the embedding store, 1 in the
  vector helper, 4 in the manifest loader and 10 in the schema SQL) were each caught by a test.
  That run found one real gap, now closed: nothing rejected a zero-length chunk with consistent
  offsets. Two of my own tests had wrong expectations (a tied and a mis-calculated vector
  ordering) that the first real run exposed.
- **Not decided yet:** the embedding model wrapper, the ingestion script, and the vector search
  query. No ANN index will be added until the corpus justifies one.
- **Status:** Accepted.

## D-013: An embedder interface, a deterministic stand-in, and batched embedding of stored chunks

- **Decision:**
  - **Interface** (`embeddings/base.py`): an `Embedder` has a `key` (its table name, for example
    `bge_m3`), a `model_name`, a `dimension`, `embed_documents(texts)` and `embed_query(text)`.
    Vectors have unit length, so cosine distance and inner product order results the same way. Any
    model-specific prefix (E5 needs `query: ` and `passage: `) is added inside the embedder, never
    by the caller, and an embedder embeds exactly the text it is given.
  - **Checks:** `validate_vectors` verifies the count, the size and finiteness of what an embedder
    returned before it is stored, and `normalize` refuses zero and non-finite vectors.
  - **Stand-in** (`embeddings/hashing.py`): `HashingEmbedder` hashes each word (SHA-256) to a
    position in the vector. It exists so storage, ranking and access control can be tested without
    a model or a download.
  - **Indexing** (`embeddings/indexing.py`): `embed_missing(conn, embedder)` registers the model,
    finds the chunks that have no vector for it, and embeds and stores them in batches. It is safe
    to call again after adding documents, and after a failure it resumes where it stopped.
- **Change from the earlier plan:** the interface also carries `key`, so the caller does not have
  to invent a table name.
- **Why:**
  - *Prefixes and exact text belong to the embedder,* so no caller can forget them and no
    pre-processing sneaks in between the stored chunk and the model (D-004).
  - *Validate what comes back:* Python's `zip` silently drops the surplus, so a model that returned
    one vector too few would leave a chunk unembedded, or attach a vector to the wrong chunk,
    without an error.
  - *A deterministic stand-in instead of random vectors,* so tests of ranking can assert that a
    query finds the chunk that shares its words. It uses SHA-256 and not Python's `hash`, which
    differs in every process; a test runs it under three hash seeds.
  - *`embed_missing` reads chunks of every access level on purpose.* Embedding is a trusted
    ingestion step that must cover restricted documents too. The embeddings tables carry no access
    level, so the search query must join each vector to its document and filter there (step 4).
- **The stand-in is not semantic, and this is measured, not assumed.** On the real corpus, queries
  that share words with a document find it (an Arabic query for the annual leave policy ranks the
  Arabic policy first), but the English query "overtime pay rates" does not bring the Arabic-only
  overtime policy into the top five, because the answer shares no words with it. A test records
  this on purpose. It must never be used to judge retrieval quality; that needs a real multilingual
  model, and is the Week 3 experiment.
- **Limits, stated openly:**
  - No real model exists yet, so no claim about retrieval quality is made anywhere.
  - `embed_missing` does not commit; the caller commits, for example from the `on_batch` callback.
  - Embedding is done in one process, with no parallelism and no retry of a failed batch.
- **Validation:** 111 new tests (821 in the suite). Twenty-seven deliberate breakages of the
  interface helpers, the stand-in and the indexing were each caught by a test. That includes
  removing the validation of vector counts, swapping SHA-256 for the per-process `hash`, and
  quietly stripping, lower-casing or NFKC-normalizing the text before embedding. The mutation run
  also drove a strengthening of the "text is embedded exactly as stored" test: its first version
  had no leading whitespace and no capital letters, so by inspection it could not have caught a
  stray `strip()` or `lower()`; it now can.
- **Status:** Accepted. The real model, and how its inputs are tokenized and truncated, comes next.