"""DOCX reader and loader tests: hand-built XML for each construct, then a real Word file."""

import io
import shutil
import zipfile
from pathlib import Path

import pytest

from bilingual_rag.ingestion import docx_reader
from bilingual_rag.ingestion.docx_reader import DocxFormatError, read_docx_pages
from bilingual_rag.ingestion.loaders import DocumentLoadError, load_document
from support.arabic import ARABIC_TRUTH, arabic_word_recall
from support.docx_builder import (
    PAGE_BREAK_RUN,
    RENDERED_BREAK_RUN,
    document_xml,
    docx_bytes,
    para,
    run,
    table,
    text_para,
)

WORD_DOCX = Path(__file__).resolve().parents[1] / "fixtures" / "docx" / "bilingual_word.docx"
W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def pages(body: str) -> list[str]:
    return read_docx_pages(docx_bytes(body))


def zip_of(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content.encode("utf-8"))
    return buffer.getvalue()


# --- paragraphs and runs -------------------------------------------------------------


def test_paragraphs_are_separated_by_a_blank_line() -> None:
    assert pages(text_para("One") + text_para("Two")) == ["One\n\nTwo"]


def test_arabic_text_is_returned_exactly_in_reading_order() -> None:
    body = "".join(text_para(line) for line in ARABIC_TRUTH)

    assert pages(body) == ["\n\n".join(ARABIC_TRUTH)]


def test_runs_split_inside_a_word_are_joined() -> None:
    # Word often splits a word into several runs (spell check, formatting, language tags).
    assert pages(para(run("الإ"), run("جازة"))) == ["الإجازة"]


def test_tabs_and_line_breaks_are_kept() -> None:
    body = para(run("a"), "<w:r><w:tab/></w:r>", run("b"), "<w:r><w:br/></w:r>", run("c"))

    assert pages(body) == ["a\tb\nc"]


def test_blank_paragraphs_are_skipped() -> None:
    assert pages(text_para("A") + "<w:p/>" + text_para("   ") + text_para("B")) == ["A\n\nB"]


def test_soft_hyphen_is_dropped_and_non_breaking_hyphen_becomes_a_hyphen() -> None:
    body = para(
        run("ab"),
        "<w:r><w:softHyphen/></w:r>",
        run("cd"),
        "<w:r><w:noBreakHyphen/></w:r>",
        run("ef"),
    )

    assert pages(body) == ["abcd-ef"]


# --- tables --------------------------------------------------------------------------


def test_table_rows_become_pipe_separated_lines() -> None:
    body = text_para("before") + table(["Leave", "Days"], ["Annual", "21"]) + text_para("after")

    assert pages(body) == ["before\n\nLeave | Days\nAnnual | 21\n\nafter"]


def test_paragraphs_inside_a_cell_are_joined_with_a_space() -> None:
    cell = f"<w:tc>{text_para('a')}{text_para('b')}</w:tc><w:tc>{text_para('c')}</w:tc>"

    assert pages(f"<w:tbl><w:tr>{cell}</w:tr></w:tbl>") == ["a b | c"]


def test_empty_table_rows_are_skipped() -> None:
    assert pages(table(["", ""], ["x", "y"])) == ["x | y"]


def test_nested_table_is_flattened_into_its_cell() -> None:
    nested = table(["n1", "n2"], ["n3", "n4"])
    outer = (
        f"<w:tbl><w:tr><w:tc>{text_para('outer')}{nested}</w:tc>"
        f"<w:tc>{text_para('side')}</w:tc></w:tr></w:tbl>"
    )

    assert pages(outer) == ["outer n1 | n2 n3 | n4 | side"]


# --- content that must, or must not, be included -----------------------------------


def test_tracked_insertions_are_kept_and_deletions_and_moves_are_dropped() -> None:
    body = para(
        run("Keep "),
        f"<w:ins>{run('added ')}</w:ins>",
        "<w:del><w:r><w:delText>removed </w:delText></w:r></w:del>",
        f"<w:del>{run('deleted in an ordinary run ')}</w:del>",  # not what Word writes, but safe
        f"<w:moveFrom>{run('moved away ')}</w:moveFrom>",
        run("end"),
    )

    assert pages(body) == ["Keep added end"]


