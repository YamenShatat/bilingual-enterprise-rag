from pathlib import Path

import pytest

from bilingual_rag.config import (
    MIN_ADMIN_KEY_LENGTH,
    ApiSettings,
    ConfigError,
    DatabaseSettings,
    load_api_settings,
    load_database_settings,
    parse_env_file,
)
from support.arabic import ARABIC_TRUTH

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestParseEnvFile:
    def test_reads_key_value_lines(self):
        assert parse_env_file("A=1\nB=two\n") == {"A": "1", "B": "two"}

    def test_ignores_blank_lines_and_comments(self):
        text = "# a comment\n\n   \nA=1\n  # indented comment\n"
        assert parse_env_file(text) == {"A": "1"}

    def test_value_is_everything_after_the_first_equals_sign(self):
        assert parse_env_file("A=b=c==") == {"A": "b=c=="}

    def test_hash_inside_a_value_is_kept(self):
        assert parse_env_file("A=pass#word") == {"A": "pass#word"}

    def test_trims_whitespace_around_key_and_value(self):
        assert parse_env_file("  A  =  x y  ") == {"A": "x y"}

    def test_removes_one_pair_of_matching_quotes(self):
        text = 'A="one"\nB=\'two\'\nC="mixed\'\nD=""\nE=""inner""'
        assert parse_env_file(text) == {
            "A": "one",
            "B": "two",
            "C": "\"mixed'",
            "D": "",
            "E": '"inner"',
        }

    def test_a_lone_quote_character_is_kept(self):
        assert parse_env_file('A="') == {"A": '"'}

    def test_later_lines_win(self):
        assert parse_env_file("A=1\nA=2") == {"A": "2"}

    def test_handles_windows_line_endings(self):
        assert parse_env_file("A=1\r\nB=2\r\n") == {"A": "1", "B": "2"}

    @pytest.mark.parametrize("line", ["no separator", "=value", "1BAD=x", "BAD-KEY=x", "A B=x"])
    def test_rejects_malformed_lines_with_the_line_number(self, line):
        with pytest.raises(ConfigError, match="line 2"):
            parse_env_file(f"OK=1\n{line}\n")

    def test_keeps_arabic_values_exactly(self):
        text = "\n".join(f"K{i}={line}" for i, line in enumerate(ARABIC_TRUTH))
        values = parse_env_file(text)
        assert list(values.values()) == ARABIC_TRUTH


class TestDatabaseSettingsFromEnv:
    def test_only_the_password_is_required(self):
        settings = DatabaseSettings.from_env({"POSTGRES_PASSWORD": "secret"})
        assert settings == DatabaseSettings("127.0.0.1", 5432, "rag", "secret", "rag")

    def test_reads_every_setting(self):
        settings = DatabaseSettings.from_env(
            {
                "POSTGRES_HOST": "db.internal",
                "POSTGRES_PORT": "6543",
                "POSTGRES_USER": "alice",
                "POSTGRES_PASSWORD": "s3cret",
                "POSTGRES_DB": "ragdb",
            }
        )
        assert settings == DatabaseSettings("db.internal", 6543, "alice", "s3cret", "ragdb")

    @pytest.mark.parametrize("environ", [{}, {"POSTGRES_PASSWORD": ""}])
    def test_missing_or_empty_password_is_an_error(self, environ):
        with pytest.raises(ConfigError, match="POSTGRES_PASSWORD"):
            DatabaseSettings.from_env(environ)

    def test_blank_optional_values_fall_back_to_defaults(self):
        settings = DatabaseSettings.from_env(
            {
                "POSTGRES_PASSWORD": "x",
                "POSTGRES_HOST": " ",
                "POSTGRES_PORT": "",
                "POSTGRES_USER": "",
                "POSTGRES_DB": "  ",
            }
        )
        assert settings == DatabaseSettings("127.0.0.1", 5432, "rag", "x", "rag")

    @pytest.mark.parametrize("port", ["abc", "0", "-1", "65536", "54.3", "5432x"])
    def test_invalid_port_is_an_error(self, port):
        with pytest.raises(ConfigError, match="POSTGRES_PORT"):
            DatabaseSettings.from_env({"POSTGRES_PASSWORD": "x", "POSTGRES_PORT": port})

    @pytest.mark.parametrize("port", ["1", "65535"])
    def test_port_limits_are_accepted(self, port):
        settings = DatabaseSettings.from_env({"POSTGRES_PASSWORD": "x", "POSTGRES_PORT": port})
        assert settings.port == int(port)

    def test_password_is_not_in_the_repr(self):
        settings = DatabaseSettings.from_env({"POSTGRES_PASSWORD": "hunter2-unique"})
        assert "hunter2-unique" not in repr(settings)
        assert "hunter2-unique" not in str(settings)

    def test_password_is_kept_exactly_including_spaces_and_arabic(self):
        password = " كلمة سر #1 = ٢١ "
        settings = DatabaseSettings.from_env({"POSTGRES_PASSWORD": password})
        assert settings.password == password
        assert settings.connect_kwargs()["password"] == password

    def test_connect_kwargs_match_psycopg_names(self):
        settings = DatabaseSettings("h", 1234, "u", "p", "d")
        assert settings.connect_kwargs() == {
            "host": "h",
            "port": 1234,
            "user": "u",
            "password": "p",
            "dbname": "d",
        }


