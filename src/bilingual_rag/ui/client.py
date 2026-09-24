"""A small client for the HTTP API, used by the Streamlit UI (D-025).

The UI never touches the database or the models: it is a client of the API like any other, so
every permission check happens in the API, where it is tested. This module has no Streamlit
import, so it is tested against the real FastAPI app through FastAPI's test client (an
``httpx.Client``).
"""

import os
from dataclasses import dataclass

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
TIMEOUT_SECONDS = 300.0  # the first question after a restart waits for the LLM to load


class ApiError(Exception):
    """The API refused a request or could not be reached. ``status`` is None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class Me:
    username: str
    role: str
    access_levels: tuple[str, ...]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def api_url() -> str:
    """``RAG_API_URL`` from the environment, or the default local API."""
    return os.environ.get("RAG_API_URL", "").strip() or DEFAULT_API_URL


def make_client() -> "ApiClient":
    """The client the UI uses (tests replace this function)."""
    return ApiClient.for_url(api_url())


class ApiClient:
    """Calls the API; after ``login`` every call sends the bearer token."""

    def __init__(self, http: httpx.Client):
        self._http = http
        self._token: str | None = None

    @classmethod
    def for_url(cls, url: str) -> "ApiClient":
        return cls(httpx.Client(base_url=url, timeout=TIMEOUT_SECONDS))

    @property
    def logged_in(self) -> bool:
        return self._token is not None

    def logout(self) -> None:
        self._token = None

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(f"cannot reach the API: {exc}") from exc
        if response.status_code == 401 and self._token is not None:
            self._token = None  # expired or revoked: the UI must ask for a login again
        if response.is_error:
            try:
                detail = response.json()["detail"]
            except (ValueError, KeyError, TypeError):
                detail = response.text or response.reason_phrase
            if not isinstance(detail, str):  # FastAPI's validation errors are a list
                detail = "; ".join(str(item.get("msg", item)) for item in detail)
            raise ApiError(str(detail), response.status_code)
        return response

    def login(self, username: str, password: str) -> Me:
        """
        Raises:
            ApiError: wrong username or password (status 401), or the API is unreachable.
        """
        self._token = None
        token = self._send(
            "POST", "/auth/token", data={"username": username, "password": password}
        ).json()["access_token"]
        self._token = token
        return self.me()

    def me(self) -> Me:
        body = self._send("GET", "/auth/me").json()
        return Me(body["username"], body["role"], tuple(body["access_levels"]))

    def ask(self, question: str, *, k: int = 5) -> dict:
        return self._send("POST", "/query", json={"question": question, "k": k}).json()

    def documents(self) -> list[dict]:
        return self._send("GET", "/documents").json()

    def upload(self, filename: str, content: bytes, fields: dict[str, str]) -> dict:
        return self._send(
            "POST", "/documents", data=fields, files={"file": (filename, content)}
        ).json()

    def health(self) -> dict:
        # /health answers 503 with a useful body when the database is down; show it, not raise.
        try:
            return self._http.get("/health").json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ApiError(f"cannot reach the API: {exc}") from exc
