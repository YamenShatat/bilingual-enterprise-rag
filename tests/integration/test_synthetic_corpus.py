"""End-to-end checks on the real synthetic corpus in ``data/synthetic``."""

import unicodedata
from pathlib import Path

import pytest

from bilingual_rag.ingestion.chunker import DEFAULT_CHUNK_SIZE
from bilingual_rag.ingestion.pipeline import ingest_directory

CORPUS = Path(__file__).resolve().parents[2] / "data" / "synthetic"

# Phrases that must reach the chunks verbatim: hamza forms, tanween, Arabic-Indic digits,
# the Arabic decimal separator, and Latin text embedded in Arabic.
EXPECTED_PHRASES = {
    "hr/annual_leave_policy_en.md": ["21 working days of paid annual leave", "1.75 days per month"],
    "hr/annual_leave_policy_ar.md": ["٢١ يوم عمل", "الإجازة السنوية", "بمعدل ١٫٧٥ يوم في الشهر"],
    "hr/remote_work_policy_en.md": ["up to 2 days per week", "USD 300"],
    "security/password_policy_ar.md": ["14 حرفًا", "180 يومًا", "security@acme-mena.example"],
}


def arabic_letter_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    arabic = [char for char in letters if "ARABIC" in unicodedata.name(char, "")]
    return len(arabic) / len(letters) if letters else 0.0


@pytest.fixture(scope="module")
def result():
    return ingest_directory(CORPUS)


def test_corpus_files_are_clean_utf8_with_lf_endings() -> None:
    files = sorted(path for path in CORPUS.rglob("*") if path.is_file())

    assert files
    for path in files:
        data = path.read_bytes()
        data.decode("utf-8")  # strict: raises on invalid UTF-8
        assert not data.startswith(b"\xef\xbb\xbf"), f"{path.name} starts with a BOM"
        assert b"\r" not in data, f"{path.name} contains carriage returns"


def test_every_corpus_file_is_ingested(result) -> None:
    assert result.skipped == ()
    ingested = {chunk.filename for chunk in result.chunks}
    assert set(EXPECTED_PHRASES) <= ingested


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


def test_language_of_every_chunk_matches_its_file(result) -> None:
    for chunk in result.chunks:
        ratio = arabic_letter_ratio(chunk.text)
        if chunk.filename.endswith("_ar.md"):
            assert ratio > 0.8, f"{chunk.filename} #{chunk.index} is only {ratio:.0%} Arabic"
        else:
            assert ratio == 0.0, f"{chunk.filename} #{chunk.index} contains Arabic letters"


def test_ingestion_is_deterministic(result) -> None:
    assert ingest_directory(CORPUS) == result


def test_overlap_is_applied_across_chunk_boundaries() -> None:
    result = ingest_directory(CORPUS, chunk_size=400, overlap=80)

    by_file: dict[str, list] = {}
    for chunk in result.chunks:
        by_file.setdefault(chunk.filename, []).append(chunk)
    for filename, chunks in by_file.items():
        shared = [max(0, a.end - b.start) for a, b in zip(chunks, chunks[1:], strict=False)]
        assert len(chunks) > 1, filename
        assert all(amount <= 80 for amount in shared), filename
        assert any(amount > 0 for amount in shared), f"no overlap at all in {filename}"
