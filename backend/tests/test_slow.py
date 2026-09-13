# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The race family's pause holds what it says it holds (`conftest._slow`).

Every race test stands on `_slow`, and a pause that lands off the path fails
nothing: the test stays green and stops testing its lock. That is how the
package cuts broke the family without a single red test -- a delay set on the
door missed every call made inside the package. So the pause itself is pinned
here, with no database: which namespaces it takes, that it gives them back,
and that no room of a package is left to be imported after it was set.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from conftest import BACKEND, _slow
from src.engine import gear, mining
from src.engine.gear import load
from src.engine.mining import _base as mining_base
from src.engine.mining import collapse, face


def test_a_package_is_held_in_the_door_and_in_every_room() -> None:
    """The door, the room that defines the function, and nothing left fast."""
    original = gear.carried_mass
    assert load.carried_mass is original, "the door re-exports the room's function"
    patch = pytest.MonkeyPatch()
    try:
        _slow(patch, gear, "carried_mass")
        assert gear.carried_mass is not original
        assert load.carried_mass is gear.carried_mass, "one pause, wherever the name is read"
    finally:
        patch.undo()
    assert gear.carried_mass is original
    assert load.carried_mass is original, "the room is given back with the door"


def test_a_plain_module_is_held_by_name_only() -> None:
    """A room passed on its own is a plain module: the test chose that path."""
    original = mining_base.session_container
    assert face.session_container is original and collapse.session_container is original
    patch = pytest.MonkeyPatch()
    try:
        _slow(patch, face, "session_container")
        assert face.session_container is not original
        assert mining_base.session_container is original
        assert collapse.session_container is original
        assert mining.session_container is original
    finally:
        patch.undo()
    assert face.session_container is original


#: Run in a process of its own: in this one, whatever an earlier test happened
#: to call has long been imported, and the check would pass by accident.
_ROOMS_LOADED = """
import importlib, pkgutil, sys
from pathlib import Path

import src.engine as engine

root = Path(engine.__file__).parent
packages = sorted(p.parent for p in root.rglob("__init__.py") if p.parent != root)
for package in packages:
    importlib.import_module(".".join(("src", *package.relative_to(root.parent).parts)))
missing = []
for package in packages:
    name = ".".join(("src", *package.relative_to(root.parent).parts))
    for info in pkgutil.iter_modules([str(package)]):
        if f"{name}.{info.name}" not in sys.modules:
            missing.append(f"{name}.{info.name}")
print("\\n".join(missing))
"""


def test_importing_a_door_loads_every_room() -> None:
    """No room is left to be imported after a pause is set.

    `_slow` takes the rooms loaded at the moment it is called. A room imported
    for the first time later -- lazily, inside the call under test -- would
    take the pause by `from ... import`, and `undo` would not know of that
    binding: the delay would leak into every later test of the worker. Every
    engine package loads all its rooms from its door today; this keeps it so.
    """
    done = subprocess.run(
        [sys.executable, "-c", _ROOMS_LOADED],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    )
    assert not done.stdout.split(), f"rooms a door does not import: {done.stdout.split()}"
