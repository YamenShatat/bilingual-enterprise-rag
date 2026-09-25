import hashlib

import pytest

from bilingual_rag.database.migrate import MigrationError, load_migrations


def _write(directory, name, sql="SELECT 1;\n"):
    (directory / name).write_bytes(sql.encode("utf-8"))


class TestLoadMigrations:
    def test_reads_files_in_version_order(self, tmp_path):
        _write(tmp_path, "0002_second.sql")
        _write(tmp_path, "0001_first.sql")
        migrations = load_migrations(tmp_path)
        assert [(m.version, m.name) for m in migrations] == [(1, "first"), (2, "second")]

    def test_the_checksum_is_the_sha256_of_the_sql(self, tmp_path):
        _write(tmp_path, "0001_a.sql", "SELECT 1;\n")
        (migration,) = load_migrations(tmp_path)
        assert migration.checksum == hashlib.sha256(b"SELECT 1;\n").hexdigest()
        assert migration.sql == "SELECT 1;\n"

    def test_windows_line_endings_do_not_change_the_checksum(self, tmp_path):
        _write(tmp_path, "0001_a.sql", "SELECT 1;\nSELECT 2;\n")
        lf = load_migrations(tmp_path)[0]
        _write(tmp_path, "0001_a.sql", "SELECT 1;\r\nSELECT 2;\r\n")
        assert load_migrations(tmp_path)[0].checksum == lf.checksum

    def test_a_byte_order_mark_is_dropped(self, tmp_path):
        (tmp_path / "0001_a.sql").write_bytes(b"\xef\xbb\xbfSELECT 1;\n")
        (migration,) = load_migrations(tmp_path)
        assert migration.sql == "SELECT 1;\n"

    def test_changing_the_sql_changes_the_checksum(self, tmp_path):
        _write(tmp_path, "0001_a.sql", "SELECT 1;\n")
        before = load_migrations(tmp_path)[0].checksum
        _write(tmp_path, "0001_a.sql", "SELECT 2;\n")
        assert load_migrations(tmp_path)[0].checksum != before

    def test_arabic_in_a_comment_is_kept(self, tmp_path):
        _write(tmp_path, "0001_a.sql", "-- سياسة الإجازة السنوية\nSELECT 1;\n")
        assert "سياسة الإجازة السنوية" in load_migrations(tmp_path)[0].sql

    def test_an_empty_directory_has_no_migrations(self, tmp_path):
        assert load_migrations(tmp_path) == []

    def test_files_that_are_not_sql_are_ignored(self, tmp_path):
        _write(tmp_path, "0001_a.sql")
        (tmp_path / "README.md").write_text("notes", encoding="utf-8")
        (tmp_path / "0002_draft.sql.bak").write_text("x", encoding="utf-8")
        assert len(load_migrations(tmp_path)) == 1

    @pytest.mark.parametrize(
        "name",
        [
            "1_short.sql",
            "0001-dash.sql",
            "0001_Upper.sql",
            "0001_.sql",
            "0001.sql",
            "add.sql",
            "0001_a b.sql",
        ],
    )
    def test_a_badly_named_sql_file_is_an_error_not_silently_skipped(self, tmp_path, name):
        _write(tmp_path, name)
        with pytest.raises(MigrationError, match="expected a name like"):
            load_migrations(tmp_path)

    def test_version_zero_is_rejected(self, tmp_path):
        _write(tmp_path, "0000_zero.sql")
        with pytest.raises(MigrationError, match="start at 0001"):
            load_migrations(tmp_path)

    def test_a_gap_in_the_versions_is_an_error(self, tmp_path):
        _write(tmp_path, "0001_a.sql")
        _write(tmp_path, "0003_c.sql")
        with pytest.raises(MigrationError, match="0002 is missing"):
            load_migrations(tmp_path)

    def test_a_first_version_other_than_one_is_an_error(self, tmp_path):
        _write(tmp_path, "0002_b.sql")
        with pytest.raises(MigrationError, match="0001 is missing"):
            load_migrations(tmp_path)

    def test_a_duplicate_version_is_an_error(self, tmp_path):
        _write(tmp_path, "0001_a.sql")
        _write(tmp_path, "0001_b.sql")
        with pytest.raises(MigrationError, match="appears twice"):
            load_migrations(tmp_path)

    def test_an_empty_file_is_an_error(self, tmp_path):
        _write(tmp_path, "0001_a.sql", "  \n\n")
        with pytest.raises(MigrationError, match="is empty"):
            load_migrations(tmp_path)

    def test_invalid_utf8_is_an_error(self, tmp_path):
        (tmp_path / "0001_a.sql").write_bytes(b"SELECT '\xff';")
        with pytest.raises(MigrationError, match="not valid UTF-8"):
            load_migrations(tmp_path)


class TestShippedMigrations:
    def test_the_migrations_in_the_package_are_valid_and_contiguous(self):
        migrations = load_migrations()
        assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
        assert len(migrations) >= 2

    def test_no_shipped_migration_uses_a_transaction_only_statement(self):
        # Each migration already runs inside a transaction, so these would fail or do nothing.
        for migration in load_migrations():
            upper = migration.sql.upper()
            assert "CONCURRENTLY" not in upper, migration.name
            assert "BEGIN;" not in upper and "COMMIT;" not in upper, migration.name
