"""Anchor test for the pytest runner (Phase 0).

Replaced by real engine/model tests as Phases 1+ land.
"""

import process_sim


def test_package_imports() -> None:
    assert process_sim.__version__ == "0.0.0"


def test_subpackages_importable() -> None:
    import process_sim.api  # noqa: F401
    import process_sim.engine  # noqa: F401
    import process_sim.io  # noqa: F401
    import process_sim.model  # noqa: F401
