"""The HTTP API against a real database, with the hashing embedder and a scripted LLM.

Every client logs in as a real user created in the test database (D-024): one employee per
access level ("user-<level>"), a default employee ("employee": public + employee) and an admin.
"""

import time
from contextlib import contextmanager
from dataclasses import replace

import jwt
import pytest
from fastapi.testclient import TestClient

from bilingual_rag.api import app as app_module
from bilingual_rag.api.app import Services, create_app
from bilingual_rag.auth.users import create_user
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
SECRET = "test-signing-secret-of-enough-length-0123"
PASSWORD = "correct horse battery staple"
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
    """One committed document per access level, each carrying a marker word, and the users."""
    with connect(fresh_migrated_database) as conn:
        for level in ACCESS_LEVELS:
            metadata = make_metadata(
                id=f"doc-{level}", path=f"x/{level}.md", title=f"Plan {level}", access_level=level
            )
            store_document(conn, metadata, make_chunks(metadata.path, [f"{TOPIC} marker-{level}"]))
            create_user(conn, f"user-{level}", PASSWORD, role="employee", access_levels=[level])
        embed_missing(conn, EMBEDDER)
        create_user(conn, "employee", PASSWORD, role="employee")
        create_user(conn, "admin", PASSWORD, role="admin")
    return fresh_migrated_database


@pytest.fixture
def llm():
    return ScriptedLLM()


def login(test_client, username, password=PASSWORD):
    return test_client.post("/auth/token", data={"username": username, "password": password})


@contextmanager
def api(database, llm, *, user="user-public", reranker=None, secret=SECRET):
    """A client logged in as ``user`` (None: not logged in)."""
    services = Services(
        database=database,
        api=ApiSettings(jwt_secret=secret),
        embedder=EMBEDDER,
        llm=llm,
        reranker=reranker,
    )
    with TestClient(create_app(lambda: services)) as test_client:
        if user is not None:
            response = login(test_client, user)
            assert response.status_code == 200, response.text
            test_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        yield test_client


@pytest.fixture
def client(database, llm):
    with api(database, llm) as test_client:
        yield test_client


@pytest.fixture
def admin(database, llm):
    with api(database, llm, user="admin") as test_client:
        yield test_client


class TestLogin:
    def test_a_right_password_gets_a_bearer_token(self, database, llm):
        with api(database, llm, user=None) as test_client:
            response = login(test_client, "employee")
        assert response.status_code == 200
        body = response.json()
        assert (body["token_type"], body["expires_in"]) == ("bearer", 3600)
        claims = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"])
        assert set(claims) == {"sub", "iat", "exp"}  # no permissions inside the token

    def test_a_wrong_password_and_an_unknown_user_get_the_same_answer(self, database, llm):
        with api(database, llm, user=None) as test_client:
            wrong = login(test_client, "employee", PASSWORD + "x")
            unknown = login(test_client, "nobody")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json() == {"detail": "wrong username or password"}

    def test_a_deactivated_user_cannot_log_in(self, database, llm):
        with connect(database) as conn:
            conn.execute("UPDATE users SET active = false WHERE username = 'employee'")
        with api(database, llm, user=None) as test_client:
            assert login(test_client, "employee").status_code == 401

    def test_me_describes_the_logged_in_user(self, database, llm):
        with api(database, llm, user="employee") as test_client:
            body = test_client.get("/auth/me").json()
        assert body == {
            "username": "employee",
            "role": "employee",
            "access_levels": ["employee", "public"],
        }

    def test_the_password_never_appears_in_a_response(self, database, llm):
        with api(database, llm, user=None) as test_client:
            response = login(test_client, "employee")
            me = test_client.get(
                "/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"}
            )
        assert PASSWORD not in response.text + me.text


