"""Load source files into :class:`Document` objects.

Loaders return text exactly as stored in the file. Cleaning and normalisation
are separate, explicit steps so that every change to Arabic text is deliberate.
"""

from collections.abc import Callable
from pathlib import Path

import pypdfium2 as pdfium

from bilingual_rag.ingestion.docx_reader import DocxFormatError, read_docx_pages
from bilingual_rag.ingestion.models import Document, Page


class UnsupportedFileTypeError(ValueError):
    """Raised when a file's extension has no loader."""


class DocumentLoadError(ValueError):
    """Raised when a supported file cannot be read or decoded."""


def _load_text(path: Path) -> Document:
    """Load a UTF-8 text file (TXT, Markdown) as a single-page document.

    Decoding is strict on purpose: ``errors="replace"`` would silently turn
    undecodable Arabic into U+FFFD. ``utf-8-sig`` transparently drops the byte
    order mark that some Windows editors add, so it never leaks into the text.
    """
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentLoadError(f"{path.name} is not valid UTF-8 text: {exc}") from exc
    return Document(filename=path.name, pages=(Page(number=1, text=text),))


def _load_pdf(path: Path) -> Document:
    """Load a PDF with one :class:`Page` per PDF page, keeping blank pages.

    Page numbers must match the printed document so citations stay correct, so a page
    with no text layer (for example a scanned image) is kept as an empty page instead of
    being dropped. Pages that are not extractable at all are reported later by the
    pipeline as "no text after cleaning".

    The file is read into memory first, so no file handle stays open (which would block
    deleting the file on Windows) and missing files raise ``FileNotFoundError`` like the
    text loader. The extractor is PDFium; see docs/decisions.md for why, and for its
    known limits with Arabic (word order within lines, lam-alef ligatures).
    """
    data = path.read_bytes()
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise DocumentLoadError(f"{path.name} could not be read as a PDF: {exc}") from exc
    try:
        pages = tuple(
            Page(number=number, text=_pdf_page_text(pdf[number - 1]))
            for number in range(1, len(pdf) + 1)
        )
    except pdfium.PdfiumError as exc:
        raise DocumentLoadError(f"{path.name} could not be read as a PDF: {exc}") from exc
    finally:
        pdf.close()
    return Document(filename=path.name, pages=pages)


def _pdf_page_text(page: pdfium.PdfPage) -> str:
    try:
        text_page = page.get_textpage()
        try:
            return text_page.get_text_range()
        finally:
            text_page.close()
    finally:
        page.close()


def _load_docx(path: Path) -> Document:
    """Load a Word document. Pages are approximate; see :mod:`docx_reader` for how.

    Word stores text in logical order, so this is the most reliable way to ingest Arabic
    source documents (see docs/pdf-extraction.md for how PDF compares).
    """
    try:
        texts = read_docx_pages(path.read_bytes())
    except DocxFormatError as exc:
        raise DocumentLoadError(f"{path.name} could not be read as a .docx file: {exc}") from exc
    pages = tuple(Page(number=number, text=text) for number, text in enumerate(texts, start=1))
    return Document(filename=path.name, pages=pages)


_LOADERS: dict[str, Callable[[Path], Document]] = {
    ".txt": _load_text,
    ".md": _load_text,
    ".pdf": _load_pdf,
    ".docx": _load_docx,
}


def load_document(path: Path) -> Document:
    """Load ``path`` with the loader matching its (case-insensitive) extension.

    Raises:
        UnsupportedFileTypeError: the extension has no loader.
        DocumentLoadError: the file could not be decoded or parsed.
        FileNotFoundError: ``path`` does not exist.
    """
    loader = _LOADERS.get(path.suffix.lower())
    if loader is None:
        supported = ", ".join(sorted(_LOADERS))
        raise UnsupportedFileTypeError(
            f"Unsupported file type {path.suffix!r} for {path.name}. Supported: {supported}"
        )
    return loader(path)
