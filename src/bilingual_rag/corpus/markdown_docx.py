"""Turn the small Markdown subset used by the synthetic corpus into a ``.docx`` file.

The corpus documents are written once as readable Markdown sources, and the DOCX files are
generated from them so text and file can never drift apart. Only what the corpus uses is
supported:

  ``# Title`` and ``## Heading``     Heading 1 and Heading 2 paragraphs
  ``- item``                          a paragraph starting with a bullet character
  ``| a | b |`` rows                  a table (the ``|---|---|`` separator row is skipped)
  ``---`` on its own line             a page break
  ``**bold**``                        bold text
  any other non-blank line            one paragraph

Paragraphs that are mostly Arabic are written as right-to-left paragraphs, and tables as
right-to-left tables, the way Word does. Output is deterministic: the same input always
gives the same bytes (fixed timestamps, no random ids), so rebuilding never creates noise
in version control.
"""

import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")
_BULLET = "\N{BULLET} "
_TABLE_WIDTH_DXA = 9000


@dataclass(frozen=True, slots=True)
class Heading:
    level: int  # 1 or 2
    text: str


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str


@dataclass(frozen=True, slots=True)
class Table:
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class PageBreak:
    pass


Block = Heading | Paragraph | Table | PageBreak


# --- parsing -------------------------------------------------------------------------


def parse_markdown(markdown: str) -> list[Block]:
    """Parse the supported Markdown subset into blocks."""
    blocks: list[Block] = []
    rows: list[tuple[str, ...]] = []

    def flush_table() -> None:
        if rows:
            blocks.append(Table(tuple(rows)))
            rows.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("|"):
            cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
            if not all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells):
                rows.append(cells)
            continue
        flush_table()
        if not line:
            continue
        if line == "---":
            blocks.append(PageBreak())
        elif line.startswith("## "):
            blocks.append(Heading(2, line[3:].strip()))
        elif line.startswith("# "):
            blocks.append(Heading(1, line[2:].strip()))
        elif line.startswith("- "):
            blocks.append(Paragraph(_BULLET + line[2:].strip()))
        else:
            blocks.append(Paragraph(line))
    flush_table()
    return blocks


def plain(text: str) -> str:
    """``text`` without ``**`` bold markers."""
    return _BOLD.sub(r"\1", text)


def expected_pages(blocks: Sequence[Block]) -> list[str]:
    """The text the DOCX reader should return for these blocks, one string per page.

    This mirrors the reader's rules (blank-line separated blocks, ``cell | cell`` table
    rows, empty pages dropped) so a test can check that writing then reading is lossless.
    """
    pages: list[list[str]] = [[]]
    for block in blocks:
        if isinstance(block, PageBreak):
            pages.append([])
            continue
        if isinstance(block, Table):
            lines = (
                " | ".join(" ".join(plain(cell).split()) for cell in row) for row in block.rows
            )
            text = "\n".join(line for line in lines if line.strip(" |"))
        else:
            text = plain(block.text).strip()
        if text:
            pages[-1].append(text)
    texts = ["\n\n".join(page) for page in pages if page]
    return texts or [""]


# --- writing -------------------------------------------------------------------------


def markdown_to_docx(markdown: str, *, title: str) -> bytes:
    """Build a ``.docx`` from Markdown. ``title`` goes into the document properties."""
    return write_docx(parse_markdown(markdown), title=title)


def write_docx(blocks: Sequence[Block], *, title: str) -> bytes:
    body = "".join(_block_xml(block) for block in blocks)
    parts = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{_W_NS}"><w:body>{body}{_SECTION}</w:body></w:document>'
        ),
        "word/_rels/document.xml.rels": _DOCUMENT_RELS,
        "word/styles.xml": _STYLES,
        "word/settings.xml": _SETTINGS,
        "docProps/core.xml": _core_properties(title),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()


def _is_rtl(text: str) -> bool:
    """True when most of the letters in ``text`` are Arabic-script letters."""
    letters = [char for char in text if char.isalpha()]
    arabic = [char for char in letters if _is_arabic_letter(char)]
    return bool(letters) and len(arabic) * 2 > len(letters)


def _is_arabic_letter(char: str) -> bool:
    code = ord(char)
    return (
        0x0600 <= code <= 0x06FF
        or 0x0750 <= code <= 0x077F
        or 0xFB50 <= code <= 0xFDFF
        or 0xFE70 <= code <= 0xFEFF
    )