class TestTokensAreRequired:
    @pytest.mark.parametrize(
        "method, path", [("post", "/query"), ("get", "/documents"), ("get", "/auth/me")]
    )
    def test_no_token_is_401(self, database, llm, method, path):
        with api(database, llm, user=None) as test_client:
            response = getattr(test_client, method)(
                path, **({"json": {"question": TOPIC}} if method == "post" else {})
            )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    def test_uploading_without_a_token_is_401(self, database, llm):
        with api(database, llm, user=None) as test_client:
            assert upload(test_client).status_code == 401

    @pytest.mark.parametrize("header", ["Bearer nonsense", "Bearer ", "Basic abc", "nonsense"])
    def test_a_bad_token_is_401(self, database, llm, header):
        with api(database, llm, user=None) as test_client:
            response = test_client.get("/documents", headers={"Authorization": header})
        assert response.status_code == 401

    def test_a_token_signed_with_another_secret_is_401(self, database, llm):
        with api(database, llm, secret="another-signing-secret-of-enough-length") as other:
            forged = other.headers["Authorization"]
        with api(database, llm, user=None) as test_client:
            response = test_client.get("/documents", headers={"Authorization": forged})
        assert response.status_code == 401

    def test_an_expired_token_is_401(self, database, llm):
        now = int(time.time())
        expired = jwt.encode({"sub": "1", "iat": now - 120, "exp": now - 60}, SECRET, "HS256")
        with api(database, llm, user=None) as test_client:
            response = test_client.get("/documents", headers={"Authorization": f"Bearer {expired}"})
        assert response.status_code == 401

    def test_a_valid_token_for_a_user_who_no_longer_exists_is_401(self, database, llm):
        with api(database, llm, user="employee") as test_client:
            with connect(database) as conn:
                conn.execute("DELETE FROM users WHERE username = 'employee'")
            assert test_client.get("/documents").status_code == 401

    def test_deactivating_a_user_stops_their_token_at_once(self, database, llm):
        with api(database, llm, user="employee") as test_client:
            assert test_client.get("/documents").status_code == 200
            with connect(database) as conn:
                conn.execute("UPDATE users SET active = false WHERE username = 'employee'")
            assert test_client.get("/documents").status_code == 401

    def test_a_permission_change_applies_to_the_next_request(self, database, llm):
        with api(database, llm, user="user-public") as test_client:
            assert [d["id"] for d in test_client.get("/documents").json()] == ["doc-public"]
            with connect(database) as conn:
                conn.execute(
                    "UPDATE users SET access_levels = ARRAY['public', 'hr']"
                    " WHERE username = 'user-public'"
                )
            assert [d["id"] for d in test_client.get("/documents").json()] == [
                "doc-hr",
                "doc-public",
            ]


class TestHealth:
    def test_everything_up_without_logging_in(self, database, llm):
        with api(database, llm, user=None) as test_client:
            response = test_client.get("/health")
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
        with api(unreachable, llm, user=None) as test_client:
            response = test_client.get("/health")
        assert response.status_code == 503
        assert response.json()["database"] == {"ok": False, "detail": "unreachable"}

    def test_health_never_reveals_the_database_password_or_the_secret(self, client, database):
        text = client.get("/health").text
        assert database.password not in text
        assert SECRET not in text


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
    def test_the_users_levels_decide_what_the_model_sees(self, database, level):
        llm = ScriptedLLM()
        with api(database, llm, user=f"user-{level}") as test_client:
            response = test_client.post("/query", json={"question": TOPIC, "k": 10})
        (prompt,) = llm.prompts
        assert f"marker-{level}" in prompt
        for other in set(ACCESS_LEVELS) - {level}:
            assert f"marker-{other}" not in prompt
        assert [c["document_id"] for c in response.json()["citations"]] == [f"doc-{level}"]

    def test_an_admin_sees_every_level(self, database):
        llm = ScriptedLLM()
        with api(database, llm, user="admin") as test_client:
            test_client.post("/query", json={"question": TOPIC, "k": 10})
        for level in ACCESS_LEVELS:
            assert f"marker-{level}" in llm.prompts[0]

    def test_a_request_cannot_choose_its_own_access_levels(self, client, llm):
        response = client.post(
            "/query", json={"question": TOPIC, "access_levels": list(ACCESS_LEVELS)}
        )
        assert response.status_code == 422
        assert llm.prompts == []

    def test_a_refusal_has_no_answer_text(self, database):
        llm = ScriptedLLM("INSUFFICIENT_EVIDENCE")
        with api(database, llm) as test_client:
            body = test_client.post("/query", json={"question": TOPIC}).json()
        assert body["answer"] is None
        assert body["refusal"] == "model_refused"
        assert body["citations"] == []

    def test_an_uncited_reply_is_not_shown_as_an_answer(self, database):
        llm = ScriptedLLM("Unlimited leave for everyone.")
        with api(database, llm) as test_client:
            body = test_client.post("/query", json={"question": TOPIC}).json()
        assert body == {**body, "answer": None, "refusal": "uncited", "citations": []}

    def test_an_arabic_question_and_answer_round_trip_as_utf8(self, database):
        # The hashing embedder is lexical: the question shares the seed text's English words so
        # it is retrieved, and carries Arabic so the round trip is tested in both directions.
        question = f"{TOPIC} {ARABIC_TRUTH[0]}"
        llm = ScriptedLLM(f"{ARABIC_TRUTH[1]} [1]")
        with api(database, llm) as test_client:
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
        with api(database, llm) as test_client:
            response = test_client.post("/query", json={"question": TOPIC})
        assert response.status_code == 503
        assert "language model is unavailable" in response.json()["detail"]

    def test_nothing_embedded_yet_is_503(self, fresh_migrated_database, llm):
        with connect(fresh_migrated_database) as conn:
            create_user(conn, "employee", PASSWORD, role="employee")
        with api(fresh_migrated_database, llm, user="employee") as test_client:
            response = test_client.post("/query", json={"question": TOPIC})
        assert response.status_code == 503
        assert response.json()["detail"] == "no documents have been embedded yet"


class TestListDocuments:
    def test_lists_only_the_users_levels_without_paths(self, database, llm):
        with api(database, llm, user="employee") as test_client:
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


def upload(test_client, *, filename="policy.md", data=None, **fields):
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
    return test_client.post("/documents", data=form, files={"file": (filename, content)})


