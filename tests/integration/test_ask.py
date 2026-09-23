"""ask() on a real database with a scripted LLM: the model sees only what the caller may read."""

import pytest

from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.generation.answer import LOW_SCORE, NO_RESULTS, ask
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from support.samples import make_chunks, make_metadata

pytestmark = pytest.mark.database

QUESTION = "executive bonus pool salary figures"


class RecordingLLM:
    model_name = "recording"

    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, system, prompt):
        self.prompts.append(prompt)
        return "Answer [1]."


@pytest.fixture
def conn(store_connection):
    for level in ACCESS_LEVELS:
        metadata = make_metadata(id=f"doc-{level}", path=f"x/{level}.md", access_level=level)
        chunks = make_chunks(metadata.path, [f"{QUESTION} marker-{level}"])
        store_document(store_connection, metadata, chunks)
    embed_missing(store_connection, HashingEmbedder(64))
    return store_connection


@pytest.mark.parametrize("level", ACCESS_LEVELS)
def test_the_model_sees_and_cites_only_what_the_caller_may_read(conn, level):
    llm = RecordingLLM()
    answer = ask(conn, HashingEmbedder(64), llm, QUESTION, {level}, k=10, min_score=0.0)
    (prompt,) = llm.prompts
    assert f"marker-{level}" in prompt
    for other in set(ACCESS_LEVELS) - {level}:
        assert f"marker-{other}" not in prompt
        assert f"doc-{other}" not in prompt
    assert [c.document_id for c in answer.citations] == [f"doc-{level}"]


def test_the_score_floor_is_passed_on(conn):
    llm = RecordingLLM()
    answer = ask(conn, HashingEmbedder(64), llm, QUESTION, set(ACCESS_LEVELS), min_score=1.01)
    assert answer.refusal == LOW_SCORE
    assert llm.prompts == []


def test_the_character_budget_is_passed_on(conn):
    with pytest.raises(ValueError, match="max_chars"):
        ask(conn, HashingEmbedder(64), RecordingLLM(), QUESTION, set(ACCESS_LEVELS), max_chars=5)


def test_no_permitted_levels_means_no_results_and_no_model_call(conn):
    llm = RecordingLLM()
    answer = ask(conn, HashingEmbedder(64), llm, QUESTION, set())
    assert answer.refusal == NO_RESULTS
    assert llm.prompts == []


def test_a_bare_string_of_levels_is_refused(conn):
    with pytest.raises(TypeError, match="not one string"):
        ask(conn, HashingEmbedder(64), RecordingLLM(), QUESTION, "employee")
