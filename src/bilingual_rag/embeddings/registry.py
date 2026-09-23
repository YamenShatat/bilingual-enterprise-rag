"""Look up an `Embedder` by name, shared by the ingestion and evaluation scripts."""

from bilingual_rag.embeddings.base import Embedder
from bilingual_rag.embeddings.hashing import HashingEmbedder

EMBEDDER_NAMES = ("bge-m3", "e5-large", "hashing")


def make_embedder(name: str) -> Embedder:
    """
    Raises:
        ValueError: `name` is not one of `EMBEDDER_NAMES`.
        RuntimeError: a real model is requested and the "embeddings" extra is not installed.
    """
    if name == "hashing":
        return HashingEmbedder()
    if name in ("bge-m3", "e5-large"):
        try:
            from bilingual_rag.embeddings import sentence_transformer
        except ImportError as exc:
            raise RuntimeError(
                f'the {name} embedder needs the "embeddings" extra: '
                'pip install -e ".[embeddings]" (see README for the Windows CUDA install order)'
            ) from exc
        factory = sentence_transformer.bge_m3 if name == "bge-m3" else sentence_transformer.e5_large
        return factory()
    raise ValueError(f"unknown embedder {name!r}, expected one of {EMBEDDER_NAMES}")
