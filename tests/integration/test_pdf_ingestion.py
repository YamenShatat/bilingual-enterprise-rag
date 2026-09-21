"""PDFs flowing through the whole ingestion pipeline (load -> clean -> chunk)."""

import shutil
from pathlib import Path

from bilingual_rag.ingestion.chunker import DEFAULT_CHUNK_SIZE
from bilingual_rag.ingestion.cleaner import clean_text
from bilingual_rag.ingestion.loaders import load_document
from bilingual_rag.ingestion.pipeline import ingest_directory

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdf"


def make_folder(tmp_path: Path, *names: str) -> Path:
    """Copy the named PDF fixtures into a fresh folder (the fixtures folder also holds a README)."""
    for name in names:
        shutil.copy(FIXTURES / name, tmp_path / name)
    return tmp_path


def test_word_pdf_produces_page_tagged_chunks(tmp_path: Path) -> None:
    result = ingest_directory(make_folder(tmp_path, "bilingual_word.pdf"))

    assert result.skipped == ()
    assert {chunk.filename for chunk in result.chunks} == {"bilingual_word.pdf"}
    assert sorted({chunk.page for chunk in result.chunks}) == [1, 2, 3]
    assert all(0 < len(chunk.text) <= DEFAULT_CHUNK_SIZE for chunk in result.chunks)


def test_english_and_arabic_facts_survive_the_pipeline(tmp_path: Path) -> None:
    result = ingest_directory(make_folder(tmp_path, "bilingual_word.pdf"))
    by_page: dict[int, str] = {}
    for chunk in result.chunks:
        by_page[chunk.page] = by_page.get(chunk.page, "") + " " + chunk.text

    assert "21 working days of paid annual leave" in " ".join(by_page[1].split())
    assert "٢١" in by_page[2]  # Arabic-Indic "21" is not reversed to ١٢
    assert "ext. 4455" in by_page[3]


def test_chunk_offsets_refer_to_the_cleaned_page_text(tmp_path: Path) -> None:
    folder = make_folder(tmp_path, "bilingual_word.pdf")
    document = load_document(folder / "bilingual_word.pdf")

    for chunk in ingest_directory(folder).chunks:
        cleaned_page = clean_text(document.pages[chunk.page - 1].text)
        assert chunk.text == cleaned_page[chunk.start : chunk.end]


def test_scanned_pdf_is_reported_as_skipped(tmp_path: Path) -> None:
    result = ingest_directory(make_folder(tmp_path, "image_only_scan.pdf"))

    assert result.chunks == ()
    assert [(skipped.filename, skipped.reason) for skipped in result.skipped] == [
        ("image_only_scan.pdf", "no text after cleaning")
    ]


def test_a_scan_does_not_hide_the_readable_files_next_to_it(tmp_path: Path) -> None:
    folder = make_folder(tmp_path, "bilingual_word.pdf", "image_only_scan.pdf")
    (folder / "notes.md").write_bytes("# Notes\n\nملاحظات قصيرة.".encode())

    result = ingest_directory(folder)

    assert {chunk.filename for chunk in result.chunks} == {"bilingual_word.pdf", "notes.md"}
    assert [skipped.filename for skipped in result.skipped] == ["image_only_scan.pdf"]
