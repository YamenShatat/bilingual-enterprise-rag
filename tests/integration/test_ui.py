"""The UI's API client, and the Streamlit page itself, against the real app and database."""

from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from bilingual_rag.database.connection import connect
from bilingual_rag.ui import client as ui_client
from bilingual_rag.ui.client import ApiClient, ApiError
from support.api_app import PASSWORD, TOPIC, ScriptedLLM, app_client, seed

pytestmark = pytest.mark.database

APP = Path(__file__).resolve().parents[2] / "src" / "bilingual_rag" / "ui" / "app.py"


@pytest.fixture
def database(fresh_migrated_database):
    return seed(fresh_migrated_database)


@pytest.fixture
def llm():
    return ScriptedLLM()


@pytest.fixture
def http(database, llm):
    with app_client(database, llm) as test_client:
        yield test_client


class TestApiClient:
    def test_login_returns_who_you_are(self, http):
        me = ApiClient(http).login("employee", PASSWORD)
        assert (me.username, me.role, me.access_levels) == (
            "employee",
            "employee",
            ("employee", "public"),
        )
        assert not me.is_admin

    def test_a_wrong_password_is_a_401_and_leaves_you_logged_out(self, http):
        client = ApiClient(http)
        with pytest.raises(ApiError) as error:
            client.login("employee", "wrong password here")
        assert error.value.status == 401
        assert not client.logged_in

    def test_calls_before_login_are_refused_by_the_api(self, http):
        with pytest.raises(ApiError) as error:
            ApiClient(http).documents()
        assert error.value.status == 401

    def test_ask_and_documents_use_the_users_permissions(self, http, llm):
        client = ApiClient(http)
        client.login("user-hr", PASSWORD)
        assert [d["id"] for d in client.documents()] == ["doc-hr"]
        result = client.ask(TOPIC, k=10)
        assert [c["document_id"] for c in result["citations"]] == ["doc-hr"]
        assert "marker-management" not in llm.prompts[0]

    def test_an_admin_can_upload_and_an_employee_cannot(self, http):
        fields = {
            "id": "ui-upload",
            "title": "UI upload",
            "department": "it",
            "language": "en",
            "access_level": "public",
            "topic": "t",
        }
        employee = ApiClient(http)
        employee.login("employee", PASSWORD)
        with pytest.raises(ApiError, match="only an admin") as error:
            employee.upload("a.md", b"some text for the upload", fields)
        assert error.value.status == 403
        admin = ApiClient(http)
        admin.login("admin", PASSWORD)
        assert admin.upload("a.md", b"some text for the upload", fields)["id"] == "ui-upload"

    def test_a_rejected_token_logs_the_client_out(self, http, database):
        client = ApiClient(http)
        client.login("employee", PASSWORD)
        with connect(database) as conn:
            conn.execute("UPDATE users SET active = false WHERE username = 'employee'")
        with pytest.raises(ApiError):
            client.documents()
        assert not client.logged_in

    def test_validation_errors_become_readable_text(self, http):
        client = ApiClient(http)
        client.login("employee", PASSWORD)
        with pytest.raises(ApiError) as error:
            client.ask("x" * 5000)
        assert error.value.status == 422
        assert "at most 2000 characters" in str(error.value)
        assert "{" not in str(error.value)  # the message, not the raw error structure

    def test_logout_forgets_the_token(self, http):
        client = ApiClient(http)
        client.login("employee", PASSWORD)
        client.logout()
        with pytest.raises(ApiError):
            client.documents()

    def test_an_unreachable_api_is_an_error_without_a_status(self):
        client = ApiClient(httpx.Client(base_url="http://127.0.0.1:9", timeout=0.5))
        with pytest.raises(ApiError, match="cannot reach") as error:
            client.login("employee", PASSWORD)
        assert error.value.status is None

    def test_health_is_available_without_login(self, http):
        assert ApiClient(http).health()["status"] == "ok"

    def test_the_api_url_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("RAG_API_URL", "http://api.internal:9000")
        assert ui_client.api_url() == "http://api.internal:9000"
        monkeypatch.delenv("RAG_API_URL")
        assert ui_client.api_url() == ui_client.DEFAULT_API_URL


@pytest.fixture
def page(http, monkeypatch):
    """The Streamlit page, running headless, talking to the real app."""
    monkeypatch.setattr(ui_client, "make_client", lambda: ApiClient(http))
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.run()
    return app


def log_in(page, username):
    page.text_input[0].input(username)
    page.text_input[1].input(PASSWORD)
    page.button[0].click()
    page.run()


class TestPage:
    def test_the_first_screen_is_the_login_form(self, page):
        assert not page.exception
        assert [t.label for t in page.text_input] == ["Username", "Password"]
        assert page.tabs == []

    def test_a_wrong_password_shows_an_error_and_stays_on_the_login(self, page):
        page.text_input[0].input("employee")
        page.text_input[1].input("not the password")
        page.button[0].click()
        page.run()
        assert [e.value for e in page.error] == ["Wrong username or password."]
        assert page.tabs == []

    def test_an_employee_sees_ask_and_documents_but_no_upload(self, page):
        log_in(page, "employee")
        assert not page.exception
        assert [t.label for t in page.tabs] == ["Ask", "Documents"]
        assert "employee, public" in page.sidebar.caption[0].value

    def test_an_admin_also_sees_upload(self, page):
        log_in(page, "admin")
        assert [t.label for t in page.tabs] == ["Ask", "Documents", "Upload"]

    def test_asking_shows_the_answer_and_its_sources(self, page):
        log_in(page, "user-public")
        page.text_area[0].input(TOPIC)
        page.button(key="FormSubmitter:ask-Ask").click()
        page.run()
        assert not page.exception
        answers = [m.value for m in page.markdown if 'dir="auto"' in m.value]
        assert answers == [
            '<div dir="auto" style="font-size:1.1rem;line-height:1.7">Answer [1].</div>'
        ]
        assert page.dataframe[0].value["ID"].tolist() == ["doc-public"]

    def test_a_refusal_is_explained_in_both_languages(self, page, llm):
        llm.reply = "INSUFFICIENT_EVIDENCE"
        log_in(page, "user-public")
        page.text_area[0].input(TOPIC)
        page.button(key="FormSubmitter:ask-Ask").click()
        page.run()
        (warning,) = page.warning
        assert "do not contain the answer" in warning.value
        assert "لا تتضمن الإجابة" in warning.value

    def test_logging_out_returns_to_the_login_form(self, page):
        log_in(page, "employee")
        page.sidebar.button[0].click()
        page.run()
        assert [t.label for t in page.text_input] == ["Username", "Password"]


def test_ask_sends_k(http, llm):
    client = ApiClient(http)
    client.login("admin", PASSWORD)  # sees all five documents
    client.ask(TOPIC, k=2)
    assert llm.prompts[-1].count(" | page ") == 2


def test_the_next_user_on_the_same_browser_never_sees_the_previous_answer(page):
    log_in(page, "user-hr")
    page.text_area[0].input(TOPIC)
    page.button(key="FormSubmitter:ask-Ask").click()
    page.run()
    assert any('dir="auto"' in m.value for m in page.markdown)
    page.sidebar.button[0].click()
    page.run()
    log_in(page, "user-public")
    assert not any('dir="auto"' in m.value for m in page.markdown)
    assert page.dataframe == [] or "doc-hr" not in str(page.dataframe[0].value)
