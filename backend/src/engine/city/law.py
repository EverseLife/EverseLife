# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Code-laws and the charter: the numbers and rules a city sets for itself.

Two different things with one shape. A **code-law** is a number the city may
move inside limits the vault fixes (a trade tax, a land price); the **charter**
is the answer to a question about how the city is arranged (who admits
citizens, who holds power). Both are read the same way -- the city's own
decision when it has one, the vault's default when it does not -- so they live
together and are set apart only by which list they come from.

Reading a law is free and unguarded; setting one asks `office.require` and
`hall.require_at_hall`, and inside this package that is the only direction of
dependency: a law does not name a citizen, `citizen` names a law.

Only inside the package, though. `set_law` opens a ballot through
`engine.vote`, and `vote` reaches back into the city for who may vote -- the
`city -> vote -> city` cycle is older than this cut and survives it. What the
split fixed is the tangle among the city's own four modules; that one is still
waiting for wave 3 of the review.
"""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants.spec import Num
from src.engine import events
from src.engine import vote as ballots
from src.engine.city._base import (
    CityError,
)
from src.engine.city.hall import require_at_hall
from src.engine.city.office import require
from src.models.city import (
    LAW_SCOPE,
    City,
    Power,
)
from src.models.energy import EnergyPool
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.vote import VoteKind
from src.models.world import Node


def law(catalog: Catalog, city: City, law_id: str):
    """A code-law's value: the city's decision, otherwise the vault default.

    Returned **as is**, and no branching on law type here (D-094): the consumer
    parses its own value.

    What "as is" holds is worth naming once, because three consumers read it
    (`customs`, the city panel, `vote.open_law`). A law written by the interface
    is **text**: `set_law` takes a string, and a law that is not a number or a
    word -- duty as a map "goods -> rate and norm" (D-123), a ban as a list --
    arrives as the JSON of it and is stored as that JSON. A vault default, on
    the other hand, is whatever the vault wrote, map included. So a consumer of
    a table law takes both: a mapping, or the text of one (`customs._unpacked`).
    """
    own_items = city.laws or {}
    if law_id in own_items:
        return own_items[law_id]
    return catalog.laws.code_law_defaults().get(law_id)


def law_number(constants: Constants, catalog: Catalog, city: City | None, law_id: str) -> float:
    """The same as a number. A default like `` `energy.tariff_default` `` expands
    into a vault constant: a law may reference it, the engine may not (D-065)."""
    raw = (
        catalog.laws.code_law_defaults().get(law_id) if city is None else law(catalog, city, law_id)
    )
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if text.startswith("`") and text.endswith("`"):
        return float(constants[Num(text.strip("`"))])
    try:
        return float(text)
    except ValueError:
        #: "no", "empty", "free" -- a law given not as a number. For a numeric
        #: consumer that is zero, and that is more honest than an exception.
        return 0.0


def shown(constants: Constants, catalog: Catalog, city: City, law_id: str) -> str | None:
    """The law's value as it is **read**: text, ready to be shown or recorded.

    The rule in force, own or default, with a vault reference expanded into
    the number it stands for -- a player must see the tariff, not
    `` `energy.tariff_default` ``. Two callers and one answer: the city panel
    draws it, and the event of a change records what it was before, so a
    chronicle line saying «было 3, стало 1» tells the truth about the rule
    rather than about the column.
    """
    raw = law(catalog, city, law_id)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        #: A composite law (duty) goes to the client as is: showing it as a
        #: string would force the client to parse it back.
        return json.dumps(raw, ensure_ascii=False)
    text = str(raw).strip()
    if text.startswith("`") and text.endswith("`"):
        return _plain(law_number(constants, catalog, city, law_id))
    return text


def _plain(value: float) -> str:
    """A number without trailing zeros: tariff "5", not "5.0"."""
    whole = int(value)
    return str(whole) if value == whole else str(value)


async def set_law(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    by: Identity,
    city: City,
    law_id: str,
    value: str,
    *,
    body=None,
) -> City:
    """Write a code-law. Only the power and the law's existence are checked.

    The engine does not interpret the value: a law is interpreted data, and
    branching on its meaning lives at the consumer (tax at the order book,
    tariff at the pool), not here. The one exception is the tariff: it lies in
    the pool as a separate column, and that column has to be moved, otherwise
    the authority's decision never reaches the meter.
    """
    await require_at_hall(session, body, city)
    known = {law.id for law in catalog.laws.code_laws}
    if law_id not in known:
        raise CityError(key="city-no-such-law", law=law_id)
    #: The right is narrow: the "minister of economy" edits duties and does not
    #: touch the tax (D-155). A `laws` holder is covered by the same check. The
    #: charter may add a council to the authority: "the council proposes laws"
    #: means as many legislators as seats, and the ruler is not the only one
    #: among them (D-164).

    if not await ballots.may_propose(session, city, by.id):
        await require(session, by.id, city, f"{LAW_SCOPE}{law_id}")

    #: The charter may hand approval to the citizens (D-161). Then the authority
    #: does not change the law but convenes a poll: the right to propose a law
    #: and the right to approve it are different things, and that is exactly
    #: what `law_approval` asks. Both the council and all citizens may approve
    #: -- the charter decides which (D-161, D-164). In both cases the ruler
    #: convenes rather than decides.

    voters = ballots.voters_for(city, VoteKind.LAW)
    if ballots.by_citizens(city) or voters == ballots.COUNCIL_VOTERS:
        await ballots.open_law(session, constants, city, by, law_id, value)
        return city

    await apply_law(session, constants, catalog, city, law_id, value, by=by)
    return city


async def apply_law(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    law_id: str,
    value,
    *,
    by: Identity | None = None,
) -> None:
    """Write a decided law into the city, carry it through and announce it.

    One step for both roads to the same decision: the authority's own
    (`set_law`) and the citizens' (`vote.close`). What follows the writing is
    not the writing -- the tariff has to reach the meter, and the world has to
    be told what changed -- and a second copy of that on the second road went
    stale on the day it was written: a tariff voted through by the citizens
    never reached the pool, and no chronicle line said the law had moved.

    `by` is whoever decided alone; a poll passes with nobody in that place --
    the decision is the city's, not the proposer's.
    """
    before = shown(constants, catalog, city, law_id)
    city.laws = {**(city.laws or {}), law_id: value}
    await session.flush()

    if law_id == "energy_tariff":
        await _apply_tariff(session, constants, catalog, city)

    await events.record(
        session,
        EventKind.CITY_LAW_SET,
        actor_identity_id=None if by is None else by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        law=law_id,
        #: What the rule **was** and **is**, both read the same way: the id
        #: names the law, and the chronicle turns it into a word (`LAW()`).
        was=before,
        now=shown(constants, catalog, city, law_id),
    )


async def _apply_tariff(
    session: AsyncSession, constants: Constants, catalog: Catalog, city: City
) -> None:
    """Push the tariff through to the pool: the pool holds it as a column (D-085)."""

    node = await session.get(Node, city.node_id)
    if node is None:  # pragma: no cover
        return
    pool = (
        await session.execute(select(EnergyPool).where(EnergyPool.node_id == node.id))
    ).scalar_one_or_none()
    if pool is None:
        return
    pool.tariff = Decimal(str(law_number(constants, catalog, city, "energy_tariff")))
    await session.flush()


async def set_charter(
    session: AsyncSession,
    catalog: Catalog,
    by: Identity,
    city: City,
    question_id: str,
    option_id: str,
    param: float | None = None,
    *,
    body=None,
) -> City:
    """Answer a charter question. The question and the option must exist in the vault."""
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.CHARTER)
    question = next((q for q in catalog.laws.charter if q.id == question_id), None)
    if question is None:
        raise CityError(key="city-no-such-question", question=question_id)
    option = next((o for o in question.options if o.id == option_id), None)
    if option is None:
        raise CityError(key="city-no-such-option", option=option_id)
    if option.requires_option is not None:
        #: An option that depends on another answer is meaningless without it:
        #: "the council decides" with no council is a typo, not a charter.
        needed_ = (city.charter or {}).get(option.requires_option)
        if needed_ in (None, "none"):
            raise CityError(
                key="city-option-requires",
                option=option.label,
                requires=option.requires_option,
            )

    #: The charter is amended by the procedure it names itself (D-163): `never`
    #: -- not amended at all, two thirds or unanimity -- by a citizens' vote.
    #: Otherwise the ruler could single-handedly forbid their own recall.

    if ballots.sealed(city):
        raise ballots.Sealed(key="city-charter-sealed")
    if ballots.amends_by_vote(city):
        await ballots.open_charter(session, current(), city, by, question_id, option_id, param)
        return city

    charter = dict(city.charter or {})
    charter[question_id] = option_id
    city.charter = charter
    if param is not None:
        params = dict(city.charter_params or {})
        params[question_id] = param
        city.charter_params = params
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_CHARTER_SET,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        question=question_id,
        option=option_id,
        param=param,
    )
    return city


#: Print conditions -- what a newcomer accepts by choosing the city's door (D-184).
SPAWN_CITIZENSHIP = "spawn_citizenship"


SPAWN_TERM = "spawn_term"


TRADE_TAX = "tax_trade"


def spawn_terms(constants: Constants, catalog: Catalog, city: City | None) -> tuple[bool, float]:
    """The city's print conditions: whether citizenship is mandatory and for how many days.

    A term without mandatory citizenship refers to nothing and is therefore
    zero: there is nothing to hold if nobody joined anything.
    """
    if city is None:
        return False, 0.0
    decision = str(law(catalog, city, SPAWN_CITIZENSHIP) or "").strip().lower()
    required = decision.startswith("обяз")
    if not required:
        return False, 0.0
    return True, max(law_number(constants, catalog, city, SPAWN_TERM), 0.0)


def may_take_city_land(catalog: Catalog, city: City, citizen: bool) -> bool:
    """Whether this person may take civic plots (`build_permit`).

    The law's value is the city's word, not an engine enumeration: options live
    in the vault and grow without code changes (D-094), so what is written is
    read.
    """
    decision = str(law(catalog, city, "build_permit") or "").strip().lower()
    if not decision:
        return True
    if decision.startswith("никто") or decision in ("нет", "-"):
        return False
    if "гражд" in decision:
        return citizen
    return True
