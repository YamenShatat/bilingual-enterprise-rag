import json
from pathlib import Path

import pytest

from bilingual_rag.ingestion.manifest import (
    ACCESS_LEVELS,
    DocumentMetadata,
    ManifestError,
    load_manifest,
    parse_manifest,
)

MANIFEST = Path(__file__).resolve().parents[2] / "data" / "manifest.json"


def _entry(**overrides):
    entry = {
        "id": "hr-leave-en",
        "path": "hr/leave_en.md",
        "title": "Leave",
        "department": "hr",
        "language": "en",
        "format": "md",
        "access_level": "employee",
        "topic": "leave",
        "pair": None,
        "digits": None,
        "source": None,
        "build": None,
    }
    return entry | overrides


def _manifest(*entries):
    return {"documents": list(entries)}


class TestRealManifest:
    def test_the_committed_manifest_is_valid(self):
        documents = load_manifest(MANIFEST)
        assert len(documents) == 32

    def test_it_matches_the_raw_json(self):
        raw = json.loads(MANIFEST.read_text(encoding="utf-8"))["documents"]
        documents = load_manifest(MANIFEST)
        assert [d.id for d in documents] == [e["id"] for e in raw]
        assert [d.access_level for d in documents] == [e["access_level"] for e in raw]

    def test_arabic_titles_are_kept_exactly(self):
        by_id = {d.id: d for d in load_manifest(MANIFEST)}
        assert by_id["hr-annual-leave-policy-ar"].title == "سياسة الإجازة السنوية"
        assert by_id["hr-annual-leave-policy-ar"].digits == "arabic-indic"

    def test_every_access_level_in_use_is_a_known_one(self):
        assert {d.access_level for d in load_manifest(MANIFEST)} <= set(ACCESS_LEVELS)


class TestParseManifest:
    def test_a_valid_entry_becomes_metadata(self):
        (document,) = parse_manifest(_manifest(_entry()))
        assert document == DocumentMetadata(
            id="hr-leave-en",
            path="hr/leave_en.md",
            title="Leave",
            department="hr",
            language="en",
            format="md",
            access_level="employee",
            topic="leave",
        )

    def test_pair_and_digits_are_optional_keys(self):
        entry = _entry()
        del entry["pair"], entry["digits"], entry["source"], entry["build"]
        assert parse_manifest(_manifest(entry))[0].pair is None

    @pytest.mark.parametrize("data", [[], {}, {"documents": "x"}, {"documents": [1]}, None])
    def test_a_malformed_structure_is_rejected(self, data):
        with pytest.raises(ManifestError):
            parse_manifest(data)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("language", "fr"),
            ("language", "EN"),
            ("format", "doc"),
            ("access_level", "Employee"),
            ("access_level", "secret"),
            ("digits", "roman"),
            ("id", "Has Space"),
            ("id", "under_score"),
            ("id", "-leading"),
            ("id", ""),
            ("title", "  "),
            ("department", ""),
            ("topic", ""),
        ],
    )
    def test_invalid_values_are_rejected_and_named(self, field, value):
        with pytest.raises(ManifestError, match=field):
            parse_manifest(_manifest(_entry(**{field: value})))

    @pytest.mark.parametrize("bad", ["/etc/passwd", "../x.md", "a/../../x.md", "a\\b.md", " "])
    def test_paths_must_be_relative_with_forward_slashes(self, bad):
        with pytest.raises(ManifestError, match="path"):
            parse_manifest(_manifest(_entry(path=bad)))

    def test_a_missing_required_field_is_named(self):
        entry = _entry()
        del entry["access_level"]
        with pytest.raises(ManifestError, match="access_level"):
            parse_manifest(_manifest(entry))

    def test_an_unknown_field_is_rejected_so_typos_do_not_pass_silently(self):
        with pytest.raises(ManifestError, match="acces_level"):
            parse_manifest(_manifest(_entry(acces_level="hr")))

    def test_duplicate_ids_and_paths_are_rejected(self):
        with pytest.raises(ManifestError, match="duplicate id"):
            parse_manifest(_manifest(_entry(), _entry(path="hr/other.md")))
        with pytest.raises(ManifestError, match="duplicate path"):
            parse_manifest(_manifest(_entry(), _entry(id="other-id")))

    def test_a_pair_must_exist_and_point_back(self):
        en = _entry(pair="hr/leave_ar.md")
        with pytest.raises(ManifestError, match="not in the manifest"):
            parse_manifest(_manifest(en))
        ar = _entry(id="hr-leave-ar", path="hr/leave_ar.md", language="ar", pair="hr/other.md")
        with pytest.raises(ManifestError, match="does not point back"):
            parse_manifest(_manifest(en, ar))

    def test_a_reciprocal_pair_is_accepted(self):
        en = _entry(pair="hr/leave_ar.md")
        ar = _entry(id="hr-leave-ar", path="hr/leave_ar.md", language="ar", pair="hr/leave_en.md")
        assert len(parse_manifest(_manifest(en, ar))) == 2

    def test_the_error_names_the_document(self):
        with pytest.raises(ManifestError, match="hr-leave-en"):
            parse_manifest(_manifest(_entry(language="fr")))


class TestLoadManifest:
    def test_invalid_json_is_an_error(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ManifestError, match="not valid"):
            load_manifest(path)

    def test_invalid_utf8_is_an_error(self, tmp_path):
        path = tmp_path / "m.json"
        path.write_bytes(b'{"documents": [\xff]}')
        with pytest.raises(ManifestError, match="not valid"):
            load_manifest(path)

    def test_a_missing_file_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path / "absent.json")

    def test_arabic_survives_a_written_manifest(self, tmp_path):
        path = tmp_path / "m.json"
        entry = _entry(title="سياسة الإجازة السنوية", language="ar", digits="arabic-indic")
        path.write_text(json.dumps(_manifest(entry), ensure_ascii=False), encoding="utf-8")
        assert load_manifest(path)[0].title == "سياسة الإجازة السنوية"
