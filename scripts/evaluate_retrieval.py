"""Run the Week 3 evaluation question set through search() and report Recall@k / MRR.

Uses whatever chunks and embeddings are already in the database (from
scripts/ingest_documents.py). Pass --chunk-size/--overlap to re-ingest the corpus at a
different chunking before evaluating (D-006); only the changed documents' chunks (and their
now-stale embeddings) are replaced, and embed_missing recomputes just what is missing.

One embedder, one chunking, per run -- this is NOT a benchmark suite. Compare models or
chunk sizes by running it more than once.

Usage (from the repository root, with the project environment active):

    python scripts/evaluate_retrieval.py --embedder bge-m3
    python scripts/evaluate_retrieval.py --embedder e5-large --chunk-size 800 --overlap 150
    python scripts/evaluate_retrieval.py --mode hybrid
"""

import argparse
import sys
from pathlib import Path

import psycopg

from bilingual_rag.config import ConfigError, load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.embeddings.registry import EMBEDDER_NAMES, make_embedder
from bilingual_rag.evaluation.questions import QuestionsError, load_questions
from bilingual_rag.evaluation.runner import DEFAULT_K_VALUES, run_questions, summarize
from bilingual_rag.ingestion.chunker import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS, ManifestError, load_manifest
from bilingual_rag.ingestion.pipeline import ingest_directory
from bilingual_rag.retrieval.hybrid import MODES

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "synthetic"
DEFAULT_MANIFEST = REPO_ROOT / "data" / "manifest.json"
DEFAULT_QUESTIONS = REPO_ROOT / "data" / "evaluation_questions.json"
ENV_FILE = REPO_ROOT / ".env"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--embedder", choices=EMBEDDER_NAMES, default="bge-m3")
    parser.add_argument("--mode", choices=MODES, default="vector", help="retrieval mode (D-022)")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    return parser.parse_args(argv)


def _print_block(name: str, block: dict) -> None:
    recalls = ", ".join(
        f"{key}={value:.3f}" for key, value in block.items() if key.startswith("recall@")
    )
    print(f"{name:16} n={block['count']:<3} {recalls}  mrr={block['mrr']:.3f}")


def main() -> int:
    args = parse_args()

    try:
        settings = load_database_settings(ENV_FILE)
        documents = load_manifest(args.manifest)
        questions = load_questions(args.questions)
    except (ConfigError, ManifestError, QuestionsError) as exc:
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
        result = ingest_directory(args.data_dir, chunk_size=args.chunk_size, overlap=args.overlap)
        chunks_by_path: dict[str, list] = {}
        for chunk in result.chunks:
            chunks_by_path.setdefault(chunk.filename, []).append(chunk)
        for document in documents:
            chunks = chunks_by_path.get(document.path)
            if chunks:
                store_document(conn, document, chunks)
        conn.commit()

        newly_embedded = embed_missing(conn, embedder, on_batch=lambda done, total: conn.commit())
        conn.commit()

        print(
            f"mode={args.mode} embedder={embedder.model_name} chunk_size={args.chunk_size} "
            f"overlap={args.overlap} newly_embedded={newly_embedded}\n"
        )
        outcomes = run_questions(conn, embedder, questions, set(ACCESS_LEVELS), mode=args.mode)
        summary = summarize(outcomes, DEFAULT_K_VALUES)
        for name, block in summary.items():
            if name == "absent_fact":
                score = block["mean_top_score"]
                print(f"{name:16} n={block['count']:<3} mean_top_score={score:.3f}")
            else:
                _print_block(name, block)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
