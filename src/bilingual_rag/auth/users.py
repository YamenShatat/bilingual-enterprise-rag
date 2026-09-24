"""Store, look up and authenticate users (migration 0004). Nothing here commits: the caller owns
the transaction, as in ``database.repository``."""

import re
from collections.abc import Collection
from dataclasses import dataclass

import psycopg

from bilingual_rag.auth.passwords import hash_password, verify_password
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS

ROLES = ("admin", "employee")
DEFAULT_EMPLOYEE_LEVELS = ("public", "employee")
_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")  # the same rule as the table's CHECK

# Checked when a username does not exist, so a failed login takes as long either way and the
# response time does not reveal which usernames exist. Made once, lazily (hashing is slow).
_dummy_hash: str | None = None


class UserError(ValueError):
    """A user cannot be created as asked (bad role, unknown level, taken username...)."""


@dataclass(frozen=True, slots=True)
class User:
    id: int
    username: str
    role: str
    access_levels: tuple[str, ...]
    active: bool

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _row_to_user(row) -> User:
    return User(id=row[0], username=row[1], role=row[2], access_levels=tuple(row[3]), active=row[4])


def create_user(
    conn: psycopg.Connection,
    username: str,
    password: str,
    *,
    role: str,
    access_levels: Collection[str] | None = None,
) -> User:
    """Create a user. An admin always gets every access level; an employee gets
    ``access_levels`` (default: public and employee).

    Raises:
        UserError: unknown role or level, levels given for an admin, or the username is taken
            or invalid.
        PasswordError: the password is too short.
    """
    if role not in ROLES:
        raise UserError(f"role must be one of {ROLES}, got {role!r}")
    if role == "admin":
        if access_levels is not None:
            raise UserError("an admin always gets every access level; do not pass levels")
        levels = list(ACCESS_LEVELS)
    else:
        if isinstance(access_levels, str):
            raise UserError("access_levels must be a collection of levels, not one string")
        levels = sorted(set(DEFAULT_EMPLOYEE_LEVELS if access_levels is None else access_levels))
        unknown = set(levels) - set(ACCESS_LEVELS)
        if unknown:
            raise UserError(f"unknown access levels {sorted(unknown)}; expected {ACCESS_LEVELS}")
    # Checked here first so the usual mistakes never abort the caller's transaction; the
    # database's own constraints remain the final guard (and catch a simultaneous insert).
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        raise UserError(
            f"invalid username {username!r}: 3-64 characters, lowercase letters, digits, "
            "'.', '_' or '-', starting with a letter or digit"
        )
    if conn.execute("SELECT 1 FROM users WHERE username = %s", (username,)).fetchone():
        raise UserError(f"username {username!r} is taken")
    password_hash = hash_password(password)
    try:
        row = conn.execute(
            "INSERT INTO users (username, password_hash, role, access_levels)"
            " VALUES (%s, %s, %s, %s)"
            " RETURNING id, username, role, access_levels, active",
            (username, password_hash, role, levels),
        ).fetchone()
    except psycopg.errors.UniqueViolation as exc:  # created by someone else just now
        raise UserError(f"username {username!r} is taken") from exc
    return _row_to_user(row)


def get_user(conn: psycopg.Connection, user_id: int) -> User | None:
    row = conn.execute(
        "SELECT id, username, role, access_levels, active FROM users WHERE id = %s", (user_id,)
    ).fetchone()
    return None if row is None else _row_to_user(row)


def authenticate(conn: psycopg.Connection, username: str, password: str) -> User | None:
    """The active user with this username and password, or None. The same work is done
    whether or not the username exists, and a deactivated user cannot log in."""
    global _dummy_hash
    row = conn.execute(
        "SELECT id, username, role, access_levels, active, password_hash FROM users"
        " WHERE username = %s",
        (username,),
    ).fetchone()
    if row is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password("not-a-real-password-just-timing")
        verify_password(password, _dummy_hash)
        return None
    user = _row_to_user(row[:5])
    if not verify_password(password, row[5]) or not user.active:
        return None
    return user
