# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: city land.

Split out of `engine/city.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import energy, estate, events, travel, utility, world
from src.engine.city._base import CityError, NoCity, NotYours
from src.engine.city.lookup import by_id, territory
from src.engine.city.polity import (
    _retire_deed,
    law,
    law_number,
    offices,
    require,
    require_at_hall,
)
from src.engine.city.treasury import treasury_balance
from src.models.city import (
    City,
    Power,
)
from src.models.estate import Deed
from src.models.event import EventKind
from src.models.identity import BodyState, Identity
from src.models.world import Node, NodePass
from src.units import ENERGY_PER_TARIFF_UNIT, money, money_str


async def allot(
    session: AsyncSession,
    by: Identity,
    city: City,
    node: Node,
    to: Identity,
    *,
    body=None,
) -> Node:
    """Allot a civic plot to a resident (D-089).

    Civic land is not taken -- the city gives it: who may take plots in the
    rings is answered by the code-law `build_permit`. The engine checks the
    `land` right: allotting land is a separate decision, neither lawmaking nor
    treasury spending (D-155).
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.LAND)
    if node.owner_city_id != city.id:
        raise CityError(key="city-land-not-civic")
    if node.owner_identity_id is not None:
        raise CityError(key="city-land-taken")

    #: The floors of a house go with the plot (D-247).
    await world.hand_over(session, node, to.id)

    #: An allotted plot is documented by a deed, like a bought one (D-116).

    await estate.issue_deed(session, node, to.id)

    await events.record(
        session,
        EventKind.LAND_CLAIMED,
        actor_identity_id=to.id,
        node_id=node.id,
        city_id=str(city.id),
        allotted_by=by.name,
    )
    return node


async def cede(session: AsyncSession, body, node: Node) -> City:
    """Hand your own plot back to the city. In person: land is given up on the spot.

    The mirror of `allot` and `buy`, and the only way back. Nobody's leave is
    asked: the land was the city's before it was yours (D-089), and the city
    loses nothing by taking it back. What changes is one thing -- the node has
    no personal holder any more, and from that moment the meter charges the
    city: a node without a holder is maintained by the treasury, which pays
    with energy it could have sold instead of with money (D-149).

    **What goes with the ground.** The deed is cancelled: civic land is not
    traded by deed (D-159). The door is removed with its lists -- on civic land
    there is no door at all, entry is decided by citizenship and duties (D-204).
    Equipment stays where it stands, but from now on it is placed and removed
    by the authority with the `laws` right, not by the last holder (D-166).

    **The debt does not go with it.** Handing over a node with a debt would be
    a way to run machines and write the bill off onto the city; the debt is
    closed first, and only then is there anything to hand over.
    """

    if body is None or body.state is not BodyState.ALIVE:
        raise CityError(key="city-land-dead")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise CityError(key="city-land-cede-on-foot")
    if node.owner_identity_id != body.identity_id:
        raise NotYours(key="city-land-not-yours")
    #: Reached from a floor of one's own house (D-247): it is held land and it
    #: is not the city's, so the refusal is the right one -- a storey is not
    #: ceded apart from the ground it stands on. Wild land never gets here:
    #: nobody holds it, and the check above turns it away first.
    if node.owner_city_id is None:
        raise NoCity(key="city-land-not-city-land")
    city = await by_id(session, node.owner_city_id)
    if city is None:  # pragma: no cover -- civic land without a city is a bug
        raise NoCity(key="city-land-city-missing")

    deed = (await session.execute(select(Deed).where(Deed.node_id == node.id))).scalar_one_or_none()
    if deed is not None and deed.sale_price is not None:
        raise CityError(key="city-land-deed-on-sale")

    meter = await utility.meter_of(session, node, create=False)
    if meter is not None and meter.debt > 0:
        raise CityError(key="city-land-debt", debt=money_str(meter.debt))

    await world.hand_over(session, node, None)
    #: Civic land has no door: a shut gate and its lists left on the node would
    #: show a lock that nobody can open any more.
    node.gated = False
    await session.execute(delete(NodePass).where(NodePass.node_id == node.id))
    await _retire_deed(session, node, city, why="участок передан городу")
    await session.flush()

    await events.record(
        session,
        EventKind.LAND_CEDED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(city.id),
    )
    return city


async def upkeep_of(session: AsyncSession, constants: Constants, city: City) -> dict:
    """What the city's own household costs it per meter period (D-149).

    The treasury pays for a civic node with energy rather than with money, and
    that spend shows up nowhere in the balance: the pool simply drains. Without
    this line the authority sees energy leaving and cannot tell what into --
    and the decision "should this node be the city's" has no figure behind it.

    `worth` is what the same energy would have fetched at the city's own tariff
    if it had been sold instead. It is not a debt and nobody is billed it: it
    is the price of the decision, and that is exactly what makes it a figure
    worth showing.
    """

    period = constants[R.ENERGY_METER_PERIOD]
    pool = await energy.pool_of(
        session, constants, await session.get(Node, city.node_id), create=False
    )
    tariff = float(pool.tariff) if pool is not None else constants[R.ENERGY_TARIFF_DEFAULT]

    draw = 0.0
    counted = 0
    for node in await territory(session, city):
        #: A holder's node is the holder's bill, wherever it stands: a bought
        #: plot is city territory too, and counting it here would double it.
        if node.owner_identity_id is not None:
            continue
        if await energy.grid_node(session, node) is None:
            continue
        draw += utility.draw_for(constants, node, period)
        counted += 1

    return {
        "nodes": counted,
        "hours": period,
        "energy": round(draw, 1),
        "worth": money(draw / ENERGY_PER_TARIFF_UNIT * tariff),
        "tariff": tariff,
    }


async def survey(session: AsyncSession, constants: Constants, catalog: Catalog, city: City) -> dict:
    """City summary: charter, laws, offices, treasury. Remote read.

    What is visible and to whom is a charter question (`treasury_publicity`),
    and it lies right here. Today the engine gives out everything: there is
    nobody to hide the treasury from until there is a second city, and
    pretending privacy works is worse than not having it.
    """
    people = {}
    for office in await offices(session, city):
        identity = await session.get(Identity, office.identity_id)
        people[str(office.id)] = {
            "id": str(office.id),
            "who": "?" if identity is None else identity.name,
            "identity": str(office.identity_id),
            "title": office.title,
            "powers": list(office.powers or ()),
        }

    return {
        "id": str(city.id),
        "name": city.name,
        #: The city's word to newcomers (D-183): the authority edits it, everyone sees it.
        "about": city.about,
        "node": (await session.get(Node, city.node_id)).key,
        "treasury": await treasury_balance(session, city),
        #: What the city's own nodes burn per meter period. Money is not paid
        #: for them at all -- the treasury pays with energy (D-149).
        "upkeep": await upkeep_of(session, constants, city),
        "offices": list(people.values()),
        "charter": dict(city.charter or {}),
        "charter_params": dict(city.charter_params or {}),
        #: Charter questions in words: the client need not know that
        #: `ruler_recall` means "can the ruler be recalled early". The text lives in the vault.
        "charter_questions": [
            {
                "id": question.id,
                "section": question.section,
                "question": question.question,
                "options": [
                    {"id": option.id, "label": option.label} for option in question.options
                ],
            }
            for question in catalog.laws.charter
        ],
        #: Laws are given out **as in force**: the own decision or the vault
        #: default. The client need not know where the value came from -- it
        #: needs to know which rule it lives by.
        "laws": {
            law.id: {
                "name": law.name,
                "unit": law.unit,
                "note": law.note,
                #: A default like `` `energy.tariff_default` `` expands into a
                #: number: the player must see the rate in force, not a
                #: reference to a vault constant.
                "value": _shown(constants, catalog, city, law.id),
                "own": law.id in (city.laws or {}),
            }
            for law in catalog.laws.code_laws
        },
    }


def _shown(constants: Constants, catalog: Catalog, city: City, law_id: str) -> str | None:
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
