import numpy as np
import pytest

from bilingual_rag.retrieval.cross_encoder import CrossEncoderReranker
from bilingual_rag.retrieval.rerank import Reranker, rerank
from support.arabic import ARABIC_TRUTH
from support.samples import make_result


class ScoreByLength:
    """Longer text scores higher, so the expected order is easy to state."""

    model_name = "by-length"

    def __init__(self):
        self.calls = []

    def score(self, query, texts):
        self.calls.append((query, list(texts)))
        return [float(len(text)) for text in texts]


def results(*texts):
    return [make_result(text, index=i, score=0.9 - i * 0.1) for i, text in enumerate(texts)]


class TestRerank:
    def test_orders_by_the_rerankers_score_and_sets_it(self):
        reranked = rerank(ScoreByLength(), "q", results("bb", "a", "ccc"))
        assert [r.chunk.text for r in reranked] == ["ccc", "bb", "a"]
        assert [r.rerank_score for r in reranked] == [3.0, 2.0, 1.0]

    def test_keeps_the_original_cosine_score(self):
        reranked = rerank(ScoreByLength(), "q", results("bb", "a", "ccc"))
        assert [r.score for r in reranked] == pytest.approx([0.7, 0.9, 0.8])

    def test_k_keeps_the_top(self):
        assert [
            r.chunk.text for r in rerank(ScoreByLength(), "q", results("bb", "a", "ccc"), k=1)
        ] == ["ccc"]

    def test_equal_scores_keep_the_retrievers_order(self):
        reranked = rerank(ScoreByLength(), "q", results("x2", "x1", "x3"))
        assert [r.chunk.text for r in reranked] == ["x2", "x1", "x3"]

    def test_the_reranker_sees_the_query_and_the_exact_texts(self):
        reranker = ScoreByLength()
        rerank(reranker, ARABIC_TRUTH[0], results(ARABIC_TRUTH[1], " spaced "))
        assert reranker.calls == [(ARABIC_TRUTH[0], [ARABIC_TRUTH[1], " spaced "])]

    def test_no_results_never_call_the_reranker(self):
        reranker = ScoreByLength()
        assert rerank(reranker, "q", []) == []
        assert reranker.calls == []

    def test_a_wrong_number_of_scores_is_an_error(self):
        class Broken:
            model_name = "broken"

            def score(self, query, texts):
                return [1.0]

        with pytest.raises(ValueError, match="1 scores for 2 texts"):
            rerank(Broken(), "q", results("a", "b"))

    def test_k_must_be_positive(self):
        with pytest.raises(ValueError, match="k"):
            rerank(ScoreByLength(), "q", results("a"), k=0)


class FakePredictor:
    def __init__(self):
        self.calls = []

    def predict(self, pairs, *, batch_size):
        self.calls.append((list(pairs), batch_size))
        return np.array([0.9, 0.1][: len(pairs)])


class TestCrossEncoderReranker:
    def make(self, **kwargs):
        self.fake = FakePredictor()
        return CrossEncoderReranker("org/fake", device="cpu", _model=self.fake, **kwargs)

    def test_scores_question_text_pairs_in_order(self):
        reranker = self.make()
        assert reranker.score("q", ["a", "b"]) == pytest.approx([0.9, 0.1])
        assert self.fake.calls == [([("q", "a"), ("q", "b")], 32)]

    def test_texts_and_the_question_reach_the_model_unchanged(self):
        reranker = self.make()
        reranker.score(ARABIC_TRUTH[0], [ARABIC_TRUTH[1]])
        assert self.fake.calls[0][0] == [(ARABIC_TRUTH[0], ARABIC_TRUTH[1])]

    def test_returns_plain_floats(self):
        assert all(type(s) is float for s in self.make().score("q", ["a", "b"]))

    def test_no_texts_never_call_the_model(self):
        reranker = self.make()
        assert reranker.score("q", []) == []
        assert self.fake.calls == []

    def test_batch_size_is_passed_on(self):
        self.make(batch_size=4).score("q", ["a"])
        assert self.fake.calls[0][1] == 4

    @pytest.mark.parametrize("bad", [None, 3, b"x"])
    def test_non_strings_are_refused(self, bad):
        with pytest.raises(TypeError):
            self.make().score("q", ["ok", bad])
        with pytest.raises(TypeError):
            self.make().score(bad, ["ok"])

    def test_one_string_is_not_a_list_of_texts(self):
        with pytest.raises(TypeError, match="not one string"):
            self.make().score("q", "abc")

    @pytest.mark.parametrize("blank", ["", "  \n"])
    def test_blank_text_or_question_is_refused(self, blank):
        with pytest.raises(ValueError, match="blank"):
            self.make().score("q", [blank])
        with pytest.raises(ValueError, match="blank"):
            self.make().score(blank, ["a"])

    def test_batch_size_must_be_positive(self):
        with pytest.raises(ValueError, match="batch_size"):
            CrossEncoderReranker("org/fake", _model=FakePredictor(), batch_size=0)

    def test_satisfies_the_reranker_protocol(self):
        assert isinstance(self.make(), Reranker)
        assert self.make().model_name == "org/fake"
