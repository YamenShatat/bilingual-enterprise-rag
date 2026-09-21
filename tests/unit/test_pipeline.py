from pathlib import Path

import pytest

from bilingual_rag.ingestion.cleaner import clean_text
from bilingual_rag.ingestion.loaders import (
    DocumentLoadError,
    UnsupportedFileTypeError,
    load_document,
)
from bilingual_rag.ingestion.pipeline import ingest_directory, ingest_file

BOM = b"\xef\xbb\xbf"
ZERO_WIDTH_SPACE = "\N{ZERO WIDTH SPACE}"


def write(path: Path, content: str | bytes) -> Path:
    """Write ``content`` (text is encoded as UTF-8), creating parent folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


# --- ingest_file -------------------------------------------------------------------


def test_ingest_file_cleans_text_before_chunking(tmp_path: Path) -> None:
    raw = f"Annual{ZERO_WIDTH_SPACE} leave  policy\r\n\r\n\r\n\r\nEmployees receive 21 days.\r\n"
    path = write(tmp_path / "leave.txt", BOM + raw.encode("utf-8"))

    chunks = ingest_file(path)

    assert len(chunks) == 1
    assert chunks[0].text == "Annual leave policy\n\nEmployees receive 21 days."
    assert (chunks[0].filename, chunks[0].page, chunks[0].index) == ("leave.txt", 1, 0)


def test_arabic_survives_the_whole_pipeline(tmp_path: Path) -> None:
    raw = f"الإ{ZERO_WIDTH_SPACE}جازة السنوية\r\n\r\n\r\nيحصل الموظف على ٢١ يومًا.\r\n"
    path = write(tmp_path / "leave_ar.md", BOM + raw.encode("utf-8"))

    chunks = ingest_file(path)

    assert [chunk.text for chunk in chunks] == ["الإجازة السنوية\n\nيحصل الموظف على ٢١ يومًا."]


def test_chunk_offsets_refer_to_the_cleaned_text(tmp_path: Path) -> None:
    raw = "\r\n".join(f"Sentence number {i} is here." for i in range(20))
    path = write(tmp_path / "many.txt", raw)
    cleaned = clean_text(load_document(path).pages[0].text)

    chunks = ingest_file(path, chunk_size=60, overlap=0)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 60 for chunk in chunks)
    assert all(chunk.text == cleaned[chunk.start : chunk.end] for chunk in chunks)


def test_ingest_file_propagates_loader_errors(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        ingest_file(write(tmp_path / "tool.exe", "data"))
    with pytest.raises(DocumentLoadError):
        ingest_file(write(tmp_path / "legacy.txt", "مرحبا".encode("cp1256")))


# --- ingest_directory --------------------------------------------------------------


def test_directory_ingestion_reports_unreadable_files_instead_of_failing(tmp_path: Path) -> None:
    write(tmp_path / "hr" / "leave.md", "# Leave\n\n21 days.")
    write(tmp_path / "it" / "vpn.txt", "Use the VPN.")
    write(tmp_path / "notes.exe", "MZ")
    write(tmp_path / "hr" / "bad.txt", "مرحبا".encode("cp1256"))
    write(tmp_path / "empty.txt", "  \n\n ")

    result = ingest_directory(tmp_path)

    assert {chunk.filename for chunk in result.chunks} == {"hr/leave.md", "it/vpn.txt"}
    assert [skipped.filename for skipped in result.skipped] == [
        "empty.txt",
        "hr/bad.txt",
        "notes.exe",
    ]
    reasons = {skipped.filename: skipped.reason for skipped in result.skipped}
    assert reasons["empty.txt"] == "no text after cleaning"
    assert "UTF-8" in reasons["hr/bad.txt"]
    assert "Unsupported file type" in reasons["notes.exe"]


def test_files_with_the_same_name_in_different_folders_stay_distinct(tmp_path: Path) -> None:
    write(tmp_path / "hr" / "policy.md", "HR policy")
    write(tmp_path / "it" / "policy.md", "IT policy")

    result = ingest_directory(tmp_path)

    assert [chunk.filename for chunk in result.chunks] == ["hr/policy.md", "it/policy.md"]


def test_directory_order_is_deterministic_and_case_sensitive(tmp_path: Path) -> None:
    for name in ["sub/c.txt", "a.txt", "B.txt"]:
        write(tmp_path / name, "x")

    result = ingest_directory(tmp_path)

    assert [chunk.filename for chunk in result.chunks] == ["B.txt", "a.txt", "sub/c.txt"]


def test_directory_ingestion_passes_chunk_settings_through(tmp_path: Path) -> None:
    write(tmp_path / "long.txt", " ".join(f"Sentence number {i} is here." for i in range(20)))

    result = ingest_directory(tmp_path, chunk_size=60, overlap=0)

    assert len(result.chunks) > 1
    assert all(len(chunk.text) <= 60 for chunk in result.chunks)


@pytest.mark.parametrize("target", ["missing", "a_file.txt"])
def test_ingest_directory_requires_a_directory(tmp_path: Path, target: str) -> None:
    write(tmp_path / "a_file.txt", "x")

    with pytest.raises(NotADirectoryError):
        ingest_directory(tmp_path / target)
