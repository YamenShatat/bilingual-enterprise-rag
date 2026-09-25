"""The real FastAPI app over a test database: shared by the API tests and the UI tests.

``seed(settings)`` stores one document per access level (each carrying a marker word) and the
users: one employee per level ("user-<level>"), a default employee ("employee": public +
employee) and an admin, all with ``PASSWORD``.
"""

from contextlib import contextmanager

from fastapi.testclient import TestClient

from bilingual_rag.api.app import Services, create_app
from bilingual_rag.auth.users import create_user
from bilingual_rag.config import ApiSettings
from bilingual_rag.database.connection import connect
from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.generation.llm import GenerationError
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from support.samples import make_chunks, make_metadata

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


def seed(settings):
    with connect(settings) as conn:
        for level in ACCESS_LEVELS:
            metadata = make_metadata(
                id=f"doc-{level}", path=f"x/{level}.md", title=f"Plan {level}", access_level=level
            )
            store_document(conn, metadata, make_chunks(metadata.path, [f"{TOPIC} marker-{level}"]))
            create_user(conn, f"user-{level}", PASSWORD, role="employee", access_levels=[level])
        embed_missing(conn, EMBEDDER)
        create_user(conn, "employee", PASSWORD, role="employee")
        create_user(conn, "admin", PASSWORD, role="admin")
    return settings


@contextmanager
def app_client(database, llm, *, reranker=None, secret=SECRET):
    """A started app (lifespan run) with no one logged in."""
    services = Services(
        database=database,
        api=ApiSettings(jwt_secret=secret),
        embedder=EMBEDDER,
        llm=llm,
        reranker=reranker,
    )
    with TestClient(create_app(lambda: services)) as test_client:
        yield test_client
