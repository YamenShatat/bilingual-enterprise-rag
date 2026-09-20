"""Load source files into :class:`Document` objects.

Loaders return text exactly as stored in the file. Cleaning and normalisation
are separate, explicit steps so that every change to Arabic text is deliberate.
"""

from collections.abc import Callable
from pathlib import Path

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


_LOADERS: dict[str, Callable[[Path], Document]] = {
    ".txt": _load_text,
    ".md": _load_text,
}


def load_document(path: Path) -> Document:
    """Load ``path`` with the loader matching its (case-insensitive) extension.

    Raises:
        UnsupportedFileTypeError: the extension has no loader.
        DocumentLoadError: the file could not be decoded.
        FileNotFoundError: ``path`` does not exist.
    """
    loader = _LOADERS.get(path.suffix.lower())
    if loader is None:
        supported = ", ".join(sorted(_LOADERS))
        raise UnsupportedFileTypeError(
            f"Unsupported file type {path.suffix!r} for {path.name}. Supported: {supported}"
        )
    return loader(path)
