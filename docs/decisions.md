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
- **Addendum, measured in real tokenizers (`scripts/measure_chunk_tokens.py`):** at the
  1200/200 character defaults, the 46 chunks are 32-364 tokens (median 230) in `bge-m3`'s and
  `multilingual-e5-large`'s tokenizer, so **none exceed either model's limit** (8192 and 512
  tokens). **`paraphrase-multilingual-mpnet-base-v2` truncates 36 of 46 chunks (78%)** at its
  128-token limit, which is baked into its own `tokenizer.json` (`Tokenizer.from_pretrained`
  applies it silently unless it is disabled before measuring; the script disables it and warns).
  This is measured evidence against choosing that model without shortening chunks first, on top
  of the token-limit concern already raised when it was proposed in section 7.2. Longest
  chunks are the Arabic policies whose text has no ASCII shortcuts for the tokenizer's BPE
  vocabulary (`legal/code_of_conduct_ar.md`, `finance/travel_expense_policy_ar.md`, both 364
  tokens for chunks under 1200 characters). Measured directly: median tokens per character in
  `bge-m3`'s tokenizer is 0.235 for the 25 English chunks and 0.309 for the 21 Arabic ones, so
  a character-based budget gives Arabic noticeably less room in tokens than English, on this
  evidence (one model, one corpus).

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
## D-014: The real embedder is bge-m3 via sentence-transformers, gated behind a `--slow` flag

- **Decision:** `embeddings/sentence_transformer.py` wraps any `sentence-transformers` model
  behind the `Embedder` interface from D-013, with a `bge_m3()` factory for `BAAI/bge-m3`
  (no query or document prefix: confirmed on the model's own card, not assumed — unlike
  `bge-large-en-v1.5` and the E5 models, "the BGE-M3 model no longer requires adding
  instructions to the queries"). PyTorch is installed with the CUDA build
  (`--index-url https://download.pytorch.org/whl/cu130`); the wrapper defaults to `cuda` when
  `torch.cuda.is_available()`, else `cpu`. Real-model tests are marked `slow` and skipped by a
  root `tests/conftest.py` unless `pytest --slow` is passed, so the everyday suite (now over
  850 tests) still runs in about 20 seconds.
- **Alternatives:** `intfloat/multilingual-e5-large` and
  `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` (both proposed alongside
  bge-m3 in the Week 2 plan, to be benchmarked properly in Week 3); calling `transformers`
  directly instead of `sentence-transformers` (would mean writing the pooling ourselves; the
  bge-m3 card itself says "you also can use sentence-transformers... to generate dense
  embeddings", confirming the packaged pooling config is meant to be used this way).
- **Why bge-m3 first:** MIT license, 1024 dimensions, an 8192-token context that the D-006
  addendum showed comfortably covers every chunk in the corpus (max 364 tokens), and no
  query-prefix logic to get wrong. `paraphrase-multilingual-mpnet-base-v2`'s 128-token limit
  measurably truncates 78% of the corpus's chunks (D-006 addendum), a concrete reason to not
  reach for it first.
- **Why a `_model` injection point on the wrapper:** it lets the wrapper's own logic (prefix
  handling, exact-text passthrough, validation, device selection, key derivation) be
  unit-tested in milliseconds with a fake standing in for `SentenceTransformer`, instead of
  every test needing the real weights. `dimension` is measured by encoding a one-word probe
  at construction, not read from a method name, because sentence-transformers has already
  renamed that method once (`get_sentence_embedding_dimension` to `get_embedding_dimension`)
  between versions this project has touched.
- **Two bugs caught before any real-model test ran, from review rather than a failure:**
  a slow real-model+database test's fixture was written module-scoped but requested the
  function-scoped `store_connection` fixture, which pytest would have refused outright
  (`ScopeMismatch`); the fix opens its own connection from the session-scoped
  `migrated_database` fixture instead. The first version of that fix then called `commit()`
  to make the writes visible across the module's tests — which would have permanently
  written six real documents into the *shared* session database, silently invalidating
  `test_corpus_in_database.py`'s "every document is inserted, not updated, the first time"
  assertion the next time that file ran in the same session. Neither bug was caught by a
  failing test; both were caught by re-reading the fixture before running it. The fixed
  version keeps everything in one transaction for the whole module and rolls it back at the
  end, matching the rollback discipline every other database test in this project follows.
- **Downloads, measured, not as quoted when asked (see below):** PyTorch installed as
  `torch 2.14.0+cu130` (a newer release appeared between when the size was quoted and when it
  was installed; the actual download was about 2.0 GB, not the 1,783 MB stated when asking
  for approval). `sentence-transformers` 6.1.0 pulled `transformers` 5.17.0, `numpy` 2.5.3,
  `scikit-learn` 1.9.1, `scipy` 1.18.1 and their own dependencies. The bge-m3 weights
  (`pytorch_model.bin`, MIT) downloaded through the Hugging Face cache as sentence-transformers
  loaded the model; first load (download plus moving weights onto the GPU) took 145 seconds.
  **This discrepancy is recorded here deliberately: a size quoted before a download is a
  snapshot, not a guarantee, because upstream projects release new versions in between.**
- **Measured (GPU, RTX 4060, driver reporting CUDA 13.4 support):** dimension 1024,
  `max_seq_length` 8192, every returned vector has unit norm. Cross-lingual signal, the
  property the `HashingEmbedder` stand-in explicitly cannot offer (D-013): cosine similarity
  between an English and an Arabic sentence about the same topic (annual leave) is 0.83,
  against 0.30 for an English sentence and an unrelated-topic Arabic one. A small curated
  subset of the real corpus (an English/Arabic pair, a different-topic pair, an Arabic-only
  topic, and one HR-restricted document) was embedded through `embed_missing` and searched
  over pgvector: an Arabic query for the VPN guide finds the English-only VPN document, an
  English query about overtime finds the Arabic-only overtime document, and the
  access-level filter still hides the restricted document from an employee-level query.
  **This is qualitative evidence that the pipeline works end to end with a real model, not a
  benchmark** — Recall@k and MRR over the full evaluation set are the Week 3 experiment.
- **Limits, stated openly:**
  - Only one model has been wired up. The comparison against `multilingual-e5-large` and
    `paraphrase-multilingual-mpnet-base-v2` is still to come.
  - The qualitative check above used 6 of 32 documents and a handful of hand-picked queries.
    It shows the mechanism works; it says nothing about accuracy at scale.
  - No fp16 or quantization; the model runs at full precision. A possible Week 3 speed
    experiment, not applied here without measuring its effect on retrieval first.
  - CPU fallback is implemented but has not been timed; only the GPU path has been measured.
- **Validation:** 45 new tests (33 fast, unit-level, using the fake model; 12 marked `slow`,
  using the real one). The full suite is 855 passed, 1 xfailed in about 20 seconds with
  `--slow` omitted (as a normal `pytest` run leaves it), and 12 passed in 33.67 seconds when
  run explicitly with `pytest --slow -m slow` against the already-downloaded model. Thirteen
  deliberate breakages of the wrapper's own logic were each caught by the fast unit tests.
- **Status:** Accepted for bge-m3 as the first model. Provisional on which model V1 ships
  with, pending the Week 3 comparison.
- **Not decided yet:** `scripts/ingest_documents.py` and the `search()` function that joins a
  query's embedding to `list_chunks`'s access-level filter (step 4).

## D-015: `search()` joins the access filter to cosine similarity in one SQL statement

- **Decision:** `retrieval/search.py` provides `search(conn, embedder, query, allowed_access_levels,
  *, k=5, language=None)`. It embeds `query` with `embedder.embed_query`, looks up that
  embedder's registered table through `get_embedding_model` (a clear `EmbeddingModelError`
  if nothing has been ingested for it yet, instead of a raw "relation does not exist"), and
  runs one query joining that table to `chunks` and `documents`, filtered by
  `d.access_level = ANY(...)` and ordered by pgvector's `<=>` (cosine distance). Each result
  pairs a `StoredChunk` (reused from `database.repository`, not duplicated) with a `score`
  (1 minus the distance, so higher is more similar). `scripts/ingest_documents.py` is the
  companion script: it applies pending migrations, stores the manifest's documents and
  chunks (idempotent, per D-012), then calls `embed_missing` for the chosen embedder
  (`bge-m3` by default, `hashing` for a fast no-download smoke test), committing after each
  batch so progress survives a crash.
