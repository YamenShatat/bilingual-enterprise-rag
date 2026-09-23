"""Load the corpus, store it, and embed every chunk. The last step before a working search.

Loads the manifest, ingests every file it lists, stores each document and its chunks
(idempotent: an unchanged document writes nothing, changed text replaces only that
document's chunks), then embeds every chunk that has no vector yet for the chosen model.

Usage (from the repository root, with the project environment active):

    docker compose up -d --wait db
    python scripts/ingest_documents.py                     # bge-m3 (needs the "embeddings" extra)
    python scripts/ingest_documents.py --embedder e5-large  # multilingual-e5-large, same extra
    python scripts/ingest_documents.py --embedder hashing   # deterministic stand-in, no download
"""

import argparse
import sys
from pathlib import Path

import psycopg

from bilingual_rag.config import ConfigError, load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.database.migrate import migrate
from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.embeddings.registry import EMBEDDER_NAMES, make_embedder
from bilingual_rag.ingestion.manifest import ManifestError, load_manifest
from bilingual_rag.ingestion.pipeline import ingest_directory

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "synthetic"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "manifest.json"
ENV_FILE = REPO_ROOT / ".env"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--embedder", choices=EMBEDDER_NAMES, default="bge-m3")
    parser.add_argument("--batch-size", type=int, default=32)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()

    try:
        settings = load_database_settings(ENV_FILE)
        documents = load_manifest(args.manifest)
    except (ConfigError, ManifestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        embedder = make_embedder(args.embedder)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        print(f"cannot connect: {str(exc).strip()}", file=sys.stderr)
        print("is it running? try: docker compose up -d --wait db", file=sys.stderr)
        return 1

    try:
        migrate(conn)  # idempotent; makes sure the schema exists before anything writes to it
        conn.commit()

        result = ingest_directory(args.data_dir)
        for skipped in result.skipped:
            print(f"skipped   {skipped.filename}: {skipped.reason}", file=sys.stderr)
        chunks_by_path: dict[str, list] = {}
        for chunk in result.chunks:
            chunks_by_path.setdefault(chunk.filename, []).append(chunk)

        outcomes = {"inserted": 0, "unchanged": 0, "updated": 0}
        for document in documents:
            chunks = chunks_by_path.get(document.path)
            if not chunks:
                print(
                    f"warning   {document.path} is in the manifest but was not ingested",
                    file=sys.stderr,
                )
                continue
            outcomes[store_document(conn, document, chunks)] += 1
        conn.commit()
        print(
            f"documents: {outcomes['inserted']} inserted, {outcomes['updated']} updated, "
            f"{outcomes['unchanged']} unchanged"
        )

        print(
            f"embedding model: {embedder.model_name} (key={embedder.key}, dim={embedder.dimension})"
        )

        def on_batch(done: int, total: int) -> None:
            conn.commit()  # keeps progress across a crash; embed_missing itself never commits
            print(f"embedded  {done}/{total}")

        newly_embedded = embed_missing(
            conn, embedder, batch_size=args.batch_size, on_batch=on_batch
        )
        conn.commit()
        print(f"newly embedded: {newly_embedded} chunks")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
