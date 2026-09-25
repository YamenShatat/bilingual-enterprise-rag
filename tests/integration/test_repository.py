from dataclasses import replace

import psycopg
import pytest

from bilingual_rag.database.repository import (
    chunk_id,
    chunks_hash,
    delete_document,
    get_document,
    list_chunks,
    list_documents,
    store_document,
)
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from bilingual_rag.ingestion.models import Chunk
from support.samples import make_chunks, make_metadata, with_text

pytestmark = pytest.mark.database

EVERYONE = set(ACCESS_LEVELS)


def _ctids(conn, table, where="true"):
    rows = conn.execute(f"SELECT ctid::text FROM {table} WHERE {where} ORDER BY 1").fetchall()
    return [row[0] for row in rows]


def _put(conn, metadata=None, texts=("alpha", "beta")):
    metadata = metadata or make_metadata()
    chunks = make_chunks(metadata.path, list(texts))
    return metadata, chunks, store_document(conn, metadata, chunks)


class TestChunkIdentity:
    def test_the_id_is_document_page_and_index(self):
        assert chunk_id("hr-annual-leave-policy-ar", 2, 5) == "hr-annual-leave-policy-ar-p2-c5"

    def test_the_hash_ignores_input_order(self):
        chunks = make_chunks("a.md", ["x", "y", "z"])
        assert chunks_hash(chunks) == chunks_hash(reversed(chunks))

    def test_the_hash_is_a_sha256_hex_digest(self):
        digest = chunks_hash(make_chunks("a.md", ["x"]))
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")

    @pytest.mark.parametrize(
        "change",
        [
            lambda c: replace(c, text="X", end=c.start + 1),
            lambda c: replace(c, start=c.start + 1, end=c.end + 1),
            lambda c: replace(c, page=2),
            lambda c: replace(c, index=1),
        ],
    )
    def test_the_hash_changes_when_any_part_of_a_chunk_changes(self, change):
        (original,) = make_chunks("a.md", ["x"])
        assert chunks_hash([change(original)]) != chunks_hash([original])

    def test_the_hash_ignores_the_filename(self):
        (chunk,) = make_chunks("a.md", ["x"])
        assert chunks_hash([chunk]) == chunks_hash([replace(chunk, filename="b.md")])

    def test_the_hash_is_stable_for_arabic(self):
        chunks = make_chunks("a.md", ["سياسة الإجازة السنوية"])
        assert chunks_hash(chunks) == chunks_hash(list(chunks))