- **Why `allowed_access_levels` is required, with no default, exactly like `list_chunks`:**
  this is the function the brief's security requirement names directly — "the search function
  take the caller's permitted access_levels from day one and filter in SQL" (HANDOFF.md 5.3).
  An empty collection returns nothing without even embedding the query; a bare string is
  refused rather than silently read as individual letters.
- **Why the embeddings table is looked up, not derived:** `embedder.key` and the table name
  `embeddings_<key>` are already kept in step by a database CHECK constraint (D-012), but
  looking the row up through `get_embedding_model` catches the one case that constraint
  cannot: nobody has run `ingest_documents.py` for this embedder yet, so its table does not
  exist. That becomes one clear error message instead of a confusing SQL exception.
- **Why order by the raw `<=>` expression, not a computed alias:** `ORDER BY score DESC`
  would work identically today, but ordering by the operator directly is what lets pgvector
  use a distance-based index later (D-012: none exists yet, and none is added until the
  corpus justifies it) without changing the query.
- **Qualitative check, real bge-m3, real ingested corpus (32 documents, `scripts/ingest_documents.py`
  run for real against the development database) — not a benchmark, eight hand-picked queries:**
  - Same-language: an English leave question ranks the English leave policy first (0.717); an
    Arabic leave question ranks the Arabic leave policy first (0.714).
  - Cross-language, by the corpus's design (D-010): an Arabic question about the laptop refresh
    cycle (an English-only fact) ranks the English laptop guide first; an Arabic question about
    the remote-work policy (also English-only) ranks the English remote-work policy first; an
    English question about overtime pay rates (an Arabic-only fact) ranks the Arabic overtime
    document first (0.554); an English question about the petty cash limit (Arabic-only) ranks
    the Arabic petty cash document first (0.505).
  - A query about reporting a lost laptop ranked the IT laptop guide first. This was checked
    against the source text directly rather than assumed correct: the guide contains "Report a
    lost or stolen laptop to the security team on extension 4400 within 1 hour," so the top
    result is right, not a lucky lexical overlap on the word "laptop."
  - A query for a deliberately absent fact (executive stock options, D-010) scored noticeably
    lower than every genuine match above (0.40 best, against 0.50-0.72 for real answers) — a
    concrete, measured hint that a similarity threshold could support future refusal logic
    (not built yet; that is a Week 4 concern once an LLM exists to refuse with).
  - **This is eight queries on one small corpus with one model. It shows the mechanism working
    end to end, including the cross-lingual retrieval the corpus was built to test. It is not
    Recall@k or MRR, and no such number is claimed here.**
- **Limits, stated openly:**
  - Single-vector search only; no keyword/BM25 component yet (hybrid search is Week 6).
  - No reranking, no LLM, no citation formatting — those are Weeks 4 and 6.
  - The absent-fact score gap above is one observation, not a calibrated threshold.
- **Validation:** 42 new tests (37 fast — including access-control tests that plant one
  restricted document per level and confirm none leaks to a lower-privileged caller — and 5
  marked `slow`, using the real model). Eleven deliberate breakages of `search()`'s own logic
  were each caught, including flipping the sort order, replacing the similarity score with the
  raw distance, and dropping the access-level filter entirely. `scripts/ingest_documents.py`
  was run for real three times against the development database: once with the hashing
  stand-in (32 inserted, 46 embedded), once again to confirm full idempotency (0 inserted, 0
  newly embedded), and once with the real bge-m3 model (0 inserted — the documents already
  existed unchanged from the hashing run — 46 newly embedded under a second, independent
  embeddings table), demonstrating that two embedding models coexist over the same chunks.
- **Status:** Accepted. Closes Week 2's stated goal: question -> embedding -> pgvector ->
  relevant chunks, with document permissions enforced in SQL and no LLM involved.
- **Not decided yet:** Week 3's retrieval evaluation (about 50 questions, Recall@k, MRR,
  comparing embedding models and chunk sizes) is the next milestone.

## D-016: The Week 3 retrieval evaluation -- 50 questions, Recall@k, MRR, two models, two chunkings

- **Decision:** `data/evaluation_questions.json` holds 50 fixed questions (25 EN, 25 AR),
  each checked against the actual document text before being written down, not assumed:
  26 `same_language` (the correct answer is in the same language as the question), 12
  `cross_lingual` (the correct answer exists only in the *other* language, from the corpus's
  seven English-only and five Arabic-only topics), 8 `near_miss` (a number that also appears,
  for a different fact, elsewhere in the corpus, for example the USD 10,000 figure that
  appears in both the procurement policy and the contract-approval procedure for different
  processes), and 4 `absent_fact` (deliberately unanswerable, `expected_document_id: null`).
  `bilingual_rag/evaluation/` provides `load_questions`, `document_rank`/`recall_at_k`/
  `mean_reciprocal_rank` (pure functions), and `run_questions`/`summarize`, which call the
  real `search()` from D-015 and rank results at document granularity (a question is
  answered by a document, not a specific chunk; results are de-duplicated by document before
  ranking, fetching `CHUNK_FETCH_K = 20` chunks so a document with several top-scoring chunks
  cannot crowd out others before Recall@5 is measured). `scripts/evaluate_retrieval.py` runs
  one embedder at one chunking per invocation and prints the summary; comparing models or
  chunk sizes means running it more than once, on purpose (no speculative multi-run
  orchestration was built for a corpus this size). Added `e5_large()` next to `bge_m3()` in
  `embeddings/sentence_transformer.py`, and a small `embeddings/registry.py` shared by
  `ingest_documents.py` and `evaluate_retrieval.py` so the embedder-selection logic is
  written once.
- **Measured, bge-m3 vs multilingual-e5-large, both at the 1200/200 default chunking:**

  | | bge-m3 | multilingual-e5-large |
  | --- | --- | --- |
  | Overall Recall@1 / @3 / @5 | 0.826 / 1.000 / 1.000 | 0.804 / 0.891 / 0.913 |
  | Overall MRR | 0.913 | 0.863 |
  | `cross_lingual` Recall@1 / MRR | **0.917 / 0.958** | **0.333 / 0.517** |
  | `same_language` Recall@1 / MRR | 0.808 / 0.904 | **1.000 / 1.000** |
  | `near_miss` Recall@1 / MRR | 0.750 / 0.875 | 0.875 / 0.938 |
  | `absent_fact` mean top score | 0.531 | 0.797 |

  **The standout, and the reason bge-m3 was already the first choice in D-014:**
  multilingual-e5-large is perfect within one language on this corpus, but its cross-lingual
  Recall@1 (0.333) is less than half of bge-m3's, on the exact retrieval direction the corpus
  was built to test (D-010). bge-m3 also separates answerable from unanswerable questions by
  score more clearly (0.53 against 0.80 for the four absent-fact questions), which matters
  once a refusal threshold is built in Week 4.
  `paraphrase-multilingual-mpnet-base-v2` was not run: D-006's addendum already showed it
  truncates 78% of this corpus's chunks, so a low score there would measure truncation, not
  the model.
