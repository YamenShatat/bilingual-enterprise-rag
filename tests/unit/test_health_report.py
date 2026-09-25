import pytest

from bilingual_rag.database.health import HealthReport


def _report(**overrides) -> HealthReport:
    fields = {
        "server_version": "17.0",
        "server_encoding": "UTF8",
        "vector_available": "0.8.6",
        "vector_installed": None,
        "cosine_distance": 1.0,
        "vector_error": None,
    }
    return HealthReport(**(fields | overrides))


def test_a_utf8_database_with_a_correct_distance_is_ok():
    assert _report().ok


@pytest.mark.parametrize("encoding", ["SQL_ASCII", "LATIN1", "WIN1252", ""])
def test_a_database_that_is_not_utf8_is_not_ok(encoding):
    assert not _report(server_encoding=encoding).ok


def test_the_encoding_comparison_ignores_case():
    assert _report(server_encoding="utf8").ok


@pytest.mark.parametrize("distance", [None, 0.0, 1.5, 1.4142135623730951, -0.0, 2.0])
def test_a_missing_or_wrong_cosine_distance_is_not_ok(distance):
    assert not _report(cosine_distance=distance).ok


def test_a_pgvector_error_is_not_ok():
    failed = _report(cosine_distance=None, vector_error="permission denied to create extension")
    assert not failed.ok
