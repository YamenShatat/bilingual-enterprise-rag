import base64
import json
import time

import jwt
import pytest

from bilingual_rag.auth.tokens import (
    DEFAULT_TTL_SECONDS,
    MIN_SECRET_LENGTH,
    TokenError,
    issue_token,
    read_token,
)

SECRET = "s" * MIN_SECRET_LENGTH
OTHER = "o" * MIN_SECRET_LENGTH


def test_a_token_names_its_user():
    assert read_token(issue_token(42, SECRET), SECRET) == 42


def test_the_token_carries_no_permissions():
    claims = jwt.decode(issue_token(42, SECRET), SECRET, algorithms=["HS256"])
    assert set(claims) == {"sub", "iat", "exp"}


def test_the_default_lifetime_is_an_hour():
    claims = jwt.decode(issue_token(1, SECRET), SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == DEFAULT_TTL_SECONDS == 3600


def test_a_token_signed_with_another_secret_is_refused():
    with pytest.raises(TokenError):
        read_token(issue_token(1, OTHER), SECRET)


def test_an_expired_token_is_refused():
    expired = jwt.encode(
        {"sub": "1", "iat": int(time.time()) - 120, "exp": int(time.time()) - 60}, SECRET, "HS256"
    )
    with pytest.raises(TokenError):
        read_token(expired, SECRET)


def _unsigned(claims):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."


def test_an_unsigned_alg_none_token_is_refused():
    now = int(time.time())
    with pytest.raises(TokenError):
        read_token(_unsigned({"sub": "1", "iat": now, "exp": now + 60}), SECRET)


def test_a_token_using_another_algorithm_is_refused():
    now = int(time.time())
    hs512 = jwt.encode({"sub": "1", "iat": now, "exp": now + 60}, SECRET, algorithm="HS512")
    with pytest.raises(TokenError):
        read_token(hs512, SECRET)


@pytest.mark.parametrize("missing", ["sub", "iat", "exp"])
def test_a_token_missing_a_required_claim_is_refused(missing):
    now = int(time.time())
    claims = {"sub": "1", "iat": now, "exp": now + 60}
    del claims[missing]
    with pytest.raises(TokenError):
        read_token(jwt.encode(claims, SECRET, algorithm="HS256"), SECRET)


def test_a_non_numeric_subject_is_refused():
    now = int(time.time())
    token = jwt.encode({"sub": "admin", "iat": now, "exp": now + 60}, SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        read_token(token, SECRET)


@pytest.mark.parametrize("garbage", ["", "not.a.token", "a.b.c", None])
def test_garbage_is_refused(garbage):
    with pytest.raises(TokenError):
        read_token(garbage, SECRET)


def test_a_tampered_token_is_refused():
    token = issue_token(1, SECRET)
    header, payload, signature = token.split(".")
    now = int(time.time())
    forged = (
        base64.urlsafe_b64encode(json.dumps({"sub": "2", "iat": now, "exp": now + 60}).encode())
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(TokenError):
        read_token(f"{header}.{forged}.{signature}", SECRET)


@pytest.mark.parametrize("secret", ["", "short", "x" * (MIN_SECRET_LENGTH - 1)])
def test_a_short_secret_is_refused_both_ways(secret):
    with pytest.raises(ValueError, match="secret"):
        issue_token(1, secret)
    with pytest.raises(ValueError, match="secret"):
        read_token("x.y.z", secret)


def test_the_lifetime_must_be_positive():
    with pytest.raises(ValueError, match="ttl"):
        issue_token(1, SECRET, ttl_seconds=0)


def test_the_minimum_secret_is_thirty_two_characters():
    # A literal, not the constant: weakening the constant must fail a test.
    with pytest.raises(ValueError):
        issue_token(1, "x" * 31)
    assert read_token(issue_token(1, "x" * 32), "x" * 32) == 1
