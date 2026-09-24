import base64

import pytest

from bilingual_rag.auth import passwords
from bilingual_rag.auth.passwords import (
    MIN_PASSWORD_LENGTH,
    PasswordError,
    hash_password,
    verify_password,
)
from support.arabic import ARABIC_TRUTH

GOOD = "correct horse battery staple"


def test_the_right_password_verifies_and_a_wrong_one_does_not():
    stored = hash_password(GOOD)
    assert verify_password(GOOD, stored)
    assert not verify_password(GOOD + "!", stored)
    assert not verify_password(GOOD.upper(), stored)


def test_the_password_itself_is_not_in_the_hash():
    assert GOOD not in hash_password(GOOD)


def test_the_same_password_hashes_differently_each_time():
    assert hash_password(GOOD) != hash_password(GOOD)  # a fresh random salt


def test_the_hash_records_its_scheme_and_cost():
    scheme, n, r, p, salt, key = hash_password(GOOD).split("$")
    assert (scheme, int(n), int(r), int(p)) == ("scrypt", passwords.N, passwords.R, passwords.P)


def test_a_hash_made_at_another_cost_still_verifies(monkeypatch):
    stored = hash_password(GOOD)
    monkeypatch.setattr(passwords, "N", 2**9)  # the cost is read from the hash, not the module
    assert verify_password(GOOD, stored)


def test_an_arabic_password_round_trips():
    password = ARABIC_TRUTH[0]
    assert len(password) >= MIN_PASSWORD_LENGTH
    assert verify_password(password, hash_password(password))


@pytest.mark.parametrize(
    "weak", ["", "short", "x" * (MIN_PASSWORD_LENGTH - 1), None, b"bytes-bytes-bytes"]
)
def test_a_weak_or_non_string_password_is_refused(weak):
    with pytest.raises(PasswordError):
        hash_password(weak)


def test_the_minimum_length_is_twelve():
    hash_password("x" * 12)
    with pytest.raises(PasswordError):
        hash_password("x" * 11)


@pytest.mark.parametrize("stored", ["", "plain", "bcrypt$1$2$3$a$b", "scrypt$x$8$1$a$b", None])
def test_a_malformed_hash_is_an_error_not_a_match(stored):
    with pytest.raises(PasswordError):
        verify_password(GOOD, stored)


def test_a_non_string_candidate_never_matches():
    assert not verify_password(None, hash_password(GOOD))


@pytest.mark.real_password_cost
def test_production_hashes_use_the_owasp_scrypt_parameters():
    scheme, n, r, p, salt, key = hash_password(GOOD).split("$")
    assert (int(n), int(r), int(p)) == (2**17, 8, 1)


def test_a_hash_with_its_last_byte_changed_does_not_verify():
    *head, key = hash_password(GOOD).split("$")
    raw = bytearray(base64.b64decode(key))
    raw[-1] ^= 1
    tampered = "$".join([*head, base64.b64encode(bytes(raw)).decode()])
    assert not verify_password(GOOD, tampered)


def test_a_well_formed_hash_of_another_scheme_is_refused():
    stored = hash_password(GOOD).replace("scrypt$", "pbkdf2$", 1)
    with pytest.raises(PasswordError):
        verify_password(GOOD, stored)
