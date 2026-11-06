"""File-format plumbing shared by project and library IO.

Every on-disk file is an *envelope*: a JSON object carrying a ``schemaVersion``
and a ``kind`` around its payload. The version makes future format changes
detectable (a file from a newer or older schema fails with a clear error rather
than silently misloading); the kind stops a library being loaded as a project
or vice versa.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import MalformedFileError, UnsupportedVersionError

__all__ = [
    "SCHEMA_VERSION",
    "PROJECT_KIND",
    "LIBRARY_KIND",
    "read_json",
    "write_json",
    "validate_envelope",
]

#: The schema this build reads and writes. Bump when the on-disk shape changes;
#: a loader then rejects or migrates anything that isn't this exact version.
SCHEMA_VERSION = 1

PROJECT_KIND = "project"
LIBRARY_KIND = "library"


def read_json(path: str | Path) -> Any:
    """Read and parse a JSON file, mapping parse errors to a clear IO error.

    A missing file raises the ordinary :class:`FileNotFoundError` (already a
    clear, catchable condition); only invalid *content* becomes a
    :class:`MalformedFileError`.
    """

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedFileError(f"{path}: not valid JSON — {exc}") from exc


def write_json(path: str | Path, data: Any) -> None:
    """Write ``data`` as pretty-printed JSON with a trailing newline."""

    path = Path(path)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def validate_envelope(data: Any, expected_kind: str) -> dict[str, Any]:
    """Check that ``data`` is a well-formed envelope of ``expected_kind``.

    Returns the envelope dict on success. Raises :class:`MalformedFileError`
    for a non-object, a missing/garbled ``schemaVersion``, or a wrong ``kind``;
    raises :class:`UnsupportedVersionError` when the version is one this build
    can't read.
    """

    if not isinstance(data, dict):
        raise MalformedFileError(f"expected a JSON object, got {type(data).__name__}")

    if "schemaVersion" not in data:
        raise MalformedFileError("missing 'schemaVersion' — not a process-sim file")

    version = data["schemaVersion"]
    # bool is an int subclass; a JSON true/false is not a version.
    if not isinstance(version, int) or isinstance(version, bool):
        raise MalformedFileError(f"'schemaVersion' must be an integer, got {version!r}")
    if version > SCHEMA_VERSION:
        raise UnsupportedVersionError(
            f"file schema v{version} is newer than this build supports "
            f"(v{SCHEMA_VERSION}); upgrade the app to open it"
        )
    if version < SCHEMA_VERSION:
        raise UnsupportedVersionError(
            f"file schema v{version} is older than v{SCHEMA_VERSION} and no "
            "migration is available"
        )

    kind = data.get("kind")
    if kind != expected_kind:
        raise MalformedFileError(
            f"expected a '{expected_kind}' file, got kind={kind!r}"
        )

    return data
