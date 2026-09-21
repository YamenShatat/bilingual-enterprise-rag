"""End-to-end checks: the real synthetic corpus in ``data/synthetic`` through the pipeline."""

import json
import unicodedata
from pathlib import Path

import pytest

from bilingual_rag.ingestion.chunker import DEFAULT_CHUNK_SIZE
from bilingual_rag.ingestion.pipeline import ingest_directory

DATA = Path(__file__).resolve().parents[2] / "data"
CORPUS = DATA / "synthetic"
DOCS = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))["documents"]
LANGUAGE = {doc["path"]: doc["language"] for doc in DOCS}
MIN_ARABIC_LETTER_RATIO = 0.85  # the lowest Arabic chunk measures 0.899

# Phrases that must reach the chunks verbatim: hamza forms, tanween, Arabic-Indic digits,
# the Arabic decimal separator, Latin text embedded in Arabic, and one fact from each format.
EXPECTED_PHRASES = {
    "hr/annual_leave_policy_en.md": ["21 working days of paid annual leave", "1.75 days per month"],
    "hr/annual_leave_policy_ar.md": ["٢١ يوم عمل", "الإجازة السنوية", "بمعدل ١٫٧٥ يوم في الشهر"],
    "hr/remote_work_policy_en.md": ["up to 2 days per week", "USD 300"],
    "security/password_policy_ar.md": ["14 حرفًا", "180 يومًا", "security@acme-mena.example"],
    "hr/working_hours_and_overtime_ar.md": [
        "أيام العمل العادية | ١٢٥٪ من الأجر الساعي",
        "٦ ساعات يوميًا",
    ],
    "finance/travel_expense_policy_ar.md": ["إقليمية | ١٥٠ دولارًا أمريكيًا | ٦٠ دولارًا أمريكيًا"],
    "security/incident_response_policy_ar.md": ["خلال 72 ساعة", "P1 حرج | اختراق بيانات مؤكد"],
    "engineering/release_process_en.md": ["every 2 weeks on Tuesday", "error rate exceeds 5%"],
    "hr/employee_benefits_guide_ar.docx": ["٢٤ ضعف الراتب الأساسي الشهري", "٥ أيام عمل بأجر كامل"],
    "operations/procurement_policy_en.docx": [
        "at least 3 written quotes",
        "Over USD 10,000 | Finance Director",
    ],
    "legal/contract_approval_procedure_ar.docx": ["المراجعة القانونية والمدير التنفيذي"],
    "hr/compensation_bands_en.docx": [
        "G4 | 44,000 | 60,000 | 12%",
        "Executive compensation is set by",
    ],
    "it/laptop_setup_guide_en.txt": ["Laptops are replaced every 36 months"],
    "finance/petty_cash_policy_ar.txt": ["بقيمة 500 دولار أمريكي", "أكثر من 50 دولارًا أمريكيًا"],
    "operations/facilities_access_policy_en.pdf": ["06:00 to 22:00", "within 2 hours"],
}


def arabic_letter_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    arabic = [char for char in letters if "ARABIC" in unicodedata.name(char, "")]
    return len(arabic) / len(letters) if letters else 0.0


@pytest.fixture(scope="module")
def result():
    return ingest_directory(CORPUS)


def test_text_files_are_clean_utf8_with_lf_endings() -> None:
    text_files = sorted(p for p in CORPUS.rglob("*") if p.suffix in {".md", ".txt"})

    assert text_files
    for path in text_files:
        data = path.read_bytes()
        data.decode("utf-8")  # strict: raises on invalid UTF-8
        assert not data.startswith(b"\xef\xbb\xbf"), f"{path.name} starts with a BOM"
        assert b"\r" not in data, f"{path.name} contains carriage returns"


def test_every_document_in_the_manifest_is_ingested_and_nothing_is_skipped(result) -> None:
    assert result.skipped == ()
    assert {chunk.filename for chunk in result.chunks} == set(LANGUAGE)


def test_every_format_produces_chunks(result) -> None:
    formats = {Path(chunk.filename).suffix for chunk in result.chunks}

    assert formats == {".md", ".txt", ".docx", ".pdf"}


def test_chunks_are_within_size_limit_and_free_of_corruption(result) -> None:
    for chunk in result.chunks:
        assert 0 < len(chunk.text) <= DEFAULT_CHUNK_SIZE
        assert "\N{REPLACEMENT CHARACTER}" not in chunk.text
        assert not any(
            unicodedata.category(char) in {"Cf", "Cc"} for char in chunk.text if char != "\n"
        )


@pytest.mark.parametrize(("filename", "phrases"), EXPECTED_PHRASES.items())
def test_key_phrases_survive_verbatim(result, filename: str, phrases: list[str]) -> None:
    texts = [chunk.text for chunk in result.chunks if chunk.filename == filename]

    for phrase in phrases:
        assert any(phrase in text for text in texts), f"{phrase!r} missing from {filename}"


def test_language_of_every_chunk_matches_the_manifest(result) -> None:
    for chunk in result.chunks:
        ratio = arabic_letter_ratio(chunk.text)
        if LANGUAGE[chunk.filename] == "ar":
            assert ratio >= MIN_ARABIC_LETTER_RATIO, f"{chunk.filename} #{chunk.index}: {ratio:.0%}"
        else:
            assert ratio == 0.0, f"{chunk.filename} #{chunk.index} contains Arabic letters"


@pytest.mark.parametrize(
    "path",
    [
        "hr/employee_benefits_guide_en.docx",
        "hr/employee_benefits_guide_ar.docx",
        "operations/procurement_policy_en.docx",
        "operations/procurement_policy_ar.docx",
    ],
)
def test_docx_page_breaks_give_two_cited_pages(result, path: str) -> None:
    pages = {chunk.page for chunk in result.chunks if chunk.filename == path}

    assert pages == {1, 2}


def test_ingestion_is_deterministic(result) -> None:
    assert ingest_directory(CORPUS) == result


def test_overlap_is_applied_within_the_limit() -> None:
    result = ingest_directory(CORPUS, chunk_size=400, overlap=80)

    # Offsets are relative to a page and chunks never cross pages, so compare within a page.
    by_page: dict[tuple[str, int], list] = {}
    for chunk in result.chunks:
        by_page.setdefault((chunk.filename, chunk.page), []).append(chunk)
    multi_chunk = {key: chunks for key, chunks in by_page.items() if len(chunks) > 1}
    overlapping = 0
    for key, chunks in multi_chunk.items():
        shared = [max(0, a.end - b.start) for a, b in zip(chunks, chunks[1:], strict=False)]
        assert all(amount <= 80 for amount in shared), key
        overlapping += any(amount > 0 for amount in shared)

    assert len(multi_chunk) >= 20
    assert overlapping >= len(multi_chunk) // 2
