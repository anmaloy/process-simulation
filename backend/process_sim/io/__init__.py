"""Project files and library exchange (WORKFLOW Phase 3).

Built on the model's own JSON serialization:

- :mod:`.project`  — load/save a whole :class:`~process_sim.model.World` as a
  single versioned project file; save→load is lossless.
- :mod:`.library`  — export/import the reusable subset (items, recipes, machine
  defs) with id-collision handling, so a library drops into a different World.
- :mod:`.errors`   — typed failures; a malformed or wrong-version file raises one
  of these rather than crashing.

Every file is a versioned envelope (``schemaVersion`` + ``kind``), so future
format changes are detectable — see :mod:`._format`.
"""

from ._format import LIBRARY_KIND, PROJECT_KIND, SCHEMA_VERSION
from .errors import (
    MalformedFileError,
    ProcessSimIOError,
    UnsupportedVersionError,
)
from .library import (
    ConflictPolicy,
    ImportResult,
    Library,
    export_library,
    import_library,
    library_from_dict,
    library_to_dict,
    load_library,
    save_library,
)
from .project import (
    load_project,
    project_from_dict,
    project_to_dict,
    save_project,
)

__all__ = [
    # format / versioning
    "SCHEMA_VERSION",
    "PROJECT_KIND",
    "LIBRARY_KIND",
    # errors
    "ProcessSimIOError",
    "MalformedFileError",
    "UnsupportedVersionError",
    # project
    "project_to_dict",
    "project_from_dict",
    "save_project",
    "load_project",
    # library
    "Library",
    "ConflictPolicy",
    "ImportResult",
    "export_library",
    "import_library",
    "library_to_dict",
    "library_from_dict",
    "save_library",
    "load_library",
]
