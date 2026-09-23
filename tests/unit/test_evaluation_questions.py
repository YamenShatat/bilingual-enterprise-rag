import json
import unicodedata
from pathlib import Path

import pytest

from bilingual_rag.evaluation.questions import (
    EvaluationQuestion,
    QuestionsError,
    load_questions,
    parse_questions,
)

REAL_FILE = Path(__file__).resolve().parents[2] / "data" / "evaluation_questions.json"


def _entry(**overrides):
    entry = {
        "id": "q1",
        "language": "en",
        "category": "same_language",
        "question": "How many days of annual leave?",
        "expected_document_id": "hr-annual-leave-policy-en",
    }
    return entry | overrides


def _questions(*entries):
    return {"questions": list(entries)}


class TestRealQuestionSet:
    def test_the_committed_question_set_is_valid(self):
        questions = load_questions(REAL_FILE)
        assert len(questions) == 50

    def test_language_split_is_25_25(self):
        questions = load_questions(REAL_FILE)
        assert sum(q.language == "en" for q in questions) == 25
        assert sum(q.language == "ar" for q in questions) == 25

    def test_arabic_text_is_kept_exactly(self):
        questions = load_questions(REAL_FILE)
        arabic = next(q for q in questions if q.language == "ar")
        assert any("ARABIC" in unicodedata.name(ch, "") for ch in arabic.question)

    def test_absent_fact_questions_have_no_expected_document(self):
        questions = load_questions(REAL_FILE)
        absent = [q for q in questions if q.category == "absent_fact"]
        assert absent
        assert all(q.expected_document_id is None for q in absent)


class TestParseQuestions:
    def test_a_valid_entry_becomes_a_question(self):
        (question,) = parse_questions(_questions(_entry()))
        assert question == EvaluationQuestion(
            id="q1",
            language="en",
            category="same_language",
            question="How many days of annual leave?",
            expected_document_id="hr-annual-leave-policy-en",
        )

    def test_notes_is_optional(self):
        (question,) = parse_questions(_questions(_entry(notes="a hint")))
        assert question.notes == "a hint"

    @pytest.mark.parametrize("data", [[], {}, {"questions": "x"}, {"questions": [1]}, None])
    def test_a_malformed_structure_is_rejected(self, data):
        with pytest.raises(QuestionsError):
            parse_questions(data)

    @pytest.mark.parametrize("language", ["fr", "EN", ""])
    def test_an_invalid_language_is_rejected(self, language):
        with pytest.raises(QuestionsError, match="language"):
            parse_questions(_questions(_entry(language=language)))

    @pytest.mark.parametrize("category", ["absent", "SAME_LANGUAGE", ""])
    def test_an_invalid_category_is_rejected(self, category):
        with pytest.raises(QuestionsError, match="category"):
            parse_questions(_questions(_entry(category=category)))

    def test_an_empty_id_or_question_is_rejected(self):
        with pytest.raises(QuestionsError):
            parse_questions(_questions(_entry(id="  ")))
        with pytest.raises(QuestionsError):
            parse_questions(_questions(_entry(question="")))

    def test_absent_fact_with_an_expected_document_is_rejected(self):
        entry = _entry(category="absent_fact", expected_document_id="hr-annual-leave-policy-en")
        with pytest.raises(QuestionsError, match="absent_fact"):
            parse_questions(_questions(entry))

    def test_absent_fact_without_an_expected_document_is_accepted(self):
        entry = _entry(category="absent_fact", expected_document_id=None)
        (question,) = parse_questions(_questions(entry))
        assert question.expected_document_id is None

    @pytest.mark.parametrize("category", ["same_language", "cross_lingual", "near_miss"])
    def test_a_non_absent_category_requires_an_expected_document(self, category):
        entry = _entry(category=category, expected_document_id=None)
        with pytest.raises(QuestionsError, match="expected_document_id"):
            parse_questions(_questions(entry))

    def test_an_unknown_field_is_rejected(self):
        with pytest.raises(QuestionsError):
            parse_questions(_questions(_entry(extra_field="x")))

    def test_duplicate_ids_are_rejected(self):
        with pytest.raises(QuestionsError, match="duplicate id"):
            parse_questions(_questions(_entry(), _entry()))

    def test_the_error_names_the_offending_question(self):
        with pytest.raises(QuestionsError, match="q2"):
            parse_questions(_questions(_entry(), _entry(id="q2", language="bad")))

    def test_an_unknown_fields_error_names_the_question_position(self):
        with pytest.raises(QuestionsError, match="question 2"):
            parse_questions(_questions(_entry(), _entry(id="q2", extra_field="x")))


class TestLoadQuestions:
    def test_invalid_json_is_an_error(self, tmp_path):
        path = tmp_path / "q.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(QuestionsError, match="not valid"):
            load_questions(path)

    def test_invalid_utf8_is_an_error(self, tmp_path):
        path = tmp_path / "q.json"
        path.write_bytes(b'{"questions": [\xff]}')
        with pytest.raises(QuestionsError, match="not valid"):
            load_questions(path)

    def test_a_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_questions(tmp_path / "absent.json")

    def test_arabic_survives_a_written_file(self, tmp_path):
        path = tmp_path / "q.json"
        entry = _entry(id="q-ar", language="ar", question="كم يوما من الإجازة السنوية؟")
        path.write_text(json.dumps(_questions(entry), ensure_ascii=False), encoding="utf-8")
        assert load_questions(path)[0].question == "كم يوما من الإجازة السنوية؟"
