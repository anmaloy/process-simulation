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
    LIBRARY_KINDS,
    ConflictPolicy,
    ImportResult,
    Library,
    export_library,
    import_library,
    library_from_dict,
    library_ids,
    library_to_dict,
    load_library,
    merge_libraries,
    remove_from_library,
    save_library,
    sync_library,
)
from .mi_import import (
    CABLE_TIER_EU,
    DEFAULT_MACHINE_TYPES,
    MACHINE_CATALOG,
    MachineSpec,
    MiImport,
    import_mi_library,
    import_mi_sources,
)
from .pack_import import (
    EXPORT_RELPATH,
    EXPORTER_INSTALL_RELPATH,
    find_game_dir,
    import_pack,
    install_exporter,
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
    "LIBRARY_KINDS",
    "library_ids",
    "sync_library",
    "remove_from_library",
    "merge_libraries",
    # Modern Industrialization import
    "DEFAULT_MACHINE_TYPES",
    "CABLE_TIER_EU",
    "MACHINE_CATALOG",
    "MachineSpec",
    "MiImport",
    "import_mi_library",
    "import_mi_sources",
    # whole-modpack import
    "EXPORT_RELPATH",
    "EXPORTER_INSTALL_RELPATH",
    "find_game_dir",
    "import_pack",
    "install_exporter",
]
