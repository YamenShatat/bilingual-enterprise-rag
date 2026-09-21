# PDF text extraction for Arabic and English

Why the project uses PDFium (via `pypdfium2`) to read PDFs, what was measured, and what
does not work yet.

**Scope of the evidence.** Two PDF producers and a handful of invented sentences. This is a
sanity check that changed a decision, not a benchmark. Measured on 2026-09-21 with
`pypdfium2` 5.13.0, `pypdf` 6.19.0, `pdfminer.six` 20260107 and `PyMuPDF` 1.28.2.
Reproduce with `scripts/compare_pdf_extractors.py`; fixtures are described in
`tests/fixtures/pdf/README.md`.

## Method

The same bilingual content (an English page, an Arabic page and a mixed Arabic/Latin page)
was produced by Microsoft Word and by Microsoft Edge (Chromium) and read back with four
extractors. Two metrics were computed against the known source sentences:

- **Word recall:** the share of ground-truth Arabic words that come back intact, ignoring
  order. Arabic presentation-form characters (U+FB50-U+FDFF, U+FE70-U+FEFF) were folded to
  base letters first, so an extractor was not penalised for them.
- **Numbers:** how many of the two numbers ٢١ and ١٠ appear unchanged. A reversal turns ٢١
  ("21") into ١٢ ("12").

## Results

| Extractor | Word PDF: recall | Word PDF: numbers | Chromium PDF: recall | Chromium PDF: numbers |
| --- | --- | --- | --- | --- |
| pypdf | 88% | 0/2 | 86% | 0/2 |
| **pypdfium2** | **90%** | **2/2** | 37% | **2/2** |
| pdfminer.six | 10% | 2/2 | 10% | 2/2 |
| PyMuPDF (reference only) | 80% | 0/2 | 88% | 0/2 |

## What each extractor gets wrong

- **pypdf** and **PyMuPDF** reverse Arabic-Indic numbers (`٢١` becomes `١٢`, `١٠` becomes `٠١`)
  on both producers. In a policy assistant that silently changes "21 days" into "12 days".
  This is the deciding failure.
- **pdfminer.six** returns fully visual order: the letters of every Arabic word are reversed.
  Unusable without an extra reordering step.
- **pypdfium2** keeps letters and numbers correct on the Word PDF but has two flaws, below.
  On the Chromium PDF, which stores Arabic in a different way, it also reverses many words
  (37% recall).
- Chromium/Edge PDFs store Arabic as presentation forms, so even a "modern" producer yields
  them from some extractors. PDFium already returns base letters, so the cleaner needs no
  NFKC step (see decision D-004).

## Decision

Use **pypdfium2** (BSD-3-Clause or Apache-2.0, with PDFium's own permissive licenses).

- It is the only candidate that keeps numbers correct on both producers and it has the best
  recall on the Word PDF, the most common enterprise producer.
- PyMuPDF is AGPL-3.0, which is a licensing problem for an MIT project, and it did not read
  numbers correctly either.
- Cost: an 8 MB binary dependency, and on Windows the venv path must be short, because
  pypdfium2 ships deeply nested license files and installing it under a very long path fails
  with `OSError: No such file or directory` (Windows 260-character path limit).

## Known limitations of the current loader

Observed on the Word PDF (`السنوية اإلجازة سياسة` is what PDFium returned for the heading
`سياسة الإجازة السنوية`):

1. **Word order within an Arabic line is visual, so effectively reversed.** Lines are in the
   right order, and the letters inside each word are right, but the words run left to right.
   Bag-of-words retrieval is largely unaffected. Sentence boundaries are: a full stop can
   appear at the start of a line, so the chunker's sentence splitting is unreliable on Arabic
   PDFs.
2. **Lam-alef ligatures come out with the two letters swapped:** `الإجازة` becomes `اإلجازة`,
   `للاتصال` becomes `لالتصال`. Words containing them fail exact matching.
3. **Tanween can be displaced:** `أهلاً` came out as `ًأهال`, and `يومًا` as `ماًيو`.
4. **Chromium/Edge-generated Arabic PDFs extract poorly** (37% word recall).
5. **Scanned PDFs have no text layer.** The loader keeps the page (numbering stays correct)
   with empty text and the pipeline reports the file as "no text after cleaning".

Consequences for the project:

- **Prefer DOCX or native text for Arabic source documents.** They store the exact text, so
  none of the above applies (see the comparison below).
- A PDF-derived Arabic answer must not be treated as more trustworthy than the extraction
  quality allows. The evaluation set should include PDF-derived questions so any loss shows
  up as a measured recall drop, not a surprise.
- Real fixes are OCR or a layout/vision model, evaluated in a later phase. Heuristic
  reordering was deliberately not added: it was tuned on only two producers and would corrupt
  PDFs that already extract correctly.

## The same content as DOCX

The Word fixture was also saved as a `.docx` (`tests/fixtures/docx/bilingual_word.docx`, same
sentences plus a small table on the English and Arabic pages) and read with the DOCX loader.
Both numbers below are enforced by tests, not just measured once.

| Same Word content | Arabic word recall | Numbers `٢١` and `١٠` | Word order in a line |
| --- | --- | --- | --- |
| PDF read with pypdfium2 | 90% | 2/2 | visual (reversed) |
| **DOCX read with the DOCX loader** | **100%** | **2/2** | reading order |

Every sentence comes back verbatim, lam-alef words and tanween included, and the tables become
`cell | cell` rows (for example `الإجازة السنوية | ٢١`). See decision D-009.

## Reproducing

```powershell
py -3.14 -m venv C:\Users\you\.venvs\pdf-exp
$py = "C:\Users\you\.venvs\pdf-exp\Scripts\python.exe"
& $py -m pip install pypdfium2 pypdf pdfminer.six pymupdf
& $py scripts\compare_pdf_extractors.py
```
