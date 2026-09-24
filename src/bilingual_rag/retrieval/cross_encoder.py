"""A `sentence-transformers` cross-encoder behind the `Reranker` interface (needs the
"embeddings" extra). Texts are scored exactly as given: nothing is stripped or normalized."""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
import torch
from sentence_transformers import CrossEncoder

DEFAULT_BATCH_SIZE = 32


class _Predictor(Protocol):
    """What this wrapper needs from a model: real `CrossEncoder` or a test stub."""

    def predict(self, pairs: list[tuple[str, str]], *, batch_size: int): ...


class CrossEncoderReranker:
    """A `Reranker` backed by a cross-encoder. ``device`` defaults to CUDA when available.

    On CUDA the weights are loaded in half precision: bge-reranker-v2-m3 then takes about 1.1 GB
    of GPU memory instead of about 2.2 GB, which is what lets it share an 8 GB GPU with the
    embedding model and the LLM (D-023). On CPU it stays in full precision.
    """

    def __init__(
        self,
        model_name: str,
        *,
        device: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        _model: _Predictor | None = None,
    ):
        """
        Raises:
            ValueError: ``batch_size`` is not positive.
        """
        if batch_size < 1:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        self.model_name = model_name
        self.batch_size = batch_size
        self.device = (
            device if device is not None else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        if _model is None:
            dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
            _model = CrossEncoder(model_name, device=self.device, model_kwargs={"dtype": dtype})
        self._model = _model

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """
        Raises:
            TypeError: ``query`` or a text is not a string, or ``texts`` is one string.
            ValueError: ``query`` or a text is blank.
        """
        if isinstance(texts, str):
            raise TypeError("texts must be a sequence of strings, not one string")
        for value in (query, *texts):
            if not isinstance(value, str):
                raise TypeError(f"expected a string, got {type(value).__name__}")
            if not value.strip():
                raise ValueError("query and texts must not be blank")
        if not texts:
            return []
        scores = self._model.predict([(query, text) for text in texts], batch_size=self.batch_size)
        return np.asarray(scores, dtype=float).reshape(-1).tolist()


def bge_reranker_v2_m3(*, device: str | None = None) -> CrossEncoderReranker:
    """BAAI/bge-reranker-v2-m3: multilingual, Apache-2.0, scores from 0 to 1 (sigmoid)."""
    return CrossEncoderReranker("BAAI/bge-reranker-v2-m3", device=device)
