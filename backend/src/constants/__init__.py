# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Balance constants and catalogs -- the only source of numbers (D-065).

The engine keeps no numeric values of its own: everything comes from the
game-design vault's `build/`. Here: loading, typing and hot replacement.
"""

from __future__ import annotations

from pathlib import Path

from src import i18n
from src.constants.catalog import (
    CATALOG_HOLDER,
    Catalog,
    current_catalog,
    load_catalog,
)
from src.constants.loader import (
    HOLDER,
    Constants,
    current,
    load_constants,
    normalize_constants,
)
from src.constants.registry import declared
from src.constants.renames import (
    RENAMES_HOLDER,
    RenameTable,
    current_renames,
    display_name,
    load_renames,
)
from src.constants.spec import ConstantError, Formula, Range, Tier

__all__ = [
    "CATALOG_HOLDER",
    "HOLDER",
    "RENAMES_HOLDER",
    "Catalog",
    "ConstantError",
    "Constants",
    "Formula",
    "Range",
    "RenameTable",
    "Tier",
    "bootstrap",
    "current",
    "current_catalog",
    "current_renames",
    "display_name",
    "load_catalog",
    "load_constants",
    "load_renames",
]


#: Where the languages live. Beside the engine rather than in the vault: the
#: vault holds the design and the names of things, while a refusal's wording
#: is the engine's own voice and changes with the code that raises it.
LOCALES_DIR = Path(__file__).resolve().parent.parent.parent / "locales"


def bootstrap(
    build_dir: Path,
    overrides: dict | None = None,
    locales_dir: Path | None = None,
) -> tuple[Constants, Catalog]:
    """Load and check everything the engine depends on.

    Called at process start. A missing constant must fail here -- not in
    production (01-tech-notes, pattern 4).
    """

    renames = load_renames(build_dir)
    #: Set before the words are read: `NAME()` resolves through this table,
    #: and a message rendered during the boot would find nothing otherwise.
    RENAMES_HOLDER.set(renames)
    #: The words of every language, so that a refusal can be said (D-251 wave
    #: III). Loaded here for the same reason as the constants: a locale that
    #: does not parse must stop the boot, not the evening's first refusal.
    i18n.HOLDER.set(i18n.load_words(locales_dir or LOCALES_DIR, names=display_name))
    constants = load_constants(build_dir, renames)
    if overrides:
        #: Admin edits written before wave II may still key sub-dicts by the
        #: Russian names; the same normalization brings them onto ids.
        constants = constants.with_overrides(normalize_constants(overrides, renames))
    constants.validate(declared())
    catalog = load_catalog(build_dir, renames)
    HOLDER.set(constants)
    CATALOG_HOLDER.set(catalog)
    return constants, catalog
