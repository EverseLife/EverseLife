# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: city land.

Split out of `engine/city.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import energy, estate, events, travel, utility, world
from src.engine.city._base import CityError, NoCity, NotYours
from src.engine.city.hall import require_at_hall
from src.engine.city.law import shown
from src.engine.city.lookup import by_id, territory
from src.engine.city.office import offices, require
from src.engine.city.treasury import treasury_balance
from src.models.city import (
    City,
    Power,
)
from src.models.estate import Deed
from src.models.event import EventKind
from src.models.identity import BodyState, Identity
from src.models.world import Node, NodePass, is_plot, storey_of
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

    What is given out is a **plot**. The city's own locations are not land in
    the queue: a location the city works from is nobody's private yard, and
    entering it is not for one holder to allow (D-199).
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.LAND)
    if node.owner_city_id != city.id:
        raise CityError(key="city-land-not-civic")
    #: A plot, and not simply a node the city owns: the core with the printer,
    #: the market, the administration, the gate are the city's own places, and
    #: the city does not hand **itself** out. The window has always listed only
    #: marked plots as free -- the wire had no such rule, and one command with
    #: another node's key turned the capital's centre into somebody's yard.
    if not is_plot(node):
        raise CityError(key="city-land-not-a-plot", node=node.name)
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

    await _into_the_citys_hands(session, node, city, why="ceded")

    await events.record(
        session,
        EventKind.LAND_CEDED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(city.id),
    )
    return city


def _handed_out_by_mistake(node: Node, city: City) -> bool:
    """A city location standing in somebody's name -- the state D-281 forbids.

    A floor is never it on its own: it is held by whoever holds the ground
    under it (D-247), and the floors go with the plot in `hand_over`.
    """
    if node.owner_city_id != city.id or node.owner_identity_id is None:
        return False
    if is_plot(node):
        return False
    return storey_of(node) is None


async def reclaim(session: AsyncSession, node: Node, city: City) -> bool:
    """Take back a city location that was handed out as if it were a plot.

    The allotment used to ask two things only -- the node is the city's, and
    nobody holds it yet -- and the capital's core answers both. So a location
    the city works from could become somebody's yard, and a yard has a door:
    one signature at the town hall shut the centre of the capital, the market
    and the printer behind one person's gate. `allot` refuses that now (and
    `estate.buy` with it); this is for the worlds where it already happened.

    What comes back is a title, and the house on it comes back with it: a
    building has no holder of its own, and the floors follow their ground.
    What lay on the floor of the location stops being behind a door -- a city
    location has none, and whoever stands there may take from its floor
    (D-204). Nothing is bought back here, because nothing was sold: all three
    doors into private title over a city location are shut now -- the
    allotment, the purchase and the sale of the paper -- so this pass can only
    be undoing what the engine gave away for nothing.

    Returns whether there was anything to take back.
    """
    #: The cheap look first, on the row we already hold: the catch-up walks
    #: every node of every city at every deploy, and locking all of them to
    #: find the nothing that is usually there would hold up a live world for
    #: the length of the seed.
    if not _handed_out_by_mistake(node, city):
        return False
    #: And now the row itself, before the decision rather than inside
    #: `hand_over` after it. The deploy starts the new backend beside the old
    #: one, so this runs against a world still being played: a deed sale
    #: slipping between the look and the write would take the buyer's money
    #: and lose them the node in the same second.
    await session.execute(
        select(Node)
        .where(Node.id == node.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not _handed_out_by_mistake(node, city):
        return False

    former = node.owner_identity_id
    await _into_the_citys_hands(session, node, city, why="not-a-plot")
    #: The former holder is told, and by name: a title, a house and a door
    #: gone with no word said would be the world changing behind somebody's
    #: back. `cede` writes its own event for the same reason -- and this is
    #: the case where the person did not choose it.
    await events.record(
        session,
        EventKind.LAND_RECLAIMED,
        actor_identity_id=former,
        node_id=node.id,
        city_id=str(city.id),
    )
    return True


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
        #:
        #: Keyed by id and **without the name**: the word for a law lives in
        #: the names table the client already holds, in every language
        #: (`lawName`), so a copy here would be a second list of the same
        #: words -- and those drift (D-225).
        "laws": {
            law.id: {
                "unit": law.unit,
                "note": law.note,
                #: A default like `` `energy.tariff_default` `` expands into a
                #: number: the player must see the rate in force, not a
                #: reference to a vault constant.
                "value": shown(constants, catalog, city, law.id),
                "own": law.id in (city.laws or {}),
            }
            for law in catalog.laws.code_laws
        },
    }


async def _into_the_citys_hands(
    session: AsyncSession,
    node: Node,
    city: City,
    *,
    why: str,
) -> None:
    """The node passes into the city's own hands, and its door goes with it.

    Civic land has no door: a shut gate and its lists left on the node would
    show a lock that nobody can open any more. The deed is cancelled with it --
    civic land is not traded by deed (D-159).
    """
    await world.hand_over(session, node, None)
    node.gated = False
    await session.execute(delete(NodePass).where(NodePass.node_id == node.id))
    #: The meter is settled with the ground. A node without a holder is
    #: maintained by the treasury (D-149): `utility.bill` charges it nothing
    #: and `utility.pay` has nobody to take payment from, so a debt left here
    #: would never be paid and the cut-off would never be lifted -- the
    #: location would come back to the city electrically dead, with neither
    #: craft nor council possible in it, for ever. `cede` never arrives with a
    #: debt (it refuses first); a location taken back does.
    meter = await utility.meter_of(session, node, create=False)
    if meter is not None:
        meter.debt = 0
        meter.cut_off = False
    await _retire_deed(session, node, city, why=why)
    await session.flush()


async def _retire_deed(
    session: AsyncSession,
    node: Node,
    city: City,
    *,
    why: str = "founding",
) -> None:
    """Cancel the deed for a node that went to the city.

    Two ways lead here, and the event must tell them apart: the founding of a
    city over the land, and the holder handing the plot back (`cede`).
    """

    deed = (await session.execute(select(Deed).where(Deed.node_id == node.id))).scalar_one_or_none()
    if deed is None:
        return
    await session.delete(deed)
    await session.flush()
    await events.record(
        session,
        EventKind.DEED_RETIRED,
        node_id=node.id,
        city_id=str(city.id),
        why=why,
    )
