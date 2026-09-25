"""Keyword search and the search_vector column (migration 0003) on a real database."""

import pytest

from bilingual_rag.database.connection import connect
from bilingual_rag.database.migrate import load_migrations, migrate
from bilingual_rag.database.repository import store_document
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from bilingual_rag.retrieval.keyword import keyword_search
from support.samples import make_chunks, make_metadata

pytestmark = pytest.mark.database

EVERYONE = set(ACCESS_LEVELS)
ARABIC_DOC = dict(
    id="hr-leave-ar", path="hr/leave_ar.md", language="ar", digits="arabic-indic", title="إجازة"
)


def lexemes(conn, chunk_id):
    row = conn.execute(
        "SELECT array(SELECT lexeme FROM unnest(search_vector)) FROM chunks WHERE id = %s",
        (chunk_id,),
    ).fetchone()
    return set(row[0])


class TestSearchVector:
    def test_an_english_chunk_is_stemmed_in_english_without_stopwords(self, store_connection):
        store_document(
            store_connection, make_metadata(), make_chunks("hr/leave_en.md", ["The days of leave"])
        )
        assert lexemes(store_connection, "hr-leave-en-p1-c0") == {"day", "leav"}

    def test_an_arabic_chunk_is_stemmed_in_arabic(self, store_connection):
        metadata = make_metadata(**ARABIC_DOC)
        store_document(store_connection, metadata, make_chunks(metadata.path, ["الإجازات السنوية"]))
        assert lexemes(store_connection, "hr-leave-ar-p1-c0") == {"اجاز", "سنو"}

    def test_the_stored_text_is_not_changed_by_stemming(self, store_connection):
        metadata = make_metadata(**ARABIC_DOC)
        store_document(store_connection, metadata, make_chunks(metadata.path, ["الإجازات السنوية"]))
        (text,) = store_connection.execute("SELECT text FROM chunks").fetchone()
        assert text == "الإجازات السنوية"

    def test_changing_a_documents_language_restems_its_chunks(self, store_connection):
        store_document(
            store_connection, make_metadata(), make_chunks("hr/leave_en.md", ["The days of leave"])
        )
        store_connection.execute("UPDATE documents SET language = 'ar' WHERE id = 'hr-leave-en'")
        # The Arabic stemmer leaves Latin words whole and knows no English stopwords.
        assert lexemes(store_connection, "hr-leave-en-p1-c0") == {"the", "days", "of", "leave"}

    def test_changing_a_chunks_text_restems_it(self, store_connection):
        store_document(store_connection, make_metadata(), make_chunks("hr/leave_en.md", ["days"]))
        store_connection.execute(
            "UPDATE chunks SET text = 'holidays', end_offset = 8 WHERE id = 'hr-leave-en-p1-c0'"
        )
        assert lexemes(store_connection, "hr-leave-en-p1-c0") == {"holiday"}

    def test_the_column_cannot_be_null(self, store_connection):
        store_document(store_connection, make_metadata(), make_chunks("hr/leave_en.md", ["days"]))
        with pytest.raises(Exception, match="null value"):
            store_connection.execute("UPDATE chunks SET search_vector = NULL")


def test_the_migration_fills_in_chunks_stored_before_it(empty_database):
    before, keyword = load_migrations()[:2], load_migrations()[:3]
    assert keyword[-1].name == "keyword_search"
    with connect(empty_database) as conn:
        migrate(conn, before)
        metadata = make_metadata(**ARABIC_DOC)
        store_document(conn, metadata, make_chunks(metadata.path, ["الإجازات السنوية"]))
        conn.commit()
        migrate(conn, keyword)
        assert lexemes(conn, "hr-leave-ar-p1-c0") == {"اجاز", "سنو"}


