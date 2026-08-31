# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""City administration: office, right, treasury (D-127, D-130, D-154, D-155).

The charter and code-laws had lain as data since D-130, but nobody could change
them: "city authority" did not exist as an entity. Exactly three things appear
here and not one more.

**Office** -- a record "identity holds a post in the city". What the post is
called is the city's decision: the engine does not care whether it is a
president or a minister of economy.

**Right** -- what the engine checks, and it can be narrow:

    law:import_duty   edit one code-law
    laws              all code-laws at once; covers any law:<id>
    charter           answer charter questions
    treasury          spend the treasury
    offices           appoint and dismiss offices
    land              allot civic plots
    dashboard         full snapshot of the economic panel
    justice           court and sanctions (declared, mechanics separate)

The list of specific laws is **not written** in code: it is exactly the one in
the vault's `data/laws.yaml`. Add a new code-law and a right for it appears at
once. There is and will be no branching on office titles here: otherwise every
new form of government would need a release (01-tech-notes, pattern 3).

**Treasury** -- the existing `city_treasury` account. Whoever has `treasury`
spends it; each spend is an ordinary posting with a ground, i.e. visible.

## Authority is in-person (D-155)

Decisions are made **in the city administration**: changing a law, answering
the charter, appointing, spending the treasury, allotting plots. Authority that
can be exercised from across the ocean needs neither a capital nor roads to it,
and seizing power becomes a matter of one click rather than geography.

Reading the panel stays remote (D-140): figures are information, they travel
over the Net. Presence is needed to **decide**, not to look.

## Where a law's value comes from

Three sources, and the order between them is strict:

1. the city's decision -- what the authority wrote into `city.laws`;
2. the vault default -- `laws.json`, so a new city works without filling in
   anything (D-130);
3. a vault constant, if the default is written as a reference like
   `` `energy.tariff_default` ``.

A city that decided nothing lives on defaults. These are not "default
settings" but the starting state: as soon as the authority decides otherwise,
the default stops meaning anything.

## Change of power (D-160, D-161, D-162)

Citizenship, polls and elections live next door: the citizen register is here,
the procedure is in `engine/vote.py`. From here the ruler is determined in two
ways: by appointment (default "founder indefinitely") and by **election**, if
the charter answered `ruler_selection: elected_citizens`. A recall vacates the
office and the city goes straight to an election.

For the engine the ruler is not a named post but **the office with the widest
set of rights**: no branching on titles here, ever (D-154).

## What is not here

The council (`council_exists`), sortition and inheritance of power, and
amending the charter itself by vote. The charter describes them, the data is
there, the mechanics will arrive as their own task.
"""

from __future__ import annotations

from src.engine.errors import Refusal
from src.models.city import (
    Power,
)

"""City administration: office, right, treasury (D-127, D-130, D-154, D-155).

The charter and code-laws had lain as data since D-130, but nobody could change
them: "city authority" did not exist as an entity. Exactly three things appear
here and not one more.

**Office** -- a record "identity holds a post in the city". What the post is
called is the city's decision: the engine does not care whether it is a
president or a minister of economy.

**Right** -- what the engine checks, and it can be narrow:

    law:import_duty   edit one code-law
    laws              all code-laws at once; covers any law:<id>
    charter           answer charter questions
    treasury          spend the treasury
    offices           appoint and dismiss offices
    land              allot civic plots
    dashboard         full snapshot of the economic panel
    justice           court and sanctions (declared, mechanics separate)

The list of specific laws is **not written** in code: it is exactly the one in
the vault's `data/laws.yaml`. Add a new code-law and a right for it appears at
once. There is and will be no branching on office titles here: otherwise every
new form of government would need a release (01-tech-notes, pattern 3).

**Treasury** -- the existing `city_treasury` account. Whoever has `treasury`
spends it; each spend is an ordinary posting with a ground, i.e. visible.

## Authority is in-person (D-155)

Decisions are made **in the city administration**: changing a law, answering
the charter, appointing, spending the treasury, allotting plots. Authority that
can be exercised from across the ocean needs neither a capital nor roads to it,
and seizing power becomes a matter of one click rather than geography.

Reading the panel stays remote (D-140): figures are information, they travel
over the Net. Presence is needed to **decide**, not to look.

## Where a law's value comes from

Three sources, and the order between them is strict:

1. the city's decision -- what the authority wrote into `city.laws`;
2. the vault default -- `laws.json`, so a new city works without filling in
   anything (D-130);
3. a vault constant, if the default is written as a reference like
   `` `energy.tariff_default` ``.

A city that decided nothing lives on defaults. These are not "default
settings" but the starting state: as soon as the authority decides otherwise,
the default stops meaning anything.

## Change of power (D-160, D-161, D-162)

Citizenship, polls and elections live next door: the citizen register is here,
the procedure is in `engine/vote.py`. From here the ruler is determined in two
ways: by appointment (default "founder indefinitely") and by **election**, if
the charter answered `ruler_selection: elected_citizens`. A recall vacates the
office and the city goes straight to an election.

For the engine the ruler is not a named post but **the office with the widest
set of rights**: no branching on titles here, ever (D-154).

## What is not here

The council (`council_exists`), sortition and inheritance of power, and
amending the charter itself by vote. The charter describes them, the data is
there, the mechanics will arrive as their own task.
"""


class CityError(Refusal):
    pass


class NoCity(CityError):
    pass


class NotAllowed(CityError):
    """No power. Authority is a record, not self-confidence."""


class NotEnoughTreasury(CityError):
    """The treasury does not have that much. An empty treasury is a political event."""


class NotReady(CityError):
    """No city yet: buildings are missing without which it is not a city (D-023)."""


class NotYours(CityError):
    """A city is founded on your own land. Somebody else's yard will not do."""


#: The founder's powers. A city arises governed: if the founder got an empty
#: set, the very first city would have no authority at all (D-130).
FOUNDER_POWERS: tuple[str, ...] = tuple(power.value for power in Power)


FOUNDER_TITLE = "Президент"


#: The thing class of machines that make a node an administration: what a
#: building is, is set by the machine in it (D-106, D-215).
HALL = "administration"