- **Measured, chunk size, bge-m3 only (1200/200 vs 600/100; 46 vs 77 chunks):**

  | | 1200/200 | 600/100 |
  | --- | --- | --- |
  | Overall Recall@1 / MRR | 0.826 / 0.913 | 0.826 / 0.908 |
  | `language:ar` Recall@1 | 0.826 | **0.696** |
  | `language:en` Recall@1 | 0.826 | **0.957** |

  Overall the two chunkings are close, but the smaller chunking helps English and hurts
  Arabic. This lines up with the D-006 addendum's measurement that Arabic runs token-heavier
  than English for the same character budget: a fixed character-based chunk size is not a
  language-neutral choice, and the corpus is too small yet to say more than that a
  language-aware or token-based chunk size is worth testing properly, not just noting.
- **The corpus was restored to 1200/200 with both models fully embedded (46/46/46) after
  these runs**, so the development database is left in the state `scripts/ingest_documents.py`
  produces normally, not mid-experiment.
- **Limits, stated openly:**
  - 50 questions over 32 documents (46 chunks) is small. The category and language splits
    above are 4-26 questions each; a single wrong answer moves a category's Recall@1 by
    0.125-0.25. These are directional findings, not precise measurements.
  - The questions and their ground truth were written by one person (with AI assistance) in
    one sitting, and the Arabic questions carry the same caveat as the corpus itself (D-010):
    not reviewed by a native-speaker editor.
  - Only two of the three originally proposed models were compared, for the stated reason
    above; `paraphrase-multilingual-mpnet-base-v2` could still be run for completeness later.
  - No reranking, no hybrid search, no LLM. Only the vector search from D-015 is measured.
  - `absent_fact` mean top score is four questions; it is a directional signal for a future
    refusal threshold, not a calibrated one.
- **Validation:** 41 new tests (35 fast: metrics edge cases, question-set validation against
  the real 50-question file, the wrapper's `e5_large()` factory; 6 database-backed, using the
  hashing stand-in for speed). Fifteen deliberate breakages of the metrics, validation and
  ranking logic were each caught, after two rounds where my own mutation-harness expected
  strings, not the code, were wrong -- confirmed each time by reproducing the failure
  directly rather than trusting the harness's first verdict, the same discipline as D-013 and
  D-014. One test itself had a real gap the harness correctly found: a first version used
  only one document, so a missing de-duplication guard had no document ranking to disturb;
  the fixed version plants two documents so the guard's absence changes a real rank.
- **Status:** Accepted. `paraphrase-multilingual-mpnet-base-v2` and a token-based or
  language-aware chunk size are candidates for a follow-up run, not required for V1.
- **Not decided yet:** hybrid search (Week 6), reranking (Week 6), the LLM and citations
  (Week 4), and whether/how the absent-fact score gap becomes a real refusal threshold.

## D-017: The LLM is qwen3:8b on a local Ollama server, called with the standard library

- **Decision:** `generation/llm.py` defines an `LLM` protocol (`generate(system, prompt) -> str`)
  and `OllamaLLM`, which posts one non-streaming request to Ollama's `/api/chat` with `urllib`.
  The default model is `qwen3:8b` (8.2B parameters, Q4_K_M quantization, Apache-2.0, 40,960-token
  context), already installed on the development machine; the user confirmed it for this
  project. Every failure (Ollama not running, model not pulled, malformed reply) becomes one
  `GenerationError` whose message says how to fix it.
- **Why no client package:** Ollama's API is one JSON request per answer. The `ollama` Python
  package would be a new dependency for about twenty lines of standard-library code, and
  LangChain-style frameworks are out of scope until V1 works.
- **Three request settings are always sent, each for a measured or stated reason:**
  - `num_ctx` (default 8192): **measured** — with no `num_ctx`, `ollama ps` showed the loaded
    model running with a 4096-token context despite its 40,960-token capacity. Ollama truncates
    a longer prompt silently rather than rejecting it, so evidence could be dropped without any
    error. 8192 leaves room for several 1200-character chunks plus instructions, and keeps the
    KV cache small enough to share the 8 GB GPU with the embedding model.
  - `think: false`: qwen3 otherwise writes a reasoning section before the answer.
  - `temperature: 0`: the same question and evidence give the same answer (a `slow` test checks
    this against the real model), which testing and debugging rely on.
- **Qualitative check before any code (not a benchmark — three prompts):** with two short
  sources (an Arabic and an English sentence on annual leave) and a strict "answer only from the
  sources, else reply INSUFFICIENT_EVIDENCE" instruction, the model answered an Arabic question
  correctly and completely in Arabic, an English question correctly, and refused an Arabic
  question on a topic the sources do not cover. It wrote the refusal token with a trailing
  period (`INSUFFICIENT_EVIDENCE.`), so later parsing must not require an exact string. The first
  call took 80 s (loading the model into GPU memory); later calls took about 2 s.
- **Validation:** 30 fast unit tests run against a stub HTTP server started inside the test
  process (no Ollama, no mocking of `urllib`), and 4 `slow` tests against the real model, which
  skip rather than fail when Ollama is not running or the model is missing. Eighteen deliberate
  breakages of the client were each caught. Two were missed on the first run and reproduced by
  hand; both were weak tests, fixed by strengthening them: the stub recorded `self.path`, which
  `http.server` rewrites (a leading `//` becomes `/`), hiding a broken trailing-slash strip; and
  an error-message regex matched Ollama's raw JSON as well as the unwrapped message.
- **Status:** Accepted.
- **Not decided yet:** the prompt, the context builder and its budget, citations, and the refusal
  threshold (the rest of Week 4).

## D-018: The context builder numbers whole chunks in rank order within a measured character budget

- **Decision:** `generation/context.py` provides `build_context(results, *, max_chars=12_000)`.
  It turns `search()` results into one evidence block of numbered sources, in rank order:

  ```text
  [1] hr-annual-leave-policy-en | page 1 | Annual Leave Policy
  <the chunk text, exactly as stored>
  ```

  and returns it with the matching `Source` records (number, chunk id, document id, title, page,
  score, text). The model will cite sources by number; the answer step (Week 4, next) maps the
  numbers back to documents and pages.
- **Why numbers, not document ids, as citation labels:** a short number is easier for a model to
  copy exactly than `hr-annual-leave-policy-ar`, and a number outside the list is plainly invalid,
  where a slightly wrong id could look plausible. The header still shows the id, page and title,
  so the model and a human reading the prompt both see where each source came from.
- **Why whole chunks and a hard stop:** a chunk is never cut short, since half a sentence of
  evidence can say something the document does not. Sources are added in rank order until the
  next would pass the budget, and nothing after that is added even if smaller, so the evidence is
  always the top of the ranking. A budget too small for even the top result is an error, not an
  empty context, because an empty context means "no evidence" and would turn a
  misconfiguration into a refusal.
