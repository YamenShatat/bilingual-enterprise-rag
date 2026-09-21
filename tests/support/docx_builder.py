"""Build minimal .docx files in memory so tests can control the exact XML."""

import io
import zipfile
from xml.sax.saxutils import escape

_NAMESPACES = " ".join(
    [
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"',
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"',
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"',
        'xmlns:v="urn:schemas-microsoft-com:vml"',
    ]
)

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)

PAGE_BREAK_RUN = '<w:r><w:br w:type="page"/></w:r>'
RENDERED_BREAK_RUN = "<w:r><w:lastRenderedPageBreak/></w:r>"


def run(text: str) -> str:
    """A run holding ``text`` (XML-escaped)."""
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def para(*parts: str) -> str:
    """A paragraph made of already-built XML parts (runs, hyperlinks, ...)."""
    return f"<w:p>{''.join(parts)}</w:p>"


def text_para(text: str) -> str:
    """A paragraph with a single run."""
    return para(run(text))


def table(*rows: list[str]) -> str:
    """A table of single-paragraph cells."""
    body = "".join(
        "<w:tr>" + "".join(f"<w:tc>{text_para(cell)}</w:tc>" for cell in row) + "</w:tr>"
        for row in rows
    )
    return f"<w:tbl>{body}</w:tbl>"


def document_xml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_NAMESPACES}><w:body>{body}</w:body></w:document>"
    )


def docx_bytes(body: str, extra_parts: dict[str, str] | None = None) -> bytes:
    """A .docx package whose body is ``body``, plus any extra parts (headers, footers, ...)."""
    parts = {"[Content_Types].xml": _CONTENT_TYPES, "word/document.xml": document_xml(body)}
    parts.update(extra_parts or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content.encode("utf-8"))
    return buffer.getvalue()
