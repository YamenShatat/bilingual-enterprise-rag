import pytest

from bilingual_rag.ingestion.cleaner import clean_document, clean_text
from bilingual_rag.ingestion.models import Document, Page

# Special characters use \N{NAME} escapes so the source shows what is being tested.

# Includes tatweel (ـ), diacritics, tanween, Arabic-Indic digits and every letter variant.
ARABIC_SAMPLE = "الســلام عَلَيْكُم، الإجازة السنوية ٢١ يومًا؛ أ إ آ ة ى ي، مُدَّة"

SOFT_HYPHEN = "\N{SOFT HYPHEN}"
ZERO_WIDTH_SPACE = "\N{ZERO WIDTH SPACE}"
WORD_JOINER = "\N{WORD JOINER}"
BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"
NBSP = "\N{NO-BREAK SPACE}"


def test_all_line_break_styles_become_newline() -> None:
    text = "a\r\nb\rc\x0cd\N{LINE SEPARATOR}e\N{PARAGRAPH SEPARATOR}f"

    assert clean_text(text) == "a\nb\nc\nd\ne\nf"


def test_removes_invisible_characters_inside_arabic_words() -> None:
    hidden = f"الإ{SOFT_HYPHEN}{ZERO_WIDTH_SPACE}ج{WORD_JOINER}ا{BOM}زة"

    assert clean_text(hidden) == "الإجازة"


@pytest.mark.parametrize(
    "joiner",
    ["\N{ZERO WIDTH NON-JOINER}", "\N{ZERO WIDTH JOINER}"],
    ids=["ZWNJ", "ZWJ"],
)
def test_keeps_joiners_that_are_meaningful_in_arabic_script(joiner: str) -> None:
    text = f"ب{joiner}ب"

    assert clean_text(text) == text


@pytest.mark.parametrize(
    "mark",
    ["\N{LEFT-TO-RIGHT MARK}", "\N{RIGHT-TO-LEFT MARK}", "\N{ARABIC LETTER MARK}"],
    ids=["LRM", "RLM", "ALM"],
)
def test_keeps_direction_marks_until_evaluated(mark: str) -> None:
    text = f"VPN{mark} سياسة"

    assert clean_text(text) == text


def test_removes_control_characters() -> None:
    assert clean_text("a\x00b\x07c\x1bd\x7fe\x9ff") == "abcdef"


def test_collapses_tabs_and_unicode_spaces_to_one_space() -> None:
    text = f"a\t\tb{NBSP}{NBSP}c\N{IDEOGRAPHIC SPACE}d   e"

    assert clean_text(text) == "a b c d e"


def test_trims_spaces_around_newlines() -> None:
    assert clean_text("first line  \n  second line") == "first line\nsecond line"


def test_whitespace_only_line_becomes_a_paragraph_break() -> None:
    assert clean_text("a\n \t \nb") == "a\n\nb"


def test_keeps_paragraph_break_but_collapses_excess_blank_lines() -> None:
    assert clean_text("one\n\ntwo") == "one\n\ntwo"
    assert clean_text("one\n\n\n\n\ntwo") == "one\n\ntwo"


def test_strips_leading_and_trailing_whitespace() -> None:
    assert clean_text("  \n hello \n\n") == "hello"


@pytest.mark.parametrize("text", ["", "   ", f" \n\t{NBSP} \r\n"], ids=["empty", "spaces", "mixed"])
def test_blank_input_becomes_empty_string(text: str) -> None:
    assert clean_text(text) == ""


def test_arabic_text_is_left_intact() -> None:
    assert clean_text(ARABIC_SAMPLE) == ARABIC_SAMPLE


@pytest.mark.parametrize("letter", ["أ", "إ", "آ", "ة", "ى", "ي", "\N{ARABIC TATWEEL}"])
def test_arabic_letters_and_tatweel_are_not_folded_or_removed(letter: str) -> None:
    text = f"كلمة {letter} كلمة"

    assert clean_text(text) == text


def test_unicode_is_not_normalized() -> None:
    # alef + combining hamza above: NFC would compose these into the single letter أ.
    decomposed = "\N{ARABIC LETTER ALEF}\N{ARABIC HAMZA ABOVE}"

    assert clean_text(decomposed) == decomposed
    assert len(clean_text(decomposed)) == 2


def test_arabic_presentation_forms_are_left_for_the_pdf_stage() -> None:
    # NFKC would rewrite this to the two letters لا; deferred until we see real PDF output.
    lam_alef_isolated = "\N{ARABIC LIGATURE LAM WITH ALEF ISOLATED FORM}"

    assert clean_text(lam_alef_isolated) == lam_alef_isolated


def test_mixed_arabic_and_english_is_left_intact() -> None:
    text = "سياسة VPN: استخدم Cisco AnyConnect (Remote Work)."

    assert clean_text(text) == text


def test_cleaning_is_idempotent() -> None:
    messy = f"  الإ{ZERO_WIDTH_SPACE}جازة \t السنوية\r\n\r\n\r\n\r\n21{NBSP}days\x00 \n \n  end  "

    once = clean_text(messy)

    assert clean_text(once) == once


def test_clean_document_cleans_every_page_and_keeps_metadata() -> None:
    original = Document(
        filename="policy.txt",
        pages=(Page(1, "  a\r\nb "), Page(2, f" ب  ب{ZERO_WIDTH_SPACE}")),
    )

    cleaned = clean_document(original)

    assert cleaned.filename == "policy.txt"
    assert [p.number for p in cleaned.pages] == [1, 2]
    assert [p.text for p in cleaned.pages] == ["a\nb", "ب ب"]
    assert original.pages[0].text == "  a\r\nb "  # the input is not mutated
