# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: land price (D-089).

Split out of `engine/estate.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from collections import deque

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.db.base import remember
from src.engine import city as town
from src.engine import events, ledger, travel
from src.engine.estate._base import BadName, EstateError, NotEnoughMoney, NotForSale, NotOwner
from src.engine.estate.building import built_area, slots
from src.engine.estate.deed import issue_deed
from src.engine.ship import ABOARD
from src.models.city import City, Power
from src.models.estate import Deed
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Edge, Layer, Node, Vein
from src.runtime import LAND_NAME_LIMIT
from src.units import (
    PERCENT,
    money,
)


async def center_of(session: AsyncSession, city: City) -> Node | None:
    """The city's centre: the bioprinter it grew from (D-023, D-089, D-208).

    Every city counts from its **own** printer, not from the capital's: the
    rate is announced at the bioprinter (D-220), and a city has one of its own
    or it is not a city.

    Which node that is, the city answers itself (`city.core`), and it is asked
    rather than worked out again here. A second way of naming the centre is a
    second answer waiting to happen -- and the written distance is measured
    **to** this node, so two names for the centre would mean measuring the city
    over and over, each reader disagreeing with what the last one wrote.
    """

    return await remember(session, ("center_of", city.id), lambda: town.core(session, city))


async def forget_distances(session: AsyncSession) -> None:
    """Drop every measured distance: the graph itself has changed.

    Called where an edge appears or goes, and nowhere else -- `travel.connect`
    and the undocking that removes a gangway. A trail laid by a scout may
    shorten the way to the centre for a whole quarter, so measuring is not
    patched here -- it is dropped, and the next reader measures again.

    Not free, and known not to be: **any** new edge drops the measurements of
    the whole world, and Pyroxis lays one on every eruption (`plates._bridge`,
    through `connect` like everything else). The cities then remeasure
    themselves the next time somebody asks a price -- the same cost a scout's
    trail already has, paid a few times a week rather than a few times a day.

    The tearing side of an eruption deletes its edges itself and does not come
    here, and that is not an omission: what is measured is the way to a city's
    centre, and Pyroxis has no cities and never will (D-230, D-233). No node of
    it carries a distance to drop.

    One statement for the world: this happens when a road is laid or a ship
    casts off, not in the course of a day's play. It touches only what was
    measured, so building a world -- where every second call lays an edge and
    nothing has been measured yet -- writes nothing at all.
    """
    await session.execute(
        update(Node)
        .where(Node.center_steps.is_not(None))
        .values(center_node_id=None, center_steps=None)
    )


async def note_new_place(session: AsyncSession, one: Node, other: Node) -> None:
    """A place just joined to the map takes its distance from what it joined to.

    This is the whole of measuring, in play. The map grows only at its edges: a
    scout hangs a node nothing led to yet, and no road is ever laid between two
    places already on it -- so a new plot is exactly one step further from the
    printer than the place it was found from, and nothing else moves. Walking
    the graph for that would be answering a question the map has already
    answered (D-220).

    Only the built-up area is counted (`city` layer): beyond the walls the land
    is nobody's and pays nothing (D-198), and a ship is a dead end of its own
    (D-201, D-202). And only from an anchor that has a distance itself -- an
    old world whose nodes were never measured is measured once, by the walk
    below, and grows by this rule from then on.
    """

    for anchor, fresh in ((one, other), (other, one)):
        if fresh.center_steps is not None or anchor.center_steps is None:
            continue
        if fresh.layer is not Layer.CITY:
            continue
        if (fresh.properties or {}).get(ABOARD) or (anchor.properties or {}).get(ABOARD):
            continue
        fresh.center_node_id = anchor.center_node_id
        fresh.center_steps = anchor.center_steps + 1
        await session.flush()
        return


async def _measure_city(session: AsyncSession, center: Node, city: City) -> dict[uuid.UUID, int]:
    """Walk from the centre once and write the result down for the whole city."""

    edges = (await session.execute(select(Edge))).scalars().all()
    neighbours: dict[uuid.UUID, list[uuid.UUID]] = {}
    for edge in edges:
        neighbours.setdefault(edge.node_a_id, []).append(edge.node_b_id)
        neighbours.setdefault(edge.node_b_id, []).append(edge.node_a_id)

    #: The walk stops at the gangway. A ship moored in the port is a whole
    #: little map of its own, and none of it is land: to a ship's node only a
    #: ship's node is ever joined, so no road leads back out through a hull and
    #: no distance is ever wanted for one. Without this the walk wandered the
    #: cabins of every ship in port, and the "farther than any road" number
    #: below moved with the shipping.
    afloat = set(
        (await session.execute(select(Node.id).where(Node.properties.has_key(ABOARD)))).scalars()
    )

    steps = {center.id: 0}
    queue: deque[uuid.UUID] = deque([center.id])
    while queue:
        here = queue.popleft()
        for neighbour in neighbours.get(here, ()):
            if neighbour not in steps and neighbour not in afloat:
                steps[neighbour] = steps[here] + 1
                queue.append(neighbour)

    #: No road to the node -- the land lies beyond the farthest ring the city
    #: reaches, and it is counted as further than any of them.
    beyond = len(steps)
    #: Written for this city's nodes only, and "this city's" must mean exactly
    #: what `city.of_node` means, in the same order: land the city holds, its
    #: own delegate node, and what hangs off that node while no other city
    #: holds it. Writing every node the walk reached instead would have the two
    #: cities of one road overwrite each other's measurements turn by turn.
    mine = (
        (
            await session.execute(
                select(Node).where(
                    or_(
                        Node.owner_city_id == city.id,
                        Node.id == city.node_id,
                        and_(Node.parent_id == city.node_id, Node.owner_city_id.is_(None)),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    for plot in [*mine, center]:
        plot.center_node_id = center.id
        plot.center_steps = steps.get(plot.id, beyond)
    await session.flush()
    return steps


async def nodes_from_center(session: AsyncSession, node: Node, city: City) -> int:
    """The plot's distance from its city's centre -- in nodes from the bioprinter.

    Land value falls with each node from the printer (D-220). Measured by
    edges, not by the "ring" property: the property is a record at generation,
    edges are how people really walk the city.

    Read from the node, walked for only when what is written there was measured
    to another centre or dropped by a change in the graph (`models/world.Node`).
    """
    center = await center_of(session, city)
    if center is None:
        #: The printer is gone from the core -- carried out, or never put back.
        #: What was measured while it stood stays: the land did not move, and
        #: the last rate the city announced is the last one it announced. The
        #: alternative was to call the distance nought, and a city that lost
        #: its machine would start charging every plot the centre's own rate --
        #: the dearest in town, for the place that just lost its centre.
        #:
        #: A plot nobody had measured by then has nothing to keep, and the
        #: engine does not invent it a distance: it stands at nought until a
        #: printer is put back and the city is measured again.
        return node.center_steps if node.center_steps is not None else 0
    if node.center_node_id == center.id and node.center_steps is not None:
        return node.center_steps

    steps = await _measure_city(session, center, city)
    return steps.get(node.id, len(steps))


async def price_of(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    node: Node,
) -> int:
    """The plot price in minor units: city rate x decay x area.

    The rate at the centre is set by the city via the code-law `land_price`
    (TC/m2); with each node from the bioprinter the price falls by
    `land.decay_per_node` -- the same decay the land tax follows, because both
    say the same thing about the same place (D-220).
    """

    rate = town.law_number(constants, catalog, city, "land_price")
    if rate <= 0:
        raise NotForSale("город не назначил цену земли: код-закон `land_price` пуст")
    decline = 1 - constants[R.LAND_DECAY_PER_NODE] / PERCENT
    steps = await nodes_from_center(session, node, city)
    per_metre = rate * (decline**steps)
    return max(1, money(per_metre * float(node.area_m2)))


async def land_tax_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, node: Node
) -> int:
    """What this plot owes its city for one day, in minor units (D-127).

    The same shape as the purchase price, and for the same reason (D-220): the
    rate is announced at the bioprinter and falls by `land.decay_per_node` with
    every node away from it. A place near the centre costs more both to buy and
    to hold -- otherwise the buyer pays the premium once and then sits on the
    centre for free.

    **The base is the whole plot** (D-236), built on or not: hold land and pay
    for land. Charged on the footprint instead, an empty plot in the centre
    cost its holder nothing, and buying up the middle of a city to sit on it
    was free. A tower and a shed on equal plots now pay equally -- storeys cost
    no ground (D-125), and this carries that rule to its end.
    """

    #: **Land tax is charged on the built-up area** -- the rings around the
    #: bioprinter, which is what the `city` layer is (D-089). What lies on the
    #: layer of the planet is nobody's: the mine, the floodplain, a scout's
    #: find beyond the walls. Out there is no authority to tax it (D-198), and
    #: no centre to count the distance from either.
    #:
    #: A hull is not land at all (D-202): a ship's node has an owner and a
    #: building of its own, and belongs to no city.
    #:
    #: Both are said here rather than left to follow from the city check below,
    #: and for one reason: the day's levy leaves these nodes out of its query,
    #: and what the levy charges must be decided by the same rule as what the
    #: plot screen shows. Two rules that agree only while a third thing stays
    #: true is how a tax comes to be shown and never taken.
    if node.layer is not Layer.CITY or (node.properties or {}).get(ABOARD):
        return 0

    city = await town.of_node(session, node)
    if city is None:
        return 0
    rate = town.law_number(constants, catalog, city, "tax_land")
    if rate <= 0:
        return 0
    held = float(node.area_m2)
    if held <= 0:  # pragma: no cover -- a plot without area is a delegate node
        return 0
    decline = 1 - constants[R.LAND_DECAY_PER_NODE] / PERCENT
    steps = await nodes_from_center(session, node, city)
    return money(rate * (decline**steps) * held)


async def levy_land_tax(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> dict[str, int]:
    """Charge every built and held plot its day of land tax. Daily tick (D-127).

    Who pays is who holds the deed, wherever the plot stands: a bought civic
    plot is still the city's land and still the holder's bill (D-149). A city's
    own node pays nothing -- a city taxing itself moves money from one pocket
    into the same pocket -- and land beyond the walls has no authority over it
    to tax it at all (D-198).

    **What cannot be paid is not paid.** The account is charged what it holds
    and no further: turning the rest into a debt would be inventing debt
    collection, and that is a mechanic of its own, not a side effect of a tax
    (D-166). The shortfall goes into the journal, where arrears can be seen --
    and counted, once there is something to count them with.
    """

    held = (
        (
            await session.execute(
                select(Node)
                .where(
                    Node.owner_identity_id.is_not(None),
                    #: **Every held plot, built on or not** (D-236): the base
                    #: is the ground, and an empty plot in the centre is
                    #: exactly the case the tax exists for. Joined with
                    #: `Building` before, it billed only what stood on the
                    #: land, and holding the land itself was free.
                    #:
                    #: The other two rules are the ones `land_tax_of` keeps,
                    #: and they must stay the same two: the planet's own land
                    #: is nobody's, and a hull is not land. Written into the
                    #: query so that the nodes it does not charge are not read.
                    Node.layer == Layer.CITY,
                    ~Node.properties.has_key(ABOARD),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    paid_total = 0
    unpaid_total = 0
    plots = 0
    for node in held:
        owed = await land_tax_of(session, constants, catalog, node)
        if owed <= 0:
            continue
        city = await town.of_node(session, node)
        if city is None:  # pragma: no cover -- `land_tax_of` already returned 0
            continue
        account = await ledger.account_for(session, AccountKind.IDENTITY, node.owner_identity_id)
        have = await ledger.balance(session, account.id)
        paid = min(owed, have) if have > 0 else 0
        short = owed - paid
        if paid > 0:
            treasury = await town.treasury(session, city)
            await ledger.transfer(
                session,
                PostingReason.TAX_LAND,
                debit=account.id,
                credit=treasury.id,
                amount=paid,
                memo={"земельный налог": node.key},
            )
        plots += 1
        paid_total += paid
        unpaid_total += short
        await events.record(
            session,
            EventKind.LAND_TAXED,
            actor_identity_id=node.owner_identity_id,
            node_id=node.id,
            city_id=str(city.id),
            owed=owed,
            paid=paid,
            unpaid=short,
        )
    return {"paid": paid_total, "unpaid": unpaid_total, "plots": plots}


async def is_vacant(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Whether the node is empty: only land with nothing on it is sold.

    The city's buildings (forge, market, administration) and nodes with a vein
    are not sold by this button: they are not an "empty plot" but working city
    property, and disposing of it is the authority's business, not a price list's.
    """
    if await built_area(session, node) > 0:
        return False
    #: The city's transit gate is a common road, not a plot (D-176).
    if (node.properties or {}).get("выход"):
        return False
    _, occupied = await slots(session, constants, node)
    if occupied > 0:
        return False

    vein = await session.scalar(select(Vein.id).where(Vein.node_id == node.id).limit(1))
    return vein is None


async def buy(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    node: Node,
) -> Deed:
    """Buy an empty civic plot. In person: land is inspected on foot.

    Money goes to the city treasury, the buyer is issued a deed. Land outside a
    city is neither bought nor taken (D-198): there is nobody to set a price,
    nowhere to pay and nobody to issue the paper -- yet everyone may work and
    build on it.
    """

    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело не покупает")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("участок покупают ногами: дойдите до него")
    if node.owner_identity_id is not None:
        raise NotForSale("участок уже за кем-то")
    if node.owner_city_id is None:
        raise NotForSale(
            "это не городская земля: за городом её не продают и не присваивают, "
            "но работать и строить там может всякий"
        )
    if not await is_vacant(session, constants, node):
        raise NotForSale("узел не пустой: застройку и жилы города прейскурант не продаёт")

    city = await town.by_id(session, node.owner_city_id)
    if city is None:  # pragma: no cover -- civic land without a city is a bug
        raise NotForSale("узел приписан к несуществующему городу")

    #: Who may take plots in the rings is answered by the code-law `build_permit`
    #: (D-089). By default -- citizens, and before D-160 that read as "everyone".
    if not town.may_take_city_land(
        catalog, city, await town.is_citizen(session, body.identity_id, city)
    ):
        raise NotForSale(
            f"«{city.name}» продаёт землю не всякому: код-закон build_permit — "
            f"«{town.law(catalog, city, 'build_permit')}». Вступите в граждане"
        )

    price = await price_of(session, constants, catalog, city, node)
    account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
    remainder = await ledger.balance(session, account.id)
    if remainder < price:
        raise NotEnoughMoney(f"участок стоит {price} минорных единиц, а на счету {remainder}")

    treasury = await town.treasury(session, city)
    await ledger.transfer(
        session,
        PostingReason.TRADE,
        debit=account.id,
        credit=treasury.id,
        amount=price,
        memo={"выкуп участка": node.key, "город": city.name},
    )

    node.owner_identity_id = body.identity_id
    deed = await issue_deed(session, node, body.identity_id, paid=price)

    await events.record(
        session,
        EventKind.LAND_BOUGHT,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(city.id),
        price=price,
        deed_id=str(deed.id),
    )
    return deed


async def may_name(session: AsyncSession, body: Body, node: Node) -> bool:
    """Whether this body may name the node (D-178).

    The owner disposes of their own land, of civic land -- the authority with
    the `land` right: the same one it hands out that land with (D-089).
    Unowned land bears no name.
    """

    if node.owner_identity_id is not None:
        return node.owner_identity_id == body.identity_id
    if node.owner_city_id is None:
        return False
    city = await town.by_id(session, node.owner_city_id)
    return city is not None and await town.may(session, body.identity_id, city, Power.LAND)


async def rename(session: AsyncSession, body: Body, node: Node, name: str) -> Node:
    """Name a plot. The nameplate is nailed on the spot, not from the Net (D-178).

    The label changes, not the node key: `terra.capital.lot2` is referenced by
    deeds, edges and events, and renaming may not break them.
    """

    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело ничего не переименовывает")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("до участка надо дойти: табличку прибивают на месте")
    if not await may_name(session, body, node):
        raise NotOwner(
            "участок не ваш: имя даёт хозяин, а городской земле — власть с правом на участки"
        )

    title = name.strip()
    if not title:
        raise BadName("у участка должно быть имя")
    if len(title) > LAND_NAME_LIMIT:
        raise BadName(f"имя длиннее {LAND_NAME_LIMIT} знаков")

    before, node.name = node.name, title
    await session.flush()
    await events.record(
        session,
        EventKind.LAND_RENAMED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        was=before,
        now=title,
    )
    return node
