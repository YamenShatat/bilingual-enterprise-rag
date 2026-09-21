# Data

This folder holds the documents used to develop and evaluate the system.

**Synthetic dataset created for demonstration purposes. No confidential company data is included.**

The corpus describes a fictional company, *Acme MENA Technology*: 32 documents in English and
Arabic, deliberately mixing bilingual pairs, English-only and Arabic-only documents so that all
four retrieval directions (EN→EN, AR→AR, AR→EN, EN→AR) can be evaluated. The full description,
coverage, designed traps and limitations are in [`docs/dataset.md`](../docs/dataset.md).

## Layout

```text
data/
├── manifest.json        one entry per document (language, format, department, access, pair)
├── synthetic/<dept>/    the corpus that gets ingested: <topic>_<language>.<ext>
└── sources/<dept>/      Markdown sources for the DOCX and PDF documents
```

- Markdown and plain-text documents live in `synthetic/` and are edited directly.
- DOCX documents are **generated**: edit the Markdown in `sources/`, then run
  `python scripts/build_corpus_docx.py` (add `--check` to verify without writing).
- PDF documents are exported from Microsoft Word; see `scripts/export_pdfs_with_word.ps1`.
- Text files are UTF-8 without a BOM with LF line endings, enforced by a test. Tests also
  check that the manifest matches the files and that bilingual pairs state the same numbers.
