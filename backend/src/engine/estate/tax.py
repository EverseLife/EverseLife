# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: the land tax (D-127, D-220, D-236, D-356).

Cut out of `estate/price.py` when it passed the eight hundred lines the
quality bar allows (2026-09-19): the day's levy and the rule of what it
charges live here, the price list and the distances it reads stay there.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, ledger, world
from src.engine.estate.price import measure_cities, nodes_from_center
from src.models.event import EventKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import ABOARD, Layer, Node, built_up
from src.units import PERCENT, money


def taxed_land() -> ColumnElement[bool]:
    """The SQL twin of the first three rules of `land_tax_of`: the ground of
    a city -- held by its title or standing in its built-up area -- and not
    a hull."""
    return and_(
        Node.layer == Layer.PLANET,
        or_(Node.owner_city_id.is_not(None), built_up()),
        ~Node.properties.has_key(ABOARD),
    )


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

    #: **Land tax is charged on a city's land** -- the built-up area round the
    #: bioprinter (D-089), and whatever else lies within the city's line: the
    #: land its highways took and its outline covers (D-332, D-356). A plot
    #: bought out there is held like one in the rings and pays like one; read
    #: by the built-up area alone, it was city land that nobody could be taxed
    #: for. What lies outside every line is nobody's: out there is no
    #: authority to tax it (D-198), and no centre to count the distance from.
    #:
    #: A hull is not land at all (D-202): a ship's node has an owner and a
    #: building of its own, and belongs to no city.
    #:
    #: Both are said here rather than left to follow from the city check below,
    #: and for one reason: the day's levy leaves these nodes out of its query
    #: (`taxed_land`), and what the levy charges must be decided by the same
    #: rule as what the plot screen shows. Two rules that agree only while a
    #: third thing stays true is how a tax comes to be shown and never taken.
    if (node.properties or {}).get(ABOARD):
        return 0
    if node.owner_city_id is None and not await world.is_built_up(session, node):
        return 0
    if node.layer is not Layer.PLANET:
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

    Measures the cities first. Not for tidiness: the levy asks how far every
    plot in the world is from its printer, and on a cold cache -- any new edge
    drops the lot, and Pyroxis lays one per eruption -- each plot would walk
    the whole edge table for itself. The per-command memory is no use here,
    because the levy writes between the questions (a transfer, a journal line)
    and a write throws that memory away. One walk per city, paid once.

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

    await measure_cities(session)

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
                    #: The other rules are the ones `land_tax_of` keeps, and
                    #: they must stay the same ones: land outside every city
                    #: is nobody's, and a hull is not land. Written into the
                    #: query so that the nodes it does not charge are not read.
                    taxed_land(),
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
