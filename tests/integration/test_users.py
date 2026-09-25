"""Users in a real database (migration 0004)."""

import pytest

from bilingual_rag.auth.passwords import PasswordError
from bilingual_rag.auth.users import UserError, authenticate, create_user, get_user
from bilingual_rag.ingestion.manifest import ACCESS_LEVELS

pytestmark = pytest.mark.database

PASSWORD = "correct horse battery staple"


class TestCreate:
    def test_an_employee_gets_public_and_employee_by_default(self, store_connection):
        user = create_user(store_connection, "sara", PASSWORD, role="employee")
        assert (user.role, user.access_levels, user.active) == (
            "employee",
            ("employee", "public"),
            True,
        )
        assert not user.is_admin

    def test_an_employee_can_be_given_department_levels(self, store_connection):
        user = create_user(
            store_connection, "hana", PASSWORD, role="employee", access_levels=["public", "hr"]
        )
        assert user.access_levels == ("hr", "public")

    def test_an_admin_gets_every_level(self, store_connection):
        user = create_user(store_connection, "root", PASSWORD, role="admin")
        assert set(user.access_levels) == set(ACCESS_LEVELS)
        assert user.is_admin

    def test_levels_cannot_be_chosen_for_an_admin(self, store_connection):
        with pytest.raises(UserError, match="every access level"):
            create_user(store_connection, "root", PASSWORD, role="admin", access_levels=["public"])

    def test_the_password_is_stored_hashed(self, store_connection):
        create_user(store_connection, "sara", PASSWORD, role="employee")
        (stored,) = store_connection.execute("SELECT password_hash FROM users").fetchone()
        assert stored.startswith("scrypt$")
        assert PASSWORD not in stored

    @pytest.mark.parametrize("role", ["superuser", "Admin", ""])
    def test_an_unknown_role_is_refused(self, store_connection, role):
        with pytest.raises(UserError, match="role"):
            create_user(store_connection, "sara", PASSWORD, role=role)

    def test_an_unknown_level_is_refused(self, store_connection):
        with pytest.raises(UserError, match="unknown access levels"):
            create_user(store_connection, "sara", PASSWORD, role="employee", access_levels=["root"])

    def test_one_string_is_not_a_list_of_levels(self, store_connection):
        with pytest.raises(UserError, match="not one string"):
            create_user(store_connection, "sara", PASSWORD, role="employee", access_levels="hr")

    def test_a_taken_username_is_refused_and_the_connection_still_works(self, store_connection):
        create_user(store_connection, "sara", PASSWORD, role="employee")
        with pytest.raises(UserError, match="taken"):
            create_user(store_connection, "sara", PASSWORD, role="admin")
        assert store_connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1

    @pytest.mark.parametrize("username", ["ab", "Sara", "-sara", "sara smith", "x" * 65, "سارة"])
    def test_an_invalid_username_is_refused(self, store_connection, username):
        with pytest.raises(UserError, match="invalid username"):
            create_user(store_connection, username, PASSWORD, role="employee")

    def test_a_short_password_is_refused(self, store_connection):
        with pytest.raises(PasswordError):
            create_user(store_connection, "sara", "short", role="employee")


class TestLookUpAndAuthenticate:
    @pytest.fixture
    def sara(self, store_connection):
        return create_user(store_connection, "sara", PASSWORD, role="employee")

    def test_get_user_by_id(self, store_connection, sara):
        assert get_user(store_connection, sara.id) == sara
        assert get_user(store_connection, sara.id + 1000) is None

    def test_the_right_password_authenticates(self, store_connection, sara):
        assert authenticate(store_connection, "sara", PASSWORD) == sara

    def test_a_wrong_password_does_not(self, store_connection, sara):
        assert authenticate(store_connection, "sara", PASSWORD + "x") is None

    def test_an_unknown_username_does_not(self, store_connection, sara):
        assert authenticate(store_connection, "nobody", PASSWORD) is None

    def test_an_unknown_username_still_does_the_password_work(self, store_connection, monkeypatch):
        # Same work either way, so response time does not reveal which usernames exist.
        from bilingual_rag.auth import users

        calls = []
        real = users.verify_password
        monkeypatch.setattr(users, "verify_password", lambda p, h: calls.append(p) or real(p, h))
        authenticate(store_connection, "nobody", PASSWORD)
        assert calls == [PASSWORD]

    def test_a_deactivated_user_cannot_log_in(self, store_connection, sara):
        store_connection.execute("UPDATE users SET active = false WHERE id = %s", (sara.id,))
        assert authenticate(store_connection, "sara", PASSWORD) is None
        assert get_user(store_connection, sara.id).active is False


class TestSchema:
    def test_the_database_refuses_an_unknown_level_even_without_the_code(self, store_connection):
        with pytest.raises(Exception, match="access_levels_check"):
            store_connection.execute(
                "INSERT INTO users (username, password_hash, role, access_levels)"
                " VALUES ('x-user', 'scrypt$x', 'employee', ARRAY['root'])"
            )

    def test_the_database_refuses_a_plain_password(self, store_connection):
        with pytest.raises(Exception, match="password_hash_check"):
            store_connection.execute(
                "INSERT INTO users (username, password_hash, role, access_levels)"
                " VALUES ('x-user', 'hunter2', 'employee', ARRAY['public'])"
            )


def test_creating_a_user_never_commits_the_callers_transaction(migrated_database):
    # A psycopg `conn.transaction()` block commits when no transaction is open yet; an earlier
    # version of create_user used one and silently committed (D-024).
    from bilingual_rag.database.connection import connect

    with connect(migrated_database) as writer, connect(migrated_database) as reader:
        create_user(writer, "uncommitted-user", PASSWORD, role="employee")
        seen = reader.execute(
            "SELECT count(*) FROM users WHERE username = 'uncommitted-user'"
        ).fetchone()[0]
        writer.rollback()
    assert seen == 0


def test_the_database_refuses_an_invalid_username_even_without_the_code(store_connection):
    with pytest.raises(Exception, match="username_check"):
        store_connection.execute(
            "INSERT INTO users (username, password_hash, role, access_levels)"
            " VALUES ('Bad Name', 'scrypt$x', 'employee', ARRAY['public'])"
        )
