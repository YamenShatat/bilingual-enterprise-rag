"""Session-wide pytest configuration: gate tests marked `slow` behind `--slow`.

Slow tests need a real embedding model (a multi-gigabyte download, GPU or CPU inference) and
are excluded from a plain `pytest` run so the everyday suite stays fast. Run them explicitly
with `pytest --slow`.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--slow",
        action="store_true",
        default=False,
        help="also run tests marked 'slow' (needs a real embedding model; large download)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--slow"):
        return
    skip_slow = pytest.mark.skip(reason="needs --slow (real embedding model, large download)")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
