"""File-format plumbing shared by project and library IO.

Every on-disk file is an *envelope*: a JSON object carrying a ``schemaVersion``
and a ``kind`` around its payload. The version makes future format changes
detectable (a file from a newer or older schema fails with a clear error rather
than silently misloading); the kind stops a library being loaded as a project
or vice versa.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .errors import MalformedFileError, UnsupportedVersionError

__all__ = [
    "SCHEMA_VERSION",
    "MIN_READABLE_VERSION",
    "PROJECT_KIND",
    "LIBRARY_KIND",
    "read_json",
    "write_json",
    "validate_envelope",
]

#: The schema this build *writes*. Bump when the on-disk shape changes.
#:
#: v2 added the hybrid per-recipe energy term (``euPerTick`` / ``machineType``),
#: tag resources (``isTag`` on items, ``tag`` on stacks), and machine power tiers
#: (``voltageTier`` / ``maxEuPerTick``). Every v2 field is optional and additive,
#: so a v1 payload *is* a valid v2 payload — migration from v1 is a no-op beyond
#: the version stamp, and ``from_dict`` fills the new fields as absent.
SCHEMA_VERSION = 2

#: The oldest schema this build still reads. Files from here up to
#: ``SCHEMA_VERSION`` load (migrating forward where needed); older ones are
#: rejected with a clear error.
MIN_READABLE_VERSION = 1

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
    """Write ``data`` as pretty-printed JSON with a trailing newline, atomically.

    The payload is written to a sibling temp file and then ``os.replace``'d into
    place, so a crash or a window closed mid-write can never leave a truncated,
    unparseable file behind (which, as an autosave, would otherwise be silently
    discarded on the next launch — losing the whole world). The replace is atomic
    on the same filesystem; readers see either the old file or the complete new
    one, never a partial write.
    """

    path = Path(path)
    text = json.dumps(data, indent=2) + "\n"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


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
    if version < MIN_READABLE_VERSION:
        raise UnsupportedVersionError(
            f"file schema v{version} is older than v{MIN_READABLE_VERSION} and no "
            "migration is available"
        )
    # Versions in [MIN_READABLE_VERSION, SCHEMA_VERSION] are read directly: every
    # bump so far has been additive/optional, so an older payload is a valid
    # current one and ``from_dict`` fills the newer fields as absent.

    kind = data.get("kind")
    if kind != expected_kind:
        raise MalformedFileError(
            f"expected a '{expected_kind}' file, got kind={kind!r}"
        )

    return data
