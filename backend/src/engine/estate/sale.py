# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: the sale of a city's empty plot (D-089, D-282, D-356).

Cut out of `estate/price.py` when it passed the eight hundred lines the
quality bar allows (2026-09-19): what is for sale and the purchase itself
live here, the price they are sold at stays there.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import events, ledger, travel, world
from src.engine.estate._base import EstateError, NotEnoughMoney, NotForSale
from src.engine.estate.building import built_area, slots
from src.engine.estate.deed import issue_deed
from src.engine.estate.price import price_of
from src.models.estate import BuildSite, Deed, SiteState
from src.models.event import EventKind
from src.models.farm import Plot
from src.models.identity import Body, BodyState
from src.models.ledger import AccountKind, PostingReason
from src.models.world import COVERED, Node, Vein, is_plot
from src.units import money_str


async def is_vacant(
    session: AsyncSession, constants: Constants, node: Node, *, own: uuid.UUID | None = None
) -> bool:
    """Whether the node is empty: only land with nothing on it changes hands.

    The city's buildings (forge, market, administration) and nodes with a vein
    are not sold by this button: they are not an "empty plot" but working city
    property, and disposing of it is the authority's business, not a price list's.

    Nor is somebody else's work in progress (D-356): a find the city took by a
    highway or by its line may carry a bed somebody marked or a house somebody
    began while the ground was wild (D-198), and the ground would change
    hands from under them. Their own is no obstacle to whoever is asking
    (`own`): a settler buys the ground under his own beds.
    """
    if await built_area(session, node) > 0:
        return False
    _, occupied = await slots(session, constants, node)
    if occupied > 0:
        return False

    vein = await session.scalar(select(Vein.id).where(Vein.node_id == node.id).limit(1))
    if vein is not None:
        return False
    bed = await session.scalar(
        select(Plot.id)
        .where(Plot.node_id == node.id, Plot.owner_identity_id.is_distinct_from(own))
        .limit(1)
    )
    if bed is not None:
        return False
    site = await session.scalar(
        select(BuildSite.id)
        .where(
            BuildSite.node_id == node.id,
            BuildSite.state != SiteState.DONE,
            BuildSite.owner_identity_id.is_distinct_from(own),
        )
        .limit(1)
    )
    return site is None


async def sale_refusal(
    session: AsyncSession, constants: Constants, node: Node, *, buyer: uuid.UUID | None = None
) -> NotForSale | None:
    """Why the city does not sell this node to `buyer`, or None when it does.

    One question for the window and for the purchase (D-356): `look` sends the
    price exactly where this says nothing, and `buy` refuses with what this
    says. Asked twice in two shapes, they parted -- the window priced the
    city's own outskirts and the land its highways took, and the purchase
    refused both. Asked of a buyer, because the buyer's own beds and builds
    on the ground are no obstacle to buying it (`is_vacant`).
    """
    if node.owner_identity_id is not None:
        return NotForSale(key="estate-land-taken")
    if node.owner_city_id is None:
        return NotForSale(key="estate-land-not-civic")
    #: A plot, and not simply a node the city owns (D-282). `is_vacant` below
    #: asks about machines, veins and the gate, not about plot-ness -- so a
    #: city location whose machines were taken down or whose vein ran out
    #: became buyable, and the city sold its own centre for coin. The same
    #: rule as the allotment's, and this is the other door into it.
    if not is_plot(node):
        #: A Forerunner ruin within the line is the city's and not a plot
        #: (D-356): told as what it is, not as a location of the city's own.
        if await town.of_the_forerunners(session, node):
            return NotForSale(key="estate-land-ruin", node=node.name)
        return NotForSale(key="estate-land-not-a-plot", node=node.name)
    if not await is_vacant(session, constants, node, own=buyer):
        return NotForSale(key="estate-land-not-vacant")
    return None


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
        raise EstateError(key="estate-land-buy-dead")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError(key="estate-land-buy-on-foot")
    #: The row for the transaction before anything is asked of it, read
    #: afresh: two buyers at one plot both found it free, both paid, and the
    #: second hand-over took the plot from the first -- whose money stayed in
    #: the treasury. And a line letting a covered plot go in the same second
    #: (`city.cover`) passes over a row held here.
    #: `FOR NO KEY UPDATE`: nothing here changes the node's key, and a row
    #: written that points at the node (an event, a deed, a container) takes
    #: `KEY SHARE` on it -- which the plain `FOR UPDATE` would keep waiting
    #: for the whole purchase (`estate.hold_ground` holds ground the same way).
    await session.execute(
        select(Node)
        .where(Node.id == node.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    refusal = await sale_refusal(session, constants, node, buyer=body.identity_id)
    if refusal is not None:
        raise refusal

    city = await town.by_id(session, node.owner_city_id)
    if city is None:  # pragma: no cover -- civic land without a city is a bug
        raise NotForSale(key="estate-land-city-missing")

    #: Who may take plots in the rings is answered by the code-law `build_permit`
    #: (D-089). By default -- citizens, and before D-160 that read as "everyone".
    if not town.may_take_city_land(
        catalog, city, await town.is_citizen(session, body.identity_id, city)
    ):
        raise NotForSale(
            key="estate-land-permit",
            city=city.name,
            law="build_permit",
            #: The law holds a key; the sentence wants a word (`CHOICE()`).
            permit=town.said(catalog, "build_permit", town.law(catalog, city, "build_permit")),
        )

    price = await price_of(session, constants, catalog, city, node)
    account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
    remainder = await ledger.balance(session, account.id)
    if remainder < price:
        raise NotEnoughMoney(
            key="estate-land-too-dear",
            price=money_str(price),
            have=money_str(remainder),
        )

    treasury = await town.treasury(session, city)
    await ledger.transfer(
        session,
        PostingReason.TRADE,
        debit=account.id,
        credit=treasury.id,
        amount=price,
        memo={"выкуп участка": node.key, "город": city.name},
    )

    #: The floors of a house go with the plot (D-247).
    await world.hand_over(session, node, body.identity_id)
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
    #: Land with paper on it draws the city's line (D-356): a covered plot
    #: bought is the frame now, and the line is asked again.
    if (node.properties or {}).get(COVERED):
        await town.cover(session, constants, city)
    return deed
