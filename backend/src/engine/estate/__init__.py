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

A package: one module per section of the old file; this file re-exports
the names so `from src.engine import estate` reads as before.
"""

from src.engine.estate._base import (  # noqa: F401
    BadName,
    EstateError,
    NoBuilding,
    NoRoom,
    NotEnoughMoney,
    NotForSale,
    NotOwner,
    Ruined,
    TooSmall,
    UnknownKind,
)
from src.engine.estate.building import (  # noqa: F401
    _equipment,
    bill,
    build_minutes,
    buildings_of,
    built_area,
    composition,
    construct,
    estimate,
    floor_growth,
    floor_mass,
    free_ground,
    kinds,
    planned_footprint,
    slots,
    space,
    under_construction,
)
from src.engine.estate.deed import (  # noqa: F401
    buy_deed,
    deeds_of,
    deeds_on_sale,
    issue_deed,
    offer_deed,
)
from src.engine.estate.demolition import (  # noqa: F401
    demolish,
    demolish_blockers,
    demolish_minutes,
    demolishing,
    finish_build,
    finish_demolish,
    salvage,
)
from src.engine.estate.price import (  # noqa: F401
    EMBLEM_PROPERTY,
    EMBLEMS,
    _measure_city,
    buy,
    center_of,
    emblem,
    forget_distances,
    is_vacant,
    land_tax_of,
    levy_land_tax,
    may_name,
    nodes_from_center,
    note_new_place,
    price_of,
    public_emblem,
    rename,
)
from src.engine.estate.upkeep import (  # noqa: F401
    collapse,
    decay,
    decay_per_day,
    finish_repair,
    missing_share,
    pause,
    repair,
    repair_bill,
    repair_minutes,
    repairing,
)