class TestUploadPermissions:
    @pytest.mark.parametrize("user", ["employee", "user-management", "user-hr"])
    def test_an_employee_cannot_upload_whatever_their_levels(self, database, llm, user):
        with api(database, llm, user=user) as test_client:
            response = upload(test_client)
        assert response.status_code == 403
        assert response.json()["detail"] == "only an admin may do this"

    def test_a_refused_upload_stores_nothing(self, database, llm):
        with api(database, llm, user="employee") as test_client:
            upload(test_client)
        with connect(database) as conn:
            count = conn.execute(
                "SELECT count(*) FROM documents WHERE id = 'uploaded-policy-ar'"
            ).fetchone()[0]
        assert count == 0


class TestUpload:
    def test_an_arabic_markdown_upload_is_stored_embedded_listed_and_answerable(self, admin, llm):
        response = upload(admin)
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "uploaded-policy-ar"
        assert body["chunks"] >= 1
        assert body["embedded"] == body["chunks"]
        assert "uploaded-policy-ar" in [d["id"] for d in admin.get("/documents").json()]
        # The hashing embedder is lexical, so ask with the uploaded text itself: one sentence of a
        # five-sentence chunk scores under the 0.50 floor (0.46 measured), a refusal by design.
        question = "\n".join(ARABIC_TRUTH)
        answer = admin.post("/query", json={"question": question, "k": 10}).json()
        assert answer["citations"][0]["document_id"] == "uploaded-policy-ar", answer
        assert ARABIC_TRUTH[1] in llm.prompts[-1]  # the uploaded text reached the evidence

    def test_an_upload_is_visible_only_to_users_with_its_level(self, database, llm):
        with api(database, llm, user="admin") as admin_client:
            upload(admin_client, id="hr-only-doc", access_level="hr")
        with api(database, llm, user="user-hr") as hr, api(database, llm) as public:
            assert "hr-only-doc" in hr.get("/documents").text
            assert "hr-only-doc" not in public.get("/documents").text

    def test_every_metadata_field_is_stored(self, admin, database):
        assert upload(admin).status_code == 201
        with connect(database) as conn:
            row = conn.execute(
                "SELECT title, department, language, format, access_level, topic, digits"
                " FROM documents WHERE id = 'uploaded-policy-ar'"
            ).fetchone()
        assert row == (ARABIC_TRUTH[0], "hr", "ar", "md", "public", "leave", "arabic-indic")

    def test_a_docx_upload_works(self, admin):
        data = docx_bytes(text_para(ARABIC_TRUTH[1]))
        response = upload(admin, filename="policy.DOCX", data=data, id="docx-policy")
        assert response.status_code == 201

    @pytest.mark.parametrize("filename", ["policy.exe", "policy", "policy.md.exe", ".md"])
    def test_an_unsupported_file_type_is_415(self, admin, filename):
        assert upload(admin, filename=filename).status_code == 415

    def test_a_file_over_the_limit_is_413(self, admin, monkeypatch):
        monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 100)
        assert upload(admin, data=b"x" * 101).status_code == 413

    def test_a_file_at_the_limit_is_accepted(self, admin, monkeypatch):
        monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 100)
        assert upload(admin, data=b"x" * 100).status_code == 201

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
    def test_invalid_metadata_is_422(self, admin, fields):
        assert upload(admin, **fields).status_code == 422

    def test_a_duplicate_id_is_409_and_the_original_is_kept(self, admin, database):
        assert upload(admin).status_code == 201
        second = upload(admin, data=b"replacement text")
        assert second.status_code == 409
        with connect(database) as conn:
            texts = conn.execute(
                "SELECT text FROM chunks WHERE document_id = 'uploaded-policy-ar'"
            ).fetchall()
        assert all("replacement" not in t for (t,) in texts)

    def test_an_existing_corpus_document_cannot_be_overwritten(self, admin):
        assert upload(admin, id="doc-management").status_code == 409

    def test_the_client_filename_never_reaches_storage(self, admin, database):
        response = upload(admin, filename="../../etc/evil.md", id="safe-name")
        assert response.status_code == 201
        with connect(database) as conn:
            (path,) = conn.execute("SELECT path FROM documents WHERE id = 'safe-name'").fetchone()
        assert path == "uploads/safe-name.md"

    def test_an_empty_file_is_422(self, admin):
        assert upload(admin, data=b"").status_code == 422

    def test_invalid_utf8_text_is_422(self, admin):
        response = upload(admin, filename="bad.txt", data=b"\xff\xfe\x00bad")
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
        with api(database, llm, reranker=reranker) as test_client:
            body = test_client.post("/query", json={"question": TOPIC}).json()
        assert reranker.calls == 1
        assert body["refusal"] == "low_score"
        assert llm.prompts == []

    def test_health_names_the_reranker(self, database, llm):
        with api(database, llm, user=None, reranker=RejectAll()) as test_client:
            assert test_client.get("/health").json()["reranker"] == "reject-all"
