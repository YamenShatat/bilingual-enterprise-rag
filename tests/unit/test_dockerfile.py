"""Guard the security-relevant lines of the Dockerfile and .dockerignore."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
IGNORE = [
    line.strip()
    for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]


def test_the_base_image_is_pinned_by_digest():
    (base,) = re.findall(r"^FROM\s+(\S+)", DOCKERFILE, flags=re.MULTILINE)
    assert re.fullmatch(r"python:[\d.]+-slim-\w+@sha256:[0-9a-f]{64}", base), base


def test_the_container_does_not_run_as_root():
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, flags=re.MULTILINE)
    assert users and users[-1] not in {"root", "0"}


def test_models_are_never_downloaded_at_run_time():
    assert "HF_HUB_OFFLINE=1" in DOCKERFILE


def test_dockerignore_is_an_allowlist_that_keeps_secrets_out():
    assert IGNORE[0] == "*"  # everything is excluded first
    allowed = {line[1:].rstrip("/") for line in IGNORE if line.startswith("!")}
    assert allowed == {
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "src",
        "scripts",
        "data",
        ".streamlit",
    }
    assert not any(name.startswith(".env") or "HANDOFF" in name for name in allowed)