def _runs(text: str, *, rtl: bool, bold: bool = False) -> str:
    """Runs for ``text``, splitting out ``**bold**`` segments."""
    runs = []
    for index, segment in enumerate(_BOLD.split(text)):  # odd indexes are the bold parts
        if segment:
            runs.append(_run(segment, rtl=rtl, bold=bold or index % 2 == 1))
    return "".join(runs)


def _run(text: str, *, rtl: bool, bold: bool) -> str:
    properties = ""
    if bold:
        properties += "<w:b/><w:bCs/>"
    if rtl:
        properties += '<w:rtl/><w:lang w:bidi="ar-SA"/>'
    formatting = f"<w:rPr>{properties}</w:rPr>" if properties else ""
    return f'<w:r>{formatting}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _paragraph(
    text: str, *, rtl: bool | None = None, style: str | None = None, bold: bool = False
) -> str:
    if rtl is None:
        rtl = _is_rtl(plain(text))
    properties = (f'<w:pStyle w:val="{style}"/>' if style else "") + ("<w:bidi/>" if rtl else "")
    formatting = f"<w:pPr>{properties}</w:pPr>" if properties else ""
    return f"<w:p>{formatting}{_runs(text, rtl=rtl, bold=bold)}</w:p>"


def _table(table: Table) -> str:
    rtl = _is_rtl(plain(" ".join(cell for row in table.rows for cell in row)))
    columns = max(len(row) for row in table.rows)
    width = _TABLE_WIDTH_DXA // columns
    border = 'w:val="single" w:sz="4" w:space="0" w:color="808080"'
    properties = (
        "<w:tblPr>"
        + ("<w:bidiVisual/>" if rtl else "")
        + f'<w:tblW w:w="{_TABLE_WIDTH_DXA}" w:type="dxa"/>'
        + "<w:tblBorders>"
        + "".join(f"<w:{side} {border}/>" for side in ("top", "left", "bottom", "right"))
        + f"<w:insideH {border}/><w:insideV {border}/></w:tblBorders>"
        + '<w:tblLook w:val="04A0"/></w:tblPr>'
    )
    grid = "<w:tblGrid>" + f'<w:gridCol w:w="{width}"/>' * columns + "</w:tblGrid>"
    rows = []
    for row_number, row in enumerate(table.rows):
        header = row_number == 0
        cells = "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>'
            + ('<w:shd w:val="clear" w:color="auto" w:fill="E7E6E6"/>' if header else "")
            + f"</w:tcPr>{_paragraph(cell, rtl=rtl, bold=header)}</w:tc>"
            for cell in row
        )
        marker = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
        rows.append(f"<w:tr>{marker}{cells}</w:tr>")
    # Word requires a paragraph after a table; an empty one also spaces what follows.
    return f"<w:tbl>{properties}{grid}{''.join(rows)}</w:tbl><w:p/>"


def _block_xml(block: Block) -> str:
    if isinstance(block, Heading):
        return _paragraph(block.text, style=f"Heading{block.level}")
    if isinstance(block, Table):
        return _table(block)
    if isinstance(block, PageBreak):
        return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
    return _paragraph(block.text)


_SECTION = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
    'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
)

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/settings.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    '<Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    'Target="docProps/core.xml"/>'
    "</Relationships>"
)

_DOCUMENT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/>'
    '<Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" '
    'Target="settings.xml"/>'
    "</Relationships>"
)

# Declares modern Word (2013+) layout, so Word does not open the file in Compatibility Mode.
_SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:settings xmlns:w="{_W_NS}"><w:compat>'
    '<w:compatSetting w:name="compatibilityMode" '
    'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/>'
    "</w:compat></w:settings>"
)

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{_W_NS}">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Arial"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US" w:bidi="ar-SA"/>'
    "</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>"
    '<w:spacing w:after="120" w:line="276" w:lineRule="auto"/>'
    "</w:pPr></w:pPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    "<w:qFormat/></w:style>"
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>'
    '<w:rPr><w:b/><w:bCs/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
    '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr>'
    '<w:rPr><w:b/><w:bCs/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>'
    "</w:styles>"
)


def _core_properties(title: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        "<cp:coreProperties "
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape(title)}</dc:title>"
        "<dc:creator>Acme MENA Technology (synthetic demo data)</dc:creator>"
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:created>'
        "</cp:coreProperties>"
    )
