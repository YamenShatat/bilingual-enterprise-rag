"""The corpus manifest, and the guarantees the synthetic documents make about themselves."""

import json
import re
from pathlib import Path

import pytest

from bilingual_rag.corpus.markdown_docx import Heading, Table, expected_pages, parse_markdown, plain
from bilingual_rag.ingestion.loaders import load_document

DATA = Path(__file__).resolve().parents[2] / "data"
CORPUS = DATA / "synthetic"
DOCS = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))["documents"]
BY_PATH = {doc["path"]: doc for doc in DOCS}
IDS = [doc["id"] for doc in DOCS]

DEPARTMENTS = {
    "hr",
    "it",
    "security",
    "finance",
    "operations",
    "legal",
    "engineering",
    "general",
}
FORMATS = {"md", "txt", "docx", "pdf"}
ACCESS_LEVELS = {"public", "employee", "engineering", "hr", "management"}
DIGIT_STYLES = {None, "arabic-indic", "western"}

# Measured source-word recall of the Word PDFs (see docs/dataset.md): 100% and 84%. The
# floors sit a little below, so they flag real regressions, not noise.
PDF_MIN_SOURCE_RECALL = {"en": 0.95, "ar": 0.80}
MIN_ARABIC_LETTER_RATIO = 0.85  # the lowest Arabic document measures 0.899

# Things the corpus deliberately does NOT contain, so "not found" answers can be tested.
ABSENT_TERMS = [
    "stock option",
    "share option",
    "pension",
    "relocation",
    "خيارات الأسهم",
    "معاش تقاعدي",
    "بدل الانتقال",
]
EXECUTIVE_TERMS = ("ceo", "chief executive", "الرئيس التنفيذي", "المدير التنفيذي")
PAY_TERMS = (
    "salary",
    "salaries",
    "pay ",
    "compensation",
    "bonus",
    "راتب",
    "رواتب",
    "أجر",
    "مكافأة",
)

_INDIC_TO_WESTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def text_of(doc: dict) -> str:
    return "\n".join(page.text for page in load_document(CORPUS / doc["path"]).pages)


def markdown_of(doc: dict) -> str:
    """The Markdown a document was written from: its source, or the file itself."""
    if doc["source"]:
        return (DATA / doc["source"]).read_text(encoding="utf-8")
    return (CORPUS / doc["path"]).read_text(encoding="utf-8")


def arabic_letter_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    arabic = [char for char in letters if 0x0600 <= ord(char) <= 0x06FF]
    return len(arabic) / len(letters) if letters else 0.0


def numbers_in(markdown: str) -> set[str]:
    """Distinct numbers, with digit styles unified. 1 and 2 are ignored: Arabic writes them as
    words ("ساعة واحدة", "يومين"), so they cannot be compared literally."""
    text = markdown.translate(_INDIC_TO_WESTERN).replace("٫", ".")
    text = re.sub(r"(?<=\d)[,٬](?=\d)", "", text)
    return set(re.findall(r"\d+(?:\.\d+)?", text)) - {"1", "2"}


def structure_of(markdown: str) -> tuple[int, int, int]:
    blocks = parse_markdown(markdown)
    headings = sum(isinstance(b, Heading) and b.level == 2 for b in blocks)
    tables = [b for b in blocks if isinstance(b, Table)]
    return headings, len(tables), sum(len(t.rows) for t in tables)


def source_words(markdown: str) -> list[str]:
    cleaned = re.sub(r"[.,،:;؛؟?!()|*#\-•]", " ", plain(markdown))
    return [word for word in cleaned.split() if word]


# --- the manifest matches the files ---------------------------------------------------


def test_manifest_lists_exactly_the_files_on_disk() -> None:
    on_disk = {p.relative_to(CORPUS).as_posix() for p in CORPUS.rglob("*") if p.is_file()}

    assert on_disk == set(BY_PATH)


def test_ids_and_paths_are_unique() -> None:
    assert len(set(IDS)) == len(DOCS)
    assert len(BY_PATH) == len(DOCS)


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_every_field_is_valid_and_consistent_with_the_path(doc: dict) -> None:
    department, filename = doc["path"].split("/")
    stem, extension = filename.rsplit(".", 1)

    assert doc["department"] == department
    assert department in DEPARTMENTS
    assert doc["format"] == extension
    assert extension in FORMATS
    assert doc["access_level"] in ACCESS_LEVELS
    assert doc["digits"] in DIGIT_STYLES
    assert doc["language"] in {"en", "ar"}
    assert stem.endswith(f"_{doc['language']}")
    assert doc["id"] == f"{department}-{doc['topic']}-{doc['language']}"
    assert (doc["digits"] is None) == (doc["language"] == "en")


def test_generated_formats_have_a_source_and_every_source_is_used() -> None:
    generated = [doc for doc in DOCS if doc["format"] in {"docx", "pdf"}]
    assert all(doc["source"] and (DATA / doc["source"]).is_file() for doc in generated)
    assert all(doc["source"] is None for doc in DOCS if doc["format"] in {"md", "txt"})
    used = {doc["source"] for doc in generated}
    on_disk = {p.relative_to(DATA).as_posix() for p in (DATA / "sources").rglob("*.md")}
    assert on_disk == used


