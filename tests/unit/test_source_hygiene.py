"""Guard against raw invisible characters in source code.

Invisible characters (zero-width, bidi marks, exotic spaces, control codes) must be
written as ``\\N{NAME}`` escapes so reviewers can see them. Printable Arabic letters,
tatweel and diacritics are fine and are not flagged.
"""

import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("src", "tests")

# Cf: format (zero-width, bidi), Zl/Zp: line/paragraph separators, Cc: control codes.
_INVISIBLE_CATEGORIES = {"Cf", "Zl", "Zp", "Cc"}
_ALLOWED_WHITESPACE = {"\n", "\t"}


def _invisible_characters(text: str) -> list[tuple[int, str]]:
    found = []
    for line_number, line in enumerate(text.split("\n"), start=1):
        for char in line:
            is_odd_space = unicodedata.category(char) == "Zs" and char != " "
            is_invisible = unicodedata.category(char) in _INVISIBLE_CATEGORIES
            if (is_invisible or is_odd_space) and char not in _ALLOWED_WHITESPACE:
                found.append((line_number, f"U+{ord(char):04X} {unicodedata.name(char, '?')}"))
    return found


def test_source_files_contain_no_raw_invisible_characters() -> None:
    problems = []
    for directory in SOURCE_DIRS:
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for line_number, description in _invisible_characters(text):
                problems.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {description}")

    assert not problems, "Use \\N{NAME} escapes instead:\n" + "\n".join(problems)


def test_hygiene_check_detects_invisible_characters() -> None:
    """The guard itself must be able to fail, otherwise it proves nothing."""
    sample = "ok\nhidden\N{ZERO WIDTH SPACE}here\nnbsp\N{NO-BREAK SPACE}here\nبسيط"

    assert [line for line, _ in _invisible_characters(sample)] == [2, 3]