class TestStoreDocument:
    def test_a_new_document_is_inserted_and_read_back(self, store_connection):
        metadata, chunks, outcome = _put(store_connection)
        assert outcome == "inserted"
        assert get_document(store_connection, metadata.id) == metadata
        stored = list_chunks(store_connection, EVERYONE)
        assert [(c.id, c.page, c.index, c.text, c.start, c.end) for c in stored] == [
            (chunk_id(metadata.id, c.page, c.index), c.page, c.index, c.text, c.start, c.end)
            for c in chunks
        ]
        assert all(c.document == metadata for c in stored)

    def test_storing_the_same_thing_again_writes_nothing(self, store_connection):
        metadata, chunks, _ = _put(store_connection)
        docs_before = _ctids(store_connection, "documents")
        chunks_before = _ctids(store_connection, "chunks")
        assert store_document(store_connection, metadata, chunks) == "unchanged"
        assert _ctids(store_connection, "documents") == docs_before
        assert _ctids(store_connection, "chunks") == chunks_before

    def test_updated_at_moves_only_when_something_changed(self, store_connection):
        metadata, chunks, _ = _put(store_connection)
        # now() is constant inside one transaction, so backdate the row to make a change visible
        store_connection.execute("UPDATE documents SET updated_at = now() - interval '1 day'")

        def updated_at():
            return store_connection.execute("SELECT updated_at FROM documents").fetchone()[0]

        backdated = updated_at()
        store_document(store_connection, metadata, chunks)  # nothing changed
        assert updated_at() == backdated
        store_document(store_connection, replace(metadata, title="Renamed"), chunks)
        assert updated_at() > backdated

    def test_input_order_does_not_matter(self, store_connection):
        metadata, chunks, _ = _put(store_connection)
        assert store_document(store_connection, metadata, list(reversed(chunks))) == "unchanged"

    def test_a_metadata_change_updates_the_row_but_leaves_the_chunks_alone(self, store_connection):
        metadata, chunks, _ = _put(store_connection)
        chunks_before = _ctids(store_connection, "chunks")
        docs_before = _ctids(store_connection, "documents")
        moved = replace(metadata, title="Renamed", access_level="hr")
        assert store_document(store_connection, moved, chunks) == "updated"
        assert get_document(store_connection, metadata.id) == moved
        assert _ctids(store_connection, "chunks") == chunks_before  # not rewritten
        assert _ctids(store_connection, "documents") != docs_before  # the row was rewritten

    def test_a_changed_access_level_takes_effect_immediately(self, store_connection):
        metadata, chunks, _ = _put(store_connection)
        assert len(list_chunks(store_connection, {"employee"})) == 2
        store_document(store_connection, replace(metadata, access_level="management"), chunks)
        assert list_chunks(store_connection, {"employee"}) == []
        assert len(list_chunks(store_connection, {"management"})) == 2

    def test_changed_text_replaces_the_chunks(self, store_connection):
        metadata, chunks, _ = _put(store_connection)
        changed = [chunks[0], with_text(chunks[1], "gamma")]
        assert store_document(store_connection, metadata, changed) == "updated"
        texts = [c.text for c in list_chunks(store_connection, EVERYONE)]
        assert texts == ["alpha", "gamma"]

    def test_fewer_chunks_removes_the_extra_ones(self, store_connection):
        metadata, chunks, _ = _put(store_connection, texts=("a", "b", "c"))
        assert store_document(store_connection, metadata, chunks[:1]) == "updated"
        assert [c.id for c in list_chunks(store_connection, EVERYONE)] == [
            chunk_id(metadata.id, 1, 0)
        ]

    def test_a_different_chunking_of_the_same_text_is_an_update(self, store_connection):
        metadata, _, _ = _put(store_connection, texts=("alphabeta",))
        rechunked = make_chunks(metadata.path, ["alpha", "beta"])
        assert store_document(store_connection, metadata, rechunked) == "updated"
        assert len(list_chunks(store_connection, EVERYONE)) == 2

    def test_a_document_with_several_pages_keeps_them_apart(self, store_connection):
        metadata = make_metadata()
        chunks = make_chunks(metadata.path, ["p1 text"], page=1)
        chunks += make_chunks(metadata.path, ["p2 first", "p2 second"], page=2)
        store_document(store_connection, metadata, chunks)
        stored = list_chunks(store_connection, EVERYONE)
        assert [(c.page, c.index, c.text) for c in stored] == [
            (1, 0, "p1 text"),
            (2, 0, "p2 first"),
            (2, 1, "p2 second"),
        ]

    def test_no_chunks_is_an_error(self, store_connection):
        with pytest.raises(ValueError, match="at least one chunk"):
            store_document(store_connection, make_metadata(), [])

    def test_a_chunk_from_another_file_is_an_error(self, store_connection):
        metadata = make_metadata()
        chunks = make_chunks("hr/other.md", ["x"])
        with pytest.raises(ValueError, match="does not match the document path"):
            store_document(store_connection, metadata, chunks)
        assert list_documents(store_connection, EVERYONE) == []

    def test_a_path_used_by_another_document_is_rejected(self, store_connection):
        _put(store_connection)
        clash = make_metadata(id="other-doc")  # same path as the first
        with pytest.raises(psycopg.errors.UniqueViolation):
            store_document(store_connection, clash, make_chunks(clash.path, ["x"]))

    def test_duplicate_page_and_index_in_the_input_are_rejected(self, store_connection):
        metadata = make_metadata()
        chunk = Chunk(metadata.path, 1, 0, "x", 0, 1)
        with pytest.raises(psycopg.errors.IntegrityError):
            store_document(store_connection, metadata, [chunk, chunk])

    def test_a_chunk_with_inconsistent_offsets_is_rejected(self, store_connection):
        metadata = make_metadata()
        bad = Chunk(metadata.path, 1, 0, "abc", 0, 10)
        with pytest.raises(psycopg.errors.CheckViolation):
            store_document(store_connection, metadata, [bad])

    def test_a_nul_character_is_refused_by_postgres(self, store_connection):
        metadata = make_metadata()
        chunks = make_chunks(metadata.path, ["bad\x00text"])
        with pytest.raises(psycopg.errors.DataError):
            store_document(store_connection, metadata, chunks)

    def test_a_failed_store_leaves_nothing_after_a_savepoint_rollback(self, store_connection):
        metadata = make_metadata()
        bad = [Chunk(metadata.path, 1, 0, "abc", 0, 10)]
        with pytest.raises(psycopg.errors.CheckViolation), store_connection.transaction():
            store_document(store_connection, metadata, bad)
        assert list_documents(store_connection, EVERYONE) == []


