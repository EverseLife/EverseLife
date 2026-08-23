# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Real estate: plot purchase, deed, building (D-089, D-106, D-116, D-125, D-218).

## Buying civic land

An empty civic node may be bought by **any** player -- land is no longer only
handed out by the authority. The price per square metre is set by the state
via the code-law `land_price`; with each **node** from the bioprinter -- the
city centre -- land gets cheaper by `land.decay_per_node`. Distance is measured
over the graph: in steps from the node with the bioprinter, not by a property
written at generation (D-220). Proceeds go to the city treasury: the city sells
its land, not the engine.

## Deed

Ownership is documented by a deed (`models/estate.Deed`) -- an electronic
document. The deed lives in the Net: the body's death does not touch it, it
is managed remotely, like the account and orders. Sale is a sale contract: the
holder lists a price (open or addressed), the buyer pays -- and the deed
together with the plot passes to them in one transaction. No escrow is needed:
both money and title change hands at one moment.

## Building

On an empty plot a building is built first, and only in a building are
machines placed: a machine takes `build.slots_per_area` square metres, so a
house's area is its capacity, not decoration (D-106). Construction is work:
the materials are written off at once, the building rises on schedule at
`build.labor_per_m2` hours per metre -- as a journal job, like every
long-running task.

**The type is the house's whole character** (D-218). `build.types` says what
goes into the wall per square metre of floor -- timber, or stone and mortar, or
iron and glass; `build.floor_growth_by_type` says how much dearer each next
floor is; `build.decay_by_type` says how fast the thing falls apart. Expensive
materials buy not stronger walls but rarer repairs.

**The footprint is bounded, the height is not.** Ground is finite in the
physical sense: a footprint is a yard taken away, and the same metre cannot be
taken twice -- so it is checked against the plot, counting sites already
started. Height takes nothing from anybody but the builder's purse, so nothing
guards it except the bill: a twenty-storey log house may be built and will
never be worth building.

**A house wears out and at nothing it collapses.** Until then it stands at full
strength -- it loses neither places nor area -- and that is what keeps repair a
decision one takes rather than a levy one stops noticing.
"""

from __future__ import annotations

from src.engine.errors import Refusal

"""Real estate: plot purchase, deed, building (D-089, D-106, D-116, D-125, D-218).

## Buying civic land

An empty civic node may be bought by **any** player -- land is no longer only
handed out by the authority. The price per square metre is set by the state
via the code-law `land_price`; with each **node** from the bioprinter -- the
city centre -- land gets cheaper by `land.decay_per_node`. Distance is measured
over the graph: in steps from the node with the bioprinter, not by a property
written at generation (D-220). Proceeds go to the city treasury: the city sells
its land, not the engine.

## Deed

Ownership is documented by a deed (`models/estate.Deed`) -- an electronic
document. The deed lives in the Net: the body's death does not touch it, it
is managed remotely, like the account and orders. Sale is a sale contract: the
holder lists a price (open or addressed), the buyer pays -- and the deed
together with the plot passes to them in one transaction. No escrow is needed:
both money and title change hands at one moment.

## Building

On an empty plot a building is built first, and only in a building are
machines placed: a machine takes `build.slots_per_area` square metres, so a
house's area is its capacity, not decoration (D-106). Construction is work:
the materials are written off at once, the building rises on schedule at
`build.labor_per_m2` hours per metre -- as a journal job, like every
long-running task.

**The type is the house's whole character** (D-218). `build.types` says what
goes into the wall per square metre of floor -- timber, or stone and mortar, or
iron and glass; `build.floor_growth_by_type` says how much dearer each next
floor is; `build.decay_by_type` says how fast the thing falls apart. Expensive
materials buy not stronger walls but rarer repairs.

**The footprint is bounded, the height is not.** Ground is finite in the
physical sense: a footprint is a yard taken away, and the same metre cannot be
taken twice -- so it is checked against the plot, counting sites already
started. Height takes nothing from anybody but the builder's purse, so nothing
guards it except the bill: a twenty-storey log house may be built and will
never be worth building.

**A house wears out and at nothing it collapses.** Until then it stands at full
strength -- it loses neither places nor area -- and that is what keeps repair a
decision one takes rather than a levy one stops noticing.
"""


class EstateError(Refusal):
    pass


class NotForSale(EstateError):
    """This land is not for sale: it is either occupied or not civic."""


class NotEnoughMoney(EstateError):
    pass


class NotOwner(EstateError):
    """The owner disposes of land; of civic land -- the authority with the `land` right."""


class BadName(EstateError):
    """The name is empty or longer than reasonable. A nameplate is not a letter."""


class NoBuilding(EstateError):
    """No building on the plot: build first, then place machines (D-106)."""


class NoRoom(EstateError):
    """No room in the building: machines take area, and it ran out."""


class TooSmall(EstateError):
    """Below `build.area_min`: that is a lean-to, not a building (D-218)."""


class UnknownKind(EstateError):
    """No such building type in `build.types` (D-218)."""


class Ruined(EstateError):
    """Nothing to mend: the house is whole, or there is no house at all."""
