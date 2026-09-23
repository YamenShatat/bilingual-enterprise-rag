import pytest

from bilingual_rag.database.repository import StoredChunk, chunk_id
from bilingual_rag.generation.context import DEFAULT_MAX_CHARS, build_context
from bilingual_rag.retrieval.search import SearchResult
from support.arabic import ARABIC_TRUTH
from support.samples import make_metadata

ARABIC_DOC = make_metadata(
    id="hr-leave-ar",
    path="hr/leave_ar.md",
    title=ARABIC_TRUTH[0],
    language="ar",
    digits="arabic-indic",
)


def result(text, *, document=None, page=1, index=0, score=0.7):
    document = document or make_metadata()
    return SearchResult(
        chunk=StoredChunk(
            id=chunk_id(document.id, page, index),
            document=document,
            page=page,
            index=index,
            text=text,
            start=0,
            end=len(text),
        ),
        score=score,
    )


class TestNumberingAndFormat:
    def test_sources_are_numbered_from_one_in_the_order_given(self):
        context = build_context([result("a", index=0), result("b", index=1), result("c", index=2)])
        assert [s.number for s in context.sources] == [1, 2, 3]
        assert [s.text for s in context.sources] == ["a", "b", "c"]

    def test_each_block_has_a_header_then_the_exact_text(self):
        context = build_context([result("Thirty days of leave.", page=3)])
        assert context.text == "[1] hr-leave-en | page 3 | Leave Policy\nThirty days of leave."

    def test_blocks_are_separated_by_a_blank_line(self):
        context = build_context([result("first"), result("second", index=1)])
        assert context.text.split("\n\n") == [
            "[1] hr-leave-en | page 1 | Leave Policy\nfirst",
            "[2] hr-leave-en | page 1 | Leave Policy\nsecond",
        ]

    def test_source_keeps_where_the_evidence_came_from(self):
        (source,) = build_context([result("x", page=2, index=4, score=0.61)]).sources
        assert source.chunk_id == "hr-leave-en-p2-c4"
        assert source.document_id == "hr-leave-en"
        assert source.title == "Leave Policy"
        assert source.page == 2
        assert source.score == 0.61

    def test_arabic_text_and_title_pass_through_unchanged(self):
        text = "\n".join(ARABIC_TRUTH[1:])
        context = build_context([result(text, document=ARABIC_DOC)])
        assert context.sources[0].text == text
        assert context.text == f"[1] hr-leave-ar | page 1 | {ARABIC_TRUTH[0]}\n{text}"

    def test_text_is_not_stripped_or_normalized(self):
        text = "  line one\n\nline two with a trailing space \n"
        assert build_context([result(text)]).sources[0].text == text

    def test_no_results_give_an_empty_context(self):
        context = build_context([])
        assert context.text == ""
        assert context.sources == ()


class TestBudget:
    def test_everything_fits_when_the_budget_allows(self):
        results = [result("x" * 100, index=i) for i in range(5)]
        assert len(build_context(results).sources) == 5

    def test_the_budget_is_exact_including_separators(self):
        results = [result("abc"), result("def", index=1)]
        full = build_context(results).text
        assert len(build_context(results, max_chars=len(full)).sources) == 2
        assert len(build_context(results, max_chars=len(full) - 1).sources) == 1

    def test_the_block_never_exceeds_the_budget(self):
        results = [result("y" * 300, index=i) for i in range(10)]
        for budget in (400, 700, 1000, 2500):
            assert len(build_context(results, max_chars=budget).text) <= budget

    def test_stops_at_the_first_result_that_does_not_fit(self):
        # A later, smaller result would fit, but rank order is kept: nothing after a gap.
        results = [result("a" * 50), result("b" * 500, index=1), result("c", index=2)]
        context = build_context(results, max_chars=200)
        assert [s.text[0] for s in context.sources] == ["a"]

    def test_a_chunk_is_never_cut_short(self):
        results = [result("a" * 50), result("b" * 500, index=1)]
        context = build_context(results, max_chars=200)
        assert "b" not in context.text

    def test_a_budget_too_small_for_the_top_result_is_an_error_not_silence(self):
        # Silently returning no evidence would turn a misconfiguration into a refusal.
        with pytest.raises(ValueError, match="cannot hold the top result"):
            build_context([result("x" * 100)], max_chars=50)

    def test_the_default_budget_holds_five_of_the_longest_corpus_chunks(self):
        results = [result("x" * 1200, index=i) for i in range(5)]
        assert len(build_context(results, max_chars=DEFAULT_MAX_CHARS).sources) == 5

    @pytest.mark.parametrize("max_chars", [0, -1, True, 1.5, "100"])
    def test_rejects_a_bad_budget(self, max_chars):
        with pytest.raises(ValueError, match="max_chars"):
            build_context([], max_chars=max_chars)


def test_accepts_any_sequence_including_a_tuple():
    assert len(build_context((result("a"),)).sources) == 1
