"""run_question(s)/summarize against a real database, using the hashing stand-in for speed."""

import pytest

from bilingual_rag.database.repository import store_document
from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.indexing import embed_missing
from bilingual_rag.evaluation.questions import EvaluationQuestion
from bilingual_rag.evaluation.runner import run_question, run_questions, summarize
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS
from support.samples import make_chunks, make_metadata

pytestmark = pytest.mark.database

EVERYONE = set(ACCESS_LEVELS)


def q(id, expected, category="same_language", question="leave policy details", language="en"):
    return EvaluationQuestion(
        id=id,
        language=language,
        category=category,
        question=question,
        expected_document_id=expected,
    )


@pytest.fixture
def seeded(store_connection):
    fake = HashingEmbedder(64)
    for name in ["leave", "vpn", "benefits"]:
        metadata = make_metadata(id=f"doc-{name}", path=f"{name}.md", access_level="employee")
        store_document(
            store_connection, metadata, make_chunks(metadata.path, [f"{name} policy details"])
        )
    embed_missing(store_connection, fake)
    return store_connection, fake


class TestRunQuestion:
    def test_the_expected_document_ranks_first_when_it_is_the_closest_match(self, seeded):
        conn, fake = seeded
        outcome = run_question(conn, fake, q("q1", "doc-leave", question="leave policy"), EVERYONE)
        assert outcome.rank == 1
        assert outcome.top_score is not None

    def test_a_wrong_expected_document_is_not_found_at_rank_one(self, seeded):
        conn, fake = seeded
        outcome = run_question(conn, fake, q("q1", "doc-vpn", question="leave policy"), EVERYONE)
        assert outcome.rank != 1  # doc-leave is the closest match, not doc-vpn

    def test_an_absent_fact_question_has_no_rank_but_has_a_top_score(self, seeded):
        conn, fake = seeded
        question = q("q1", None, category="absent_fact", question="leave policy")
        outcome = run_question(conn, fake, question, EVERYONE)
        assert outcome.rank is None
        assert outcome.top_score is not None

    def test_an_access_level_that_hides_everything_gives_no_rank_and_no_score(self, seeded):
        conn, fake = seeded
        outcome = run_question(conn, fake, q("q1", "doc-leave", question="leave policy"), set())
        assert outcome.rank is None
        assert outcome.top_score is None

    def test_a_document_with_several_top_chunks_does_not_push_down_another_documents_rank(
        self, store_connection
    ):
        # Without de-duplication, doc-vpn's two top-ranked chunks would occupy positions 1
        # and 2, pushing doc-leave's chunk to rank 3 instead of its correct rank 2.
        fake = HashingEmbedder(64)
        vpn = make_metadata(id="doc-vpn", path="vpn.md", access_level="employee")
        store_document(
            store_connection,
            vpn,
            make_chunks(vpn.path, ["vpn setup guide alpha", "vpn setup guide beta"]),
        )
        leave = make_metadata(id="doc-leave", path="leave.md", access_level="employee")
        store_document(store_connection, leave, make_chunks(leave.path, ["leave policy guide"]))
        embed_missing(store_connection, fake)

        outcome = run_question(
            store_connection, fake, q("q1", "doc-leave", question="vpn setup guide"), EVERYONE
        )
        assert outcome.rank == 2


class TestRunQuestions:
    def test_returns_one_outcome_per_question_in_order(self, seeded):
        conn, fake = seeded
        questions = [q("q1", "doc-leave", question="leave"), q("q2", "doc-vpn", question="vpn")]
        outcomes = run_questions(conn, fake, questions, EVERYONE)
        assert [o.question.id for o in outcomes] == ["q1", "q2"]


