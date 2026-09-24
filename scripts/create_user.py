"""Create a user who can log in to the API (D-024). There is no self-registration.

The password is typed twice at a hidden prompt, or read from standard input with
--password-stdin (for scripts). It is never taken as a command-line argument, which would leave
it in the shell history and the process list.

Usage (from the repository root, with the project environment active):

    python scripts/create_user.py --username admin --role admin
    python scripts/create_user.py --username sara --role employee --levels public,employee,hr
"""

import argparse
import getpass
import sys
from pathlib import Path

import psycopg

from bilingual_rag.auth.passwords import MIN_PASSWORD_LENGTH, PasswordError
from bilingual_rag.auth.users import DEFAULT_EMPLOYEE_LEVELS, ROLES, UserError, create_user
from bilingual_rag.config import ConfigError, load_database_settings
from bilingual_rag.database.connection import connect
from bilingual_rag.database.migrate import migrate

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument(
        "--levels",
        help="employees only, comma-separated (default: " + ",".join(DEFAULT_EMPLOYEE_LEVELS) + ")",
    )
    parser.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin"
    )
    return parser.parse_args(argv)


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        # PowerShell 5.1 starts piped text with a byte-order mark; it is never part of a password
        # (measured: without this, a piped password was stored with an invisible U+FEFF first).
        return sys.stdin.readline().lstrip("\N{BYTE ORDER MARK}").rstrip("\r\n")
    first = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters): ")
    if getpass.getpass("Repeat the password: ") != first:
        raise PasswordError("the two passwords differ")
    return first


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.role == "admin" and args.levels:
        print("error: an admin always gets every access level; drop --levels", file=sys.stderr)
        return 2
    levels = None
    if args.levels:
        levels = [part.strip() for part in args.levels.split(",") if part.strip()]
    try:
        settings = load_database_settings(ENV_FILE)
        password = read_password(args.password_stdin)
    except (ConfigError, PasswordError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        with connect(settings) as conn:
            migrate(conn)
            user = create_user(conn, args.username, password, role=args.role, access_levels=levels)
    except psycopg.OperationalError as exc:
        print(f"cannot connect: {str(exc).strip()}", file=sys.stderr)
        return 1
    except (UserError, PasswordError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    levels_text = ", ".join(user.access_levels)
    print(f"created {user.role} {user.username!r} (id {user.id}) with access to: {levels_text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
