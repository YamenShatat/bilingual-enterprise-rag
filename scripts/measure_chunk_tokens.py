"""Measure the real corpus's chunks in each candidate embedding model's own tokenizer.

Answers the open question from D-006 ("sizes are in characters, not tokens"): how many chunks
at the current 1200/200 character defaults exceed each model's real token limit. The tokenizer
is used for MEASURING only here, never for splitting.

Downloads only each model's small tokenizer.json (10-20 MB) through the Hugging Face cache, not
the multi-gigabyte model weights, and needs network access. A tokenizer's own truncation
setting (mpnet's is baked in at 128) is disabled before measuring, or every count would be
capped at the limit and every model would wrongly look safe.

Usage (from the repository root, with the project environment active):

    python scripts/measure_chunk_tokens.py
"""

import sys
from pathlib import Path

from tokenizers import Tokenizer

from bilingual_rag.ingestion.pipeline import ingest_directory

DATA = Path(__file__).resolve().parents[1] / "data" / "synthetic"

# (model name, the max_seq_length from its sentence_bert_config.json, checked on huggingface.co)
MODELS = [
    ("BAAI/bge-m3", 8192),
    ("intfloat/multilingual-e5-large", 512),
    ("sentence-transformers/paraphrase-multilingual-mpnet-base-v2", 128),
]


def main() -> int:
    chunks = ingest_directory(DATA).chunks
    if not chunks:
        print(f"no chunks found under {DATA}", file=sys.stderr)
        return 1
    print(f"{len(chunks)} chunks\n")

    for model_name, limit in MODELS:
        tokenizer = Tokenizer.from_pretrained(model_name)
        if tokenizer.truncation is not None:
            baked_in = tokenizer.truncation["max_length"]
            tokenizer.no_truncation()
            print(f"({model_name} truncates at {baked_in} tokens by default; disabled to measure)")

        lengths = sorted(len(tokenizer.encode(chunk.text).ids) for chunk in chunks)
        over = sum(1 for length in lengths if length > limit)
        median = lengths[len(lengths) // 2]
        print(
            f"{model_name}\n"
            f"  limit {limit} tokens | min {lengths[0]} median {median} max {lengths[-1]}\n"
            f"  chunks exceeding the limit: {over} / {len(chunks)}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
