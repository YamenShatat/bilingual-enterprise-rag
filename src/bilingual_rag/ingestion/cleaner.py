"""Mechanical text cleaning that is safe for Arabic and English.

The cleaner only fixes things that are never meaningful in a document: mixed line
endings, control characters, a few invisible characters and irregular whitespace.

It deliberately does NOT change Arabic letters. No Unicode normalisation (NFC/NFKC),
no folding of alef/ya/ta-marbuta variants, and no removal of tatweel or diacritics.
Whether any of those helps retrieval must be measured in the evaluation stage first.

Also left untouched on purpose, pending evaluation or real PDF output:
  * ZWNJ (U+200C) and ZWJ (U+200D), which are meaningful in Arabic-script orthography.
  * Direction marks (LRM, RLM, ALM and bidi embeddings/isolates).
  * Arabic presentation forms (U+FB50-U+FEFF) that PDF extractors sometimes emit.

Special characters are written as ``\\N{NAME}`` escapes so the source stays readable.
"""

import re
from dataclasses import replace

from bilingual_rag.ingestion.models import Document

# Every kind of line break becomes "\n". Vertical tab, form feed, NEL and the
# Unicode line/paragraph separators are line breaks in practice (often from PDFs).
_LINE_BREAK = re.compile("\r\n|[\r\x0b\x0c\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]")

# C0/C1 control characters except tab and newline (line breaks were handled above).
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f]")

# Invisible characters that carry no meaning in running text. An explicit list is
# used instead of the whole Unicode "format" category, which would also delete the
# meaningful ZWNJ/ZWJ.
_INVISIBLE = re.compile(
    "[\N{SOFT HYPHEN}\N{ZERO WIDTH SPACE}\N{WORD JOINER}\N{ZERO WIDTH NO-BREAK SPACE}]"
)

# Any whitespace except newline (tabs, NBSP, ideographic space, ...).
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
_SPACE_AROUND_NEWLINE = re.compile(r" ?\n ?")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Return ``text`` with line endings, control characters and whitespace tidied.

    Paragraph breaks (a blank line) are preserved because the chunker splits on them.
    The function is idempotent: cleaning already-clean text changes nothing.
    """
    text = _LINE_BREAK.sub("\n", text)
    text = _CONTROL.sub("", text)
    text = _INVISIBLE.sub("", text)
    text = _HORIZONTAL_SPACE.sub(" ", text)
    text = _SPACE_AROUND_NEWLINE.sub("\n", text)
    text = _EXCESS_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def clean_document(document: Document) -> Document:
    """Return a copy of ``document`` with every page cleaned."""
    pages = tuple(replace(page, text=clean_text(page.text)) for page in document.pages)
    return replace(document, pages=pages)
