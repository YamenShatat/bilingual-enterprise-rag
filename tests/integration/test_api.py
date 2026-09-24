"""The HTTP API against a real database, with the hashing embedder and a scripted LLM."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from bilingual_rag.api import app as app_module
from bilingual_rag.api.app import Services, create_app
from bilingual_rag.config import ApiSettings
from bilingual_rag.database.connection import connect
from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.generation.llm import GenerationError
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from support.arabic import ARABIC_TRUTH
from support.docx_builder import docx_bytes, text_para
from support.samples import make_chunks, make_metadata

pytestmark = pytest.mark.database

TOPIC = "executive bonus pool salary figures"
ADMIN_KEY = "correct-horse-battery-staple"
EMBEDDER = HashingEmbedder(64)


class ScriptedLLM:
    model_name = "scripted"

    def __init__(self, reply="Answer [1]."):
        self.reply = reply
        self.prompts: list[str] = []
        self.down = False

    def generate(self, system, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def check(self):
        if self.down:
            raise GenerationError("cannot reach Ollama (test)")


@pytest.fixture
def database(fresh_migrated_database):
    """One committed document per access level, each carrying a marker word."""
    with connect(fresh_migrated_database) as conn:
        for level in ACCESS_LEVELS:
            metadata = make_metadata(
                id=f"doc-{level}", path=f"x/{level}.md", title=f"Plan {level}", access_level=level
            )
            store_document(conn, metadata, make_chunks(metadata.path, [f"{TOPIC} marker-{level}"]))
        embed_missing(conn, EMBEDDER)
    return fresh_migrated_database


@pytest.fixture
def llm():
    return ScriptedLLM()


def make_client(database, llm, *, levels=("public",), admin_key=ADMIN_KEY, reranker=None):
    services = Services(
        database=database,
        api=ApiSettings(access_levels=frozenset(levels), admin_api_key=admin_key),
        embedder=EMBEDDER,
        llm=llm,
        reranker=reranker,
    )
    return TestClient(create_app(lambda: services))


@pytest.fixture
def client(database, llm):
    with make_client(database, llm) as test_client:
        yield test_client


class TestHealth:
    def test_everything_up(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"]["ok"] is True
        assert body["llm"] == {"ok": True, "model": "scripted", "detail": None}
        assert body["reranker"] is None

    def test_llm_down_is_degraded_but_still_200(self, client, llm):
        llm.down = True
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert "cannot reach Ollama" in response.json()["llm"]["detail"]

    def test_database_down_is_503(self, database, llm):
        unreachable = replace(database, port=1)
        with make_client(unreachable, llm) as test_client:
            response = test_client.get("/health")
        assert response.status_code == 503
        assert response.json()["database"] == {"ok": False, "detail": "unreachable"}

    def test_health_never_reveals_the_database_password(self, client, database):
        assert database.password not in client.get("/health").text


class TestQuery:
    def test_an_answer_with_citations(self, client):
        response = client.post("/query", json={"question": TOPIC})
        assert response.status_code == 200
        assert response.json() == {
            "answer": "Answer [1].",
            "refusal": None,
            "citations": [
                {"number": 1, "document_id": "doc-public", "title": "Plan public", "page": 1}
            ],
            "top_score": response.json()["top_score"],
        }

    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_the_server_setting_decides_what_the_model_sees(self, database, level):
        llm = ScriptedLLM()
        with make_client(database, llm, levels=(level,)) as test_client:
            response = test_client.post("/query", json={"question": TOPIC, "k": 10})
        (prompt,) = llm.prompts
        assert f"marker-{level}" in prompt
        for other in set(ACCESS_LEVELS) - {level}:
            assert f"marker-{other}" not in prompt
        assert [c["document_id"] for c in response.json()["citations"]] == [f"doc-{level}"]

    def test_a_request_cannot_choose_its_own_access_levels(self, client, llm):
        response = client.post(
            "/query", json={"question": TOPIC, "access_levels": list(ACCESS_LEVELS)}
        )
        assert response.status_code == 422
        assert llm.prompts == []

    def test_a_refusal_has_no_answer_text(self, database):
        llm = ScriptedLLM("INSUFFICIENT_EVIDENCE")
        with make_client(database, llm) as test_client:
            body = test_client.post("/query", json={"question": TOPIC}).json()
        assert body["answer"] is None
        assert body["refusal"] == "model_refused"
        assert body["citations"] == []

    def test_an_uncited_reply_is_not_shown_as_an_answer(self, database):
        llm = ScriptedLLM("Unlimited leave for everyone.")
        with make_client(database, llm) as test_client:
            body = test_client.post("/query", json={"question": TOPIC}).json()
        assert body == {**body, "answer": None, "refusal": "uncited", "citations": []}

    def test_an_arabic_question_and_answer_round_trip_as_utf8(self, database):
        # The hashing embedder is lexical: the question shares the seed text's English words so
        # it is retrieved, and carries Arabic so the round trip is tested in both directions.
        question = f"{TOPIC} {ARABIC_TRUTH[0]}"
        llm = ScriptedLLM(f"{ARABIC_TRUTH[1]} [1]")
        with make_client(database, llm) as test_client:
            response = test_client.post("/query", json={"question": question})
        assert response.headers["content-type"].startswith("application/json")
        assert f"Question: {question}\n" in llm.prompts[0]
        assert response.json()["answer"] == f"{ARABIC_TRUTH[1]} [1]"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"question": ""},
            {"question": "x" * (app_module.MAX_QUESTION_CHARS + 1)},
            {"question": TOPIC, "k": 0},
            {"question": TOPIC, "k": 21},
            {"question": 42},
        ],
    )
    def test_invalid_requests_are_422_and_never_reach_the_model(self, client, llm, body):
        assert client.post("/query", json=body).status_code == 422
        assert llm.prompts == []

    def test_a_whitespace_question_is_422(self, client, llm):
        assert client.post("/query", json={"question": "   "}).status_code == 422
        assert llm.prompts == []

    def test_an_llm_failure_is_503_not_a_refusal(self, database):
        llm = ScriptedLLM(GenerationError("cannot reach Ollama"))
        with make_client(database, llm) as test_client:
            response = test_client.post("/query", json={"question": TOPIC})
        assert response.status_code == 503
        assert "language model is unavailable" in response.json()["detail"]

    def test_nothing_embedded_yet_is_503(self, fresh_migrated_database, llm):
        with make_client(fresh_migrated_database, llm) as test_client:
            response = test_client.post("/query", json={"question": TOPIC})
        assert response.status_code == 503
        assert response.json()["detail"] == "no documents have been embedded yet"


class TestListDocuments:
    def test_lists_only_the_permitted_levels_without_paths(self, database, llm):
        with make_client(database, llm, levels=("public", "employee")) as test_client:
            body = test_client.get("/documents").json()
        assert [d["id"] for d in body] == ["doc-employee", "doc-public"]
        assert set(body[0]) == {
            "id",
            "title",
            "department",
            "language",
            "format",
            "access_level",
            "topic",
        }
        assert "x/" not in str(body)  # no storage paths

    def test_restricted_titles_are_not_listed(self, client):
        text = client.get("/documents").text
        for hidden in ("Plan hr", "Plan management", "Plan engineering", "Plan employee"):
            assert hidden not in text


def upload(test_client, *, key=ADMIN_KEY, filename="policy.md", data=None, **fields):
    form = {
        "id": "uploaded-policy-ar",
        "title": ARABIC_TRUTH[0],
        "department": "hr",
        "language": "ar",
        "access_level": "public",
        "topic": "leave",
        "digits": "arabic-indic",
    } | fields
    content = data if data is not None else "\n".join(ARABIC_TRUTH).encode("utf-8")
    headers = {} if key is None else {"X-Admin-Key": key}
    return test_client.post(
        "/documents", data=form, files={"file": (filename, content)}, headers=headers
    )


class TestUpload:
    def test_an_arabic_markdown_upload_is_stored_embedded_listed_and_answerable(self, client, llm):
        response = upload(client)
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "uploaded-policy-ar"
        assert body["chunks"] >= 1
        assert body["embedded"] == body["chunks"]
        assert "uploaded-policy-ar" in [d["id"] for d in client.get("/documents").json()]
        # The hashing embedder is lexical, so ask with the uploaded text itself: one sentence of a
        # five-sentence chunk scores under the 0.50 floor (0.46 measured), a refusal by design.
        question = "\n".join(ARABIC_TRUTH)
        answer = client.post("/query", json={"question": question, "k": 10}).json()
        assert answer["citations"][0]["document_id"] == "uploaded-policy-ar", answer
        assert ARABIC_TRUTH[1] in llm.prompts[-1]  # the uploaded text reached the evidence

    def test_every_metadata_field_is_stored(self, client, database):
        assert upload(client).status_code == 201
        with connect(database) as conn:
            row = conn.execute(
                "SELECT title, department, language, format, access_level, topic, digits"
                " FROM documents WHERE id = 'uploaded-policy-ar'"
            ).fetchone()
        assert row == (ARABIC_TRUTH[0], "hr", "ar", "md", "public", "leave", "arabic-indic")

    def test_a_docx_upload_works(self, client):
        data = docx_bytes(text_para(ARABIC_TRUTH[1]))
        response = upload(client, filename="policy.DOCX", data=data, id="docx-policy")
        assert response.status_code == 201

    def test_disabled_when_no_admin_key_is_configured(self, database, llm):
        with make_client(database, llm, admin_key=None) as test_client:
            response = upload(test_client)
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"]

    @pytest.mark.parametrize("key", [None, "", "wrong-key-of-enough-length", ADMIN_KEY + "x"])
    def test_a_missing_or_wrong_key_is_401(self, client, key):
        assert upload(client, key=key).status_code == 401

    def test_a_rejected_upload_stores_nothing(self, client):
        upload(client, key="wrong-key-of-enough-length")
        assert "uploaded-policy-ar" not in client.get("/documents").text

    @pytest.mark.parametrize("filename", ["policy.exe", "policy", "policy.md.exe", ".md"])
    def test_an_unsupported_file_type_is_415(self, client, filename):
        assert upload(client, filename=filename).status_code == 415

    def test_a_file_over_the_limit_is_413(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 100)
        assert upload(client, data=b"x" * 101).status_code == 413

    def test_a_file_at_the_limit_is_accepted(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 100)
        assert upload(client, data=b"x" * 100).status_code == 201

    @pytest.mark.parametrize(
        "fields",
        [
            {"access_level": "admin"},
            {"language": "fr"},
            {"id": "Has Spaces"},
            {"id": "../escape"},
            {"title": "  "},
        ],
    )
    def test_invalid_metadata_is_422(self, client, fields):
        assert upload(client, **fields).status_code == 422

    def test_a_duplicate_id_is_409_and_the_original_is_kept(self, client, database):
        assert upload(client).status_code == 201
        second = upload(client, data=b"replacement text")
        assert second.status_code == 409
        with connect(database) as conn:
            texts = conn.execute(
                "SELECT text FROM chunks WHERE document_id = 'uploaded-policy-ar'"
            ).fetchall()
        assert all("replacement" not in t for (t,) in texts)

    def test_an_existing_corpus_document_cannot_be_overwritten(self, client):
        assert upload(client, id="doc-management").status_code == 409

    def test_the_client_filename_never_reaches_storage(self, client, database):
        response = upload(client, filename="../../etc/evil.md", id="safe-name")
        assert response.status_code == 201
        with connect(database) as conn:
            (path,) = conn.execute("SELECT path FROM documents WHERE id = 'safe-name'").fetchone()
        assert path == "uploads/safe-name.md"

    def test_an_empty_file_is_422(self, client):
        assert upload(client, data=b"").status_code == 422

    def test_invalid_utf8_text_is_422(self, client):
        response = upload(client, filename="bad.txt", data=b"\xff\xfe\x00bad")
        assert response.status_code == 422


class RejectAll:
    """A reranker that finds nothing relevant, so the rerank floor refuses every question."""

    model_name = "reject-all"

    def __init__(self):
        self.calls = 0

    def score(self, query, texts):
        self.calls += 1
        return [0.0] * len(texts)


class TestReranking:
    def test_query_uses_the_configured_reranker(self, database):
        reranker, llm = RejectAll(), ScriptedLLM()
        with make_client(database, llm, reranker=reranker) as test_client:
            body = test_client.post("/query", json={"question": TOPIC}).json()
        assert reranker.calls == 1
        assert body["refusal"] == "low_score"
        assert llm.prompts == []

    def test_health_names_the_reranker(self, database, llm):
        with make_client(database, llm, reranker=RejectAll()) as test_client:
            assert test_client.get("/health").json()["reranker"] == "reject-all"
