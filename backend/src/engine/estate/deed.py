# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: deed (D-116).

Split out of `engine/estate.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import events, ledger, world
from src.engine.estate._base import EstateError, NotEnoughMoney, NotForSale
from src.engine.estate.building.site import sites_of
from src.models.estate import Deed
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import money_str


async def issue_deed(
    session: AsyncSession, node: Node, owner_id: uuid.UUID, *, paid: int = 0
) -> Deed:
    """Issue a deed for a plot. One per node: a repeated issue rewrites the
    holder -- that is a change of title, not a second deed."""
    existing = (
        await session.execute(select(Deed).where(Deed.node_id == node.id))
    ).scalar_one_or_none()
    if existing is not None:
        existing.owner_identity_id = owner_id
        existing.sale_price = None
        existing.sale_to_identity_id = None
        await session.flush()
        return existing

    deed = Deed(node_id=node.id, owner_identity_id=owner_id, paid=paid)
    session.add(deed)
    await session.flush()
    await events.record(
        session,
        EventKind.DEED_ISSUED,
        actor_identity_id=owner_id,
        node_id=node.id,
        deed_id=str(deed.id),
        paid=paid,
    )
    return deed


async def _no_site_open(session: AsyncSession, deed: Deed) -> None:
    """A plot with a construction site on it stays with whoever laid it (D-266).

    The site is the owner's: they alone start and finish it, and the ground it
    holds is theirs until the house is up. Sold under it, the site would
    belong to the seller on the buyer's land -- refused, the way demolition
    is refused while a build is under way.
    """
    node = await session.get(Node, deed.node_id)
    if node is not None and await sites_of(session, node):
        raise EstateError(key="estate-deed-site-open")


async def offer_deed(
    session: AsyncSession,
    identity: Identity,
    deed: Deed,
    price: int,
    *,
    to: Identity | None = None,
) -> Deed:
    """List a deed for sale: open or addressed. A remote action.

    A zero price takes the deed off sale.
    """
    if deed.owner_identity_id != identity.id:
        raise EstateError(key="estate-deed-not-yours")
    if price > 0:
        await _no_site_open(session, deed)
    if price <= 0:
        deed.sale_price = None
        deed.sale_to_identity_id = None
    else:
        deed.sale_price = price
        deed.sale_to_identity_id = None if to is None else to.id
    await session.flush()

    await events.record(
        session,
        EventKind.DEED_OFFERED,
        actor_identity_id=identity.id,
        node_id=deed.node_id,
        deed_id=str(deed.id),
        price=deed.sale_price,
        to=None if to is None else to.name,
    )
    return deed


async def buy_deed(session: AsyncSession, buyer: Identity, deed: Deed) -> Deed:
    """Buy a listed deed: money to the seller, title to the buyer.

    A sale contract in one transaction: no escrow is needed because both money
    and deed change hands at one moment. A remote action -- documents live in
    the Net.
    """
    if deed.sale_price is None:
        raise NotForSale(key="estate-deed-not-on-sale")
    if deed.owner_identity_id == buyer.id:
        raise EstateError(key="estate-deed-own")
    if deed.sale_to_identity_id is not None and deed.sale_to_identity_id != buyer.id:
        raise NotForSale(key="estate-deed-addressed")
    await _no_site_open(session, deed)

    price = int(deed.sale_price)
    account = await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)
    remainder = await ledger.balance(session, account.id)
    if remainder < price:
        raise NotEnoughMoney(
            key="estate-deed-too-dear",
            price=money_str(price),
            have=money_str(remainder),
        )

    seller = await ledger.account_for(session, AccountKind.IDENTITY, deed.owner_identity_id)
    await ledger.transfer(
        session,
        PostingReason.TRADE,
        debit=account.id,
        credit=seller.id,
        amount=price,
        memo={"договор купли-продажи": str(deed.id)},
    )

    previous = deed.owner_identity_id
    deed.owner_identity_id = buyer.id
    deed.sale_price = None
    deed.sale_to_identity_id = None

    #: The title is the ownership: the node passes together with the deed.
    node = await session.get(Node, deed.node_id)
    if node is not None:
        #: The floors of a house go with the plot (D-247).
        await world.hand_over(session, node, buyer.id)
    await session.flush()

    await events.record(
        session,
        EventKind.DEED_SOLD,
        actor_identity_id=buyer.id,
        node_id=deed.node_id,
        deed_id=str(deed.id),
        price=price,
        seller=str(previous),
    )
    return deed


async def deeds_of(session: AsyncSession, identity_id: uuid.UUID) -> list[Deed]:
    return list(
        (await session.execute(select(Deed).where(Deed.owner_identity_id == identity_id)))
        .scalars()
        .all()
    )


async def deeds_on_sale(session: AsyncSession, identity_id: uuid.UUID) -> list[Deed]:
    """Deeds this identity may buy: open ones and those addressed to it."""
    rows = (
        (
            await session.execute(
                select(Deed).where(
                    Deed.sale_price.is_not(None),
                    Deed.owner_identity_id != identity_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        deed
        for deed in rows
        if deed.sale_to_identity_id is None or deed.sale_to_identity_id == identity_id
    ]