class TestLoadDatabaseSettings:
    def test_reads_the_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_PASSWORD=from-file\nPOSTGRES_DB=filedb\n", encoding="utf-8")
        settings = load_database_settings(env_file, environ={})
        assert (settings.password, settings.dbname) == ("from-file", "filedb")

    def test_the_environment_overrides_the_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_PASSWORD=from-file\nPOSTGRES_DB=filedb\n", encoding="utf-8")
        settings = load_database_settings(env_file, environ={"POSTGRES_DB": "envdb"})
        assert (settings.password, settings.dbname) == ("from-file", "envdb")

    def test_a_missing_file_is_fine_when_the_environment_is_complete(self, tmp_path):
        settings = load_database_settings(
            tmp_path / "absent.env", environ={"POSTGRES_PASSWORD": "from-env"}
        )
        assert settings.password == "from-env"

    def test_no_file_and_no_password_is_an_error(self, tmp_path):
        with pytest.raises(ConfigError, match="POSTGRES_PASSWORD"):
            load_database_settings(tmp_path / "absent.env", environ={})

    def test_env_file_none_uses_only_the_environment(self):
        settings = load_database_settings(None, environ={"POSTGRES_PASSWORD": "p"})
        assert settings.password == "p"

    def test_a_utf8_bom_from_powershell_is_dropped(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"\xef\xbb\xbfPOSTGRES_PASSWORD=bom-ok\n")
        assert load_database_settings(env_file, environ={}).password == "bom-ok"

    def test_arabic_values_survive_the_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"POSTGRES_PASSWORD={ARABIC_TRUTH[1]}\n", encoding="utf-8")
        assert load_database_settings(env_file, environ={}).password == ARABIC_TRUTH[1]

    def test_invalid_utf8_is_an_error(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"POSTGRES_PASSWORD=\xff\xfe\n")
        with pytest.raises(ConfigError, match="not valid UTF-8"):
            load_database_settings(env_file, environ={})

    def test_a_malformed_file_is_an_error(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("POSTGRES_PASSWORD=x\nnot a setting\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="line 2"):
            load_database_settings(env_file, environ={})

    def test_the_example_file_is_valid_and_defines_every_setting(self):
        example = REPO_ROOT / ".env.example"
        values = parse_env_file(example.read_text(encoding="utf-8"))
        assert set(values) == {
            "POSTGRES_PASSWORD",
            "POSTGRES_USER",
            "POSTGRES_DB",
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "API_ACCESS_LEVELS",
            "ADMIN_API_KEY",
        }
        assert load_database_settings(example, environ={}).port == 5432
        api = load_api_settings(example, environ={})
        assert api.access_levels == {"public"}
        assert api.admin_api_key is None  # uploads are off unless someone sets a key


class TestApiSettings:
    def test_defaults_to_public_only_and_uploads_disabled(self):
        settings = ApiSettings.from_env({})
        assert settings.access_levels == frozenset({"public"})
        assert settings.admin_api_key is None

    def test_reads_a_comma_separated_list_with_spaces(self):
        settings = ApiSettings.from_env({"API_ACCESS_LEVELS": " public , employee,hr "})
        assert settings.access_levels == frozenset({"public", "employee", "hr"})

    def test_a_blank_value_means_the_default(self):
        assert ApiSettings.from_env({"API_ACCESS_LEVELS": "  "}).access_levels == {"public"}

    @pytest.mark.parametrize("raw", ["admin", "public,Employee", "public,,superuser", ","])
    def test_an_unknown_level_is_an_error_not_ignored(self, raw):
        with pytest.raises(ConfigError, match="API_ACCESS_LEVELS"):
            ApiSettings.from_env({"API_ACCESS_LEVELS": raw})

    def test_a_short_admin_key_is_refused(self):
        with pytest.raises(ConfigError, match="ADMIN_API_KEY"):
            ApiSettings.from_env({"ADMIN_API_KEY": "x" * (MIN_ADMIN_KEY_LENGTH - 1)})

    def test_the_minimum_is_sixteen_characters(self):
        # A literal, not the constant: weakening the constant must fail a test.
        with pytest.raises(ConfigError):
            ApiSettings.from_env({"ADMIN_API_KEY": "fifteen-chars-x"})
        assert ApiSettings.from_env({"ADMIN_API_KEY": "sixteen-chars-xx"}).admin_api_key

    def test_an_admin_key_of_the_minimum_length_is_accepted(self):
        key = "x" * MIN_ADMIN_KEY_LENGTH
        assert ApiSettings.from_env({"ADMIN_API_KEY": key}).admin_api_key == key

    def test_a_blank_admin_key_disables_uploads(self):
        assert ApiSettings.from_env({"ADMIN_API_KEY": "   "}).admin_api_key is None

    def test_the_admin_key_is_hidden_from_repr(self):
        key = "a-very-secret-admin-key"
        assert key not in repr(ApiSettings.from_env({"ADMIN_API_KEY": key}))

    def test_load_merges_the_env_file_and_the_environment(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("API_ACCESS_LEVELS=employee\n", encoding="utf-8")
        assert load_api_settings(env_file, {}).access_levels == {"employee"}
        overridden = load_api_settings(env_file, {"API_ACCESS_LEVELS": "hr"})
        assert overridden.access_levels == {"hr"}
