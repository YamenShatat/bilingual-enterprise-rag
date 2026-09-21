# DOCX test fixtures

| File | Producer | Pages | Purpose |
| --- | --- | --- | --- |
| `bilingual_word.docx` | Microsoft Word 16.0.10417 (saved through automation) | 3 | The same content as `tests/fixtures/pdf/bilingual_word.pdf`, plus a small English table on page 1 and a right-to-left Arabic table on page 2. |

Page 1 is English, page 2 is Arabic, and page 3 mixes Arabic and Latin text. The pages are
separated by explicit page breaks, and Word also wrote its `lastRenderedPageBreak` markers.

The expected text of every page is spelled out in `tests/unit/test_docx_loader.py`, and the
Arabic ground truth is in `tests/support/arabic.py`. Because a DOCX stores text in reading
order, the loader is expected to return it exactly, unlike the PDF of the same content.

Other constructs (tracked changes, text boxes, fields, page-break variants, hostile XML) are
covered by small hand-built documents from `tests/support/docx_builder.py`, not by fixtures.