class TestSummarize:
    def test_recall_and_mrr_over_answerable_questions(self, seeded):
        conn, fake = seeded
        questions = [
            q("q1", "doc-leave", question="leave policy"),  # rank 1
            q("q2", "doc-leave", question="vpn policy"),  # wrong expectation, not rank 1
        ]
        outcomes = run_questions(conn, fake, questions, EVERYONE)
        summary = summarize(outcomes, k_values=(1, 3))
        assert summary["overall"]["count"] == 2
        assert summary["overall"]["recall@1"] == 0.5  # only q1 hit rank 1
        assert 0.0 < summary["overall"]["mrr"] <= 1.0

    def test_language_and_category_breakdowns_exist(self, seeded):
        conn, fake = seeded
        questions = [
            q("q1", "doc-leave", question="leave policy", language="en", category="same_language"),
            q("q2", "doc-vpn", question="vpn policy", language="ar", category="cross_lingual"),
        ]
        summary = summarize(run_questions(conn, fake, questions, EVERYONE))
        assert summary["language:en"]["count"] == 1
        assert summary["language:ar"]["count"] == 1
        assert summary["category:same_language"]["count"] == 1
        assert summary["category:cross_lingual"]["count"] == 1

    def test_absent_fact_is_reported_separately_and_excluded_from_recall(self, seeded):
        conn, fake = seeded
        questions = [
            q("q1", "doc-leave", question="leave policy"),
            q("q2", None, category="absent_fact", question="leave policy"),
        ]
        summary = summarize(run_questions(conn, fake, questions, EVERYONE))
        assert summary["overall"]["count"] == 1  # the absent_fact question is not "answerable"
        assert summary["absent_fact"]["count"] == 1
        assert summary["absent_fact"]["mean_top_score"] is not None

    def test_no_answerable_questions_is_an_error(self, seeded):
        conn, fake = seeded
        questions = [q("q1", None, category="absent_fact", question="leave policy")]
        with pytest.raises(ValueError, match="no answerable"):
            summarize(run_questions(conn, fake, questions, EVERYONE))


class FavourVpn:
    """Scores the VPN chunk 0.9 and everything else 0.1."""

    model_name = "favour-vpn"

    def score(self, query, texts):
        return [0.9 if "vpn" in text else 0.1 for text in texts]


class TestWithReranker:
    def test_the_reranker_decides_the_rank_and_the_top_score(self, seeded):
        conn, fake = seeded
        question = q("q1", "doc-vpn", question="leave policy")
        plain = run_question(conn, fake, question, EVERYONE)
        reranked = run_question(conn, fake, question, EVERYONE, reranker=FavourVpn())
        assert plain.rank != 1
        assert reranked.rank == 1
        assert reranked.top_score == 0.9  # the rerank score, not the cosine

    def test_the_mode_is_passed_on(self, seeded):
        conn, fake = seeded
        # Keyword mode finds nothing for words no chunk contains; vector mode always finds some.
        question = q("q1", "doc-leave", question="zzz unrelated words")
        assert run_question(conn, fake, question, EVERYONE, mode="keyword").top_score is None
        assert run_question(conn, fake, question, EVERYONE, mode="vector").top_score is not None


class TestScoreBounds:
    def test_the_lowest_answerable_and_highest_absent_top_scores_are_reported(self, seeded):
        conn, fake = seeded
        questions = [
            q("q1", "doc-leave", question="leave policy details"),
            q("q2", "doc-vpn", question="vpn"),
            q("q3", None, category="absent_fact", question="leave policy details"),
            q("q4", None, category="absent_fact", question="benefits"),
        ]
        outcomes = run_questions(conn, fake, questions, EVERYONE)
        summary = summarize(outcomes)
        scores = {o.question.id: o.top_score for o in outcomes}
        assert summary["answerable_top_score"] == {
            "count": 2,
            "min_top_score": min(scores["q1"], scores["q2"]),
        }
        assert summary["absent_fact"]["max_top_score"] == max(scores["q3"], scores["q4"])


def test_run_questions_passes_the_reranker_to_every_question(seeded):
    conn, fake = seeded
    questions = [
        q("q1", "doc-vpn", question="leave policy"),
        q("q2", "doc-vpn", question="benefits"),
    ]
    outcomes = run_questions(conn, fake, questions, EVERYONE, reranker=FavourVpn())
    assert [o.rank for o in outcomes] == [1, 1]