def test_field_codes_are_dropped_but_displayed_field_text_is_kept() -> None:
    body = para(
        run("Page "),
        '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>',
        f'<w:fldSimple w:instr="PAGE">{run("7")}</w:fldSimple>',
    )

    assert pages(body) == ["Page 7"]


def test_hyperlink_and_content_control_text_is_kept() -> None:
    body = para(run("See "), f'<w:hyperlink w:anchor="a">{run("this link")}</w:hyperlink>')
    body += f"<w:sdt><w:sdtPr/><w:sdtContent>{text_para('inside a control')}</w:sdtContent></w:sdt>"

    assert pages(body) == ["See this link\n\ninside a control"]


def test_a_text_box_is_read_once_although_word_writes_it_twice() -> None:
    box = f"<w:txbxContent>{text_para('boxed text')}</w:txbxContent>"
    body = para(
        "<w:r><mc:AlternateContent>"
        '<mc:Choice Requires="wps"><w:drawing><wps:wsp>'
        f"<wps:txbx>{box}</wps:txbx></wps:wsp></w:drawing></mc:Choice>"
        f"<mc:Fallback><w:pict><v:textbox>{box}</v:textbox></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r>"
    )

    (text,) = pages(body)

    assert text.count("boxed text") == 1


def test_headers_and_footers_are_not_extracted() -> None:
    extra = {
        "word/header1.xml": document_xml(text_para("SECRET HEADER")),
        "word/footer1.xml": document_xml(text_para("SECRET FOOTER")),
    }

    result = read_docx_pages(docx_bytes(text_para("Body text"), extra))

    assert result == ["Body text"]


def test_empty_or_whitespace_only_documents_give_one_empty_page() -> None:
    assert pages("") == [""]
    assert pages(text_para("   ") + "<w:p/>") == [""]


# --- pages ---------------------------------------------------------------------------


def test_an_explicit_page_break_starts_a_new_page() -> None:
    assert pages(text_para("p1") + para(PAGE_BREAK_RUN) + text_para("p2")) == ["p1", "p2"]


def test_word_writing_both_markers_at_a_break_is_not_counted_twice() -> None:
    body = text_para("p1") + para(PAGE_BREAK_RUN) + para(RENDERED_BREAK_RUN, run("p2"))

    assert pages(body) == ["p1", "p2"]


def test_a_rendered_page_break_inside_a_paragraph_splits_it() -> None:
    assert pages(para(run("before "), RENDERED_BREAK_RUN, run("after"))) == ["before", "after"]


def test_page_break_before_starts_a_new_page_unless_switched_off() -> None:
    on = "<w:p><w:pPr><w:pageBreakBefore/></w:pPr>" + run("p2") + "</w:p>"
    off = '<w:p><w:pPr><w:pageBreakBefore w:val="0"/></w:pPr>' + run("p2") + "</w:p>"

    assert pages(text_para("p1") + on) == ["p1", "p2"]
    assert pages(text_para("p1") + off) == ["p1\n\np2"]


def test_leading_and_repeated_breaks_do_not_create_empty_pages() -> None:
    body = para(PAGE_BREAK_RUN) + text_para("a") + para(PAGE_BREAK_RUN) + para(PAGE_BREAK_RUN)
    body += text_para("b")

    assert pages(body) == ["a", "b"]


def test_a_page_break_inside_a_table_does_not_split_the_table() -> None:
    cell = para(run("a"), PAGE_BREAK_RUN, run("b"))

    assert pages(f"<w:tbl><w:tr><w:tc>{cell}</w:tc></w:tr></w:tbl>") == ["a b"]


# --- untrusted input -----------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        b"just some text",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,  # the OLE header of an encrypted docx
        docx_bytes(text_para("x"))[:60],  # truncated archive
    ],
    ids=["plain-text", "encrypted-ole-container", "truncated"],
)
def test_input_that_is_not_a_readable_archive_is_rejected(data: bytes) -> None:
    with pytest.raises(DocxFormatError, match="not a readable .docx"):
        read_docx_pages(data)


