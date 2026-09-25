"""The constraints in the schema, exercised with raw SQL (the repository is tested elsewhere)."""

import psycopg
import pytest

from bilingual_rag.ingestion.manifest import ACCESS_LEVELS, DIGIT_STYLES, FORMATS, LANGUAGES

pytestmark = pytest.mark.database

DOCUMENT = {
    "id": "doc-one",
    "path": "hr/one.md",
    "title": "One",
    "department": "hr",
    "language": "en",
    "format": "md",
    "access_level": "employee",
    "topic": "one",
    "pair": None,
    "digits": None,
    "content_hash": "a" * 64,
}
CHUNK = {
    "id": "doc-one-p1-c0",
    "document_id": "doc-one",
    "page": 1,
    "chunk_index": 0,
    "text": "hello",
    "start_offset": 0,
    "end_offset": 5,
}


def _insert(conn, table, **values):
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values()))


def _rejected(conn, table, **values):
    """True if PostgreSQL refuses the row; a savepoint keeps the transaction usable."""
    try:
        with conn.transaction():
            _insert(conn, table, **values)
    except psycopg.errors.IntegrityError:
        return True
    return False


@pytest.fixture
def conn(store_connection):
    _insert(store_connection, "documents", **DOCUMENT)
    return store_connection


class TestDocuments:
    def test_a_valid_document_is_accepted(self, conn):
        assert conn.execute("SELECT count(*) FROM documents").fetchone() == (1,)

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_every_language_the_code_allows_is_accepted(self, conn, language):
        assert not _rejected(
            conn, "documents", **{**DOCUMENT, "id": "x", "path": "x", "language": language}
        )

    @pytest.mark.parametrize("format_", FORMATS)
    def test_every_format_the_code_allows_is_accepted(self, conn, format_):
        assert not _rejected(
            conn, "documents", **{**DOCUMENT, "id": "x", "path": "x", "format": format_}
        )

    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_every_access_level_the_code_allows_is_accepted(self, conn, level):
        assert not _rejected(
            conn, "documents", **{**DOCUMENT, "id": "x", "path": "x", "access_level": level}
        )

    @pytest.mark.parametrize("digits", DIGIT_STYLES)
    def test_every_digit_style_the_code_allows_is_accepted(self, conn, digits):
        assert not _rejected(
            conn, "documents", **{**DOCUMENT, "id": "x", "path": "x", "digits": digits}
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("language", "fr"),
            ("language", "EN"),
            ("format", "doc"),
            ("access_level", "Employee"),
            ("access_level", "secret"),
            ("access_level", ""),
            ("digits", "roman"),
            ("id", "Has Space"),
            ("id", "under_score"),
            ("id", "-lead"),
            ("id", ""),
            ("path", "  "),
            ("title", ""),
            ("department", " "),
            ("topic", ""),
            ("content_hash", "abc"),
            ("content_hash", "A" * 64),
            ("content_hash", "g" * 64),
        ],
    )
    def test_invalid_values_are_rejected(self, conn, field, value):
        row = {**DOCUMENT, "id": "x", "path": "x", field: value}
        assert _rejected(conn, "documents", **row)

    @pytest.mark.parametrize(
        "field",
        ["title", "department", "language", "format", "access_level", "topic", "content_hash"],
    )
    def test_required_columns_cannot_be_null(self, conn, field):
        row = {**DOCUMENT, "id": "x", "path": "x", field: None}
        assert _rejected(conn, "documents", **row)

    def test_pair_and_digits_may_be_null(self, conn):
        assert not _rejected(
            conn, "documents", **{**DOCUMENT, "id": "y", "path": "y", "pair": None, "digits": None}
        )

    def test_ids_and_paths_are_unique(self, conn):
        assert _rejected(conn, "documents", **{**DOCUMENT, "path": "other"})
        assert _rejected(conn, "documents", **{**DOCUMENT, "id": "other"})

    def test_the_sql_and_the_python_value_sets_are_the_same(self, conn):
        """Anything the SQL accepts but Python does not (or the reverse) would be drift."""
        candidates = ("public", "employee", "engineering", "hr", "management", "admin", "secret")
        accepted = set()
        for number, level in enumerate(candidates):
            # a fresh id and path each time, so only the access level can cause a rejection
            row = {**DOCUMENT, "id": f"p{number}", "path": f"p{number}", "access_level": level}
            if not _rejected(conn, "documents", **row):
                accepted.add(level)
        assert accepted == set(ACCESS_LEVELS)


