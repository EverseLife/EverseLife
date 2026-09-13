# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The carry limit's vocabulary and floor: every refusal the load can make,
what a thing weighs, and whether a thing can hold anything inside it. Asks
nobody above itself.
"""

from __future__ import annotations

from src.constants import Catalog
from src.constants.catalog import ItemKind
from src.engine.errors import Refusal

# Which containers a thing owns is the model's word now: the world's floor
# ends a thing together with them (`world.destroy`) and cannot call up here.
# Kept importable from this floor for `gear.load` and the package's door.
from src.models.inventory import INSIDE_KINDS  # noqa: F401 -- re-exported


class GearError(Refusal):
    pass


class NotGear(GearError):
    """This thing is not worn: an item's slot comes from vault data."""


class Overloaded(GearError):
    """No more than the limit is taken in hand. Everything above -- only by vehicle."""


class Worn(GearError):
    """A worn thing is not moved, sold or taken apart: it comes off first."""


class Unmade(GearError):
    """A thing already being taken apart is not put on: the work ends it."""


def mass_of(catalog: Catalog, type_key: str, quantity: float) -> float:
    """The mass of this much of this item, kg."""
    return catalog.recipes.mass_of(type_key) * quantity


def has_store(catalog: Catalog, type_key: str) -> bool:
    """Whether the vault gives this thing capacity (`store`, D-181).

    The primitive `storage.is_storage` is built on; it lives here because the
    carry limit is below both `storage` and `transport` in the import order
    and cannot call up to either.
    """
    try:
        return bool(catalog.recipes.recipe(type_key).store)
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


def is_vehicle_kind(catalog: Catalog, type_key: str) -> bool:
    """Whether the vault calls this a vehicle (`kind: vehicle`, D-090, D-157).

    The primitive `transport.is_vehicle` is built on -- see `has_store`.
    """
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.VEHICLE
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


def holds_things(catalog: Catalog, type_key: str) -> bool:
    """Whether a thing of this kind can have anything inside it: a storage has
    capacity, a vehicle has a hold. One question, so no door forgets a half."""
    return has_store(catalog, type_key) or is_vehicle_kind(catalog, type_key)
