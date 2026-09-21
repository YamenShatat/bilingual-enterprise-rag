"""PDF loader tests, using real PDFs from Microsoft Word and Chromium (see fixtures README)."""

import re
import shutil
from pathlib import Path

import pytest

from bilingual_rag.ingestion.loaders import DocumentLoadError, load_document

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdf"
WORD_PDF = FIXTURES / "bilingual_word.pdf"
CHROMIUM_PDF = FIXTURES / "bilingual_chromium.pdf"
SCAN_PDF = FIXTURES / "image_only_scan.pdf"

# The Arabic sentences the two bilingual fixtures were built from (ground truth).
ARABIC_TRUTH = [
    "سياسة الإجازة السنوية",
    "يحصل الموظف على ٢١ يومًا من الإجازة السنوية بعد إنهاء فترة التجربة.",
    "يجب تقديم الطلب عبر بوابة الموارد البشرية قبل ١٠ أيام عمل.",
    "أهلاً بكم في شركة أكمي، آمل أن تكون على ما يرام في مدرسة الهدى.",
    "للاتصال بالشبكة الداخلية استخدم VPN ثم افتح Cisco AnyConnect.",
]

# Measured on the Word fixture with pypdfium2 5.13: 90% word recall. The guard sits a
# little below the measurement so it flags real regressions, not noise.
MIN_WORD_RECALL = 0.85


def words(text: str) -> list[str]:
    return [w for w in re.sub(r"[.,،:;؛؟?!()]", " ", text).split() if w]


def arabic_word_recall(document_text: str) -> float:
    """Share of ground-truth words that appear intact, ignoring their order."""
    expected = [w for line in ARABIC_TRUTH for w in words(line)]
    found = set(words(document_text))
    return sum(word in found for word in expected) / len(expected)


def full_text(path: Path) -> str:
    return "\n".join(page.text for page in load_document(path).pages)


def has_presentation_forms(text: str) -> bool:
    return any(0xFB50 <= ord(c) <= 0xFDFF or 0xFE70 <= ord(c) <= 0xFEFF for c in text)


# --- structure ---------------------------------------------------------------------


def test_pages_are_numbered_from_one_and_keep_the_filename() -> None:
    document = load_document(WORD_PDF)

    assert document.filename == "bilingual_word.pdf"
    assert [page.number for page in document.pages] == [1, 2, 3]


def test_english_text_is_extracted_exactly() -> None:
    first_page = " ".join(load_document(WORD_PDF).pages[0].text.split())

    assert "Full-time employees are entitled to 21 working days of paid annual leave" in first_page
    assert "Requests must be submitted through the HR portal." in first_page


def test_latin_terms_inside_arabic_text_are_kept() -> None:
    third_page = load_document(WORD_PDF).pages[2].text

    assert "VPN" in third_page
    assert "Cisco AnyConnect" in third_page
    assert "ext. 4455" in third_page


def test_arabic_indic_numbers_are_not_reversed() -> None:
    """Why PDFium was chosen: pypdf and PyMuPDF turned ٢١ into ١٢, silently changing facts."""
    arabic_page = load_document(WORD_PDF).pages[1].text

    assert "٢١" in arabic_page
    assert "١٠" in arabic_page


@pytest.mark.parametrize("path", [WORD_PDF, CHROMIUM_PDF], ids=["word", "chromium"])
def test_arabic_presentation_forms_do_not_reach_the_text(path: Path) -> None:
    """PDFium returns base letters, so no NFKC step is needed (see docs/decisions.md D-004)."""
    assert not has_presentation_forms(full_text(path))


# --- Arabic extraction quality (measured, see docs/decisions.md D-008) --------------


def test_arabic_word_recall_on_a_word_pdf_stays_above_the_measured_floor() -> None:
    assert arabic_word_recall(full_text(WORD_PDF)) >= MIN_WORD_RECALL


@pytest.mark.xfail(
    strict=True,
    reason="Known limitation: PDFium reads Chromium-generated Arabic poorly (37% recall)",
)
def test_arabic_word_recall_on_a_chromium_pdf() -> None:
    assert arabic_word_recall(full_text(CHROMIUM_PDF)) >= MIN_WORD_RECALL


def test_visual_order_arabic_is_read_back_in_logical_order() -> None:
    """A legacy-style page (glyphs stored right-to-left) comes back as the word سياسة."""
    legacy_page = load_document(CHROMIUM_PDF).pages[3].text

    assert "سياسة" in legacy_page


# --- pages without text ------------------------------------------------------------


def test_image_only_pdf_keeps_its_page_with_empty_text() -> None:
    document = load_document(SCAN_PDF)

    assert [page.number for page in document.pages] == [1]
    assert document.pages[0].text.strip() == ""


# --- errors and file handling ------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [b"just some text", b"%PDF-1.4\nthis is not a real pdf", WORD_PDF.read_bytes()[:300]],
    ids=["not-a-pdf", "bad-body", "truncated"],
)
def test_unreadable_pdf_raises_load_error(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(content)

    with pytest.raises(DocumentLoadError, match="broken.pdf"):
        load_document(path)


def test_extension_matching_is_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "REPORT.PDF"
    shutil.copy(WORD_PDF, path)

    assert len(load_document(path).pages) == 3


def test_file_is_not_locked_after_loading(tmp_path: Path) -> None:
    """On Windows an open handle would make this delete fail."""
    path = tmp_path / "temporary.pdf"
    shutil.copy(WORD_PDF, path)

    load_document(path)
    path.unlink()

    assert not path.exists()


def test_missing_pdf_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_document(tmp_path / "missing.pdf")