class TestArabicRoundTrip:
    def test_arabic_text_and_metadata_come_back_exactly(self, store_connection):
        metadata = make_metadata(
            id="hr-annual-leave-policy-ar",
            path="hr/annual_leave_policy_ar.md",
            title="سياسة الإجازة السنوية",
            language="ar",
            digits="arabic-indic",
        )
        texts = ["يحصل الموظف على ٢١ يومًا من الإجازة السنوية.", "للاتصال بالشبكة استخدم VPN."]
        chunks = make_chunks(metadata.path, texts)
        store_document(store_connection, metadata, chunks)
        (first, second) = list_chunks(store_connection, EVERYONE)
        assert (first.text, second.text) == tuple(texts)
        assert first.document.title == "سياسة الإجازة السنوية"
        assert first.document.digits == "arabic-indic"

    def test_characters_that_are_easy_to_lose_survive_unchanged(self, store_connection):
        text = (
            "\N{ARABIC LETTER KAF}\N{ARABIC LETTER TEH}\N{ARABIC TATWEEL}\N{ARABIC TATWEEL}"
            "\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}"
            "\N{ARABIC FATHA}\N{ARABIC KASRA}\N{ARABIC SHADDA}"
            "\N{RIGHT-TO-LEFT MARK}\N{ZERO WIDTH NON-JOINER}\N{NO-BREAK SPACE}"
            "\N{ARABIC-INDIC DIGIT TWO}\N{ARABIC-INDIC DIGIT ONE}"
            "\N{ARABIC LIGATURE LAM WITH ALEF ISOLATED FORM}"  # a presentation form
            "\N{ARABIC LETTER ALEF WITH HAMZA ABOVE}\N{ARABIC LETTER ALEF}\N{ARABIC HAMZA ABOVE}"
            " \N{GRINNING FACE}"  # outside the Basic Multilingual Plane
        )
        metadata = make_metadata(language="ar")
        store_document(store_connection, metadata, make_chunks(metadata.path, [text]))
        (stored,) = list_chunks(store_connection, EVERYONE)
        assert stored.text == text
        assert [ord(ch) for ch in stored.text] == [ord(ch) for ch in text]
        assert (stored.end - stored.start) == len(text)

    def test_a_decomposed_and_a_composed_form_stay_different(self, store_connection):
        composed = "\N{ARABIC LETTER ALEF WITH MADDA ABOVE}"
        decomposed = "\N{ARABIC LETTER ALEF}\N{ARABIC MADDAH ABOVE}"
        metadata = make_metadata(language="ar")
        store_document(
            store_connection, metadata, make_chunks(metadata.path, [composed, decomposed])
        )
        first, second = list_chunks(store_connection, EVERYONE)
        assert (first.text, second.text) == (composed, decomposed)  # nothing was normalized


class TestListing:
    def test_documents_are_ordered_by_path_in_byte_order(self, store_connection):
        for number, path in enumerate(["b.md", "B.md", "a.md", "hr/a.md", "_x.md"]):
            metadata = make_metadata(id=f"doc-{number}", path=path)
            store_document(store_connection, metadata, make_chunks(path, ["x"]))
        paths = [d.path for d in list_documents(store_connection, EVERYONE)]
        assert paths == sorted(paths)  # Python compares code points, like the "C" collation

    def test_a_missing_document_is_none(self, store_connection):
        assert get_document(store_connection, "nope") is None

    def test_an_empty_database_lists_nothing(self, store_connection):
        assert list_documents(store_connection, EVERYONE) == []
        assert list_chunks(store_connection, EVERYONE) == []

    def test_chunks_are_ordered_by_path_then_page_then_index(self, store_connection):
        for number, path in enumerate(["b.md", "a.md"]):
            metadata = make_metadata(id=f"doc-{number}", path=path)
            chunks = make_chunks(path, ["p2 c0", "p2 c1"], page=2)
            chunks += make_chunks(path, ["p1 c0"], page=1)
            store_document(store_connection, metadata, chunks)
        order = [
            (c.document.path, c.page, c.index) for c in list_chunks(store_connection, EVERYONE)
        ]
        assert order == [
            ("a.md", 1, 0),
            ("a.md", 2, 0),
            ("a.md", 2, 1),
            ("b.md", 1, 0),
            ("b.md", 2, 0),
            ("b.md", 2, 1),
        ]

    def test_filters_by_document_and_language(self, store_connection):
        en = make_metadata()
        ar = make_metadata(id="hr-leave-ar", path="hr/leave_ar.md", language="ar")
        store_document(store_connection, en, make_chunks(en.path, ["english"]))
        store_document(store_connection, ar, make_chunks(ar.path, ["عربي"]))
        assert [c.text for c in list_chunks(store_connection, EVERYONE, language="ar")] == ["عربي"]
        assert [c.text for c in list_chunks(store_connection, EVERYONE, language="en")] == [
            "english"
        ]
        assert [c.text for c in list_chunks(store_connection, EVERYONE, document_id=ar.id)] == [
            "عربي"
        ]
        assert list_chunks(store_connection, EVERYONE, document_id=ar.id, language="en") == []


