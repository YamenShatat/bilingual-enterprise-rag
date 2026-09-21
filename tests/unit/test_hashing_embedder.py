import json
import math
import os
import subprocess
import sys

import pytest

from bilingual_rag.embeddings.hashing import HashingEmbedder, _digest
from support.arabic import ARABIC_TRUTH


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))  # vectors are unit length


@pytest.fixture
def embedder():
    return HashingEmbedder()


class TestConstruction:
    def test_defaults(self, embedder):
        assert embedder.dimension == 256
        assert embedder.key == "hashing_256"
        assert embedder.model_name == "hashing-bag-of-words-256"

    def test_the_key_follows_the_dimension(self):
        assert HashingEmbedder(64).key == "hashing_64"

    @pytest.mark.parametrize("dimension", [0, -1, 16001, 3.5, "8", None, True])
    def test_an_invalid_dimension_is_rejected(self, dimension):
        with pytest.raises(ValueError, match="dimension"):
            HashingEmbedder(dimension)

    @pytest.mark.parametrize("dimension", [1, 16000])
    def test_the_limits_are_accepted(self, dimension):
        assert len(HashingEmbedder(dimension).embed_query("word")) == dimension


class TestVectors:
    def test_a_vector_has_the_right_length_and_unit_norm(self, embedder):
        vector = embedder.embed_query("Employees receive 21 days of annual leave.")
        assert len(vector) == 256
        assert math.hypot(*vector) == pytest.approx(1.0)

    def test_the_same_text_gives_the_same_vector(self, embedder):
        text = "Employees receive 21 days of annual leave."
        assert embedder.embed_query(text) == embedder.embed_query(text)
        assert HashingEmbedder().embed_query(text) == embedder.embed_query(text)

    def test_the_result_is_identical_across_processes_and_hash_seeds(self, embedder):
        text = ARABIC_TRUTH[1] + " annual leave"
        code = (
            "import json, sys;"
            "from bilingual_rag.embeddings.hashing import HashingEmbedder;"
            "print(json.dumps(HashingEmbedder(64).embed_query(sys.argv[1])))"
        )
        expected = HashingEmbedder(64).embed_query(text)
        for seed in ("0", "1", "12345"):
            env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}
            run = subprocess.run(
                [sys.executable, "-c", code, text],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                check=True,
            )
            assert json.loads(run.stdout) == expected, f"differs with PYTHONHASHSEED={seed}"

    def test_batching_does_not_change_a_vector(self, embedder):
        texts = ["annual leave", "vpn guide", ARABIC_TRUTH[0], "code review"]
        together = embedder.embed_documents(texts)
        alone = [embedder.embed_documents([text])[0] for text in texts]
        assert together == alone

    def test_order_and_count_are_kept(self, embedder):
        texts = ["one", "two", "three"]
        vectors = embedder.embed_documents(texts)
        assert len(vectors) == 3
        assert vectors[1] == embedder.embed_query("two")

    def test_no_texts_gives_no_vectors(self, embedder):
        assert embedder.embed_documents([]) == []

    def test_a_query_and_a_document_of_the_same_text_agree(self, embedder):
        assert embedder.embed_documents(["annual leave"])[0] == embedder.embed_query("annual leave")


