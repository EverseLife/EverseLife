# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Exploration: the map grows on foot, not by patch (D-152).

The world was set by the seed and did not grow: a plot could be taken only
where a node was already drawn, and new veins never appeared. Exploration
answers where the world beyond the walls comes from, and the answer "the
developers drew it" contradicts the design.

## Three search goals, and they differ

One seeks not "something" but what is needed. The goal is chosen before
leaving, and what ends up on the map depends on it:

| Goal | Where sought | What is found |
|---|---|---|
| `lot` | on the city layer | a free plot for building -- civic land (D-089) |
| `site` | on the planet | a place for a future city: a wild node with properties |
| `vein` | on the planet | a vein; the species can be named in advance |

**A named species is found worse than an unnamed one.** The chance is
multiplied by its share in the mining pace (`harvest.rates`): copper is rarer
than iron, and aiming at the rare means coming back empty more often.
Otherwise everyone would seek only the most expensive, and exploration would
become a faucet.

## How a run works

A run is an ordinary journal job: it goes offline, survives a restart and fires
exactly once. At the deadline a roll against the chance; for a vein without a
named species `explore.vein_share` applies as well.

**An empty run is normal.** Without it the map would grow by click, and
exploration would become a formality.

## The run's price is a property of the place, not the player (D-156)

Every node has a count of finds made when leaving from it. While the
surroundings are untrodden a run lasts `explore.attempt_minutes` -- minutes --
and the chance `explore.find_chance` is close to certain. Each find from this
node multiplies the duration by `explore.effort_growth` and the chance by
`explore.find_decay`, until the duration hits the ceiling
`explore.attempt_hours` and the chance the floor `explore.find_floor`.

**Stamina is charged by time in the field:** `explore.attempt_stamina` is the
price of a full-length run; a one-minute one costs correspondingly less.
Otherwise stamina would lock early runs instead of hours, and the fix would
amount to swapping one lock for another.

**Without the strength for it nobody leaves** (D-147, D-293). The length of a run is
rolled at departure, so it is asked for by the longest the place can give --
the very number the forecast shows -- and paid for by the length that came up.
Any lower threshold would be a re-throw of the dice on the second press.

The count lives on the node, not on the player: an exploration level would be
character progress and turn the world into a backdrop for grinding. A trodden
neighbourhood grows poorer for everyone at once, and a run from a fresh find is
cheap again -- so the map grows in breadth, not as a star from the birthplace.

## Crowding turns a city outwards (D-207)

Depletion is about the neighbourhood; crowding is about the **shape** of the map.
A find is an edge, and edges pile up where everybody wants to be: at the
bioprinter, because the centre is where one wants to live, and at the city gate,
because everything wild couples to it (D-206). Left alone that grows a star of
thirty edges -- a place one can neither walk through nor look at.

So the chance is multiplied by the crowding of the node the find will **hang
on**: its own edges plus its neighbours' extra ones, `explore.crowding_decay` per
edge over `explore.crowding_free`, never below `explore.crowding_floor`. The
centre saturates first, and the next plot is sought where edges are few -- in the
outer rings. The city grows in rings because searching the centre stops paying,
not because a rule forbids it.

**The chance is promised at departure, not at return.** It is computed at the
moment of leaving and travels in the job: while the scout is in the field the
neighbours may tread the area, but the price is already named, and changing it
retroactively is dishonest.

## What exactly is found

The vein's species is chosen from what is mined at all -- the `gives` list of
the "Mining" operation in the vault. A species' weight equals its pace in
`harvest.rates`: the rare is mined slower, so it also turns up rarer. The
engine keeps no list of "which ores exist": add a fifth species in the vault
and it starts being found without a code change (D-151).

A place's merits are rolled under a common budget `site.quality_budget`: a
place good in everything never drops (D-126). A river eats part of the budget,
and the more water, the less is left for fertility.

**What is found belongs to nobody.** The finder gets the right of first night,
not ownership: a plot is taken in person, like any wild land.

## Where each part of it lives

The file grew past what one file should hold, and it was three subjects all
along -- so it is three now, and this one is the door:

* `_base` -- the words all three speak: the goals, the place signs, the refusals;
* `odds` -- what the next run from here is worth: depletion, crowding, the
  forecast the player is shown and the roll the run is made by;
* `site` -- the place found: its ground rolled under a common budget, its signs
  and its vein;
* `run` -- the walk between them: setting out, coming back, calling it off.

The door publishes what the world outside the package actually asks for.
What one section says to another and nobody else -- `site.lay`, which lays the
found node, and the arithmetic of a run's length -- stays behind it: `lay` was
private for a reason, and "anybody may lay a find without a search" is that
reason.
"""

from src.engine.explore._base import (  # noqa: F401
    FAR,
    FOREST,
    FOUND_HERE,
    GOALS,
    LOT,
    MEADOW,
    MINING_OPERATION,
    NEAR,
    PLOT,
    REACHES,
    ROOM,
    SITE,
    STONES,
    VEIN,
    WILD,
    WOODS,
    AlreadyOut,
    ExploreError,
    NoStrength,
    NotOut,
    mineable,  # noqa: F401
)
from src.engine.explore.odds import (  # noqa: F401
    aim_at,
    anchor_of,
    chance,
    crowding,
    found_here,
    outlook,
    possible,
    price,
)
from src.engine.explore.run import (  # noqa: F401
    cancel,
    pending,
    returned,
    survey,
)
from src.engine.explore.site import (  # noqa: F401
    civic_properties,
    properties,
    species_of,
)
