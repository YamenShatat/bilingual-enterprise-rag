"""The evaluation question set: one fixed question per expected document (or none)."""

import json
from dataclasses import dataclass
from pathlib import Path

from bilingual_rag.ingestion.manifest import LANGUAGES

CATEGORIES = ("same_language", "cross_lingual", "near_miss", "absent_fact")


class QuestionsError(ValueError):
    """The question set, or one of its entries, is invalid."""


@dataclass(frozen=True, slots=True)
class EvaluationQuestion:
    """One question. ``expected_document_id`` is None for a deliberately unanswerable
    question (``category == "absent_fact"``): nothing in the corpus should score highly."""

    id: str
    language: str
    category: str
    question: str
    expected_document_id: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.question.strip():
            raise QuestionsError(f"{self.id!r}: id and question must be non-empty")
        if self.language not in LANGUAGES:
            raise QuestionsError(f"{self.id}: language must be one of {LANGUAGES}")
        if self.category not in CATEGORIES:
            raise QuestionsError(f"{self.id}: category must be one of {CATEGORIES}")
        if self.category == "absent_fact" and self.expected_document_id is not None:
            raise QuestionsError(f"{self.id}: absent_fact questions must have no expected document")
        if self.category != "absent_fact" and self.expected_document_id is None:
            raise QuestionsError(
                f"{self.id}: expected_document_id is required for {self.category!r}"
            )


def parse_questions(data: object) -> tuple[EvaluationQuestion, ...]:
    """
    Raises:
        QuestionsError: the structure is wrong, an entry is invalid, or an id is duplicated.
    """
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise QuestionsError('expected an object with a "questions" list')

    questions = []
    seen_ids: set[str] = set()
    for position, entry in enumerate(data["questions"], start=1):
        if not isinstance(entry, dict):
            raise QuestionsError(f"question {position} must be an object")
        try:
            question = EvaluationQuestion(**entry)
        except TypeError as exc:
            raise QuestionsError(f"question {position}: {exc}") from exc
        if question.id in seen_ids:
            raise QuestionsError(f"duplicate id {question.id!r}")
        seen_ids.add(question.id)
        questions.append(question)
    return tuple(questions)


def load_questions(path: Path) -> tuple[EvaluationQuestion, ...]:
    """
    Raises:
        QuestionsError: the file is not valid UTF-8 JSON, or fails `parse_questions`.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuestionsError(f"{path} is not valid UTF-8 JSON: {exc}") from exc
    return parse_questions(data)