class TestWhatItDoesAndDoesNotCapture:
    def test_shared_words_mean_a_closer_vector(self, embedder):
        chunk = embedder.embed_query("Employees receive 21 days of annual leave each year.")
        related = embedder.embed_query("how many days of annual leave do I get")
        unrelated = embedder.embed_query("vpn troubleshooting for the cisco client")
        assert cosine(chunk, related) > cosine(chunk, unrelated) + 0.2

    def test_arabic_words_are_matched_too(self, embedder):
        chunk = embedder.embed_query(ARABIC_TRUTH[1])  # ... الإجازة السنوية ...
        related = embedder.embed_query("الإجازة السنوية")
        unrelated = embedder.embed_query(ARABIC_TRUTH[4])  # the VPN sentence
        assert cosine(chunk, related) > cosine(chunk, unrelated) + 0.2

    def test_it_has_no_idea_that_two_languages_mean_the_same_thing(self, embedder):
        english = embedder.embed_query("annual leave policy")
        arabic = embedder.embed_query("سياسة الإجازة السنوية")
        assert abs(cosine(english, arabic)) < 0.3  # no shared words, so no similarity

    def test_word_order_does_not_matter(self, embedder):
        assert embedder.embed_query("annual leave policy") == embedder.embed_query(
            "policy leave annual"
        )

    def test_case_is_ignored(self, embedder):
        assert embedder.embed_query("Annual LEAVE") == embedder.embed_query("annual leave")

    @pytest.mark.parametrize(
        ("with_marks", "plain"),
        [
            ("leave, policy.", "leave policy"),
            ("## Leave Policy", "leave policy"),
            ("| leave | policy |", "leave policy"),
            ("(leave) [policy]", "leave policy"),
            ("«leave» policy؟", "leave policy"),
            ("الإجازة، السنوية.", "الإجازة السنوية"),
            ("**bold**", "bold"),
        ],
    )
    def test_punctuation_and_symbols_at_a_words_edge_are_ignored(self, embedder, with_marks, plain):
        assert embedder.embed_query(with_marks) == embedder.embed_query(plain)

    def test_punctuation_inside_a_word_is_kept(self, embedder):
        assert embedder.embed_query("e-mail") != embedder.embed_query("email")
        assert embedder.embed_query("1,000") != embedder.embed_query("1000")


class TestNoArabicNormalization:
    """The embedder does not fold Arabic variants: that is a Week 3 experiment (D-004)."""

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("يومًا", "يوما"),  # with and without tanween
            ("أحمد", "احمد"),  # alef with hamza vs bare alef
            (
                "\N{ARABIC LETTER KAF}\N{ARABIC LETTER TEH}\N{ARABIC TATWEEL}"
                "\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}",
                "\N{ARABIC LETTER KAF}\N{ARABIC LETTER TEH}\N{ARABIC LETTER ALEF}"
                "\N{ARABIC LETTER BEH}",
            ),  # with and without tatweel
            ("مدرسة", "مدرسه"),  # teh marbuta vs heh
            ("٢١", "21"),  # Arabic-Indic vs Western digits
        ],
    )
    def test_variants_stay_different(self, embedder, a, b):
        assert embedder.embed_query(a) != embedder.embed_query(b)


class TestEdgeCases:
    def test_a_text_of_only_punctuation_still_gets_a_vector(self, embedder):
        for text in ("---", "***", "|", "؟؟"):
            vector = embedder.embed_query(text)
            assert math.hypot(*vector) == pytest.approx(1.0), text

    def test_different_punctuation_only_texts_differ(self):
        wide = HashingEmbedder(4096)
        assert wide.embed_query("---") != wide.embed_query("***")

    def test_words_whose_signs_cancel_still_give_a_unit_vector(self):
        tiny = HashingEmbedder(1)  # every word lands on the one position
        plus = next(w for w in map(str, range(50)) if _digest(w)[8] & 1)
        minus = next(w for w in map(str, range(50)) if not _digest(w)[8] & 1)
        assert tiny.embed_query(f"{plus} {minus}") == [1.0]  # they cancel; the fallback kicks in
        assert tiny.embed_query(plus) == [1.0]
        assert tiny.embed_query(minus) == [-1.0]

    @pytest.mark.parametrize("blank", ["", " ", "\n\t  "])
    def test_blank_text_is_rejected(self, embedder, blank):
        with pytest.raises(ValueError, match="blank"):
            embedder.embed_query(blank)
        with pytest.raises(ValueError, match="blank"):
            embedder.embed_documents(["fine", blank])

    @pytest.mark.parametrize("bad", [None, 5, b"bytes", ["x"]])
    def test_a_non_string_is_rejected(self, embedder, bad):
        with pytest.raises(TypeError):
            embedder.embed_query(bad)
        with pytest.raises(TypeError):
            embedder.embed_documents([bad])

    def test_one_string_is_refused_not_read_as_characters(self, embedder):
        with pytest.raises(TypeError, match="not one string"):
            embedder.embed_documents("annual leave")

    def test_a_very_long_text_is_fine(self, embedder):
        vector = embedder.embed_query("word " * 100_000)
        assert math.hypot(*vector) == pytest.approx(1.0)
