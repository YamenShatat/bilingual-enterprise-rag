r"""Compare PDF text extractors on the bilingual fixtures.

This is a sanity check, not a benchmark: two PDF producers and a handful of sentences.
It exists so the numbers quoted in docs/pdf-extraction.md can be reproduced.

The alternatives are NOT project dependencies. Run it in a separate throwaway venv:

    py -3.14 -m venv C:\Users\you\.venvs\pdf-exp
    $py = "C:\Users\you\.venvs\pdf-exp\Scripts\python.exe"
    & $py -m pip install pypdfium2 pypdf pdfminer.six pymupdf
    & $py scripts\compare_pdf_extractors.py

Keep the venv path short: pypdfium2 contains deeply nested files and installation fails
when the total path exceeds Windows' 260-character limit.

Metrics, computed against the known source sentences:
  word recall  share of ground-truth Arabic words found intact, ignoring order
  numbers      how many of the numbers ٢١ and ١٠ appear unchanged (reversal turns ٢١ into ١٢)
PyMuPDF is included as a reference only: it is AGPL-licensed and is not used by the project.
"""

import io
import re
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "pdf"
PDFS = ["bilingual_word.pdf", "bilingual_chromium.pdf"]

ARABIC_TRUTH = [
    "سياسة الإجازة السنوية",
    "يحصل الموظف على ٢١ يومًا من الإجازة السنوية بعد إنهاء فترة التجربة.",
    "يجب تقديم الطلب عبر بوابة الموارد البشرية قبل ١٠ أيام عمل.",
    "أهلاً بكم في شركة أكمي، آمل أن تكون على ما يرام في مدرسة الهدى.",
    "للاتصال بالشبكة الداخلية استخدم VPN ثم افتح Cisco AnyConnect.",
]
NUMBERS = ["٢١", "١٠"]


def fold_presentation_forms(text: str) -> str:
    """NFKC applied only to Arabic presentation forms, so unrelated characters are untouched."""
    return "".join(
        unicodedata.normalize("NFKC", char)
        if 0xFB50 <= ord(char) <= 0xFDFF or 0xFE70 <= ord(char) <= 0xFEFF
        else char
        for char in text
    )


def words(text: str) -> list[str]:
    return [w for w in re.sub(r"[.,،:;؛؟?!()]", " ", text).split() if w]


def with_pypdf(data: bytes) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]


def with_pypdfium2(data: bytes) -> list[str]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        return [page.get_textpage().get_text_range() for page in pdf]
    finally:
        pdf.close()


def with_pdfminer(data: bytes) -> list[str]:
    from pdfminer.high_level import extract_text

    return extract_text(io.BytesIO(data)).split("\x0c")[:-1]


def with_pymupdf(data: bytes) -> list[str]:
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        return [page.get_text() for page in document]


EXTRACTORS: list[tuple[str, Callable[[bytes], list[str]]]] = [
    ("pypdf", with_pypdf),
    ("pypdfium2", with_pypdfium2),
    ("pdfminer.six", with_pdfminer),
    ("PyMuPDF (reference)", with_pymupdf),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    expected = [word for line in ARABIC_TRUTH for word in words(line)]
    for name in PDFS:
        data = (FIXTURES / name).read_bytes()
        print(f"\n### {name}")
        print(f"{'extractor':22} {'pages':>5} {'word recall':>12} {'numbers':>8}")
        for label, extract in EXTRACTORS:
            try:
                pages = extract(data)
            except ImportError as exc:
                print(f"{label:22} skipped ({exc})")
                continue
            text = fold_presentation_forms("\n".join(pages))
            found = set(words(text))
            recall = sum(word in found for word in expected) / len(expected)
            numbers = sum(number in text for number in NUMBERS)
            print(f"{label:22} {len(pages):>5} {recall:>12.0%} {numbers:>5}/{len(NUMBERS)}")


if __name__ == "__main__":
    main()