class TestDelete:
    def test_deletes_the_document_and_its_chunks(self, store_connection):
        metadata, _, _ = _put(store_connection)
        assert delete_document(store_connection, metadata.id) is True
        assert get_document(store_connection, metadata.id) is None
        assert list_chunks(store_connection, EVERYONE) == []

    def test_deleting_a_missing_document_reports_false(self, store_connection):
        assert delete_document(store_connection, "nope") is False

    def test_only_the_named_document_is_deleted(self, store_connection):
        keep, _, _ = _put(store_connection)
        other = make_metadata(id="other", path="other.md")
        store_document(store_connection, other, make_chunks(other.path, ["x"]))
        delete_document(store_connection, other.id)
        assert [d.id for d in list_documents(store_connection, EVERYONE)] == [keep.id]
        assert len(list_chunks(store_connection, EVERYONE)) == 2


class TestAccessFilter:
    """The filter is part of the SQL statement: restricted text never leaves the database."""

    @pytest.fixture
    def documents(self, store_connection):
        for level in ACCESS_LEVELS:
            metadata = make_metadata(id=f"doc-{level}", path=f"{level}.md", access_level=level)
            store_document(
                store_connection, metadata, make_chunks(metadata.path, [f"secret of {level}"])
            )

    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_a_single_level_returns_only_that_levels_documents(
        self, store_connection, documents, level
    ):
        assert [c.text for c in list_chunks(store_connection, {level})] == [f"secret of {level}"]

    def test_several_levels_return_the_union(self, store_connection, documents):
        chunks = list_chunks(store_connection, ["public", "employee"])
        assert sorted(c.document.access_level for c in chunks) == ["employee", "public"]

    def test_every_level_returns_everything(self, store_connection, documents):
        assert len(list_chunks(store_connection, EVERYONE)) == len(ACCESS_LEVELS)

    def test_an_empty_collection_returns_nothing(self, store_connection, documents):
        for empty in ([], (), set(), frozenset()):
            assert list_chunks(store_connection, empty) == []

    def test_an_unknown_level_matches_nothing(self, store_connection, documents):
        assert list_chunks(store_connection, {"admin", "Employee", ""}) == []

    def test_a_string_that_looks_like_sql_is_only_data(self, store_connection, documents):
        for attack in ("employee' OR '1'='1", "x') OR true --", 'public"; DROP TABLE chunks; --'):
            assert list_chunks(store_connection, {attack}) == []
        assert len(list_chunks(store_connection, EVERYONE)) == len(ACCESS_LEVELS)  # table intact

    def test_a_bare_string_is_refused_not_read_as_letters(self, store_connection, documents):
        with pytest.raises(TypeError, match="not one string"):
            list_chunks(store_connection, "employee")

    def test_repeated_levels_do_not_duplicate_rows(self, store_connection, documents):
        assert len(list_chunks(store_connection, ["hr", "hr", "hr"])) == 1

    def test_the_filter_combines_with_the_other_filters(self, store_connection, documents):
        assert list_chunks(store_connection, {"hr"}, document_id="doc-management") == []
        assert len(list_chunks(store_connection, {"hr"}, document_id="doc-hr")) == 1

    def test_restricted_text_is_absent_from_every_field_of_the_result(
        self, store_connection, documents
    ):
        chunks = list_chunks(store_connection, {"public", "employee"})
        blob = repr(chunks)
        for hidden in ("hr", "management", "engineering"):
            assert f"secret of {hidden}" not in blob
            assert f"{hidden}.md" not in blob


class TestDocumentListAccessFilter:
    """Titles and paths can be sensitive too: the document list is filtered like chunks."""

    @pytest.fixture(autouse=True)
    def documents(self, store_connection):
        for level in ACCESS_LEVELS:
            metadata = make_metadata(
                id=f"doc-{level}", path=f"{level}.md", title=f"Plan {level}", access_level=level
            )
            store_document(store_connection, metadata, make_chunks(metadata.path, ["x"]))

    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_a_single_level_lists_only_that_levels_documents(self, store_connection, level):
        assert [d.id for d in list_documents(store_connection, {level})] == [f"doc-{level}"]

    def test_several_levels_list_the_union_in_path_order(self, store_connection):
        listed = list_documents(store_connection, ["public", "employee"])
        assert [d.path for d in listed] == ["employee.md", "public.md"]

    def test_an_empty_collection_lists_nothing(self, store_connection):
        assert list_documents(store_connection, set()) == []

    def test_a_bare_string_is_refused(self, store_connection):
        with pytest.raises(TypeError, match="not one string"):
            list_documents(store_connection, "employee")

    def test_sql_looking_levels_are_only_data(self, store_connection):
        assert list_documents(store_connection, {"public' OR '1'='1"}) == []
        assert len(list_documents(store_connection, EVERYONE)) == len(ACCESS_LEVELS)
