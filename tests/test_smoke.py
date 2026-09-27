import tomllib
from pathlib import Path

import bilingual_rag

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_package_is_importable_and_versioned() -> None:
    assert bilingual_rag.__version__ == "1.0.0"


def test_the_package_and_pyproject_agree_on_the_version() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["version"] == bilingual_rag.__version__
