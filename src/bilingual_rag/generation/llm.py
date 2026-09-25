"""The LLM interface and a client for a local Ollama server, using only the standard library.

Ollama's HTTP API is one JSON request per answer, so a client package would add a dependency
for about twenty lines of ``urllib``. Three request settings matter and are always sent:

- ``num_ctx``: Ollama's default context window is 4096 tokens whatever the model supports, and a
  longer prompt is truncated silently, not rejected. The window is set explicitly every call.
- ``think: false``: qwen3 otherwise writes its reasoning before the answer.
- ``temperature: 0``: the same question and evidence give the same answer, which tests and
  debugging rely on.
"""

import json
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "qwen3:8b"
# 127.0.0.1, not localhost: Ollama listens on IPv4 loopback, and localhost may resolve to IPv6.
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_NUM_CTX = 8192


class GenerationError(Exception):
    """The LLM could not be reached or returned something unusable."""


@runtime_checkable
class LLM(Protocol):
    """Anything that answers a prompt under a system instruction."""

    model_name: str

    def generate(self, system: str, prompt: str) -> str:
        """The model's reply to ``prompt``, following ``system``, exactly as returned."""
        ...


class OllamaLLM:
    """Chat with one model on a local Ollama server."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
        num_ctx: int = DEFAULT_NUM_CTX,
        timeout: float = 300.0,
    ):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-blank string")
        if isinstance(num_ctx, bool) or not isinstance(num_ctx, int) or num_ctx < 1:
            raise ValueError(f"num_ctx must be a positive integer, got {num_ctx!r}")
        if not timeout > 0:
            raise ValueError(f"timeout must be positive, got {timeout!r}")
        self.model_name = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout = timeout

    def generate(self, system: str, prompt: str) -> str:
        """
        Raises:
            TypeError: ``system`` or ``prompt`` is not a string.
            ValueError: ``prompt`` is blank.
            GenerationError: Ollama is unreachable, the model is missing, or the reply is not
                the expected JSON.
        """
        if not isinstance(system, str) or not isinstance(prompt, str):
            raise TypeError("system and prompt must be strings")
        if not prompt.strip():
            raise ValueError("prompt must not be blank")
        body = {
            "model": self.model_name,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # Ollama explains itself in {"error": "..."}, e.g. a model that was never pulled.
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail)["error"]
            except (ValueError, KeyError, TypeError):
                pass
            raise GenerationError(
                f"Ollama returned HTTP {exc.code} for model {self.model_name!r}: {detail} "
                f"(is it pulled? `ollama pull {self.model_name}`)"
            ) from exc
        except OSError as exc:  # URLError (refused, DNS) and timeouts are both OSErrors
            raise GenerationError(
                f"cannot reach Ollama at {self.base_url} ({exc}); is it running?"
            ) from exc

        try:
            content = json.loads(raw.decode("utf-8"))["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GenerationError(f"unexpected reply from Ollama: {raw[:200]!r}") from exc
        if not isinstance(content, str):
            raise GenerationError(f"unexpected reply from Ollama: {raw[:200]!r}")
        return content
