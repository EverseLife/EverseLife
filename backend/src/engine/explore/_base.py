# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""explore: the words the search is spoken in -- its goals, its signs, its refusals.

Split out of `engine/explore.py` along its sections: the run, the odds and
the place found each grew a file of their own, and all three say `vein`,
`лес` and "already out in the field".
"""

from __future__ import annotations

from src.constants import Catalog
from src.engine import ruins
from src.engine.errors import Refusal

#: The vault operation from which the engine learns what is mined in this world at all.
MINING_OPERATION = "mining"

#: Count of finds made from this node. Lives in the node's properties:
#: depletion is a property of the place, not the player, and needs no migration (D-156).
FOUND_HERE = "surveyed"

#: Search goals. As strings, not an enumeration: the list grows with the map,
#: and the client names the goal with the same word as the engine.
LOT = "lot"
SITE = "site"
VEIN = "vein"
#: Woods to fell (D-191). The find is an ordinary wild node -- what makes it a
#: forest is the same place property the felling reads (D-177).
FOREST = "forest"
#: A room of a Forerunner city (D-232). The one goal that **reveals** instead of
#: creating: the city stood before anybody came, and the search opens its next
#: door (`engine.ruins`).
ROOM = ruins.ROOM
GOALS = (LOT, SITE, VEIN, FOREST, ROOM)

#: The goals in the player's own words, for a refusal that names what **is**
#: possible here rather than only what is not.
WORDS = {
    LOT: "участок",
    SITE: "новое место",
    VEIN: "жилу",
    FOREST: "лес",
    ROOM: "помещения Предтеч",
}

#: The place property both the search and the felling operation look at.
WOODS = "woods"
#: Stony ground and meadow (D-196): stone and wild flax are gathered by hand,
#: and that is the first step of the whole ladder.
STONES = "stones"
MEADOW = "meadow"

#: The mark of a city plot: a node the authority hands out inside its rings
#: (D-089). Land, whatever else it is -- so it carries soil like any other
#: (D-246).
PLOT = "plot"
#: Nobody's land beyond the walls. A city plot is not it.
WILD = "wild"


class ExploreError(Refusal):
    pass


class AlreadyOut(ExploreError):
    """A run is already going. One body cannot explore in two directions."""


class NotOut(ExploreError):
    """The body is not exploring: nowhere to return from."""


def mineable(catalog: Catalog) -> tuple[str, ...]:
    """What is mined in this world at all -- the `gives` list of the "Mining" operation.

    The engine keeps no species list: add a fifth in the vault and it appears
    both in the goal choice and in finds, without a code change (D-151).
    """
    operation = next(
        (op for op in catalog.recipes.operations if (op.id or op.name) == MINING_OPERATION),
        None,
    )
    return tuple(operation.gives) if operation is not None else ()
