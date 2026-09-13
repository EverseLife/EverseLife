# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""Carried load: mass, limit and gear slots (D-146, D-129).

The carry limit was in the vault from the very start -- `inventory.carry_mass`,
"everything above -- only by vehicle" -- but items had no mass, and it meant
nothing. A player carried a thousand ore in the pocket, and the geography
everything was built for cost nothing.

## How it is computed

**Load** is the sum of masses of everything in the hands, including what is
worn -- an exoskeleton does not become weightless because it is put on -- and
including what those things hold inside (D-313): a full canister weighs its
fill, a full chest weighs what is stacked in it. A worn pack lightens the
first kilograms it holds (`inventory.pack`, D-268); the rest weighs what it
weighs.

**Limit** is `inventory.carry_mass` plus `inventory.exo_bonus` for a worn
exoskeleton -- while a charged battery rides in the hands (D-268). A pack
raises nothing; clothes and armour take the slot but add nothing to carry --
their effect arrives with environment and combat.

**One slot per thing.** Without slots a player would wear three backpacks and
the limit would cease to exist; the slot is the constraint itself, not an
interface decoration.

**Worn means in the hands** (D-305). The slot names a thing, and a thing goes
where hands take it; a slot naming a pack that lies on the floor names
nothing. `is_worn` holds that rule for the whole engine -- the load, the
exoskeleton's lift, the suit that breathes (`oxygen.suited`) and the suit that
warms (`frost`) all ask it rather than the bare row -- and `require_off` is the
same rule said to a player: a worn thing comes off before it goes anywhere.

## Where the limit is checked

Where the player **takes a thing in hand**: purchase from the terminal,
harvest, emptying a hopper. This is not an error message but the reason
wagons, caravans and the carter's profession exist.

Two doors, because the question has two shapes. `check_carry` asks about
**goods by name** -- a harvest, a poured litre, an hour at the face: matter
that arrives without a row of its own and holds nothing. `check_carry_thing`
asks about a **row that moves whole** -- off the floor, out of a chest, out
of a hold, from another's hands -- and that one weighs what is inside it
(D-313). A door that moves a thing and asks the first question has a hole
the size of the thing's contents.

What is made at a machine does not fall under the limit: it lies where it was
made and becomes a load only when taken. Likewise with what is mined at the
face -- it stays at the face until somebody comes for it, and with a machine
taken down off its stand: `station.take` leaves it lying, and the limit
answers at the pick-up (D-308).

## What is not here yet

* **Volume.** `inventory.carry_volume` exists in the vault, items have no
  volume. Creating it in code would mean inventing data that does not exist (D-065);
* **Transport.** It is the answer to the limit (D-107) and arrives with its
  own mechanic: cargo finally has mass, and `transport.mass_*` were waiting for it.

## Where it lives

The package is a stack, and each room names only the ones below it
(`backend/pyproject.toml` pins it):

* `_base` -- the refusals and the mass of a thing: what it weighs, whether it
  holds anything inside. Asks nobody;
* `worn` -- the worn rule (D-305): `is_worn`, `require_off`, `equipped`;
* `load` -- the load and the limit: the masses, the pack's bent line, the
  exoskeleton's lift and the two doors that check a pick-up;
* `slot` -- putting on and taking off, and every road to a fallen limit:
  a frame taken off, a charge run dry (`wear_exoskeletons`), a worn thing
  worn through (`losing_worn`) -- and the one settle they share.
"""

from src.engine.gear._base import (  # noqa: F401
    INSIDE_KINDS,
    GearError,
    NotGear,
    Overloaded,
    Unmade,
    Worn,
    has_store,
    holds_things,
    is_vehicle_kind,
    mass_of,
)
from src.engine.gear.load import (  # noqa: F401
    capacity,
    carried_mass,
    check_carry,
    check_carry_thing,
    exo_bonus,
    inner_mass,
    load_of,
    matter_over,
    matter_within,
    moved_inside,
    packed,
    room_for,
)
from src.engine.gear.slot import (  # noqa: F401
    equip,
    losing_worn,
    settle_lost,
    unequip,
    wear_exoskeletons,
)
from src.engine.gear.worn import (  # noqa: F401
    equipped,
    is_worn,
    require_off,
)
