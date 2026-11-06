"""Exceptions raised by the IO layer.

A malformed or unreadable file must fail with one of these — a clear, typed
error — never an unhandled crash (``WORKFLOW.md`` Phase 3 acceptance bar). The
base is :class:`ProcessSimIOError`; callers that only care about "the file was
bad" can catch that.
"""

from __future__ import annotations

__all__ = [
    "ProcessSimIOError",
    "MalformedFileError",
    "UnsupportedVersionError",
]


class ProcessSimIOError(Exception):
    """Base for every IO-layer failure (bad JSON, wrong kind, bad version)."""


class MalformedFileError(ProcessSimIOError):
    """The file isn't valid JSON, isn't a process-sim file, or is the wrong kind."""


class UnsupportedVersionError(ProcessSimIOError):
    """The file's ``schemaVersion`` is one this build can't read (too new or old)."""