- **Why 12,000 characters — measured, not assumed:** D-006's tokens-per-character figures came
  from the embedding models' tokenizers, not the LLM's. Counting all 46 corpus chunks with
  qwen3:8b itself (Ollama's `prompt_eval_count`, raw mode, no chat template) gave:

  | Language | Chunks | Mean tokens/char | Max tokens/char |
  | --- | --- | --- | --- |
  | Arabic | 21 | 0.403 | 0.466 |
  | English | 25 | 0.245 | 0.348 |

  Arabic costs qwen3 about 1.6 times as many tokens per character as English, and more than
  bge-m3's tokenizer measured (0.309), so reusing the D-006 figure would have under-budgeted
  Arabic by about a third. At the worst rate, 12,000 characters is at most about 5,600 tokens of
  the 8,192-token window (D-017). **Checked end to end:** a default-budget context built from the
  most token-dense Arabic chunks in the corpus held 13 sources in 11,632 characters and measured
  4,821 qwen3 tokens, leaving 3,371 tokens for the instructions, the question and the answer. At
  `k=5` (at most 6,000 characters of chunk text) the budget never binds; it is a guard for larger
  `k`, not a tuning knob.
- **Access control and untrusted text:** the builder only ever drops results, never adds one, so
  the evidence can contain nothing that `search()` did not already return for the caller's
  access levels (D-015); an integration test plants one document per access level and checks
  that each caller's evidence names only its own. Chunk text is passed through unmodified (D-004)
  and is not trusted: a chunk could contain a line that looks like a source header. At worst that
  makes the model attribute text to the wrong one of the caller's own sources; it cannot reveal
  anything the caller may not read. Resisting instructions inside chunk text is the answer
  step's job and gets its own test there.
- **Validation:** 20 unit tests and 6 database tests. Sixteen deliberate breakages were each
  caught on the first run, including skipping an oversized source instead of stopping, forgetting
  the blank-line separator in the budget, an off-by-one at the exact budget, and stripping chunk
  text.
- **Status:** Accepted.
- **Not decided yet:** the system prompt, citation parsing and validation, and the refusal
  threshold (the answer step).

## D-019: Grounded answers with four refusal gates, and the measured Week 4 results

- **Decision:** `generation/answer.py` provides `ask(conn, embedder, llm, question,
  allowed_access_levels, *, k=5, min_score=0.50, max_chars=12_000)` and the database-free
  `answer_from_results(question, results, llm, ...)` it calls. The returned `Answer` has the
  text, structured `citations` (number, document id, title, page, chunk id), a `refusal` reason
  (None for an answer) and the top retrieval score. The system, not the model, decides what is a
  grounded answer. It refuses at four gates, and says which:
  1. `no_results`: the caller may read nothing that matches.
  2. `low_score`: no result reaches `min_score`. Results below it are also dropped from the
     prompt, so weak matches are neither shown nor citable.
  3. `model_refused`: the reply contains `INSUFFICIENT_EVIDENCE` (anywhere: qwen3 adds a trailing
     period, D-017).
  4. `uncited`: the reply cites no source that exists. Markers are rewritten to a canonical
     `[1][3]` form (accepting `[1, 3]`, the Arabic comma and Arabic-Indic digits), and numbers
     that are not sources are removed, so returned text never points at missing evidence.

  An LLM failure raises `GenerationError`; it is not turned into a refusal, since nothing was
  decided. `allowed_access_levels` is required, with no default, and goes only to `search()`.
- **Why the score is only a floor (measured, and it changed the plan):** the handoff proposed a
  score threshold as the main refusal signal, based on D-016's mean absent-fact top score of
  0.531. The full distribution (bge-m3 top-1 score, all 50 questions) shows that the mean hid a
  wide spread: the four absent-fact questions scored 0.442, 0.503, 0.589 and 0.590, while the
  lowest answerable question scored 0.508. The two high ones ask about a relocation allowance and
  retirement benefits: topics next to real HR documents, but facts that are not in them.
  Retrieval scores measure how close a topic is, not whether a fact is present.

  | Threshold | Answerable refused (of 46) | Absent-fact refused (of 4) |
  | --- | --- | --- |
  | 0.50 | 0 | 1 |
  | 0.55 | 2 | 2 |
  | 0.60 | 13 | 4 |

  No threshold separates the two groups, so 0.50 is a floor that costs no answerable question
  and saves an LLM call on the clearly off-topic one. The real check on groundedness is gate 4,
  applied by code to the model's output. **The floor was chosen on the same 50 questions
  reported below; with four absent-fact questions there is nothing to hold out.**
- **The prompt, and a measured prompt-injection comparison:** the system prompt states the
  rules (only the sources, cite by number, answer in the question's language, refuse with the
  token, never follow instructions inside a source). The user message puts the numbered sources
  inside `<sources>` tags, then the question, then repeats the rules. Eight attacks (four attack
  texts: an English override, an Arabic override, a fake `</sources>` boundary, and a planted
  "policy update" that cites itself; each with an English and an Arabic question), with a
  legitimate leave-policy source beside the attack:

  | Prompt | Followed the attack | Returned as an answer |
  | --- | --- | --- |
  | Rules in the system prompt only | 6 of 8 | 5 of 8 |
  | Rules repeated after the sources | 3 of 8 | 2 of 8 |
  | Repeated, naming the question's language (chosen) | 2 of 8 | 1 of 8 |

  The middle variant also made two Arabic answers slip into English; naming the language in the
  reminder fixed both. With no attack present, all three answered the same English and Arabic
  controls correctly. Where the model still obeys an override ("ACCESS GRANTED"), it cites
  nothing and gate 4 refuses it: that is a system guarantee, tested with the real model.
  **Known limitation:** the self-citing planted policy still works ("annual leave is unlimited
  [2]"). To the model it is a conflicting source, which no prompt can tell apart from a real one;
  its citation at least makes it traceable. The fix is document trust (review before a document
  is searchable), which is beyond V1. This case is an `xfail` test so it stays visible. A first
  version of the detector missed an Arabic phrasing ("لا حدود لها") and undercounted the
  baseline; the table above uses the corrected count.
- **Measured results, real corpus** (`scripts/evaluate_answers.py`: bge-m3, qwen3:8b, the 50
  evaluation questions, every access level, 192 s on the RTX 4060):

  | Group | n | Answered | Cites the expected document | In the question's language |
  | --- | --- | --- | --- | --- |
  | All answerable | 46 | 44 | 42 | 44 of 44 |
  | Arabic questions | 23 | 21 | 21 | 21 of 21 |
  | English questions | 23 | 23 | 21 | 23 of 23 |
  | Same-language | 26 | 25 | 24 | 25 of 25 |
  | Cross-lingual | 12 | 12 | 11 | 12 of 12 |
  | Near-miss | 8 | 7 | 7 | 7 of 7 |
  | **Absent fact** | 4 | **0 (all refused)** | n/a | n/a |

  The absent-fact refusals were one `low_score` (executive stock options, 0.442) and three
  `model_refused`, including both on-topic questions the floor cannot catch. **Every miss was
  read and checked against the source text**, not just counted:
  - q043 (Arabic near-miss) was refused by the model: retrieval ranked the wrong document first
    (its top score, 0.508, was the lowest of any answerable question), so refusing was right for
    the evidence it had.
  - q026 (Arabic, petty cash) was refused as `uncited`: its only source above the floor was `[1]`
    and the model cited `[3]`. The fact (USD 50) was right; the citation pointed nowhere, and the
    system does not return an answer it cannot trace.
  - q029 (cross-lingual) stated the right fact (200% overtime on official holidays, from the
    Arabic overtime policy's table) but cited the Arabic annual-leave policy, which only
    mentions official holidays. **A right fact with a wrong citation:** the one real
    citation-accuracy failure found.
  - q007 cited the Arabic translation of the travel policy instead of the English one; both
    state the same USD 220 limit, so the citation is correct and the metric undercounts it.

  Nine answers were also checked against the source text for content (q002, q005, q007, q026,
  q029, q030, q031, q036, q041); all nine facts were correct. Two English answers quoted an
  amount in Arabic ("50 دولارًا أمريكيًا") inside an English sentence: counted as English (most
  letters are), but a visible quality flaw.
- **What these numbers are not:** there are no reference answers, so answer correctness is not
  scored; the nine checks above are a spot check, not a measurement. "Cites the expected
  document" is strict (a translated pair with the same fact counts as a miss) and does not verify
  that each sentence is supported by what it cites (q029 shows that can fail). One run, one
  model, temperature 0, 46 chunks: directional, like D-016.
- **Validation:** 53 new fast tests (the four gates, citation parsing including Arabic-Indic
  digits and the Arabic comma, the prompt, the evaluation counts, and a database test that plants
  one document per access level and checks what reaches the model) and 12 `slow` tests with the
  real model (English, Arabic, both cross-language directions, an on-topic absent fact, three
  injection attacks in two languages, and the `xfail` limitation). Deliberate breakages: 19 of
  the answer module; 3 were missed on the first run and analysed one by one. Two were real gaps
  (the returned text was never checked for canonical markers; `ask()` was never checked to pass
  on `min_score`), confirmed when new tests caught both on a re-run. One was equivalent
  (`setdefault` versus assignment, since a dict keeps a key's first position), and the code now
  uses the plain assignment.
  Eleven of the evaluation module, where one miss showed a redundant guard, now removed.
- **Status:** Accepted. Closes Week 4's goal: question to grounded answer with citations, or an
  honest refusal, in Arabic, English and across languages.
- **Not decided yet:** hybrid search and reranking (Week 6), which may fix q043's retrieval
  miss; the HTTP API (Week 5); per-sentence citation checking; document trust against planted
  sources.

## D-020: A FastAPI backend whose permissions come from the server, never the request

- **Decision:** `bilingual_rag/api/app.py` provides `create_app()`, run with
  `uvicorn bilingual_rag.api.app:create_app --factory`. Four endpoints:
  - `POST /query` (`{"question", "k"}`) returns `{"answer", "refusal", "citations", "top_score"}`
    from `ask()` (D-019). `answer` is null exactly when `refusal` is set, so a refused or uncited
    reply is never shown as an answer. A missing LLM or an empty embeddings table is 503, not a
    refusal: nothing was decided.
  - `GET /documents` lists the permitted documents' metadata, without storage paths.
  - `POST /documents` uploads one file (multipart: the file plus the manifest's metadata fields),
    then chunks, stores and embeds it in one transaction.
  - `GET /health` reports the database (the existing `check_health`) and the LLM
    (`OllamaLLM.check()`, which reads Ollama's model list and generates nothing). It is 503 only
    when the database is down: without the LLM, documents can still be listed.
- **Dependencies (asked for and approved, each justified):** an `api` extra with `fastapi`
  (MIT), `uvicorn` (BSD-3), `python-multipart` (Apache-2.0, for uploads) and `httpx` (BSD-3,
  for FastAPI's test client; already installed by the embeddings extra). Versions and wheel sizes
  were read from PyPI's metadata before asking (about 2.8 MB new, `pydantic-core` the largest at
  about 2 MB); the installed versions matched the quote exactly.
- **Why the caller's access levels come from a server setting (the user's decision):**
  authentication is Week 7. Until then every caller gets `API_ACCESS_LEVELS` from the server's
  environment (default `public`). A request cannot supply levels: `QueryRequest` rejects unknown
  fields (`extra="forbid"`), so a client that sends `access_levels` gets a 422 instead of having
  it silently ignored. Week 7 replaces the setting with the levels in the caller's token; the
  endpoints do not change.
- **Why uploads need a static admin key (the user's decision):** an open upload endpoint would
  let anyone plant a document at any access level, the attack D-019 could not fully stop. Uploads
  need `X-Admin-Key` to match `ADMIN_API_KEY` (compared with `secrets.compare_digest`, so timing
  does not reveal how much of a guess was right); an unset key disables uploads (403), a missing
  or wrong one is 401, and a key under 16 characters is refused at startup. Week 7 replaces it
  with the Admin role.
- **Upload handling, each rule tested:** only the client filename's extension is used (md, txt,
  docx, pdf; anything else is 415), and the stored path is always `uploads/<id>.<ext>`, so the
  name cannot traverse paths or reach the database. The file is read up to 10 MB plus one byte
  (413 above the limit, so an oversized upload is never read whole), written to a temporary
  folder for the existing loaders, and deleted. Metadata goes through the same `DocumentMetadata`
  validation as the manifest (422 on failure). An existing id is 409: `store_document` would
  otherwise quietly replace a corpus document, and an upload is not the way to do that. A file
  that cannot be decoded or holds no text is 422.
- **A security gap found while building `GET /documents`, fixed first in its own commit:**
  `list_documents()` returned every document's metadata, including restricted titles, and titles
  can be sensitive on their own. It now requires `allowed_access_levels` with no default and
  filters in SQL, like `list_chunks` and `search()`. Only tests called it before.
- **Checked for real, over HTTP (not only through the test client):** uvicorn with bge-m3 and
  qwen3:8b against the development database, `API_ACCESS_LEVELS=public,employee`, and a random
  admin key generated in memory and never printed. Health 200 with both parts up; 27 of 32
  documents listed, only `public` and `employee`; the English and Arabic leave questions answered
  with citations; an English question answered from an Arabic source; the relocation question
  refused by the model; **"the maximum salary for grade G4" refused as `low_score`**, because its
  document is `hr`-level and never reached the evidence (access control, confirmed against the
  database); a request that tried to send its own levels, 422; an upload without the key, 401;
  with it, 201, embedded, and answerable at once ("valid for 12 months [1]"). The first query
  took 39 s while Ollama loaded the model into GPU memory; later ones took 5 to 12 s. The log
  held request lines only (no question text, no secrets). The smoke-test document was then
  deleted and the database checked: back to 32 documents, 46 chunks, 46 bge-m3 embeddings.
- **Validation:** 79 new tests: 50 API tests against a real database (one scratch database per
  test, since the API commits), 13 for the settings, 7 for `check()`, 9 for the document-list
  filter. Deliberate breakages: 38. In the API: 24, including widening either endpoint's levels,
  allowing extra request fields, skipping the key comparison, returning refused text as the
  answer, and storing the client's filename. One miss on the first run (the upload's `digits`
  field was never checked), fixed with a test that checks every stored field. In the settings: 7,
  one miss (tests used the constant for the key's minimum length, so weakening it passed), fixed
  with a literal 15-character key. In `check()`: 4, none missed. In the document-list filter: 3,
  one equivalent (removing the empty-levels early return, since `ANY('{}')` matches nothing in
  SQL either; kept for symmetry with `list_chunks` and `search()`).
- **Limits, stated openly:**
  - One PostgreSQL connection per request, no pool. Fine for a single user and a demo;
    `psycopg_pool` is the upgrade if concurrency matters.
  - The static admin key and the server-wide access levels are stand-ins until Week 7's users
    and roles; every caller shares one identity.
  - Starlette's test client warns that `httpx` is deprecated in favour of `httpx2`. It works; the
    switch is a new dependency, so it is not made without asking.
- **Status:** Accepted. Closes Week 5's goal.
- **Not decided yet:** hybrid search and reranking (Week 6); JWT users and roles (Week 7).

## D-021: Keyword search with PostgreSQL full-text search and per-language Snowball stemmers

- **Decision:** migration 0003 adds `chunks.search_vector` (a `tsvector`, GIN-indexed), and
  `retrieval/keyword.py` provides `keyword_search(conn, query, allowed_access_levels, *, k=5,
  language=None)`. Each chunk is stemmed in its document's language with PostgreSQL's built-in
  Snowball stemmers (`arabic` or `english`). The question, whose language is unknown, is stemmed
  with both, and its stemmed words are joined with OR. Results are ranked with `ts_rank_cd`.
- **Alternatives:** BM25 implemented in Python over the permitted chunks (true IDF weighting,
  but every query would read every permitted chunk out of the database, and the access filter
  would move out of SQL); a BM25 extension such as ParadeDB (not in the pgvector image, a new
  dependency); the `simple` configuration with no stemming (Arabic attaches the article and
  other affixes to the word, so `الإجازة` would never match `إجازة`).
- **Why PostgreSQL full-text search:** no new dependency, it scales with an index, and the access
  filter stays in the same SQL statement as the match, exactly like `search()` (D-015): restricted
  text never leaves the database. Measured on this server (PostgreSQL 17.11) before choosing:
  the `arabic` configuration exists and stems usefully (`الإجازات` to `اجاز`, `السنوية` to `سنو`),
  and `english` stems plurals and verb forms (`days` to `day`).
- **Why one stemmer per chunk, not both:** the Arabic stemmer passes Latin words through
  untouched and knows no English stopwords, so stemming every chunk with both would index "of"
  and "the" in every English chunk, and `ts_rank_cd` has no IDF to discount them. A chunk has no
  language column (its document does), so a trigger computes the vector on insert and on a text
  change, and a second trigger restems a document's chunks if its language changes. The schema
  keeps the index right whatever code writes a chunk (the D-012 principle).
- **Arabic text is still not normalized (D-004):** the Arabic stemmer folds hamza forms and
  strips the article, but only inside this index. The stored text, the embedding input and the
  text the LLM sees are unchanged; a test checks the stored text after stemming.
- **The question is untrusted input:** it is passed as a parameter, never formatted into SQL,
  and each stemmed word is quoted with `quote_literal` before becoming a tsquery. A mutation test
  showed the quoting is currently redundant: PostgreSQL's parser already splits on every tsquery
  operator character (measured with `localhost:8000`, `it's`, URLs, Windows paths and times; all
  built the same query quoted or not). It is kept as defence in depth against a future dictionary.
  Tests pass tsquery syntax (`& | ! :* <->`), quotes, backslashes and an SQL injection attempt as
  questions; each is treated as data.
- **Observed on the development corpus (qualitative):** the English and Arabic leave questions
  rank their own-language leave policy first; a question whose answer exists only in the other
  language gets only weak, irrelevant matches, as expected (keyword search cannot cross
  languages; that is what the vector search is for). Questions of only stopwords or punctuation
  match nothing. Applying the migration to the development database filled in all 46 chunks.
- **Validation:** 32 database tests (stemming per language, the triggers, the backfill of chunks
  stored before the migration, ranking, the language filter, access control with one planted
  document per level, untrusted input). Nineteen deliberate breakages (13 in the Python, 6 in the
  migration's SQL): 17 caught; 2 equivalent, both kept on purpose (the quoting above, and the
  empty-levels early return, since `ANY('{}')` matches nothing in SQL either; kept for symmetry
  with `search()` and `list_chunks`).
- **Limits:** `ts_rank_cd` is not BM25 (no IDF, no length normalization by default); hybrid
  search uses only its rank order, which blunts this, but a rare exact term is not boosted.
- **Status:** Accepted. Recall@k of keyword search alone and fused with vector search: D-022.

## D-022: Hybrid search by Reciprocal Rank Fusion, measured, and not the default

- **Decision:** `retrieval/hybrid.py` provides `hybrid_search` (vector and keyword search, top
  20 of each, fused with Reciprocal Rank Fusion, `1 / (60 + rank)` summed per chunk) and
  `retrieve(..., mode=)` for `vector`, `keyword` and `hybrid`, shared by the evaluation and,
  next, `ask()`. Hybrid results keep their **cosine similarity** as `score`, including chunks only
  the keyword search found (scored with one more vector query restricted to those chunk ids), so
  the answer step's 0.50 floor (D-019) keeps its meaning; only the order comes from RRF.
  `scripts/evaluate_retrieval.py --mode` measures any of the three. **The default stays
  `vector`**, because of the measurement below.
- **Why RRF:** it combines rankings by position alone, so cosine similarity and `ts_rank_cd`
  (incompatible scales) are never mixed, and it has one constant, left at the original paper's
  60 rather than tuned on 50 questions.
- **Measured (bge-m3, the 50 evaluation questions, 1200/200 chunking, every access level):**

  | Mode | Overall R@1 / R@3 / R@5 | MRR | Same-language R@1 | Near-miss R@1 | Cross-lingual R@1 / R@5 |
  | --- | --- | --- | --- | --- | --- |
  | Vector | 0.826 / 1.000 / 1.000 | 0.913 | 0.808 | 0.750 | **0.917 / 1.000** |
  | Keyword | 0.522 / 0.717 / 0.739 | 0.627 | 0.731 | 0.625 | 0.000 / 0.083 |
  | Hybrid (RRF) | 0.717 / 0.783 / 0.826 | 0.778 | **0.962** | **0.875** | **0.083 / 0.417** |

  Hybrid fixes most same-language top-1 misses (0.808 to 0.962) and helps near-misses, where an
  exact number or term matters. It **wrecks cross-lingual retrieval** (0.917 to 0.083), the
  retrieval direction this project exists to serve, and overall it is worse than vector alone.
- **The mechanism, checked question by question, not assumed:** on the 12 cross-lingual
  questions the right chunk was vector rank 1 in 11 cases, and fused rank 3 to 13 in most of
  them. **Every chunk ranked above it appeared in both lists** (7 of 7, 10 of 10, 12 of 12, and
  so on). RRF's defining rule, "found by both beats first in one", is biased against the other
  language here: the keyword list can only ever contain chunks in the question's language, so
  its vote always goes to the wrong language. The small corpus sharpens this: the vector top 20
  covers 43% of the 46 chunks, so 92 of 175 keyword hits were also in the vector list and
  collected two votes.
- **What was not done, on purpose:** keyword weights or the RRF constant were not tuned until
  the numbers looked better. With 50 questions (12 cross-lingual), any weight chosen here would
  be fitted to the test set. The principled fix is to use retrieval for candidates and let a
  cross-encoder reranker, which reads each question and chunk together in any language pair,
  decide the order (D-023).
- **Validation:** 42 tests (9 for the fusion function; database tests for hybrid search,
  `retrieve`, and access control in all three modes with one planted document per level; a
  hand-built embedder whose vector ranking deliberately disagrees with word overlap, since the
  hashing embedder is lexical and makes the two searches agree). 18 deliberate breakages. The
  first run missed 6 of the 16 in the fusion module, all weak tests: a tie test whose chunk ids
  sorted the same way by position and by id; a "keyword-only" test in which nothing was
  keyword-only (each search looks `max(k, candidates)` deep, which covered the whole 3-chunk
  test corpus); and fusion-order, keyword-list and default-mode tests that could not tell
  hybrid from vector with a lexical embedder. All 6 are caught after the hand-built embedder
  was added.
- **Status:** Accepted, with vector search as the default.

## D-023: A cross-encoder reranker (bge-reranker-v2-m3), measured, and used by default

- **Decision:** `retrieval/rerank.py` defines a `Reranker` protocol and `rerank()`;
  `retrieval/cross_encoder.py` wraps `BAAI/bge-reranker-v2-m3` (Apache-2.0, multilingual, scores
  0 to 1). `retrieve(..., reranker=)` fetches 20 candidates and the reranker keeps the top `k`.
  `SearchResult` gains `rerank_score`; `score` stays the cosine similarity, so nothing that read it
  changes meaning. `ask()` takes an optional reranker, and **the API uses vector search plus the
  reranker by default**. Scripts: `evaluate_retrieval.py --reranker`, `evaluate_answers.py
  --reranker`.
- **Model choice (asked for and approved):** read from each model card before downloading.
  bge-reranker-v2-m3 (2.27 GB of weights, 2.29 GB downloaded with config and tokenizer files); the
  smaller `mmarco-mMiniLMv2` (471 MB, trained on machine-translated data) was the alternative.
  Excluded: `gte-multilingual-reranker-base` (needs `trust_remote_code=True`, which runs Python
  code downloaded with the model) and `jina-reranker-v2-base-multilingual` (CC-BY-NC-4.0,
  non-commercial). A first probe before any code: a relevant pair scored 0.995 (English), 0.994
  (Arabic) and 0.980 / 0.948 across languages; an unrelated pair 0.0000; the relocation question
  against the leave policy (on topic, fact absent) 0.0028.
- **Retrieval, measured (bge-m3, the 50 questions, 20 candidates):**

  | Mode | Overall R@1 | MRR | Same-language R@1 | Near-miss R@1 | Cross-lingual R@1 |
  | --- | --- | --- | --- | --- | --- |
  | Vector | 0.826 | 0.913 | 0.808 | 0.750 | 0.917 |
  | Hybrid (D-022) | 0.717 | 0.778 | 0.962 | 0.875 | 0.083 |
  | **Vector + rerank** | **0.978** | **0.989** | 0.962 | **1.000** | **1.000** |
  | Hybrid + rerank | 0.978 | 0.989 | 0.962 | 1.000 | 1.000 |

  Vector search already has the right document in its top 3 for every question (R@3 = 1.000), so
  the only room left was the order, which is what a cross-encoder fixes. Hybrid adds no candidate
  the reranker can use here, so it is identical after reranking and not used. The one rank-1 miss
  (q020, an Arabic hotel-limit question) put the English translation of the same policy first,
  which states the same USD 220: the translated-pair undercount already seen in D-019.
- **A refusal signal, better than cosine but not clean (measured, top rerank score):** the lowest
  answerable question scored 0.021 and the highest absent-fact one 0.206, so no threshold
  separates them. The lowest answerable scores are all cross-lingual (0.021, 0.164, 0.168, 0.239):
  the reranker ranks them right but scores them lower, so a high floor would again refuse the
  other language first. At **0.01** no answerable question is refused and two of the four
  absent-fact ones are (cosine's floor caught one); the nearest answerable score, 0.021, is about
  twice the floor. **Chosen on the same 50 questions, with four absent-fact ones: a thin margin,
  not a calibrated threshold.** A reranked result is judged by its rerank score alone, never by
  its cosine (next point).
- **End to end, the first attempt was worse, and two causes were found and fixed:**

  | Configuration | Answered (of 46) | Cites expected | Right language | Absent refused |
  | --- | --- | --- | --- | --- |
  | Baseline (D-019: vector, old prompt) | 44 | 42 | 44 of 44 | 4 of 4 (1 by floor) |
  | Rerank, first attempt | 41 | 38 | 41 of 41 | 4 of 4 (2 by floor) |
  | New prompt, vector only | 46 | 38 | 46 of 46 | 4 of 4 (1 by floor) |
  | **New prompt, vector + rerank (chosen)** | **45** | **44** | 45 of 45 | 4 of 4 (2 by floor) |

  1. **The two floors fought.** q043's right chunk has rerank score 0.525 but cosine 0.488; the
     reranker surfaced it and D-019's cosine floor dropped it. Fix: reranked results are filtered
     by the rerank floor only.
  2. **The model copied the example citation number.** Rerank scores are sharply split (0.98
     against 0.003), so the floor often leaves one source, and with one source labelled `[1]` the
     model cited `[3]` or `[٤]`. Three of the five invented numbers across both runs were `[3]`,
     and the system prompt's example read "for example [1] or [1][3]". Tested on those questions
     before changing code: with the example replaced by "use only the numbers shown at the start
     of the sources; never invent a number", 4 of 5 cited correctly with the reranker (1 of 5
     before). A test now fails if the system prompt contains any example citation.
  After both fixes, every one of the 45 answered questions cites a document that holds its fact
  (44 the expected document, q004 its translation), including q029 (D-019's one wrong citation)
  and q043 (D-019's retrieval miss, answered correctly for the first time: "10,000 USD", checked
  against the contract procedure's table). The one refusal is q045, uncited: the model wrote
  `[٤]` with one source, and the system refused rather than guess.
- **What the prompt change costs without the reranker, stated plainly:** with vector search only,
  the new prompt answered all 46 but cited worse (38), and **produced one confidently wrong
  answer that passed every gate**: q043 "150,000 USD", a salary figure from the compensation
  bands, cited as if it were the contract limit (the old prompt refused this question). Five of
  its eight misses were translated-pair citations. The reranker is therefore the default, and the
  no-reranker path is documented as weaker, not recommended.
- **Memory and speed, measured:** in 16-bit the reranker takes 1,083 MiB of GPU memory. With bge-m3
  in 32-bit (about 2.2 GB) as well, the 8 GB GPU was full (7,573 MiB) and ten questions took
  61.5 s; with bge-m3 in 16-bit, 37.5 s (39% faster). 16-bit query embeddings gave the same top 5
  as 32-bit for 49 of 50 questions and the same Recall@k after reranking (R@1 0.978, cross-lingual
  1.000), so the API loads bge-m3 in 16-bit (`bge_m3(half=True)`); the ingestion script still
  embeds documents in 32-bit. Over real HTTP with all three models loaded (7,775 of 8,188 MiB,
  qwen3 100% on the GPU): warm answers in 2.6 to 5.9 s, and a clearly off-topic question refused by
  the rerank floor in 0.3 s without calling the LLM.
- **Unchanged and re-checked:** the injection tests against the real model (D-019) pass with the
  new prompt; the self-citing planted document is still the known `xfail`. Reranking only reorders
  what the access-filtered search returned; a test plants one document per access level and checks
  the reranker never sees another level's text.
- **Two operational notes:** one evaluation run failed with a database connection timeout while
  the database was healthy; free memory was 4.0 GB before the rerun, consistent with memory
  pressure from loading two models on a 15.8 GB machine (not proven). And a first count of this
  work's new tests was wrong (a `git stash` left the new, untracked test files in place); counted
  again against a clean worktree: 53.
- **Validation:** 53 new tests (reranking and the cross-encoder wrapper with stubs, 4 `slow` tests
  with the real model, reranking in `retrieve`, the floors, the API, 16-bit loading, the runner).
  38 deliberate breakages across seven files; one missed on the first run (`run_questions` never
  checked to pass the reranker on to each question), caught after a test was added.
- **Status:** Accepted. Closes Week 6: hybrid search built and measured (not used: D-022), and a
  reranker that measurably improves retrieval and, after two fixes, answers.

## D-024: Users, scrypt passwords and JWT login; permissions read from the database per request

- **Decision:** migration 0004 adds a `users` table (username, scrypt password hash, role,
  access levels, active flag). `bilingual_rag/auth/` provides password hashing, JWT issuing and
  checking, and `create_user` / `authenticate` / `get_user`. The API gains `POST /auth/token`
  (OAuth2 password form) and `GET /auth/me`; every endpoint except `/health` and `/auth/token`
  needs a bearer token. **This replaces D-020's interim stand-ins**: `API_ACCESS_LEVELS` and
  `ADMIN_API_KEY` are gone; the API now needs `JWT_SECRET` (at least 32 characters) and
  optionally `TOKEN_TTL_MINUTES` (default 60). Users are created with
  `scripts/create_user.py`; there is no self-registration.
- **Roles (the user's decision):** an **admin** reads every access level and may upload; an
  **employee** reads the levels chosen for them (default public and employee; for example an HR
  employee also gets hr). Admins are always created with every level, so a user's access levels
  are the whole of their document permission, and the role decides only who may upload.
- **Dependencies (asked for and approved):** `pyjwt` (MIT, 33 KB) in the `api` extra. Password
  hashing needs no dependency: the standard library's `hashlib.scrypt`.
- **Why the token carries no permissions:** it names the user (`sub`) and expires (`iat`,
  `exp`), nothing else. The API reads the user's role, levels and active flag from the database
  on **every** request, so a changed permission, a deactivated user or a deleted user takes
  effect on the next request, not when the token expires (tested: all three). The cost is one
  indexed lookup per request.
- **Why these password and token settings:**
  - scrypt with OWASP's recommended parameters (N=2^17, r=8, p=1): measured 0.36 s and about
    128 MiB per hash, which is what makes a stolen hash expensive to brute-force. Parameters are
    stored in each hash, so they can be raised later. Minimum password length 12.
  - JWTs are HS256 and **only HS256 is accepted when decoding**: tests send an unsigned
    `alg: none` token, an HS512 token, a token with a forged payload, tokens missing each required
    claim and an expired token; all are refused.
  - A wrong password and an unknown username get the same response, and an unknown username
    still runs one scrypt check, so neither the message nor the timing reveals which usernames
    exist (tested by counting the verification calls).
- **Two real bugs, both caught before they shipped:**
  - `create_user` first used psycopg's `conn.transaction()` to keep the connection usable after
    a duplicate username. In psycopg 3 that block **commits** when no transaction is already
    open, so the function silently committed its caller's transaction (breaking the "nothing
    here commits" rule of the repository layer). A test's rollback failed to undo a user, which
    exposed it. Fixed by checking the username format and uniqueness first, with the database
    constraints kept as the final guard; a new test checks from a second connection that nothing
    is visible before the caller commits.
  - `create_user.py --password-stdin`, run with a password piped from PowerShell 5.1, stored the
    password with an invisible byte-order mark (U+FEFF) in front, so the real password never
    matched. Found in the HTTP check below when a correct login failed, confirmed by testing the
    saved password with and without the mark. The script now strips a leading BOM; tests pipe
    text with and without one.
- **Checked for real, over HTTP** (bge-m3, the reranker and qwen3; a random `JWT_SECRET` added to
  the local `.env`, never printed; two throwaway users created with the script, deleted
  afterwards): no token, 401; a wrong password and an unknown user, the same 401 message; an HR
  employee sees 28 of the 32 documents (no engineering or management ones) and gets "60,000 USD
  [1]" for the grade G4 salary from the HR compensation bands (the question D-020 had to refuse,
  when every caller shared one public identity); an admin sees all 32; an employee's upload, 403;
  a tampered token, 401. The server log held no passwords or tokens.
- **Validation:** 97 new tests, net (Week 5's admin-key tests were replaced by role tests):
  passwords and tokens (pure), users against a real database, the create-user script, and the
  API with a real user per access level plus an admin in every test (the login, missing, bad,
  expired, foreign-secret and stale tokens, per-level access, the admin-only upload, and
  permission changes taking effect at once). 49 deliberate breakages across five files: 4 missed on
  the first run. Three were real gaps, each fixed with a test: comparing only the first bytes of a
  password hash (a tampered last byte now must fail), skipping the scheme check (a well-formed
  hash of another scheme now must be refused), and weakening the signing secret's minimum length
  (tests had used the constant; a literal 31-character secret is now refused). One was
  equivalent (an explicit missing-token check that `read_token` already covers) and the redundant
  check was removed. Tests hash with a lower scrypt cost for speed (each hash records its own
  cost); one marked test checks the production parameters.
- **Limits, stated openly:** no rate limiting or lockout on `/auth/token` (scrypt's cost slows
  guessing but does not stop it; a reverse proxy or a lockout table is the upgrade); no token
  revocation list (deactivating the user is the revocation); no password reset or user
  management endpoint (the script only creates users; changing one is SQL for now).
- **Status:** Accepted.

## D-025: A Streamlit UI that is only a client of the API

- **Decision:** `src/bilingual_rag/ui/app.py`, run with `streamlit run src/bilingual_rag/ui/app.py`:
  a login form, then an **Ask** tab (question, answer, sources table, or a plain refusal), a
  **Documents** tab (what the user may read) and, for admins only, an **Upload** tab.
  `ui/client.py` is a small `httpx` client for the API; `ui/text.py` holds what the page shows as
  plain functions. `RAG_API_URL` points the UI at the API (default `http://127.0.0.1:8000`).
- **Dependency (asked for and approved):** `streamlit` (Apache-2.0) in its own `ui` extra, about
  70 MB of wheels with its dependencies (262 MB installed), so the API never depends on it.
- **Why the UI only calls the API:** it never touches the database or the models, so every
  permission check stays in one tested place (D-024). The upload tab is hidden from employees, but
  that is convenience, not security: the API refuses their uploads (403) either way.
- **Security in the page:**
  - The login token lives in the browser session's server-side state, never in the URL or a
    cookie. Logging out clears the whole session, so the next person on the same browser cannot
    see the previous user's last answer (tested); a token the API rejects (expired, user
    deactivated) sends the user back to the login form.
  - Model output is untrusted text: answers are HTML-escaped before display (a test sends
    `<script>` and `<img onerror=...>`), then shown with `dir="auto"` so Arabic answers read
    right to left.
  - Refusals are explained in plain English and Arabic, one message per reason (D-019, D-023).
- **Three defaults changed in `.streamlit/config.toml`, each found by running it:**
  - Streamlit listened on **every network interface** by default and, at startup, looked up and
    printed this machine's public IP address. It now binds to `127.0.0.1` (confirmed from the
    operating system's listening sockets); serving it on a network should be a deliberate,
    HTTPS-fronted choice.
  - Anonymous usage statistics are switched off (on by default).
  - The developer toolbar, with its "Deploy" to Streamlit's cloud button, is hidden. Uploads above
    10 MB are refused by the page before reaching the API's own limit.
- **Checked for real:** the API and the UI running together; the login page renders in a browser.
  The logged-in flows were driven with Streamlit's own headless `AppTest` against the real API
  and database rather than through the browser: typing a password into a login form is left to a
  person, even for a throwaway local account.
- **Validation:** 27 new tests. The API client against the real app (login, a wrong password,
  per-user documents and answers, admin-only upload, a rejected token logging the client out,
  readable validation errors, an unreachable API); the page itself through `AppTest` (login form,
  wrong password, tabs by role, answer and sources, a bilingual refusal, logout); the display
  helpers. 23 deliberate breakages across three files; 3 missed on the first run, all real gaps,
  each fixed with a test: validation errors shown as a raw structure (the message was still inside
  it), `k` not sent to the API (the test user could see one document), and **logging out without
  clearing the session**, which would have shown the previous user's last answer to the next one.
- **Limits:** English interface labels (the content, answers and refusals are bilingual); one
  question at a time, no chat history; the first question after the API starts waits for the LLM
  to load (measured 31.5 to 39 s, D-020 and D-023).
- **Status:** Accepted. Closes Week 7: Streamlit UI, JWT authentication, Admin and Employee roles,
  document access control.

## D-026: GitHub Actions CI runs lint and the fast suite against a real database

- **Decision:** `.github/workflows/ci.yml` runs on every push to `main` and every pull request.
  One job on `ubuntu-latest` with Python 3.14: `ruff check`, `ruff format --check`, then the fast
  pytest suite against a `pgvector/pgvector:0.8.6-pg17` service container, the image
  `docker-compose.yml` uses. Settings come from environment variables only; there is no `.env`
  in CI (D-011).
- **Database tests cannot pass by skipping:** the job sets `RAG_REQUIRE_DATABASE=1` (D-011), so
  an unreachable database fails them. Checked: with the database pointed at a closed port, the 11
  tests of `test_database_connection.py` are skipped without the flag and error with it.
- **No model downloads:** `HF_HUB_OFFLINE=1`, so a fast test that tries to fetch a model fails
  instead of downloading gigabytes. The `slow` tests (bge-m3, the reranker, qwen3:8b through
  Ollama) are not run in CI: they need a GPU and gigabytes of weights. Run them locally with
  `pytest --slow`.
- **CPU-only torch:** two fast unit tests import modules that import torch at the top
  (`embeddings.sentence_transformer`, `retrieval.cross_encoder`). The job installs PyTorch's
  CPU wheel from PyTorch's own index (196 MB) before the extras. From PyPI, pip would install the
  CUDA build and several GB of NVIDIA libraries the suite never uses.
- **Hardening:** actions pinned by commit SHA (a tag can be moved to other code), a read-only
  `GITHUB_TOKEN`, Git credentials not kept after checkout. The database password is a fixed value
  for a container that only exists during the job, not a secret.
- **Checked for real:** the first run (PR #23) passed on Linux with the same result as on
  Windows, **1375 passed, 37 skipped (all `slow`), 1 xfailed**. It took 2 min 27 s: installing 77 s,
  tests 42 s. Before pushing, the same suite passed in a clean local worktree with no `.env` and
  `HF_HUB_OFFLINE=1`.
- **Limits:** one Python version (3.14, the one developed on; `requires-python` allows 3.12). The
  `slow` tests and the measured evaluations run only on the developer's machine. No branch
  protection yet: a failing run does not block merging until the repository settings require it.
- **Status:** Accepted. Week 8, step 1.
