"""Login tokens: JWTs signed with HS256 (D-024).

A token names a user (``sub``) and expires; it carries no permissions. The API reads the user's
role and access levels from the database on every request, so a changed permission or a
deactivated user takes effect at once instead of when the token expires.

Only HS256 is accepted when decoding. Letting the token choose its own algorithm (``none``, or
an asymmetric one with the secret as a "public key") is the classic JWT mistake.
"""

import time

import jwt

ALGORITHM = "HS256"
DEFAULT_TTL_SECONDS = 60 * 60
MIN_SECRET_LENGTH = 32


class TokenError(Exception):
    """A token is missing, malformed, badly signed or expired."""


def _check_secret(secret: str) -> None:
    if not isinstance(secret, str) or len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"the signing secret needs at least {MIN_SECRET_LENGTH} characters")


def issue_token(user_id: int, secret: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """
    Raises:
        ValueError: the secret is too short, or ``ttl_seconds`` is not positive.
    """
    _check_secret(secret)
    if ttl_seconds < 1:
        raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
    now = int(time.time())
    claims = {"sub": str(user_id), "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def read_token(token: str, secret: str) -> int:
    """The user id a valid token names.

    Raises:
        TokenError: the token is malformed, not signed with ``secret`` using HS256, expired, or
            missing ``sub``, ``iat`` or ``exp``.
        ValueError: the secret is too short.
    """
    _check_secret(secret)
    try:
        claims = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": ["sub", "iat", "exp"]}
        )
        return int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise TokenError("invalid or expired token") from exc
