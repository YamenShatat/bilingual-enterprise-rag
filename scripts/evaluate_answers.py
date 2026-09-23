"""Run the evaluation question set through ask() and report refusal, citation and language counts.

Uses the chunks and embeddings already in the database (run scripts/ingest_documents.py first)
and a local Ollama model. It does not judge whether an answer's content is correct (the questions
have no reference answers); pass --output to save every answer for reading (D-019).

Usage (from the repository root, with the project environment active and Ollama running):

    python scripts/evaluate_answers.py
    python scripts/evaluate_answers.py --output answers.json
"""

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import psycopg

from bilingual_rag.config import ConfigError, load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.embeddings.registry import EMBEDDER_NAMES, make_embedder
from bilingual_rag.evaluation.answers import run_answer_questions, summarize_answers
from bilingual_rag.evaluation.questions import QuestionsError, load_questions
from bilingual_rag.generation.llm import DEFAULT_MODEL, GenerationError, OllamaLLM
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUESTIONS = REPO_ROOT / "data" / "evaluation_questions.json"
ENV_FILE = REPO_ROOT / ".env"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--embedder", choices=EMBEDDER_NAMES, default="bge-m3")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, help="write every question and answer as JSON")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    try:
        settings = load_database_settings(ENV_FILE)
        questions = load_questions(args.questions)
        embedder = make_embedder(args.embedder)
    except (ConfigError, QuestionsError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        conn = connect(settings)
    except psycopg.OperationalError as exc:
        print(f"cannot connect: {str(exc).strip()}", file=sys.stderr)
        print("is it running? try: docker compose up -d --wait db", file=sys.stderr)
        return 1

    llm = OllamaLLM(args.model)
    started = time.perf_counter()
    try:
        # Every access level: this measures answering, not permissions (tested separately).
        outcomes = run_answer_questions(conn, embedder, llm, questions, set(ACCESS_LEVELS))
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    elapsed = time.perf_counter() - started

    print(f"embedder={embedder.model_name} llm={llm.model_name} questions={len(questions)}")
    print(f"elapsed={elapsed:.0f}s\n")
    for name, block in summarize_answers(outcomes).items():
        print(
            f"{name:24} n={block['count']:<3} answered={block['answered']:<3} "
            f"cites_expected={block['cites_expected']:<3} "
            f"right_language={block['right_language']:<3} refusals={block['refusals']}"
        )

    if args.output:
        records = [
            {"question": dataclasses.asdict(o.question), "answer": dataclasses.asdict(o.answer)}
            for o in outcomes
        ]
        args.output.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {len(records)} answers to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
