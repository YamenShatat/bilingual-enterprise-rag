"""Session-wide pytest configuration: gate tests marked `slow` behind `--slow`.

Slow tests need a real embedding model or a local Ollama LLM (multi-gigabyte downloads, GPU or
CPU inference) and are excluded from a plain `pytest` run so the everyday suite stays fast. Run
them explicitly with `pytest --slow`.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--slow",
        action="store_true",
        default=False,
        help="also run tests marked 'slow' (real embedding model or Ollama LLM; large download)",
    )


@pytest.fixture(autouse=True)
def fast_password_hashing(request, monkeypatch):
    """Real scrypt costs 0.36 s and 128 MiB per hash by design (D-024), too slow for tests that
    create users. Each hash records its own cost, so a cheap one still verifies correctly. Mark a
    test `real_password_cost` to keep the production setting."""
    if "real_password_cost" in request.keywords:
        return
    from bilingual_rag.auth import passwords

    monkeypatch.setattr(passwords, "N", 2**8)


def pytest_collection_modifyitems(config, items):
    if config.getoption("--slow"):
        return
    skip_slow = pytest.mark.skip(reason="needs --slow (real embedding model or Ollama LLM)")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
