"""DOCX files flowing through the whole ingestion pipeline, compared with PDF."""

import shutil
from pathlib import Path

from bilingual_rag.ingestion.pipeline import ingest_directory
from support.arabic import ARABIC_TRUTH, arabic_word_recall
from support.docx_builder import docx_bytes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
WORD_DOCX = FIXTURES / "docx" / "bilingual_word.docx"
WORD_PDF = FIXTURES / "pdf" / "bilingual_word.pdf"


def chunk_text_of(result, filename: str) -> str:
    return "\n".join(chunk.text for chunk in result.chunks if chunk.filename == filename)


def test_word_document_produces_page_tagged_chunks(tmp_path: Path) -> None:
    shutil.copy(WORD_DOCX, tmp_path / "policy.docx")

    result = ingest_directory(tmp_path)

    assert result.skipped == ()
    assert {chunk.filename for chunk in result.chunks} == {"policy.docx"}
    assert sorted({chunk.page for chunk in result.chunks}) == [1, 2, 3]


def test_arabic_sentences_and_table_rows_survive_verbatim(tmp_path: Path) -> None:
    shutil.copy(WORD_DOCX, tmp_path / "policy.docx")

    text = chunk_text_of(ingest_directory(tmp_path), "policy.docx")

    assert all(sentence in text for sentence in ARABIC_TRUTH)
    assert "الإجازة السنوية | ٢١" in text  # Arabic table row, number not reversed
    assert "Annual leave | 21" in text  # English table row
    assert "ext. 4455" in text


def test_docx_reads_arabic_better_than_the_pdf_of_the_same_content(tmp_path: Path) -> None:
    shutil.copy(WORD_DOCX, tmp_path / "policy.docx")
    shutil.copy(WORD_PDF, tmp_path / "policy.pdf")

    result = ingest_directory(tmp_path)

    docx_recall = arabic_word_recall(chunk_text_of(result, "policy.docx"))
    pdf_recall = arabic_word_recall(chunk_text_of(result, "policy.pdf"))
    assert docx_recall == 1.0
    assert docx_recall > pdf_recall


def test_a_document_with_no_text_is_reported_as_skipped(tmp_path: Path) -> None:
    (tmp_path / "empty.docx").write_bytes(docx_bytes("<w:p/>"))

    result = ingest_directory(tmp_path)

    assert result.chunks == ()
    assert [(s.filename, s.reason) for s in result.skipped] == [
        ("empty.docx", "no text after cleaning")
    ]


def test_a_damaged_docx_does_not_hide_the_readable_files_next_to_it(tmp_path: Path) -> None:
    shutil.copy(WORD_DOCX, tmp_path / "good.docx")
    (tmp_path / "damaged.docx").write_bytes(WORD_DOCX.read_bytes()[:100])
    (tmp_path / "notes.md").write_bytes("# Notes\n\nملاحظات قصيرة.".encode())

    result = ingest_directory(tmp_path)

    assert {chunk.filename for chunk in result.chunks} == {"good.docx", "notes.md"}
    assert [skipped.filename for skipped in result.skipped] == ["damaged.docx"]
    assert "could not be read as a .docx file" in result.skipped[0].reason
