"""Shared test helpers: load the real World JSON fixtures built during P2.

These fixtures (a furnace→chest chain, a powered multi-machine process) are the
engine's acceptance scenarios and are reused in P3 (io) and P4 (api).
"""

import json
from pathlib import Path

import pytest

from process_sim.model import World

FIXTURES = Path(__file__).parent / "fixtures"


def load_world(name: str) -> World:
    """Load a fixture World by file stem (e.g. ``"powered_process"``)."""

    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return World.from_dict(data)


@pytest.fixture
def furnace_chest() -> World:
    return load_world("furnace_chest")


@pytest.fixture
def powered_process() -> World:
    return load_world("powered_process")
