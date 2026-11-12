"""Bounded, deterministic RTL repair-patch protocol.

Model output is deliberately kept smaller than a complete SystemVerilog file.
The preferred JSON representation is::

    {
      "schema_version": 1,
      "edits": [
        {
          "path": "rtl/top.sv",
          "expected": "the exact old text, appearing once",
          "replacement": "the exact new text"
        }
      ],
      "notes": "optional, bounded diagnostic"
    }

``expected`` is an exact, unique anchor in the *current* file text.  This
makes application deterministic and prevents a repair from silently changing
the wrong copy of a repeated construct.  A legacy ``files`` payload containing
complete file contents remains supported for existing callers, but it is
subject to the same path and size restrictions.

The functions in this module do not touch the filesystem.  They operate only
on the supplied project-file mapping so callers can validate a model response
before writing a single RTL file.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


PATCH_SCHEMA_VERSION = 1

# These limits are intentionally generous enough for a real RTL fix while
# preventing a malformed model answer from becoming another full-project dump.
MAX_PATCH_EDITS = 24
MAX_ANCHOR_BYTES = 48 * 1024
MAX_REPLACEMENT_BYTES = 64 * 1024
MAX_TOTAL_EDIT_BYTES = 256 * 1024
MAX_RESULT_FILE_BYTES = 512 * 1024
MAX_LEGACY_FILES = 12
MAX_LEGACY_FILE_BYTES = 256 * 1024
MAX_LEGACY_TOTAL_BYTES = 512 * 1024
MAX_NOTES_BYTES = 4 * 1024
MAX_PAYLOAD_BYTES = 1024 * 1024

# Do not normalize paths: requiring the mapping's exact canonical key keeps a
# model from reaching a file through aliases such as ``./rtl/top.sv``.
_SAFE_SV_PATH = re.compile(
    r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)*[A-Za-z0-9_][A-Za-z0-9_.-]*\.sv$"
)
_ROOT_KEYS = frozenset({"schema_version", "edits", "files", "notes"})
_EDIT_KEYS = frozenset({"path", "expected", "replacement"})
_LEGACY_FILE_KEYS = frozenset({"path", "content"})


class PatchProtocolError(ValueError):
    """Raised when a repair payload cannot be applied safely and exactly."""


@dataclass(frozen=True)
class RepairApplication:
    """The validated changes and compact provenance for one repair payload."""

    changed_files: dict[str, str]
    manifest: dict[str, Any]


def _byte_length(value: str) -> int:
    return len(value.encode("utf-8", "surrogatepass"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_payload(payload: Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(payload, str):
        if _byte_length(payload) > MAX_PAYLOAD_BYTES:
            raise PatchProtocolError("repair payload exceeds the maximum size")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise PatchProtocolError("repair payload is not valid JSON") from error
        payload = decoded
    if not isinstance(payload, Mapping):
        raise PatchProtocolError("repair payload must be a JSON object")
    result = dict(payload)
    unknown = set(result).difference(_ROOT_KEYS)
    if unknown:
        raise PatchProtocolError(
            "repair payload contains unsupported keys: " + ", ".join(sorted(map(str, unknown)))
        )
    version = result.get("schema_version", PATCH_SCHEMA_VERSION)
    if type(version) is not int or version != PATCH_SCHEMA_VERSION:
        raise PatchProtocolError(f"unsupported repair schema_version: {version!r}")
    notes = result.get("notes")
    if notes is not None:
        if not isinstance(notes, str):
            raise PatchProtocolError("repair notes must be a string")
        if _byte_length(notes) > MAX_NOTES_BYTES:
            raise PatchProtocolError("repair notes exceed the maximum size")
    return result


def _validate_path(path: Any, project_files: Mapping[str, str]) -> str:
    if not isinstance(path, str) or not _SAFE_SV_PATH.fullmatch(path):
        raise PatchProtocolError("repair path must be a canonical relative .sv path")
    if path not in project_files:
        raise PatchProtocolError(f"repair path is not an existing project RTL file: {path}")
    if not isinstance(project_files[path], str):
        raise PatchProtocolError(f"project RTL content is not text: {path}")
    return path


def _validate_text(value: Any, label: str, maximum: int, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise PatchProtocolError(f"{label} must be a string")
    if not allow_empty and not value:
        raise PatchProtocolError(f"{label} must not be empty")
    if "\x00" in value:
        raise PatchProtocolError(f"{label} must not contain NUL bytes")
    if _byte_length(value) > maximum:
        raise PatchProtocolError(f"{label} exceeds the maximum size")
    return value


def _require_exact_keys(entry: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = set(entry).difference(allowed)
    missing = allowed.difference(entry)
    if unknown or missing:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unsupported " + ", ".join(sorted(map(str, unknown))))
        raise PatchProtocolError(f"{label} has " + "; ".join(details))


def _build_manifest(
    *,
    repair_format: str,
    changed_files: dict[str, str],
    original_files: Mapping[str, str],
    operations: list[dict[str, str]],
) -> dict[str, Any]:
    operation_counts = Counter(item["path"] for item in operations)
    file_changes = [
        {
            "path": path,
            "before_sha256": _sha256(original_files[path]),
            "after_sha256": _sha256(changed_files[path]),
            "operation_count": operation_counts[path],
        }
        for path in sorted(changed_files)
    ]
    fingerprint_input = {
        "schema_version": PATCH_SCHEMA_VERSION,
        "format": repair_format,
        "operations": operations,
        "file_changes": file_changes,
    }
    return {
        "schema_version": PATCH_SCHEMA_VERSION,
        "format": repair_format,
        "fingerprint": _sha256(_canonical_json(fingerprint_input)),
        "edit_count": len(operations),
        "changed_paths": sorted(changed_files),
        "files": file_changes,
    }


def _apply_anchored_edits(
    edits: Any, project_files: Mapping[str, str]
) -> RepairApplication:
    if not isinstance(edits, list) or not edits:
        raise PatchProtocolError("edits must be a non-empty array")
    if len(edits) > MAX_PATCH_EDITS:
        raise PatchProtocolError("repair has too many exact-anchor edits")

    working = dict(project_files)
    original: dict[str, str] = {}
    operations: list[dict[str, str]] = []
    total_bytes = 0

    for index, raw_edit in enumerate(edits, start=1):
        if not isinstance(raw_edit, Mapping):
            raise PatchProtocolError(f"edit {index} must be an object")
        edit = dict(raw_edit)
        _require_exact_keys(edit, _EDIT_KEYS, f"edit {index}")
        path = _validate_path(edit["path"], project_files)
        expected = _validate_text(edit["expected"], f"edit {index}.expected", MAX_ANCHOR_BYTES, allow_empty=False)
        replacement = _validate_text(
            edit["replacement"], f"edit {index}.replacement", MAX_REPLACEMENT_BYTES, allow_empty=True
        )
        if expected == replacement:
            raise PatchProtocolError(f"edit {index} is a no-op")
        total_bytes += _byte_length(expected) + _byte_length(replacement)
        if total_bytes > MAX_TOTAL_EDIT_BYTES:
            raise PatchProtocolError("repair edits exceed the total size limit")

        current = working[path]
        occurrences = current.count(expected)
        if occurrences == 0:
            raise PatchProtocolError(f"edit {index} anchor was not found in {path}")
        if occurrences != 1:
            raise PatchProtocolError(f"edit {index} anchor is ambiguous in {path} ({occurrences} matches)")
        updated = current.replace(expected, replacement, 1)
        if _byte_length(updated) > MAX_RESULT_FILE_BYTES:
            raise PatchProtocolError(f"edit {index} would make {path} too large")
        original.setdefault(path, project_files[path])
        working[path] = updated
        operations.append(
            {
                "path": path,
                "expected_sha256": _sha256(expected),
                "replacement_sha256": _sha256(replacement),
            }
        )

    changed = {path: working[path] for path in original if working[path] != original[path]}
    if not changed:
        raise PatchProtocolError("repair produced no file changes")
    return RepairApplication(
        changed_files=changed,
        manifest=_build_manifest(
            repair_format="anchored_edits",
            changed_files=changed,
            original_files=original,
            operations=operations,
        ),
    )


def _apply_legacy_files(files: Any, project_files: Mapping[str, str]) -> RepairApplication:
    if not isinstance(files, list) or not files:
        raise PatchProtocolError("legacy files must be a non-empty array")
    if len(files) > MAX_LEGACY_FILES:
        raise PatchProtocolError("legacy repair contains too many files")

    changed: dict[str, str] = {}
    original: dict[str, str] = {}
    operations: list[dict[str, str]] = []
    total_bytes = 0
    seen_paths: set[str] = set()
    for index, raw_file in enumerate(files, start=1):
        if not isinstance(raw_file, Mapping):
            raise PatchProtocolError(f"legacy file {index} must be an object")
        entry = dict(raw_file)
        _require_exact_keys(entry, _LEGACY_FILE_KEYS, f"legacy file {index}")
        path = _validate_path(entry["path"], project_files)
        if path in seen_paths:
            raise PatchProtocolError(f"legacy repair repeats {path}")
        seen_paths.add(path)
        content = _validate_text(
            entry["content"], f"legacy file {index}.content", MAX_LEGACY_FILE_BYTES, allow_empty=True
        )
        total_bytes += _byte_length(content)
        if total_bytes > MAX_LEGACY_TOTAL_BYTES:
            raise PatchProtocolError("legacy repair exceeds the total size limit")
        if _byte_length(content) > MAX_RESULT_FILE_BYTES:
            raise PatchProtocolError(f"legacy file {index} would make {path} too large")
        previous = project_files[path]
        if content == previous:
            continue
        original[path] = previous
        changed[path] = content
        operations.append(
            {
                "path": path,
                "before_sha256": _sha256(previous),
                "after_sha256": _sha256(content),
            }
        )

    if not changed:
        raise PatchProtocolError("legacy repair makes no changes")
    return RepairApplication(
        changed_files=changed,
        manifest=_build_manifest(
            repair_format="legacy_files",
            changed_files=changed,
            original_files=original,
            operations=operations,
        ),
    )


def apply_repair_payload(
    payload: Mapping[str, Any] | str, project_files: Mapping[str, str]
) -> RepairApplication:
    """Validate and apply a model repair response in memory.

    The returned ``changed_files`` contains only changed existing files.  The
    input mapping is never mutated, and validation is atomic: if any operation
    is missing, ambiguous, unsafe, or oversized, no partial result is returned.
    """

    if not isinstance(project_files, Mapping):
        raise PatchProtocolError("project_files must be a mapping of paths to RTL text")
    parsed = _parse_payload(payload)
    has_edits = "edits" in parsed
    has_files = "files" in parsed
    if has_edits == has_files:
        raise PatchProtocolError("repair payload must contain exactly one of edits or files")
    if has_edits:
        return _apply_anchored_edits(parsed["edits"], project_files)
    return _apply_legacy_files(parsed["files"], project_files)
