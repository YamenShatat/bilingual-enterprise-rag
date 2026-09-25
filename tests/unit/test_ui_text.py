from bilingual_rag.generation.answer import LOW_SCORE, MODEL_REFUSED, NO_RESULTS, UNCITED
from bilingual_rag.ui.text import REFUSAL_MESSAGES, answer_html, citation_rows, refusal_message
from support.arabic import ARABIC_TRUTH


def test_every_refusal_reason_the_api_can_send_has_a_message():
    assert set(REFUSAL_MESSAGES) == {NO_RESULTS, LOW_SCORE, MODEL_REFUSED, UNCITED}


def test_a_refusal_message_is_in_english_and_arabic():
    english, arabic = refusal_message(MODEL_REFUSED).split("\n\n")
    assert english == REFUSAL_MESSAGES[MODEL_REFUSED][0]
    assert any("ARABIC" in __import__("unicodedata").name(ch, "") for ch in arabic)


def test_an_unknown_refusal_code_is_still_shown():
    assert "new_reason" in refusal_message("new_reason")


def test_markup_in_an_answer_is_escaped():
    html = answer_html('<script>alert("x")</script><img src=x onerror=alert(1)>')
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_the_browser_picks_the_text_direction():
    assert answer_html("x").startswith('<div dir="auto"')


def test_arabic_is_kept_and_line_breaks_are_shown():
    html = answer_html(f"{ARABIC_TRUTH[1]}\n{ARABIC_TRUTH[2]}")
    assert f"{ARABIC_TRUTH[1]}<br>{ARABIC_TRUTH[2]}" in html


def test_citation_rows_are_in_number_order_with_readable_columns():
    citations = [
        {"number": 2, "document_id": "b", "title": "B", "page": 3},
        {"number": 1, "document_id": "a", "title": "A", "page": 1},
    ]
    assert citation_rows(citations) == [
        {"#": 1, "Document": "A", "Page": 1, "ID": "a"},
        {"#": 2, "Document": "B", "Page": 3, "ID": "b"},
    ]