class TestChunks:
    def test_a_valid_chunk_is_accepted(self, conn):
        assert not _rejected(conn, "chunks", **CHUNK)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("id", "doc-one-p1-c9"),
            ("id", "wrong"),
            ("page", 0),
            ("chunk_index", -1),
            ("text", ""),
            ("start_offset", -1),
            ("end_offset", 6),
            ("end_offset", 4),
            ("document_id", "missing-doc"),
        ],
    )
    def test_invalid_chunks_are_rejected(self, conn, field, value):
        assert _rejected(conn, "chunks", **{**CHUNK, field: value})

    def test_an_empty_chunk_is_rejected_even_when_its_offsets_are_consistent(self, conn):
        # start == end and text == "" satisfies the span rule, so only the non-empty rule stops it
        assert _rejected(
            conn, "chunks", **{**CHUNK, "text": "", "start_offset": 3, "end_offset": 3}
        )

    def test_the_id_must_be_derived_from_document_page_and_index(self, conn):
        row = {**CHUNK, "page": 2, "id": "doc-one-p2-c0", "chunk_index": 0}
        assert not _rejected(conn, "chunks", **row)
        assert _rejected(conn, "chunks", **{**CHUNK, "page": 3})  # id still says page 1

    def test_the_span_must_match_the_length_of_arabic_text_in_characters(self, conn):
        text = "سياسة الإجازة"  # 13 characters, more bytes
        assert len(text) == 13 and len(text.encode("utf-8")) > 13
        ok = {**CHUNK, "text": text, "end_offset": 13}
        assert not _rejected(conn, "chunks", **ok)

    def test_span_length_counts_characters_not_bytes(self, conn):
        text = "سياسة الإجازة"
        assert _rejected(
            conn, "chunks", **{**CHUNK, "text": text, "end_offset": len(text.encode("utf-8"))}
        )

    def test_a_page_and_index_can_be_used_once_per_document(self, conn):
        _insert(conn, "chunks", **CHUNK)
        assert _rejected(conn, "chunks", **CHUNK)

    def test_deleting_a_document_deletes_its_chunks(self, conn):
        _insert(conn, "chunks", **CHUNK)
        conn.execute("DELETE FROM documents WHERE id = 'doc-one'")
        assert conn.execute("SELECT count(*) FROM chunks").fetchone() == (0,)


class TestEmbeddingModels:
    ROW = {
        "key": "bge_m3",
        "model_name": "BAAI/bge-m3",
        "dimension": 1024,
        "table_name": "embeddings_bge_m3",
    }

    def test_a_valid_model_is_accepted(self, conn):
        assert not _rejected(conn, "embedding_models", **self.ROW)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("key", "Bge"),
            ("key", "1bge"),
            ("key", "bge-m3"),
            ("key", "a" * 41),
            ("key", ""),
            ("model_name", " "),
            ("dimension", 0),
            ("dimension", 16001),
            ("table_name", "embeddings_other"),
            ("table_name", "chunks"),
        ],
    )
    def test_invalid_models_are_rejected(self, conn, field, value):
        assert _rejected(conn, "embedding_models", **{**self.ROW, field: value})

    def test_the_dimension_limits_are_accepted(self, conn):
        assert not _rejected(
            conn,
            "embedding_models",
            **{**self.ROW, "key": "small", "table_name": "embeddings_small", "dimension": 1},
        )
        assert not _rejected(
            conn,
            "embedding_models",
            **{**self.ROW, "key": "big", "table_name": "embeddings_big", "dimension": 16000},
        )
