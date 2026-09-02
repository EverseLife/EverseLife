# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""City administration: office, right, treasury (D-127, D-130, D-154, D-155).

The charter and code-laws had lain as data since D-130, but nobody could change
them: "city authority" did not exist as an entity. Three things were added for
that, and they are described below.

The package has grown past those three since -- land, treasury, grants,
succession, and the four modules `polity.py` was cut into (`founding`, `law`,
`office`, `citizen`, with `hall` beside them). This file is the whole of what
the city offers the rest of the engine: everything importable as `town.<name>`
is re-exported here, and the modules underneath are free to move without any
caller noticing.

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

A package: one module per section of the old file; this file re-exports
the names so `from src.engine import city` reads as before.
"""

from src.engine.city._base import (  # noqa: F401
    FOUNDER_POWERS,
    FOUNDER_TITLE,
    HALL,
    CityError,
    NoCity,
    NotAllowed,
    NotEnoughTreasury,
    NotReady,
    NotYours,
)
from src.engine.city.citizen import (  # noqa: F401
    ADMISSION,
    APPLICATION,
    INVITE,
    OPEN,
    AlreadyCitizen,
    Bound,
    NotCitizen,
    _enrol_founder,
    _enroll,
    admission,
    admit,
    bind,
    citizens_of,
    citizenship,
    describe,
    exile,
    exited,
    invite,
    is_citizen,
    join,
    leave,
    request_of,
    requests_of,
)
from src.engine.city.founding import (  # noqa: F401
    _mark_gate,
    _retire_deed,
    establish,
    found,
    foundation_needs,
    install_founder,
    missing_for_foundation,
)
from src.engine.city.grant import (  # noqa: F401
    welcome,
)
from src.engine.city.hall import require_at_hall  # noqa: F401
from src.engine.city.land import (  # noqa: F401
    allot,
    cede,
    survey,
    upkeep_of,
)
from src.engine.city.law import (  # noqa: F401
    SPAWN_CITIZENSHIP,
    SPAWN_TERM,
    TRADE_TAX,
    _apply_tariff,
    apply_law,
    law,
    law_number,
    may_take_city_land,
    set_charter,
    set_law,
    spawn_terms,
)
from src.engine.city.lookup import (  # noqa: F401
    by_id,
    by_node,
    core,
    gate,
    of_node,
    territory,
)
from src.engine.city.office import (  # noqa: F401
    _office,
    appoint,
    covers,
    may,
    offices,
    powers_of,
    require,
    revoke,
)
from src.engine.city.succession import (  # noqa: F401
    dismiss,
    hand_over,
    ruler,
    schedule_term,
    term_ended,
)
from src.engine.city.treasury import (  # noqa: F401
    spend,
    treasury,
    treasury_balance,
)
