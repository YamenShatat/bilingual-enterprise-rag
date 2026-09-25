"""scripts/create_user.py: reading the password and refusing bad arguments (no database)."""

import importlib.util
import io
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "create_user.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("create_user_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "piped",
    [
        "correct horse battery\n",
        "correct horse battery\r\n",
        "\N{BYTE ORDER MARK}correct horse battery\r\n",  # what PowerShell 5.1 pipes
        "correct horse battery",
    ],
)
def test_a_piped_password_loses_only_the_bom_and_line_ending(script, monkeypatch, piped):
    monkeypatch.setattr("sys.stdin", io.StringIO(piped))
    assert script.read_password(from_stdin=True) == "correct horse battery"


def test_spaces_inside_and_around_a_password_are_kept(script, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("  two  spaces  \n"))
    assert script.read_password(from_stdin=True) == "  two  spaces  "


def test_the_prompt_asks_twice_and_refuses_a_mismatch(script, monkeypatch):
    answers = iter(["first password", "second password"])
    monkeypatch.setattr(script.getpass, "getpass", lambda prompt: next(answers))
    with pytest.raises(script.PasswordError, match="differ"):
        script.read_password(from_stdin=False)


def test_levels_for_an_admin_are_refused_before_anything_else(script, capsys):
    assert script.main(["--username", "x-admin", "--role", "admin", "--levels", "hr"]) == 2
    assert "every access level" in capsys.readouterr().err
