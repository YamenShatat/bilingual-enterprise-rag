"""Runtime configuration read from environment variables.

Secrets never live in the repository. They come from the process environment or from a local
``.env`` file (ignored by Git; ``.env.example`` lists the names). Docker Compose reads the same
``POSTGRES_*`` names, so the database container and the Python code cannot disagree.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ENV_FILE = Path(".env")

_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ConfigError(ValueError):
    """A required setting is missing or invalid."""


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines; blank lines and lines starting with ``#`` are ignored.

    A value is everything after the first ``=``, trimmed, with one pair of matching single or
    double quotes removed. There are no inline comments (``#`` may appear in a value) and no
    variable expansion, so keep secrets free of quotes: Docker Compose reads the same file and
    treats quotes and ``#`` slightly differently.

    Raises:
        ConfigError: a line is not ``KEY=VALUE`` or the key is not a valid variable name.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            raise ConfigError(f"line {number}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Where and how to reach PostgreSQL. The password is hidden from ``repr``."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)
    dbname: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "DatabaseSettings":
        """Build settings from ``POSTGRES_*`` variables. Only the password is required.

        The host defaults to ``127.0.0.1`` and not ``localhost``: the container publishes on
        IPv4 loopback only, and ``localhost`` may resolve to IPv6 first.

        Raises:
            ConfigError: the password is missing or empty, or the port is not 1-65535.
        """
        password = environ.get("POSTGRES_PASSWORD", "")
        if not password:
            raise ConfigError(
                "POSTGRES_PASSWORD is not set (copy .env.example to .env and edit it)"
            )

        raw_port = environ.get("POSTGRES_PORT", "").strip() or "5432"
        try:
            port = int(raw_port)
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            raise ConfigError(f"POSTGRES_PORT must be a number from 1 to 65535, got {raw_port!r}")

        def text(name: str, default: str) -> str:
            return environ.get(name, "").strip() or default

        return cls(
            host=text("POSTGRES_HOST", "127.0.0.1"),
            port=port,
            user=text("POSTGRES_USER", "rag"),
            password=password,
            dbname=text("POSTGRES_DB", "rag"),
        )

    def connect_kwargs(self) -> dict[str, str | int]:
        """Keyword arguments for ``psycopg.connect``. Contains the password: never log it."""
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "dbname": self.dbname,
        }


MIN_JWT_SECRET_LENGTH = 32
DEFAULT_TOKEN_TTL_MINUTES = 60


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """How the HTTP API signs login tokens (D-024). Permissions are not settings: each user's
    access levels live in the database. The secret is hidden from ``repr``."""

    jwt_secret: str = field(repr=False)
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_MINUTES * 60

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "ApiSettings":
        """``JWT_SECRET`` (required, at least 32 characters) and ``TOKEN_TTL_MINUTES``
        (optional, 1 to 1440, default 60).

        Raises:
            ConfigError: the secret is missing or short, or the lifetime is not 1 to 1440.
        """
        secret = environ.get("JWT_SECRET", "").strip()
        if len(secret) < MIN_JWT_SECRET_LENGTH:
            raise ConfigError(
                f"JWT_SECRET must be set to at least {MIN_JWT_SECRET_LENGTH} random characters "
                '(for example: python -c "import secrets; print(secrets.token_urlsafe(48))")'
            )
        raw = environ.get("TOKEN_TTL_MINUTES", "").strip() or str(DEFAULT_TOKEN_TTL_MINUTES)
        try:
            minutes = int(raw)
        except ValueError:
            minutes = 0
        if not 1 <= minutes <= 1440:
            raise ConfigError(f"TOKEN_TTL_MINUTES must be 1 to 1440, got {raw!r}")
        return cls(jwt_secret=secret, token_ttl_seconds=minutes * 60)


def _merged_env(env_file: Path | None, environ: Mapping[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        try:
            # utf-8-sig: PowerShell 5.1 writes a BOM with -Encoding utf8.
            merged.update(parse_env_file(env_file.read_text(encoding="utf-8-sig")))
        except UnicodeDecodeError as exc:
            raise ConfigError(f"{env_file} is not valid UTF-8") from exc
    merged.update(os.environ if environ is None else environ)
    return merged


def load_database_settings(
    env_file: Path | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> DatabaseSettings:
    """Load settings from ``env_file`` (if it exists) overlaid by the real environment.

    Values already set in the environment win over the file, so a CI job or a shell can
    override a local ``.env``. A missing file is fine when the environment has everything.

    Raises:
        ConfigError: the file is not valid UTF-8 or not ``KEY=VALUE`` lines, or a setting is
            missing or invalid.
    """
    return DatabaseSettings.from_env(_merged_env(env_file, environ))


def load_api_settings(
    env_file: Path | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> ApiSettings:
    """Like ``load_database_settings``, for ``ApiSettings``."""
    return ApiSettings.from_env(_merged_env(env_file, environ))