class TestKeywordSearch:
    @pytest.fixture(autouse=True)
    def _seeded(self, store_connection):
        english = make_metadata()
        store_document(
            store_connection,
            english,
            make_chunks(
                english.path,
                [
                    "Employees receive 21 working days of paid annual leave.",
                    "The VPN client must be updated every month.",
                ],
            ),
        )
        arabic = make_metadata(**ARABIC_DOC)
        store_document(
            store_connection,
            arabic,
            make_chunks(arabic.path, ["يحصل الموظفون على الإجازة السنوية المدفوعة."]),
        )
        self.conn = store_connection

    def ids(self, query, levels=EVERYONE, **kwargs):
        return [r.chunk.id for r in keyword_search(self.conn, query, levels, **kwargs)]

    def test_matches_english_word_forms_by_their_stem(self):
        assert self.ids("How many holidays? leaving day")[0] == "hr-leave-en-p1-c0"

    def test_matches_arabic_word_forms_by_their_stem(self):
        # الإجازات (plural, with the article) finds الإجازة (singular) in the chunk.
        assert self.ids("كم عدد الإجازات؟") == ["hr-leave-ar-p1-c0"]

    def test_more_shared_words_rank_higher(self):
        results = keyword_search(self.conn, "paid annual leave VPN", EVERYONE)
        assert [r.chunk.id for r in results] == ["hr-leave-en-p1-c0", "hr-leave-en-p1-c1"]
        assert results[0].score > results[1].score

    def test_does_not_match_across_languages(self):
        assert self.ids("annual leave", language=None) == ["hr-leave-en-p1-c0"]

    def test_the_language_filter(self):
        assert self.ids("leave الإجازة", language="ar") == ["hr-leave-ar-p1-c0"]
        assert self.ids("leave الإجازة", language="en") == ["hr-leave-en-p1-c0"]

    def test_k_limits_the_results(self):
        assert len(self.ids("leave VPN", k=1)) == 1

    def test_only_stopwords_or_punctuation_match_nothing(self):
        assert self.ids("the of and") == []
        assert self.ids("?? !! ،؟") == []

    @pytest.mark.parametrize(
        "query",
        ["leave & | ! :* ( )", "leave'", "O'Brien's leave", "leave\\", "leave' | 'x", "<-> leave"],
    )
    def test_tsquery_syntax_in_the_question_is_only_data(self, query):
        assert self.ids(query) == ["hr-leave-en-p1-c0"]

    def test_sql_in_the_question_is_only_data(self):
        assert self.ids("leave'); DROP TABLE chunks; --") == ["hr-leave-en-p1-c0"]
        assert self.conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 3

    def test_a_blank_query_is_refused(self):
        with pytest.raises(ValueError, match="blank"):
            keyword_search(self.conn, "  ", EVERYONE)

    def test_a_non_string_query_is_refused(self):
        with pytest.raises(TypeError):
            keyword_search(self.conn, None, EVERYONE)

    def test_k_must_be_positive(self):
        with pytest.raises(ValueError, match="k"):
            keyword_search(self.conn, "leave", EVERYONE, k=0)


class TestAccessControl:
    @pytest.fixture(autouse=True)
    def _seeded(self, store_connection):
        for level in ACCESS_LEVELS:
            metadata = make_metadata(id=f"doc-{level}", path=f"x/{level}.md", access_level=level)
            store_document(
                store_connection, metadata, make_chunks(metadata.path, [f"bonus secret {level}"])
            )
        self.conn = store_connection

    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_only_the_callers_levels_are_searched(self, level):
        results = keyword_search(self.conn, "bonus secret", {level}, k=10)
        assert [r.chunk.document.access_level for r in results] == [level]

    def test_an_empty_collection_returns_nothing(self):
        assert keyword_search(self.conn, "bonus", set()) == []

    def test_a_bare_string_is_refused(self):
        with pytest.raises(TypeError, match="not one string"):
            keyword_search(self.conn, "bonus", "employee")

    def test_restricted_text_is_absent_from_every_field_of_the_result(self):
        blob = repr(keyword_search(self.conn, "bonus secret", {"public", "employee"}, k=10))
        for hidden in ("hr", "management", "engineering"):
            assert f"secret {hidden}" not in blob
            assert f"x/{hidden}.md" not in blob
