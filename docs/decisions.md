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
- **Open item:** Arabic presentation forms (U+FB50-FEFF) sometimes appear in PDF
  extraction. NFKC would fix them but also rewrites unrelated characters. To be decided
  when the PDF loader exists and real extractor output can be inspected.
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
