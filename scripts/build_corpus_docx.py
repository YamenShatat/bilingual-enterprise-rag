"""Build the generated corpus files from their Markdown sources.

The manifest (data/manifest.json) lists every corpus document. Documents whose format is
``docx`` are generated from a Markdown source under data/sources/. Documents whose format
is ``pdf`` are made in two steps: this script writes an intermediate .docx, and
scripts/export_pdfs_with_word.ps1 turns it into the final PDF with Microsoft Word (so the
PDF is a genuine Word PDF, with the extraction behaviour real enterprise PDFs have).

Usage (from the repository root, with the project environment active):

    python scripts/build_corpus_docx.py                       write the DOCX corpus files
    python scripts/build_corpus_docx.py --check               verify committed DOCX files match
                                                              their sources (writes nothing)
    python scripts/build_corpus_docx.py --pdf-intermediates D write the .docx used for PDFs to D
"""

import argparse
import json
import sys
from pathlib import Path

from bilingual_rag.corpus.markdown_docx import expected_pages, markdown_to_docx, parse_markdown
from bilingual_rag.ingestion.docx_reader import read_docx_pages

DATA = Path(__file__).resolve().parents[1] / "data"


def load_manifest() -> list[dict]:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    return manifest["documents"]


def build(document: dict) -> bytes:
    source = (DATA / document["source"]).read_text(encoding="utf-8")
    return markdown_to_docx(source, title=document["title"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    parser.add_argument("--pdf-intermediates", type=Path, metavar="DIR")
    args = parser.parse_args()

    failures = 0
    for document in load_manifest():
        if document["format"] == "docx":
            target = DATA / "synthetic" / document["path"]
            if args.check:
                source = (DATA / document["source"]).read_text(encoding="utf-8")
                expected = expected_pages(parse_markdown(source))
                actual = read_docx_pages(target.read_bytes()) if target.exists() else None
                status = "ok" if actual == expected else "OUT OF DATE"
                failures += status != "ok"
                print(f"{status:12} {document['path']}")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(build(document))
                print(f"wrote        {document['path']}")
        elif document["format"] == "pdf" and args.pdf_intermediates and not args.check:
            args.pdf_intermediates.mkdir(parents=True, exist_ok=True)
            name = Path(document["path"]).with_suffix(".docx").name
            (args.pdf_intermediates / name).write_bytes(build(document))
            print(f"intermediate {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