def test_a_zip_without_a_word_document_part_is_rejected() -> None:
    with pytest.raises(DocxFormatError, match=r"missing word/document\.xml"):
        read_docx_pages(zip_of({"other.xml": "<x/>"}))


def test_documents_with_a_dtd_are_rejected() -> None:
    entity_bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        f"<w:document {W_NS}><w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p></w:body></w:document>"
    )

    with pytest.raises(DocxFormatError, match="DTD"):
        read_docx_pages(zip_of({"word/document.xml": entity_bomb}))


def test_invalid_xml_is_rejected() -> None:
    with pytest.raises(DocxFormatError, match="not valid XML"):
        read_docx_pages(zip_of({"word/document.xml": f"<w:document {W_NS}><w:body>"}))


def test_a_document_without_a_body_is_rejected() -> None:
    with pytest.raises(DocxFormatError, match="no body"):
        read_docx_pages(zip_of({"word/document.xml": f"<w:document {W_NS}/>"}))


def test_oversized_xml_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docx_reader, "MAX_XML_BYTES", 100)

    with pytest.raises(DocxFormatError, match="too large"):
        read_docx_pages(docx_bytes(text_para("x" * 500)))


# --- a real Word document ------------------------------------------------------------

WORD_PAGE_1 = (
    "Annual Leave Policy\n\n"
    "Full-time employees are entitled to 21 working days of paid annual leave "
    "per calendar year.\n\n"
    "Requests must be submitted through the HR portal.\n\n"
    "Leave type | Days\nAnnual leave | 21\nLong service | 26"
)
WORD_PAGE_2 = (
    "سياسة الإجازة السنوية\n\n"
    "يحصل الموظف على ٢١ يومًا من الإجازة السنوية بعد إنهاء فترة التجربة.\n\n"
    "يجب تقديم الطلب عبر بوابة الموارد البشرية قبل ١٠ أيام عمل.\n\n"
    "أهلاً بكم في شركة أكمي، آمل أن تكون على ما يرام في مدرسة الهدى.\n\n"
    "نوع الإجازة | عدد الأيام\nالإجازة السنوية | ٢١\nبعد خمس سنوات | ٢٦"
)
WORD_PAGE_3 = (
    "للاتصال بالشبكة الداخلية استخدم VPN ثم افتح Cisco AnyConnect.\n\n"
    "Contact the IT service desk: ext. 4455."
)


def test_real_word_document_is_read_exactly_page_by_page() -> None:
    document = load_document(WORD_DOCX)

    assert document.filename == "bilingual_word.docx"
    assert [(page.number, page.text) for page in document.pages] == [
        (1, WORD_PAGE_1),
        (2, WORD_PAGE_2),
        (3, WORD_PAGE_3),
    ]


def test_real_word_document_loses_no_arabic_unlike_the_pdf_of_the_same_content() -> None:
    """The same content exported to PDF reaches only 90% word recall (see pdf-extraction.md)."""
    text = "\n".join(page.text for page in load_document(WORD_DOCX).pages)

    assert arabic_word_recall(text) == 1.0
    assert all(sentence in text for sentence in ARABIC_TRUTH)


# --- file handling -------------------------------------------------------------------


def test_unreadable_file_raises_load_error_naming_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.docx"
    path.write_bytes(WORD_DOCX.read_bytes()[:200])

    with pytest.raises(DocumentLoadError, match="broken.docx"):
        load_document(path)


def test_a_text_file_renamed_to_docx_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fake.docx"
    path.write_text("not really a document", encoding="utf-8")

    with pytest.raises(DocumentLoadError, match="fake.docx"):
        load_document(path)


def test_extension_matching_is_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "REPORT.DOCX"
    shutil.copy(WORD_DOCX, path)

    assert len(load_document(path).pages) == 3


def test_file_is_not_locked_after_loading(tmp_path: Path) -> None:
    path = tmp_path / "temporary.docx"
    shutil.copy(WORD_DOCX, path)

    load_document(path)
    path.unlink()

    assert not path.exists()


def test_missing_docx_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_document(tmp_path / "missing.docx")