@pytest.mark.parametrize("doc", [d for d in DOCS if d["format"] == "docx"], ids=lambda d: d["id"])
def test_committed_docx_files_match_their_markdown_sources(doc: dict) -> None:
    pages = [page.text for page in load_document(CORPUS / doc["path"]).pages]

    assert pages == expected_pages(parse_markdown(markdown_of(doc)))


@pytest.mark.parametrize("doc", [d for d in DOCS if d["format"] == "pdf"], ids=lambda d: d["id"])
def test_word_pdfs_keep_the_measured_share_of_their_source_words(doc: dict) -> None:
    expected = source_words(markdown_of(doc))
    found = set(source_words(text_of(doc)))
    recall = sum(word in found for word in expected) / len(expected)

    assert recall >= PDF_MIN_SOURCE_RECALL[doc["language"]]


# --- language, digits and titles ----------------------------------------------------


@pytest.mark.parametrize("doc", DOCS, ids=IDS)
def test_each_document_is_written_in_its_declared_language(doc: dict) -> None:
    ratio = arabic_letter_ratio(text_of(doc))

    if doc["language"] == "en":
        assert ratio == 0.0
    else:
        assert ratio >= MIN_ARABIC_LETTER_RATIO


@pytest.mark.parametrize("doc", [d for d in DOCS if d["language"] == "ar"], ids=lambda d: d["id"])
def test_arabic_documents_use_only_their_declared_digit_style(doc: dict) -> None:
    text = text_of(doc)
    has_western = any(char in "0123456789" for char in text)
    has_indic = any(char in ARABIC_INDIC_DIGITS for char in text)

    if doc["digits"] == "arabic-indic":
        assert has_indic and not has_western
    else:
        assert has_western and not has_indic


@pytest.mark.parametrize("doc", [d for d in DOCS if d["format"] != "pdf"], ids=lambda d: d["id"])
def test_the_manifest_title_is_the_document_title(doc: dict) -> None:
    first_line = next(line for line in text_of(doc).splitlines() if line.strip())

    assert first_line.removeprefix("# ").strip().casefold() == doc["title"].casefold()


# --- pairs ---------------------------------------------------------------------------


PAIRED = [doc for doc in DOCS if doc["pair"]]


@pytest.mark.parametrize("doc", PAIRED, ids=lambda d: d["id"])
def test_pairs_are_symmetric_and_share_topic_department_and_access(doc: dict) -> None:
    other = BY_PATH[doc["pair"]]

    assert other["pair"] == doc["path"]
    assert other["language"] != doc["language"]
    assert (other["topic"], other["department"], other["access_level"]) == (
        doc["topic"],
        doc["department"],
        doc["access_level"],
    )


@pytest.mark.parametrize("doc", [d for d in PAIRED if d["language"] == "en"], ids=lambda d: d["id"])
def test_paired_documents_state_the_same_numbers_in_the_same_structure(doc: dict) -> None:
    english = markdown_of(doc)
    arabic = markdown_of(BY_PATH[doc["pair"]])

    only_english = numbers_in(english) - numbers_in(arabic)
    only_arabic = numbers_in(arabic) - numbers_in(english)
    assert (only_english, only_arabic) == (set(), set()), "the two versions disagree on numbers"
    assert structure_of(english) == structure_of(arabic), "headings/tables differ between versions"


# --- designed gaps -------------------------------------------------------------------


def test_facts_that_are_meant_to_be_missing_really_are_missing() -> None:
    corpus_text = "\n".join(text_of(doc) for doc in DOCS).casefold()

    present = [term for term in ABSENT_TERMS if term.casefold() in corpus_text]
    assert present == []


def test_no_document_states_an_executive_salary() -> None:
    offending = []
    for doc in DOCS:
        for line in text_of(doc).casefold().splitlines():
            if any(t in line for t in EXECUTIVE_TERMS) and any(t in line for t in PAY_TERMS):
                offending.append((doc["path"], line))
    assert offending == []


# --- coverage the corpus was designed for --------------------------------------------


def test_corpus_covers_every_scenario_it_was_designed_for() -> None:
    english_only = [d for d in DOCS if d["language"] == "en" and not d["pair"]]
    arabic_only = [d for d in DOCS if d["language"] == "ar" and not d["pair"]]

    assert len(DOCS) >= 30
    assert {d["format"] for d in DOCS} == FORMATS
    assert {d["department"] for d in DOCS} == DEPARTMENTS
    assert {d["access_level"] for d in DOCS} == ACCESS_LEVELS
    assert len(PAIRED) >= 16  # at least 8 bilingual pairs
    assert len(english_only) >= 5 and len(arabic_only) >= 4
    assert {d["digits"] for d in DOCS if d["language"] == "ar"} == {"arabic-indic", "western"}
    assert any(d["language"] == "ar" and d["format"] == "pdf" for d in DOCS)
