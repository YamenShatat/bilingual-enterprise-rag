"""Wrap a `sentence-transformers` model behind the `Embedder` interface.

Any per-model prefix a model's own card calls for (E5 needs ``query: `` and ``passage: ``;
bge-m3's card says explicitly it needs neither) is supplied by the caller at construction,
never guessed here. Text is embedded exactly as given, with only that prefix prepended:
nothing is stripped, lower-cased or otherwise normalized (D-004, D-013).
"""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

from bilingual_rag.embeddings.base import model_key

DEFAULT_BATCH_SIZE = 32


class _Encodable(Protocol):
    """What this wrapper needs from a model: real `SentenceTransformer` or a test stub."""

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
    ): ...


class SentenceTransformerEmbedder:
    """An `Embedder` backed by a `sentence-transformers` model.

    ``device`` defaults to CUDA when it is available, else CPU. ``dimension`` is not read from
    a method name (sentence-transformers has already renamed
    ``get_sentence_embedding_dimension`` to ``get_embedding_dimension`` once); it is measured
    by encoding one probe string, which also confirms the model actually loads.
    """

    def __init__(
        self,
        model_name: str,
        *,
        key: str | None = None,
        device: str | None = None,
        query_prefix: str = "",
        document_prefix: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
        _model: _Encodable | None = None,
    ):
        """
        Raises:
            ValueError: `batch_size` is not positive.
        """
        if batch_size < 1:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        self.model_name = model_name
        self.key = key if key is not None else model_key(model_name)
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self.batch_size = batch_size
        self.device = (
            device if device is not None else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        self._model = (
            _model if _model is not None else SentenceTransformer(model_name, device=self.device)
        )
        self.dimension = len(self._encode(["x"])[0])

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True, convert_to_numpy=True
        )
        return np.asarray(vectors, dtype=float).tolist()

    @staticmethod
    def _check(text: object) -> None:
        if not isinstance(text, str):
            raise TypeError(f"text must be a string, got {type(text).__name__}")
        if not text.strip():
            raise ValueError("text must not be blank")

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, str):
            raise TypeError("texts must be a sequence of strings, not one string")
        texts = list(texts)
        for text in texts:
            self._check(text)
        if not texts:
            return []
        return self._encode([self.document_prefix + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        self._check(text)
        return self._encode([self.query_prefix + text])[0]


def bge_m3(
    *, device: str | None = None, batch_size: int = DEFAULT_BATCH_SIZE
) -> SentenceTransformerEmbedder:
    """BAAI/bge-m3: multilingual, 1024-dim, 8192-token context, MIT.

    No query or document prefix: the model card states "the BGE-M3 model no longer requires
    adding instructions to the queries" (unlike bge-large-en-v1.5 or the E5 models).
    """
    return SentenceTransformerEmbedder("BAAI/bge-m3", device=device, batch_size=batch_size)


def e5_large(
    *, device: str | None = None, batch_size: int = DEFAULT_BATCH_SIZE
) -> SentenceTransformerEmbedder:
    """intfloat/multilingual-e5-large: multilingual, 1024-dim, 512-token limit, MIT.

    Both prefixes are required: the model card states "each input text should start with
    'query: ' or 'passage: ', even for non-English texts... otherwise you will see a
    performance degradation."
    """
    return SentenceTransformerEmbedder(
        "intfloat/multilingual-e5-large",
        device=device,
        query_prefix="query: ",
        document_prefix="passage: ",
        batch_size=batch_size,
    )
