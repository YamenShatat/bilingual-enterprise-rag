import unicodedata

import pytest

from bilingual_rag.ingestion.chunker import chunk_document, chunk_spans
from bilingual_rag.ingestion.models import Document, Page


def paragraphs(*lengths: int) -> list[str]:
    """One paragraph per length, each filled with a different letter (a, b, c, ...)."""
    return [chr(ord("a") + i) * length for i, length in enumerate(lengths)]


def chunk_texts(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    return [text[s:e] for s, e in chunk_spans(text, chunk_size=chunk_size, overlap=overlap)]


def make_mixed_text() -> str:
    """Arabic + English paragraphs, plus a long run with no punctuation."""
    english = "Employees accrue annual leave monthly. Unused days may be carried over. " * 6
    arabic = "يحصل الموظف على ٢١ يومًا من الإجازة السنوية. يجب تقديم الطلب قبل أسبوعين؟ " * 6
    unpunctuated = "word " * 80
    return "\n\n".join([english.strip(), arabic.strip(), unpunctuated.strip(), english.strip()])


MIXED_TEXT = make_mixed_text()
SETTINGS = [(100, 0), (100, 30), (300, 50), (1200, 200), (64, 63)]


# --- basic behaviour ---------------------------------------------------------------


def test_short_text_is_a_single_chunk() -> None:
    text = "Employees receive 21 days of annual leave."

    assert chunk_spans(text, chunk_size=1200, overlap=200) == [(0, len(text))]


def test_surrounding_whitespace_is_excluded_from_the_chunk() -> None:
    assert chunk_texts("  hi  \n", chunk_size=100, overlap=0) == ["hi"]


@pytest.mark.parametrize("text", ["", "   ", "\n\n \t\n"])
def test_blank_text_has_no_chunks(text: str) -> None:
    assert chunk_spans(text, chunk_size=100, overlap=0) == []


# --- boundaries and overlap --------------------------------------------------------


def test_packs_whole_paragraphs_with_overlap() -> None:
    a, b, c, d, e = paragraphs(30, 30, 30, 30, 30)
    text = "\n\n".join([a, b, c, d, e])

    assert chunk_texts(text, chunk_size=70, overlap=35) == [
        f"{a}\n\n{b}",
        f"{b}\n\n{c}",
        f"{c}\n\n{d}",
        f"{d}\n\n{e}",
    ]


def test_without_overlap_chunks_do_not_repeat_paragraphs() -> None:
    a, b, c, d, e = paragraphs(30, 30, 30, 30, 30)
    text = "\n\n".join([a, b, c, d, e])

    assert chunk_texts(text, chunk_size=70, overlap=0) == [f"{a}\n\n{b}", f"{c}\n\n{d}", e]


def test_a_chunk_may_be_exactly_chunk_size() -> None:
    a, b, c = paragraphs(30, 30, 30)  # a + separator + b is exactly 62 characters

    assert chunk_texts("\n\n".join([a, b, c]), chunk_size=62, overlap=0) == [f"{a}\n\n{b}", c]


def test_overlap_is_dropped_when_it_would_make_the_next_chunk_too_large() -> None:
    a, b, c = paragraphs(30, 30, 60)
    # Repeating b before c would need 92 characters, more than chunk_size.
    assert chunk_texts("\n\n".join([a, b, c]), chunk_size=70, overlap=40) == [f"{a}\n\n{b}", c]


def test_block_without_blank_lines_is_split_at_line_breaks() -> None:
    line = " ".join(["word"] * 8)  # 39 characters, no sentence punctuation
    text = "\n".join([line] * 4)

    assert chunk_texts(text, chunk_size=90, overlap=0) == [f"{line}\n{line}"] * 2


def test_long_paragraph_is_split_at_sentence_boundaries() -> None:
    text = " ".join(f"This is sentence number {i}." for i in range(1, 11))

    texts = chunk_texts(text, chunk_size=60, overlap=0)

    assert len(texts) == 5
    assert all(t.startswith("This is sentence") and t.endswith(".") for t in texts)
    assert all(t.count("sentence number") == 2 for t in texts)


def test_arabic_question_mark_alone_ends_a_sentence() -> None:
    question = "ما هي سياسة الإجازة السنوية؟"  # 28 characters, no other punctuation to split on
    text = " ".join([question] * 8)

    assert chunk_texts(text, chunk_size=60, overlap=0) == [f"{question} {question}"] * 4


def test_arabic_sentences_split_after_arabic_question_mark_and_full_stop() -> None:
    question = "ما هي سياسة الإجازة السنوية؟"
    answer = "يحصل الموظف على ٢١ يومًا من الإجازة بعد التجربة."
    text = " ".join([question, answer] * 4)

    assert chunk_texts(text, chunk_size=100, overlap=0) == [f"{question} {answer}"] * 4


# --- hard cuts ---------------------------------------------------------------------


def test_unbroken_run_is_hard_cut_to_chunk_size() -> None:
    text = "x" * 250

    texts = chunk_texts(text, chunk_size=100, overlap=0)

    assert [len(t) for t in texts] == [100, 100, 50]
    assert "".join(texts) == text


def test_hard_cut_never_starts_a_chunk_with_an_arabic_diacritic() -> None:
    fatha = "\N{ARABIC FATHA}"
    text = ("ب" + fatha) * 60  # base letter + mark alternate, so a cut at 51 would orphan a mark

    texts = chunk_texts(text, chunk_size=51, overlap=0)

    assert len(texts) > 1
    assert all(len(t) <= 51 for t in texts)
    assert all(unicodedata.category(t[0]) != "Mn" for t in texts)
    assert "".join(texts) == text


# --- invariants over mixed Arabic/English text -------------------------------------


@pytest.mark.parametrize(("chunk_size", "overlap"), SETTINGS)
def test_no_chunk_exceeds_chunk_size(chunk_size: int, overlap: int) -> None:
    texts = chunk_texts(MIXED_TEXT, chunk_size=chunk_size, overlap=overlap)

    assert texts
    assert all(0 < len(t) <= chunk_size for t in texts)


@pytest.mark.parametrize(("chunk_size", "overlap"), SETTINGS)
def test_every_non_whitespace_character_is_covered(chunk_size: int, overlap: int) -> None:
    spans = chunk_spans(MIXED_TEXT, chunk_size=chunk_size, overlap=overlap)

    covered = set()
    for start, end in spans:
        covered.update(range(start, end))
    missing = [i for i, char in enumerate(MIXED_TEXT) if not char.isspace() and i not in covered]

    assert missing == []


@pytest.mark.parametrize(("chunk_size", "overlap"), SETTINGS)
def test_chunks_always_move_forward(chunk_size: int, overlap: int) -> None:
    spans = chunk_spans(MIXED_TEXT, chunk_size=chunk_size, overlap=overlap)

    assert all(s2 > s1 and e2 > e1 for (s1, e1), (s2, e2) in zip(spans, spans[1:], strict=False))


@pytest.mark.parametrize(("chunk_size", "overlap"), SETTINGS)
def test_overlap_between_chunks_never_exceeds_the_limit(chunk_size: int, overlap: int) -> None:
    spans = chunk_spans(MIXED_TEXT, chunk_size=chunk_size, overlap=overlap)

    shared = [max(0, e1 - s2) for (_, e1), (s2, _) in zip(spans, spans[1:], strict=False)]

    assert all(amount <= overlap for amount in shared)


# --- validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (-5, 0), (100, -1), (100, 100), (100, 150)],
)
def test_invalid_settings_are_rejected(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="chunk_size|overlap"):
        chunk_spans("some text", chunk_size=chunk_size, overlap=overlap)


# --- documents ---------------------------------------------------------------------


def test_chunk_document_keeps_metadata_and_exact_text() -> None:
    page_one = "\n\n".join(paragraphs(40, 40, 40))
    page_two = "سياسة الإجازة السنوية.\n\nيحصل الموظف على ٢١ يومًا."
    document = Document("policy.txt", pages=(Page(1, page_one), Page(2, page_two)))

    chunks = chunk_document(document, chunk_size=90, overlap=0)

    assert {c.filename for c in chunks} == {"policy.txt"}
    assert [(c.page, c.index) for c in chunks] == [(1, 0), (1, 1), (2, 0)]
    for chunk in chunks:
        page_text = document.pages[chunk.page - 1].text
        assert chunk.text == page_text[chunk.start : chunk.end]
    assert chunks[2].text == page_two


def test_chunk_document_skips_blank_pages() -> None:
    document = Document("policy.txt", pages=(Page(1, "   \n"), Page(2, "Real content.")))

    chunks = chunk_document(document)

    assert [(c.page, c.text) for c in chunks] == [(2, "Real content.")]
