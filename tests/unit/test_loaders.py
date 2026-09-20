from pathlib import Path

import pytest

from bilingual_rag.ingestion.loaders import (
    DocumentLoadError,
    UnsupportedFileTypeError,
    load_document,
)

# Covers أ إ آ ة ى ي, tanween (ً), shadda/fatha diacritics and Arabic-Indic digits.
ARABIC_SAMPLE = (
    "سياسة الإجازة السنوية: يحصل الموظف على ٢١ يومًا من الإجازة السنوية بعد إنهاء فترة التجربة.\n"
    "أهلاً بكم في شركة أكمي، آمل أن تكون الحياة على ما يرام. مَدرَسة، هدى، على، مُدَّة."
)


def write_utf8(path: Path, text: str) -> Path:
    """Write ``text`` as UTF-8 bytes, independent of the platform's default encoding."""
    path.write_bytes(text.encode("utf-8"))
    return path


def test_loads_english_text_as_single_numbered_page(tmp_path: Path) -> None:
    path = write_utf8(tmp_path / "leave_policy.txt", "Employees receive 21 days of annual leave.")

    document = load_document(path)

    assert document.filename == "leave_policy.txt"
    assert len(document.pages) == 1
    assert document.pages[0].number == 1
    assert document.pages[0].text == "Employees receive 21 days of annual leave."


def test_arabic_text_is_preserved_exactly(tmp_path: Path) -> None:
    path = write_utf8(tmp_path / "leave_policy_ar.txt", ARABIC_SAMPLE)

    assert load_document(path).pages[0].text == ARABIC_SAMPLE


@pytest.mark.parametrize("letter", ["أ", "إ", "آ", "ة", "ى", "ي"])
def test_arabic_letter_variants_are_not_folded(tmp_path: Path, letter: str) -> None:
    text = f"كلمة {letter} كلمة"
    path = write_utf8(tmp_path / "letters.txt", text)

    assert load_document(path).pages[0].text == text


def test_unicode_is_not_normalized(tmp_path: Path) -> None:
    # alef + combining hamza above: NFC would compose these into the single letter أ.
    decomposed = "\N{ARABIC LETTER ALEF}\N{ARABIC HAMZA ABOVE}"
    path = write_utf8(tmp_path / "decomposed.txt", decomposed)

    loaded = load_document(path).pages[0].text

    assert loaded == decomposed
    assert len(loaded) == 2


def test_mixed_arabic_and_english_is_preserved(tmp_path: Path) -> None:
    text = "سياسة VPN: استخدم Cisco AnyConnect للاتصال بالشبكة (Remote Work)."
    path = write_utf8(tmp_path / "mixed.txt", text)

    assert load_document(path).pages[0].text == text


def test_utf8_bom_is_stripped(tmp_path: Path) -> None:
    path = tmp_path / "with_bom.txt"
    path.write_bytes(b"\xef\xbb\xbf" + "مرحبا".encode())

    text = load_document(path).pages[0].text

    assert text == "مرحبا"
    assert not text.startswith("\N{ZERO WIDTH NO-BREAK SPACE}")


def test_line_endings_are_left_untouched(tmp_path: Path) -> None:
    path = write_utf8(tmp_path / "crlf.txt", "first\r\nsecond\n")

    assert load_document(path).pages[0].text == "first\r\nsecond\n"


def test_empty_file_loads_as_one_empty_page(tmp_path: Path) -> None:
    path = write_utf8(tmp_path / "empty.txt", "")

    document = load_document(path)

    assert len(document.pages) == 1
    assert document.pages[0].text == ""


def test_markdown_is_loaded_verbatim(tmp_path: Path) -> None:
    text = "# سياسة الإجازة\n\n- **21** يومًا\n- Annual leave\n"
    path = write_utf8(tmp_path / "policy.md", text)

    assert load_document(path).pages[0].text == text


def test_extension_matching_is_case_insensitive(tmp_path: Path) -> None:
    path = write_utf8(tmp_path / "POLICY.TXT", "hello")

    assert load_document(path).pages[0].text == "hello"


def test_non_utf8_bytes_raise_instead_of_corrupting_text(tmp_path: Path) -> None:
    path = tmp_path / "legacy_windows_1256.txt"
    path.write_bytes("مرحبا بكم".encode("cp1256"))

    with pytest.raises(DocumentLoadError, match="legacy_windows_1256.txt"):
        load_document(path)


@pytest.mark.parametrize("name", ["malware.exe", "notes.xyz", "no_extension"])
def test_unsupported_file_type_is_rejected(tmp_path: Path, name: str) -> None:
    path = write_utf8(tmp_path / name, "data")

    with pytest.raises(UnsupportedFileTypeError, match="Supported"):
        load_document(path)


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_document(tmp_path / "does_not_exist.txt")
