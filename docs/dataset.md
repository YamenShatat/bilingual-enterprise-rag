# Synthetic dataset

**Synthetic dataset created for demonstration purposes. No confidential company data is
included.** Acme MENA Technology is a fictional company, and every document, number, name,
extension and email address (all on the reserved `.example` domain) is invented.

## At a glance

Measured on the committed corpus:

| | |
| --- | --- |
| Documents | 32 (17 English, 15 Arabic) across 22 topics |
| Words | about 3,200 English and 2,700 Arabic (whitespace-separated) |
| Chunks at the default settings (1200/200) | 46 (25 English, 21 Arabic); median 853 characters |
| Formats | 22 Markdown, 6 DOCX, 2 plain text, 2 PDF |
| Departments | HR, IT, Security, Finance, Operations, Legal, Engineering, General |
| Access levels | public (4), employee (23), engineering (2), hr (1), management (2) |

Every document ingests without being skipped, and every DOCX and the English PDF return 100%
of their source words. The Arabic PDF returns 84% (see [Formats](#formats-and-provenance)).

## Layout

```text
data/
├── manifest.json        one entry per document: language, format, department, access, pair
├── synthetic/<dept>/    the corpus that gets ingested
└── sources/<dept>/      Markdown sources the DOCX and PDF documents are generated from
```

Files are named `<topic>_<language>.<ext>`. `manifest.json` is the single source of truth, and
tests keep it in step with the files (see [Guarantees](#guarantees-enforced-by-tests)).

## Topics

| # | Topic | Department | Languages | Format | Access |
| --- | --- | --- | --- | --- | --- |
| 1 | Annual leave policy | HR | EN + AR | md | employee |
| 2 | Employee benefits guide | HR | EN + AR | docx | employee |
| 3 | Onboarding guide | HR | EN + AR | md | employee |
| 4 | Code of conduct | Legal | EN + AR | md | public |
| 5 | VPN troubleshooting guide | IT | EN + AR | md | employee |
| 6 | Incident response policy | Security | EN + AR | md | employee |
| 7 | Travel and expense policy | Finance | EN + AR | md | employee |
| 8 | Data retention policy | Legal | EN + AR | md | employee |
| 9 | Procurement policy | Operations | EN + AR | docx | employee |
| 10 | Employee handbook | General | EN + AR | md | public |
| 11 | Remote work policy | HR | EN only | md | employee |
| 12 | Laptop setup guide | IT | EN only | txt | employee |
| 13 | Compensation bands | HR | EN only | docx | hr |
| 14 | Code review guidelines | Engineering | EN only | md | engineering |
| 15 | Release process | Engineering | EN only | md | engineering |
| 16 | Facilities access policy | Operations | EN only | pdf | employee |
| 17 | Budget review procedure | Finance | EN only | md | management |
| 18 | Password policy | Security | AR only | md | employee |
| 19 | Working hours and overtime | HR | AR only | md | employee |
| 20 | Petty cash policy | Finance | AR only | txt | employee |
| 21 | Contract approval procedure | Legal | AR only | docx | management |
| 22 | Business continuity plan | Operations | AR only | pdf | employee |

## What the corpus is designed to test

**All four retrieval directions.** Ten topics exist in both languages. Seven exist only in
English and five only in Arabic, so a question can have its answer in the *other* language:

| Direction | Where the answer lives |
| --- | --- |
| English question, English document | any English document |
| Arabic question, Arabic document | any Arabic document |
| Arabic question, English document | the 7 English-only topics (for example the release cadence) |
| English question, Arabic document | the 5 Arabic-only topics (for example the overtime rates) |

**Facts that exist in one language only.** Overtime pay rates of 125%, 150% and 200%, the
6-hour Ramadan working day, the petty cash limits, contract approval thresholds and the
business continuity recovery targets appear only in Arabic. The release cadence, the code
review rules, the compensation bands and the laptop refresh cycle appear only in English.

**Near-misses that a careless retriever will confuse.**

- Approval thresholds appear in four places with different values: purchases (USD 1,000 and
  10,000), contracts (USD 10,000 and 100,000), travel (USD 3,000) and budget reallocation
  (USD 25,000).
- USD 50 is both the gift limit in the code of conduct and the single-payment limit for petty
  cash.
- Reporting deadlines differ: a lost laptop within 1 hour, a lost badge within 2 hours, a P1
  incident response within 15 minutes.
- The working week is stated in the handbook (English and Arabic) and in the Arabic overtime
  policy, and the numbers must agree.
- Annual leave (21 days), sick leave (10 days) and maternity leave (60 days) sit in different
  documents.

**Facts that are deliberately absent**, for "I could not find this information" tests:
executive compensation (the compensation bands document says the Board sets it and it is not
described), stock options, pensions and relocation allowances. A test fails if any of these
appear, or if any line links an executive to a salary.

**Access control.** One HR-only document (compensation bands), two management-only documents
(budget review, contract approval), and two engineering-only documents. These exist so
Week 7 can prove that unauthorised content never reaches the language model.

**Digit systems.** Six Arabic documents use Arabic-Indic digits (٢١) and nine use Western
digits (21), as real Arabic business documents do. Each document uses only one style, and the
manifest records which.

## Formats and provenance

- **Markdown and plain text** are written directly.
- **DOCX** files are generated from the Markdown in `data/sources/` by
  `scripts/build_corpus_docx.py`, using the standard-library writer in
  `src/bilingual_rag/corpus/markdown_docx.py`. Right-to-left paragraphs and tables are marked as
  Word does, headings use Heading 1 and 2 styles, and two guides (benefits and procurement, four
  files) have an explicit page break so citations can name page 2. All six open in Microsoft Word without a compatibility banner.
  They were generated, not authored in Word.
- **PDF** files were exported by Microsoft Word 16.0.10417 from an intermediate `.docx` with
  `scripts/export_pdfs_with_word.ps1`, so they behave like genuine Word PDFs.

Text survival through the pipeline, measured as the share of source words recovered:

| Format | Documents | Source-word recall |
| --- | --- | --- |
| DOCX | 6 | 100% for each |
| PDF, English | 1 | 100% |
| PDF, Arabic | 1 | 84% |

The Arabic PDF is the honest weak spot: it is the known PDFium limitation described in
`docs/pdf-extraction.md`, measured here on a whole document. Retrieval results should be
reported separately for it, so the effect of PDF extraction is visible and not averaged away.

## Guarantees enforced by tests

`tests/integration/test_corpus_manifest.py` and `test_synthetic_corpus.py` check that:

- the manifest lists exactly the files on disk, with unique ids and valid fields;
- every DOCX equals what its Markdown source produces, and every source is used;
- each document is in its declared language (no Arabic letters in English documents, at least
  85% Arabic letters in Arabic ones) and uses only its declared digit style;
- the two members of every bilingual pair share topic, department and access level, and state
  **the same numbers in the same structure** (same number of sections and table rows);
- facts meant to be absent are absent, and no line links an executive to a salary;
- every document ingests, key Arabic and English phrases survive verbatim, chunks stay within
  the size limit, and ingestion is deterministic.

Each of these was checked by deliberately breaking the corpus in a realistic way (changing a
number in one language only, dropping a heading, mixing digit styles, editing a source without
rebuilding, leaking an absent fact, mislabelling a language or access level, deleting a file)
and confirming the test fails.

## Limitations

- **Small.** About 6,000 words and 46 chunks. Retrieval will be easy on it, so the Week 3
  benchmark should add harder distractors, and later the corpus should grow.
- **Uniform style.** The documents were written in one voice, are short (18 of 32 fit in a
  single chunk), and lack images, footnotes, long tables of contents and scanned pages.
- **AI-written Arabic.** The English and Arabic text was written with AI assistance. The Arabic
  is Modern Standard Arabic written directly, not run through a translation service, but it has
  **not been reviewed by a professional translator or a native-speaker editor**, so phrasing may
  differ from what a native corporate writer would use. Facts and numbers are kept identical by
  the tests; wording quality is not.
- **One Arabic PDF and no dialect.** Conclusions about Arabic PDFs rest on a single document
  plus the fixtures, and no Gulf, Levantine or Egyptian dialect text is included.
- **Generated DOCX.** The DOCX files come from a script, so they lack the messy formatting
  (manual numbering, stray styles, tracked changes) that real Word files have.
