# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Balance constants and catalogs -- the only source of numbers (D-065).

The engine keeps no numeric values of its own: everything comes from the
game-design vault's `build/`. Here: loading, typing and hot replacement.
"""

from __future__ import annotations

from pathlib import Path

from src.constants.catalog import (
    CATALOG_HOLDER,
    Catalog,
    current_catalog,
    load_catalog,
)
from src.constants.loader import HOLDER, Constants, current, load_constants
from src.constants.registry import declared
from src.constants.spec import ConstantError, Formula, Range, Tier

__all__ = [
    "CATALOG_HOLDER",
    "HOLDER",
    "Catalog",
    "ConstantError",
    "Constants",
    "Formula",
    "Range",
    "Tier",
    "bootstrap",
    "current",
    "current_catalog",
    "load_catalog",
    "load_constants",
]


def bootstrap(build_dir: Path, overrides: dict | None = None) -> tuple[Constants, Catalog]:
    """Load and check everything the engine depends on.

    Called at process start. A missing constant must fail here -- not in
    production (01-tech-notes, pattern 4).
    """

    constants = load_constants(build_dir)
    if overrides:
        constants = constants.with_overrides(overrides)
    constants.validate(declared())
    catalog = load_catalog(build_dir)
    HOLDER.set(constants)
    CATALOG_HOLDER.set(catalog)
    return constants, catalog
