# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Piece or weight: in what quantities a thing exists at all (D-212).

Amounts are stored as integer thousandths of a unit (`units.AMOUNT_SCALE`), and
that fractionality is there for a reason: half a kilo of ore is a real thing to
own. But the same fractionality reached the ingot, and half an ingot is not:
it could not be smelted, handed over or spent, yet it lay in the hands and the
market book all the same.

So the vault names what is **measured** -- ore, coal, sand, grain, water, oils,
acids, fibre, seed, harvest -- and everything it does not name is **counted**:
an ingot, a board, a brick, a nail, a coin, a tool. A counted thing exists in
whole pieces only, and this module is the one place that knows how to make a
number obey that.

Which way to round is not a detail:

* **what arrives is floored** (`whole`). Matter appears in whole pieces --
  three quarters of an ingot is no ingot at all, and inventing the fourth
  quarter would be minting matter out of rounding;
* **what is spent is raised** (`whole(..., up=True)`). A batch that needs two
  and a half boards takes three: the half board it does not use is not
  returned, because it was cut. This is the visible price of a piece being
  whole, and the forecast shows it before the batch starts (D-092).
"""

from __future__ import annotations

import math

from src.constants import Catalog, ConstantError, current_catalog
from src.constants.catalog import ItemKind
from src.engine.errors import Refusal
from src.units import AMOUNT_SCALE

#: Where a float stops being a number and becomes noise. Amounts live in
#: thousandths, so anything below that is the arithmetic's own dust: 2.9999999
#: boards are three boards, not two.
_DUST = 1 / AMOUNT_SCALE


class NotWhole(Refusal):
    """Asked for a fraction of a counted thing, and the fraction is nothing.

    Rounding down is enough while something is left: half a nail out of three
    is two nails. When nothing is left, silence would be a button that does
    nothing -- so the refusal says what the trouble is.
    """


def counted(name: str, catalog: Catalog | None = None) -> bool:
    """Whether the thing is counted in pieces rather than measured (D-212)."""
    return (catalog or current_catalog()).recipes.counted(name)


#: Kinds that fold into one stack. The other four -- tool, gear, station,
#: vehicle -- never do: each piece wears on its own and carries its own mark
#: (04-items, D-058).
LOOSE = (ItemKind.MATERIAL, ItemKind.CONSUMABLE, ItemKind.MONEY)


def stackable(name: str, catalog: Catalog | None = None) -> bool:
    """Whether two identical lots of this thing are one stack (04-items, D-214).

    Raw material has no recipe at all -- it comes out of a vein or a plot, not
    off a bench -- and it always stacks. Everything with a recipe is judged by
    the kind the vault gave it.
    """
    try:
        kind = (catalog or current_catalog()).recipes.recipe(name).kind
    except ConstantError:
        return True
    return kind in LOOSE


def whole(name: str, value: float, *, up: bool = False, catalog: Catalog | None = None) -> float:
    """The amount of this thing as the world can actually hold it.

    Measured things are returned untouched: fractions of them are honest. A
    counted thing is floored to whole pieces, or raised to them when the number
    is what a work is about to spend.
    """
    if not counted(name, catalog):
        return value
    return float(math.ceil(value - _DUST) if up else math.floor(value + _DUST))


def at_least_one(name: str, value: float, *, catalog: Catalog | None = None) -> float:
    """The amount to move, floored to pieces -- refusing when nothing is left.

    Used where the player named the amount: taking from a stack, putting into a
    chest, an order in the book. A quarter of an ingot is not a small move, it
    is no move at all.
    """
    got = whole(name, value, catalog=catalog)
    if got <= 0 < value:
        raise NotWhole(
            f"«{name}» считается штуками: {value:g} — это меньше одной, "
            "берут и кладут целыми"
        )
    return got
