"""The corpus manifest: the metadata for every document, loaded and validated.

``data/manifest.json`` is the single source of truth for what each document is (language,
department, access level, bilingual pair). A manifest ``path`` equals the ``filename`` of the
chunks the pipeline produces for it, so the two join directly.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

LANGUAGES = ("en", "ar")
FORMATS = ("md", "txt", "docx", "pdf")
ACCESS_LEVELS = ("public", "employee", "engineering", "hr", "management")
DIGIT_STYLES = ("arabic-indic", "western")

_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_FIELDS = {
    "id",
    "path",
    "title",
    "department",
    "language",
    "format",
    "access_level",
    "topic",
    "pair",
    "digits",
}
# Build provenance: valid in the manifest, not part of the stored metadata.
_IGNORED_FIELDS = {"source", "build"}


class ManifestError(ValueError):
    """The manifest, or one of its documents, is invalid."""


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    """What is known about one document besides its text.

    ``pair`` is the path of the same document in the other language, if there is one.
    ``digits`` says which digit system an Arabic document uses; it is None for English ones.

    Raises:
        ManifestError: a value is missing, empty or outside its allowed set.
    """

    id: str
    path: str
    title: str
    department: str
    language: str
    format: str
    access_level: str
    topic: str
    pair: str | None = None
    digits: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "path", "title", "department", "topic"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"{name} must be a non-empty string, got {value!r}")
        if not _ID.fullmatch(self.id):
            raise ManifestError(f"id {self.id!r} must be lowercase letters, digits and hyphens")
        for name, path in (("path", self.path), ("pair", self.pair)):
            if path is not None:
                _check_relative_path(name, path)
        for name, allowed in (
            ("language", LANGUAGES),
            ("format", FORMATS),
            ("access_level", ACCESS_LEVELS),
        ):
            if getattr(self, name) not in allowed:
                raise ManifestError(
                    f"{name} must be one of {', '.join(allowed)}, got {getattr(self, name)!r}"
                )
        if self.digits is not None and self.digits not in DIGIT_STYLES:
            raise ManifestError(
                f"digits must be one of {', '.join(DIGIT_STYLES)} or null, got {self.digits!r}"
            )


def _check_relative_path(name: str, path: str) -> None:
    pure = PurePosixPath(path)
    if not path.strip() or "\\" in path or pure.is_absolute() or ".." in pure.parts:
        raise ManifestError(f"{name} must be a relative path with '/' separators, got {path!r}")


def parse_manifest(data: object) -> tuple[DocumentMetadata, ...]:
    """Validate a decoded manifest and return its documents in file order.

    Raises:
        ManifestError: the structure is wrong, a document is invalid, an id or path is
            duplicated, or a bilingual pair does not point back at its twin.
    """
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ManifestError('the manifest must be an object with a "documents" list')

    documents: list[DocumentMetadata] = []
    for position, entry in enumerate(data["documents"], start=1):
        if not isinstance(entry, dict):
            raise ManifestError(f"document {position} must be an object")
        label = f"document {position} ({entry.get('id')!r})"
        unknown = set(entry) - _FIELDS - _IGNORED_FIELDS
        if unknown:
            raise ManifestError(f"{label}: unknown fields {sorted(unknown)}")
        missing = {"id", "path", "title", "department", "language", "format"} - set(entry)
        missing |= {"access_level", "topic"} - set(entry)
        if missing:
            raise ManifestError(f"{label}: missing fields {sorted(missing)}")
        try:
            documents.append(DocumentMetadata(**{k: v for k, v in entry.items() if k in _FIELDS}))
        except ManifestError as exc:
            raise ManifestError(f"{label}: {exc}") from exc

    for field in ("id", "path"):
        seen: set[str] = set()
        for document in documents:
            value = getattr(document, field)
            if value in seen:
                raise ManifestError(f"duplicate {field} {value!r}")
            seen.add(value)

    by_path = {document.path: document for document in documents}
    for document in documents:
        if document.pair is None:
            continue
        twin = by_path.get(document.pair)
        if twin is None:
            raise ManifestError(f"{document.id}: pair {document.pair!r} is not in the manifest")
        if twin.pair != document.path:
            raise ManifestError(f"{document.id}: its pair {twin.id} does not point back at it")
    return tuple(documents)


def load_manifest(path: Path) -> tuple[DocumentMetadata, ...]:
    """Read and validate a manifest file (strict UTF-8).

    Raises:
        ManifestError: the file is not valid UTF-8 or JSON, or fails ``parse_manifest``.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{path} is not valid UTF-8 JSON: {exc}") from exc
    return parse_manifest(data)
