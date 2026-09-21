"""Extract text from ``.docx`` files (Office Open XML) with the standard library only.

A ``.docx`` is a ZIP archive whose body lives in ``word/document.xml``. Text is stored in
logical (reading) order, so unlike PDF there is nothing to reorder for Arabic.

What is extracted
  * Paragraphs, separated by a blank line so the chunker sees them as paragraphs.
  * Table rows as ``cell | cell`` lines, so the column relationship survives cleaning.
  * Text inside hyperlinks, content controls, tracked insertions and text boxes.

What is deliberately left out
  * Deleted text (tracked deletions and moves), field codes, and comments.
  * Headers, footers, footnotes and endnotes.
  * List numbers and bullets: Word does not store them in the paragraph text.

Pages
  A ``.docx`` has no fixed pages, because layout depends on the renderer. Pages are derived
  from explicit page breaks and from the ``lastRenderedPageBreak`` markers Word writes when
  it saves. Word writes both at a hard break, so pages that end up with no text are dropped
  and the pair is not counted twice. Page numbers are therefore approximate: files that were
  not saved by Word may come out as a single page.

The input is untrusted, so it is size-limited and any document containing a DTD is
rejected (this blocks entity-expansion attacks; Word never writes one).
"""

import io
import zipfile
import zlib
from collections.abc import Iterator
from xml.etree import ElementTree

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"

MAX_XML_BYTES = 50 * 1024 * 1024

# Elements that hold formatting or deleted content and never contribute visible text.
_SKIP = frozenset(
    _W + name
    for name in (
        "pPr",
        "rPr",
        "sectPr",
        "tblPr",
        "trPr",
        "tcPr",
        "tblGrid",
        "del",
        "moveFrom",
        "delText",
        "instrText",
        "delInstrText",
    )
)
_FALSY = frozenset({"0", "false", "off"})


class DocxFormatError(ValueError):
    """Raised when the bytes are not a readable Word document."""


class _PageBreak:
    """Sentinel yielded where a new page starts."""


_PAGE_BREAK = _PageBreak()
_Item = str | _PageBreak


def read_docx_pages(data: bytes) -> list[str]:
    """Return the text of each page of a ``.docx`` file (at least one, possibly empty)."""
    root = _document_root(data)
    body = root.find(f"{_W}body")
    if body is None:
        raise DocxFormatError("word/document.xml has no body")

    pages: list[list[str]] = [[]]
    for item in _block_items(body):
        if isinstance(item, _PageBreak):
            pages.append([])
        elif item.strip():
            pages[-1].append(item.strip())
    # Pages that received no text are dropped, which also merges Word's double markers.
    texts = ["\n\n".join(blocks) for blocks in pages if blocks]
    return texts or [""]


def _document_root(data: bytes) -> ElementTree.Element:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            try:
                info = archive.getinfo("word/document.xml")
            except KeyError:
                raise DocxFormatError("missing word/document.xml, so not a Word document") from None
            if info.file_size > MAX_XML_BYTES:
                raise DocxFormatError("word/document.xml is too large")
            with archive.open(info) as stream:
                xml = stream.read(MAX_XML_BYTES + 1)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
        raise DocxFormatError(
            f"not a readable .docx archive (it may be damaged or password-protected): {exc}"
        ) from exc
    if len(xml) > MAX_XML_BYTES:
        raise DocxFormatError("word/document.xml is too large")
    if b"<!DOCTYPE" in xml:
        raise DocxFormatError("word/document.xml contains a DTD, which Word never writes")
    try:
        return ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocxFormatError(f"word/document.xml is not valid XML: {exc}") from exc


def _block_items(container: ElementTree.Element) -> Iterator[_Item]:
    """Yield the paragraphs and tables under ``container`` as text, plus page breaks."""
    for child in container:
        if child.tag == f"{_W}p":
            yield from _paragraph_items(child)
        elif child.tag == f"{_W}tbl":
            yield _table_text(child)
        elif child.tag == f"{_W}sdt":
            content = child.find(f"{_W}sdtContent")
            if content is not None:
                yield from _block_items(content)
        elif child.tag in (f"{_W}ins", f"{_W}moveTo", f"{_W}customXml", f"{_W}smartTag"):
            yield from _block_items(child)


def _paragraph_items(paragraph: ElementTree.Element) -> Iterator[_Item]:
    properties = paragraph.find(f"{_W}pPr")
    if properties is not None:
        page_break_before = properties.find(f"{_W}pageBreakBefore")
        if page_break_before is not None and page_break_before.get(f"{_W}val") not in _FALSY:
            yield _PAGE_BREAK

    buffer: list[str] = []
    for token in _inline(paragraph):
        if isinstance(token, _PageBreak):
            yield "".join(buffer)  # text before the break stays on the current page
            buffer = []
            yield _PAGE_BREAK
        else:
            buffer.append(token)
    yield "".join(buffer)


def _inline(element: ElementTree.Element) -> Iterator[_Item]:
    """Yield the text pieces and page breaks inside a paragraph or run, in order."""
    for child in element:
        tag = child.tag
        if tag in _SKIP:
            continue
        if tag == f"{_W}t":
            yield child.text or ""
        elif tag == f"{_W}tab":
            yield "\t"
        elif tag == f"{_W}br":
            yield _PAGE_BREAK if child.get(f"{_W}type") == "page" else "\n"
        elif tag == f"{_W}cr":
            yield "\n"
        elif tag == f"{_W}noBreakHyphen":
            yield "-"
        elif tag == f"{_W}lastRenderedPageBreak":
            yield _PAGE_BREAK
        elif tag == f"{_W}txbxContent":
            yield "\n" + _flat_text(child) + "\n"
        elif tag == f"{_MC}AlternateContent":
            # Word writes the same text box twice (Choice and Fallback); read only one.
            choice = child.find(f"{_MC}Choice")
            if choice is not None:
                yield from _inline(choice)
        else:  # runs, hyperlinks, tracked insertions, fields, drawings, ...
            yield from _inline(child)


def _flat_text(container: ElementTree.Element) -> str:
    """Text of block content that cannot start a new page (table cells, text boxes)."""
    blocks = (item for item in _block_items(container) if isinstance(item, str))
    return "\n\n".join(block.strip() for block in blocks if block.strip())


def _table_text(table: ElementTree.Element) -> str:
    rows = []
    for row in table.findall(f"{_W}tr"):
        cells = [_cell_text(cell) for cell in row.findall(f"{_W}tc")]
        line = " | ".join(cells)
        if line.strip(" |"):
            rows.append(line)
    return "\n".join(rows)


def _cell_text(cell: ElementTree.Element) -> str:
    return " ".join(_flat_text(cell).split())
