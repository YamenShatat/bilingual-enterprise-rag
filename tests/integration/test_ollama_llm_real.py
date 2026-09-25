"""Real-model tests: needs Ollama running with qwen3:8b pulled, and `pytest --slow`.

Skipped (not failed) when Ollama is not running or the model is missing, so a machine without
Ollama can still run `pytest --slow` for the embedding models.
"""

import json
import unicodedata
import urllib.request

import pytest

from bilingual_rag.generation.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, OllamaLLM

pytestmark = pytest.mark.slow

SYSTEM = (
    "Answer ONLY from the sources. Reply in the language of the question. "
    "If the sources do not contain the answer, reply exactly: INSUFFICIENT_EVIDENCE"
)
SOURCES = (
    "[hr-ar, p.1] يحق للموظف الحصول على إجازة سنوية مدفوعة الأجر مدتها ثلاثون يومًا بعد إتمام "
    "سنة كاملة من الخدمة.\n"
    "[hr-en, p.1] Employees receive 30 days of paid annual leave after one full year of service."
)


@pytest.fixture(scope="module")
def llm():
    try:
        with urllib.request.urlopen(f"{DEFAULT_BASE_URL}/api/tags", timeout=3) as response:
            names = {model["name"] for model in json.load(response)["models"]}
    except OSError:
        pytest.skip(f"Ollama is not running at {DEFAULT_BASE_URL}")
    if DEFAULT_MODEL not in names:
        pytest.skip(f"{DEFAULT_MODEL} is not pulled (`ollama pull {DEFAULT_MODEL}`)")
    return OllamaLLM()


def ask(llm, question):
    return llm.generate(SYSTEM, f"Sources:\n{SOURCES}\n\nQuestion: {question}")


def test_english_answer_comes_from_the_source(llm):
    assert "30" in ask(llm, "How many days of annual leave do employees get?")


def test_arabic_question_gets_an_arabic_answer_from_the_source(llm):
    answer = ask(llm, "كم يومًا من الإجازة السنوية يحصل عليها الموظف؟")
    arabic_letters = sum("ARABIC" in unicodedata.name(ch, "") for ch in answer)
    assert arabic_letters > len(answer) / 3, answer
    assert any(form in answer for form in ("30", "٣٠", "ثلاثون", "ثلاثين")), answer


def test_absent_fact_is_refused(llm):
    assert "INSUFFICIENT_EVIDENCE" in ask(llm, "What is the remote-work policy?")


def test_same_question_gives_the_same_answer(llm):
    question = "How many days of annual leave do employees get?"
    assert ask(llm, question) == ask(llm, question)
