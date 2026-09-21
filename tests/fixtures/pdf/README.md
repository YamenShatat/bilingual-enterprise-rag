# PDF test fixtures

Small PDFs made for the PDF loader tests. They contain only invented sample sentences.

| File | Producer | Pages | Purpose |
| --- | --- | --- | --- |
| `bilingual_word.pdf` | Microsoft Word 16.0.10417 (`ExportAsFixedFormat`) | 3 | English page, Arabic page, mixed Arabic/Latin page. Represents the most common enterprise producer. |
| `bilingual_chromium.pdf` | Microsoft Edge 153.0.4234.48 (headless print to PDF) | 4 | Same content as the Word file, plus a fourth "legacy-style" page whose glyphs are stored in visual order. Chromium stores Arabic as presentation forms, which makes it a hard case. |
| `image_only_scan.pdf` | Microsoft Edge 153.0.4234.48 | 1 | A single image and no text layer, standing in for a scanned page. |

The Arabic ground truth lives in `tests/unit/test_pdf_loader.py` (`ARABIC_TRUTH`).

## How they were made

- **Word:** a native Word document built through automation with the English text on page 1, the
  right-to-left Arabic paragraphs on page 2 and the mixed paragraph on page 3, then exported to PDF.
- **Chromium:** an HTML page with one `<div>` per page, printed with
  `msedge --headless=new --no-pdf-header-footer --print-to-pdf=...`. Page 4 uses
  `unicode-bidi: bidi-override` on the presentation-form glyphs of the word سياسة, in reversed
  order, to mimic legacy PDF tools.
- **Scan:** an HTML page containing only a generated PNG, printed the same way.

These are regression fixtures, not a benchmark: two producers and a handful of sentences.
The measurements they support are recorded in `docs/decisions.md` (D-008).
