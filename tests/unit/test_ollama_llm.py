"""OllamaLLM against a stub HTTP server running in this process: no Ollama needed."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bilingual_rag.generation.llm import LLM, GenerationError, OllamaLLM
from support.arabic import ARABIC_TRUTH


class StubOllama:
    """Records each request body and answers with a programmable status and body."""

    def __init__(self):
        self.requests: list[dict] = []
        self.paths: list[str] = []
        self.status = 200
        self.reply: bytes = json.dumps({"message": {"content": "ok"}}).encode("utf-8")

    def respond(self, content: str) -> None:
        self.reply = json.dumps({"message": {"role": "assistant", "content": content}}).encode(
            "utf-8"
        )


@pytest.fixture
def stub():
    state = StubOllama()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            # The raw request line: self.path has a leading "//" collapsed to "/" by http.server.
            state.paths.append(self.requestline.split()[1])
            state.requests.append(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(state.reply)))
            self.end_headers()
            self.wfile.write(state.reply)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    # A short poll interval: shutdown() waits for one poll, 0.5 s by default, in every test.
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()
    server.server_close()


def client(stub, **kwargs):
    return OllamaLLM("test-model", base_url=stub.url, timeout=5, **kwargs)


class TestRequest:
    def test_posts_to_the_chat_endpoint(self, stub):
        client(stub).generate("sys", "question")
        assert stub.paths == ["/api/chat"]

    def test_a_trailing_slash_in_the_base_url_is_harmless(self, stub):
        OllamaLLM("test-model", base_url=stub.url + "/", timeout=5).generate("sys", "q")
        assert stub.paths == ["/api/chat"]

    def test_sends_system_then_user_message(self, stub):
        client(stub).generate("the rules", "the question")
        assert stub.requests[0]["messages"] == [
            {"role": "system", "content": "the rules"},
            {"role": "user", "content": "the question"},
        ]

    def test_always_sets_the_context_window_explicitly(self, stub):
        # Ollama's own default is 4096 tokens, and a longer prompt is truncated silently.
        client(stub, num_ctx=12288).generate("sys", "q")
        assert stub.requests[0]["options"]["num_ctx"] == 12288

    def test_default_context_window_is_larger_than_ollamas_default(self, stub):
        client(stub).generate("sys", "q")
        assert stub.requests[0]["options"]["num_ctx"] > 4096

    def test_is_deterministic_non_streaming_and_without_thinking(self, stub):
        client(stub).generate("sys", "q")
        body = stub.requests[0]
        assert body["model"] == "test-model"
        assert body["stream"] is False
        assert body["think"] is False
        assert body["options"]["temperature"] == 0

    def test_arabic_prompt_reaches_the_server_unchanged(self, stub):
        text = "\n".join(ARABIC_TRUTH)
        client(stub).generate(text, text)
        assert stub.requests[0]["messages"][0]["content"] == text
        assert stub.requests[0]["messages"][1]["content"] == text


class TestReply:
    def test_returns_the_content_exactly(self, stub):
        stub.respond("  30 days.\n")
        assert client(stub).generate("sys", "q") == "  30 days.\n"

    def test_arabic_reply_is_returned_unchanged(self, stub):
        stub.respond(ARABIC_TRUTH[1])
        assert client(stub).generate("sys", "q") == ARABIC_TRUTH[1]

    def test_http_error_explains_ollamas_message(self, stub):
        stub.status = 404
        stub.reply = json.dumps({"error": "model 'test-model' not found"}).encode("utf-8")
        with pytest.raises(
            GenerationError, match=r"404.*not found.*ollama pull test-model"
        ) as info:
            client(stub).generate("sys", "q")
        assert '"error"' not in str(info.value)  # the message is unwrapped, not raw JSON

    def test_http_error_without_json_still_raises_generation_error(self, stub):
        stub.status = 500
        stub.reply = b"internal failure"
        with pytest.raises(GenerationError, match="500.*internal failure"):
            client(stub).generate("sys", "q")

    @pytest.mark.parametrize(
        "reply",
        [
            b"not json",
            b"{}",
            b'{"message": {}}',
            b'{"message": {"content": null}}',
            b"[]",
            b"\xff\xfe",
        ],
    )
    def test_malformed_reply_raises_generation_error(self, stub, reply):
        stub.reply = reply
        with pytest.raises(GenerationError, match="unexpected reply"):
            client(stub).generate("sys", "q")

    def test_unreachable_server_raises_generation_error(self):
        with socket.socket() as sock:  # a port that was free a moment ago
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        llm = OllamaLLM(base_url=f"http://127.0.0.1:{port}", timeout=0.5)
        with pytest.raises(GenerationError, match="cannot reach Ollama"):
            llm.generate("sys", "q")


class TestValidation:
    @pytest.mark.parametrize("num_ctx", [0, -1, True, 1.5, "8192"])
    def test_rejects_a_bad_context_window(self, num_ctx):
        with pytest.raises(ValueError, match="num_ctx"):
            OllamaLLM(num_ctx=num_ctx)

    @pytest.mark.parametrize("model", ["", "   ", None])
    def test_rejects_a_blank_model(self, model):
        with pytest.raises(ValueError, match="model"):
            OllamaLLM(model)

    def test_rejects_a_non_positive_timeout(self):
        with pytest.raises(ValueError, match="timeout"):
            OllamaLLM(timeout=0)

    def test_rejects_a_blank_prompt_without_calling_the_server(self, stub):
        with pytest.raises(ValueError, match="blank"):
            client(stub).generate("sys", "  \n")
        assert stub.requests == []

    def test_rejects_non_string_arguments(self, stub):
        with pytest.raises(TypeError):
            client(stub).generate(None, "q")
        with pytest.raises(TypeError):
            client(stub).generate("sys", b"q")


def test_satisfies_the_llm_protocol():
    assert isinstance(OllamaLLM(), LLM)
    assert OllamaLLM().model_name == "qwen3:8b"
