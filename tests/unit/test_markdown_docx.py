"""The Markdown-to-DOCX writer used to generate corpus files."""

import io
import zipfile
from xml.etree import ElementTree

import pytest

from bilingual_rag.corpus.markdown_docx import (
    Heading,
    PageBreak,
    Paragraph,
    Table,
    expected_pages,
    markdown_to_docx,
    parse_markdown,
    plain,
)
from bilingual_rag.ingestion.docx_reader import read_docx_pages

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SAMPLE = """\
# Leave Policy
**Acme MENA Technology — HR**
Owner: HR · Version 2.1

## 1. Entitlement
Employees receive **21 days** of leave & may carry over 5 days if < 6 remain.
- First bullet
- Second bullet

| Type | Days |
| --- | --- |
| Annual | 21 |
| Sick | 10 |

---
## 2. سياسة الإجازة
يحصل الموظف على ٢١ يومًا من الإجازة السنوية.

| النوع | الأيام |
| :--- | ---: |
| سنوية | ٢١ |
"""


def document_xml(data: bytes) -> ElementTree.Element:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return ElementTree.fromstring(archive.read("word/document.xml"))


def paragraph_with(root: ElementTree.Element, needle: str) -> ElementTree.Element:
    for paragraph in root.iter(f"{W}p"):
        if needle in "".join(t.text or "" for t in paragraph.iter(f"{W}t")):
            return paragraph
    raise AssertionError(f"no paragraph contains {needle!r}")


# --- parsing -------------------------------------------------------------------------


def test_parse_recognises_every_supported_construct() -> None:
    blocks = parse_markdown(SAMPLE)

    assert blocks[0] == Heading(1, "Leave Policy")
    assert Paragraph("**Acme MENA Technology — HR**") in blocks
    assert Heading(2, "1. Entitlement") in blocks
    assert Paragraph("\N{BULLET} First bullet") in blocks
    assert Table((("Type", "Days"), ("Annual", "21"), ("Sick", "10"))) in blocks
    assert PageBreak() in blocks
    assert Table((("النوع", "الأيام"), ("سنوية", "٢١"))) in blocks


def test_blank_lines_and_table_separator_rows_are_ignored() -> None:
    blocks = parse_markdown("\n\nText\n\n| a | b |\n| --- | :---: |\n| 1 | 2 |\n\n")

    assert blocks == [Paragraph("Text"), Table((("a", "b"), ("1", "2")))]


def test_plain_removes_bold_markers_only() -> None:
    assert plain("a **bold** and *not bold*") == "a bold and *not bold*"


# --- the round trip ------------------------------------------------------------------


def test_writing_then_reading_is_lossless() -> None:
    data = markdown_to_docx(SAMPLE, title="Leave Policy")

    assert read_docx_pages(data) == expected_pages(parse_markdown(SAMPLE))


def test_the_round_trip_keeps_arabic_exactly_and_splits_pages_at_rules() -> None:
    first, second = read_docx_pages(markdown_to_docx(SAMPLE, title="t"))

    assert "Annual | 21" in first
    assert "**" not in first + second  # bold markers never reach the text
    assert "يحصل الموظف على ٢١ يومًا من الإجازة السنوية." in second
    assert "سنوية | ٢١" in second


def test_special_xml_characters_survive() -> None:
    markdown = "Fish & chips <b>not markup</b> 5 > 3"

    assert read_docx_pages(markdown_to_docx(markdown, title="t")) == [markdown]


# --- structure -----------------------------------------------------------------------


def test_arabic_paragraphs_and_tables_are_right_to_left_and_english_ones_are_not() -> None:
    root = document_xml(markdown_to_docx(SAMPLE, title="t"))

    arabic = paragraph_with(root, "يحصل الموظف")
    english = paragraph_with(root, "First bullet")
    assert arabic.find(f"{W}pPr/{W}bidi") is not None
    assert arabic.find(f"{W}r/{W}rPr/{W}rtl") is not None
    assert english.find(f"{W}pPr/{W}bidi") is None
    tables = list(root.iter(f"{W}tbl"))
    assert [table.find(f"{W}tblPr/{W}bidiVisual") is not None for table in tables] == [False, True]


def test_headings_use_heading_styles_and_bold_text_gets_bold_runs() -> None:
    root = document_xml(markdown_to_docx(SAMPLE, title="t"))

    title = paragraph_with(root, "Leave Policy")
    section = paragraph_with(root, "1. Entitlement")
    body = paragraph_with(root, "21 days")
    assert title.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "Heading1"
    assert section.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "Heading2"
    bold_runs = [run for run in body.iter(f"{W}r") if run.find(f"{W}rPr/{W}b") is not None]
    assert ["".join(t.text for t in run.iter(f"{W}t")) for run in bold_runs] == ["21 days"]


def test_a_rule_becomes_a_real_page_break() -> None:
    root = document_xml(markdown_to_docx("One\n\n---\n\nTwo", title="t"))

    breaks = [br for br in root.iter(f"{W}br") if br.get(f"{W}type") == "page"]
    assert len(breaks) == 1


def test_every_part_is_well_formed_xml_and_the_title_is_recorded() -> None:
    data = markdown_to_docx(SAMPLE, title="Leave & Policy")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        for name in names:
            ElementTree.fromstring(archive.read(name))  # raises if malformed
        core = archive.read("docProps/core.xml").decode("utf-8")
    assert names[0] == "[Content_Types].xml"
    assert {"word/document.xml", "word/styles.xml", "_rels/.rels"} <= set(names)
    assert "<dc:title>Leave &amp; Policy</dc:title>" in core


def test_output_is_deterministic() -> None:
    assert markdown_to_docx(SAMPLE, title="t") == markdown_to_docx(SAMPLE, title="t")


@pytest.mark.parametrize("markdown", ["", "\n\n", "---"])
def test_documents_without_text_still_produce_a_valid_file(markdown: str) -> None:
    assert read_docx_pages(markdown_to_docx(markdown, title="t")) == [""]
