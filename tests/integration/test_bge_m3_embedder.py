"""Real-model tests: needs the bge-m3 weights downloaded and `pytest --slow` (see D-014).

Qualitative evidence, not a benchmark. Recall/MRR come from the Week 3 evaluation.
"""

import pytest

from bilingual_rag.embeddings.hashing import HashingEmbedder
from bilingual_rag.embeddings.sentence_transformer import bge_m3
from support.arabic import ARABIC_TRUTH

pytestmark = pytest.mark.slow


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # vectors are unit length


@pytest.fixture(scope="module")
def embedder():
    return bge_m3()


class TestShape:
    def test_the_dimension_matches_the_model_card(self, embedder):
        assert embedder.dimension == 1024

    def test_vectors_have_unit_length(self, embedder):
        vector = embedder.embed_query("Employees receive 21 days of annual leave.")
        assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-4)

    def test_documents_and_a_query_of_the_same_text_give_close_vectors(self, embedder):
        text = "Annual leave policy"
        assert cosine(embedder.embed_query(text), embedder.embed_documents([text])[0]) > 0.999

    def test_arabic_text_is_accepted_and_gives_a_unit_vector(self, embedder):
        vector = embedder.embed_query(ARABIC_TRUTH[1])
        assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-4)


class TestCrossLingualSignal:
    """The property HashingEmbedder explicitly lacks (see D-013): shared meaning, not words."""

    LEAVE_EN = "Employees receive 21 days of annual leave per year."
    LEAVE_AR = "يحصل الموظف على ٢١ يومًا من الإجازة السنوية."
    VPN_EN = "VPN troubleshooting guide for the Cisco client."
    VPN_AR = "دليل استكشاف أخطاء الشبكة الافتراضية."

    def test_the_same_topic_in_the_other_language_scores_higher_than_an_unrelated_one(
        self, embedder
    ):
        leave_en, leave_ar, vpn_en, vpn_ar = embedder.embed_documents(
            [self.LEAVE_EN, self.LEAVE_AR, self.VPN_EN, self.VPN_AR]
        )
        same_topic = cosine(leave_en, leave_ar)
        different_topic = cosine(leave_en, vpn_ar)
        # Measured once at 0.83 vs 0.30 (see D-014); a wide margin keeps this robust to
        # ordinary run-to-run float variation while still requiring the real property.
        assert same_topic > different_topic + 0.3

    def test_it_clears_a_margin_the_hashing_stand_in_cannot(self, embedder):
        real = cosine(embedder.embed_query(self.LEAVE_EN), embedder.embed_query(self.LEAVE_AR))
        fake = HashingEmbedder(256)
        stand_in = cosine(fake.embed_query(self.LEAVE_EN), fake.embed_query(self.LEAVE_AR))
        assert real > 0.5  # shares no words with the Arabic sentence, yet scores high
        assert stand_in < 0.3  # the stand-in has no way to do this; see D-013
