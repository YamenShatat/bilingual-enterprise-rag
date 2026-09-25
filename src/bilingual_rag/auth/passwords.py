"""Password hashing with the standard library's scrypt, no dependency (D-024).

scrypt is memory-hard: every guess costs about 128 MiB and a fraction of a second, which is what
makes a stolen hash expensive to brute-force. The parameters are OWASP's recommendation for
scrypt (N=2**17, r=8, p=1) and are stored with each hash, so they can be raised later without
breaking existing passwords.
"""

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**17, 8, 1
SALT_BYTES = 16
KEY_BYTES = 32
MIN_PASSWORD_LENGTH = 12
_MAXMEM = 256 * 1024 * 1024  # scrypt needs 128 * N * r bytes = 128 MiB at these settings


class PasswordError(ValueError):
    """A password is too weak, or a stored hash is malformed."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=KEY_BYTES
    )


def hash_password(password: str) -> str:
    """``scrypt$n$r$p$salt$hash`` with a fresh random salt.

    Raises:
        PasswordError: the password is not a string of at least ``MIN_PASSWORD_LENGTH``
            characters.
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordError(f"a password needs at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(SALT_BYTES)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(_scrypt(password, salt, N, R, P))}"


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` matches ``stored``, compared in constant time.

    Raises:
        PasswordError: ``stored`` is not a hash made by ``hash_password``.
    """
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            raise ValueError(scheme)
        salt_bytes, expected_bytes = base64.b64decode(salt), base64.b64decode(expected)
        n, r, p = int(n), int(r), int(p)
    except (ValueError, AttributeError) as exc:
        raise PasswordError("not a scrypt password hash") from exc
    if not isinstance(password, str):
        return False
    return hmac.compare_digest(_scrypt(password, salt_bytes, n, r, p), expected_bytes)
