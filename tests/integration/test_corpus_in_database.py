"""The real 32-document corpus stored in, and read back from, PostgreSQL."""

from pathlib import Path

import pytest

from bilingual_rag.database.embedding_store import (
    chunk_ids_without_embeddings,
    ensure_embedding_model,
    store_embeddings,
)
from bilingual_rag.database.repository import (
    chunk_id,
    list_chunks,
    list_documents,
    store_document,
)
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS, load_manifest
from bilingual_rag.ingestion.pipeline import ingest_directory

pytestmark = pytest.mark.database

DATA = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="module")
def corpus():
    """(manifest documents, chunks grouped by path) from the files on disk."""
    documents = load_manifest(DATA / "manifest.json")
    result = ingest_directory(DATA / "synthetic")
    assert result.skipped == ()
    by_path: dict[str, list] = {}
    for chunk in result.chunks:
        by_path.setdefault(chunk.filename, []).append(chunk)
    return documents, by_path


@pytest.fixture
def loaded(store_connection, corpus):
    documents, by_path = corpus
    outcomes = [store_document(store_connection, d, by_path[d.path]) for d in documents]
    return store_connection, documents, by_path, outcomes


def test_the_manifest_and_the_ingested_files_describe_the_same_documents(corpus):
    documents, by_path = corpus
    assert len(documents) == 32
    assert {d.path for d in documents} == set(by_path)  # the manifest path joins the chunk filename


def test_every_document_is_stored_the_first_time_and_nothing_is_rewritten_the_second(loaded):
    conn, documents, by_path, outcomes = loaded
    assert outcomes == ["inserted"] * 32
    assert [store_document(conn, d, by_path[d.path]) for d in documents] == ["unchanged"] * 32


def test_the_stored_metadata_equals_the_manifest(loaded):
    conn, documents, _, _ = loaded
    assert list_documents(conn, ACCESS_LEVELS) == sorted(
        documents, key=lambda d: d.path.encode("utf-8")
    )


def test_every_chunk_comes_back_exactly(loaded):
    conn, documents, by_path, _ = loaded
    expected = [
        (chunk_id(d.id, c.page, c.index), c.page, c.index, c.text, c.start, c.end)
        for d in sorted(documents, key=lambda d: d.path.encode("utf-8"))
        for c in sorted(by_path[d.path], key=lambda c: (c.page, c.index))
    ]
    stored = list_chunks(conn, set(ACCESS_LEVELS))
    assert [(c.id, c.page, c.index, c.text, c.start, c.end) for c in stored] == expected


def test_arabic_chunks_are_stored_letter_for_letter(loaded):
    conn, documents, by_path, _ = loaded
    arabic_paths = {d.path for d in documents if d.language == "ar"}
    expected = {c.text for path in arabic_paths for c in by_path[path]}
    stored = {c.text for c in list_chunks(conn, set(ACCESS_LEVELS), language="ar")}
    assert stored == expected
    assert any("الإجازة" in text for text in stored)
    assert any("٢١" in text for text in stored)  # Arabic-Indic digits survive


@pytest.mark.parametrize(
    "levels",
    [
        {"public"},
        {"employee"},
        {"public", "employee"},
        {"hr"},
        {"management"},
        {"engineering"},
        {"hr", "management"},
    ],
)
def test_each_set_of_levels_returns_exactly_the_matching_documents(loaded, levels):
    conn, documents, by_path, _ = loaded
    expected_paths = {d.path for d in documents if d.access_level in levels}
    expected_chunks = sum(len(by_path[path]) for path in expected_paths)
    stored = list_chunks(conn, levels)
    assert {c.document.path for c in stored} == expected_paths
    assert len(stored) == expected_chunks


def test_an_employee_never_receives_restricted_documents(loaded):
    conn, documents, _, _ = loaded
    restricted = {d.id for d in documents if d.access_level in {"hr", "management", "engineering"}}
    assert restricted  # the corpus has them
    seen = {c.document.id for c in list_chunks(conn, {"public", "employee"})}
    assert not seen & restricted


def test_a_restricted_document_is_returned_only_to_its_level(loaded):
    conn, _, _, _ = loaded
    for level in ("hr", "management", "engineering"):
        ids = {c.document.id for c in list_chunks(conn, {level})}
        assert ids, level
        assert {c.document.access_level for c in list_chunks(conn, {level})} == {level}


def test_the_corpus_can_be_embedded_with_a_stand_in_model(loaded):
    conn, _, _, _ = loaded
    ensure_embedding_model(conn, "fake_4", "deterministic test model", 4)
    pending = chunk_ids_without_embeddings(conn, "fake_4")
    assert len(pending) == len(list_chunks(conn, set(ACCESS_LEVELS)))
    store_embeddings(conn, "fake_4", {chunk: [1.0, 0.0, 0.0, 0.0] for chunk in pending})
    assert chunk_ids_without_embeddings(conn, "fake_4") == []
