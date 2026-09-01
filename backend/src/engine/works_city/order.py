# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city's orders (D-248): posted for repair, building or fuel, and
cancelled with what cancellation honestly owes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import energy, events, ledger, works
from src.engine.estate.building import build_minutes, buildings_of, kinds
from src.engine.estate.upkeep import missing_share, repair_minutes
from src.engine.works_city._base import (
    WorksCityError,
    _work_under_way,
    labor_tariff,
    open_city_order,
    split_labor,
)
from src.models.city import City, Power
from src.models.event import EventKind
from src.models.identity import Body, Identity
from src.models.ledger import AccountKind
from src.models.ledger import PostingReason as Reason
from src.models.works import WorkOrder, WorkOrderKind, WorkOrderState
from src.models.world import Node
from src.units import MINUTES_PER_HOUR, money, money_str


async def _post(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    kind: WorkOrderKind,
    node: Node,
    *,
    offer: int,
    labor: int,
    payload: dict,
) -> WorkOrder:
    """Common posting path: powers, both escrows, the event.

    The order of refusals is the order of pockets: the fund first -- its
    emptiness is nobody's fault and needs saying plainly -- then the treasury,
    whose emptiness is the city's own business.
    """
    await town.require_at_hall(session, body, city)
    await town.require(session, by.id, city, Power.TREASURY)
    if offer < 0:
        raise WorksCityError(key="works-city-offer-negative")
    if labor <= 0:
        raise WorksCityError(key="works-city-no-labor")
    if await open_city_order(session, kind, node) is not None:
        raise WorksCityError(key="works-city-order-exists")

    city_labor, fund_labor = split_labor(constants, labor)
    city_part = offer + city_labor
    if await works.fund_balance(session) < fund_labor:
        raise WorksCityError(key="works-city-fund-empty", money=money_str(fund_labor))

    order = WorkOrder(
        kind=kind,
        node_id=node.id,
        city_id=city.id,
        payload={
            **payload,
            "city_part": city_part,
            "fund_part": fund_labor,
            "city_paid": 0,
            "fund_used": 0,
            "offer": offer,
        },
        tariff=city_part + fund_labor,
    )
    session.add(order)
    await session.flush()

    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    if city_part > 0:
        try:
            await ledger.transfer(
                session,
                Reason.ESCROW_HOLD,
                debit=(await town.treasury(session, city)).id,
                credit=escrow.id,
                amount=city_part,
                memo={"госзаказ города": str(order.id)},
            )
        except ledger.InsufficientFunds as poor:
            raise WorksCityError(
                key="works-city-treasury-poor", money=money_str(city_part)
            ) from poor
    if fund_labor > 0:
        await ledger.transfer(
            session,
            Reason.ESCROW_HOLD,
            debit=(await works.fund_account(session)).id,
            credit=escrow.id,
            amount=fund_labor,
            memo={"доля фонда в госзаказе": str(order.id)},
        )
    await events.record(
        session,
        EventKind.WORKS_ORDER_POSTED,
        actor_identity_id=by.id,
        node_id=node.id,
        order_id=str(order.id),
        order_kind=kind.value,
        city=city.name,
        tariff=order.tariff,
    )
    return order


async def post_repair_order(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    node: Node,
    *,
    offer: float,
) -> WorkOrder:
    """Order the mending of the city's own plot. The offer covers the materials
    the worker walls in -- the fund pays labour only (D-002)."""
    if node.owner_city_id != city.id:
        raise WorksCityError(key="works-city-repair-not-own")
    houses = await buildings_of(session, node)
    if not houses or missing_share(houses) <= 0:
        raise WorksCityError(key="works-city-nothing-to-repair")
    labor = labor_tariff(constants, repair_minutes(constants, houses))
    return await _post(
        session,
        constants,
        city,
        by,
        body,
        WorkOrderKind.BUILDING_REPAIR,
        node,
        offer=money(offer),
        labor=labor,
        payload={"node": str(node.id)},
    )


async def post_build_order(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    node: Node,
    *,
    building_kind: str,
    footprint: float,
    floors: int,
    offer: float,
) -> WorkOrder:
    """Order a building on the city's plot. Materials are the worker's, the
    offer compensates them; the engine verifies the finished house by kind,
    floors and footprint."""
    if node.owner_city_id != city.id:
        raise WorksCityError(key="works-city-build-not-own")
    if building_kind not in kinds(constants):
        raise WorksCityError(key="works-city-unknown-building", building=building_kind)
    if footprint <= 0 or floors < 1:
        raise WorksCityError(key="works-city-no-footprint")
    labor = labor_tariff(
        constants, build_minutes(constants, footprint=footprint, floors=floors, kind=building_kind)
    )
    return await _post(
        session,
        constants,
        city,
        by,
        body,
        WorkOrderKind.BUILDING_BUILD,
        node,
        offer=money(offer),
        labor=labor,
        payload={
            "node": str(node.id),
            "building_kind": building_kind,
            "footprint": footprint,
            "floors": floors,
        },
    )


async def post_fuel_order(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    by: Identity,
    body: Body,
    node: Node,
    *,
    type_key: str,
    amount: float,
    price_per_unit: float,
) -> WorkOrder:
    """Order fuel hauled to a station on the city's territory.

    The city buys the fuel itself -- `price_per_unit` is its own offer, its
    own politics -- and the fund subsidises the haul: hours from the cargo's
    mass at `works.haul_kg_per_hour`. Paid per unit as it lands in the
    station, through the ordinary pouring mechanic.
    """
    place = await town.of_node(session, node)
    if place is None or place.id != city.id:
        raise WorksCityError(key="works-city-station-not-in-city")
    view = await energy.plant_view(session, constants, node)
    if view is None:
        raise WorksCityError(key="works-city-no-station")
    if type_key not in view["fuels"]:
        raise WorksCityError(key="works-city-not-a-fuel", goods=type_key, station=view["station"])
    if amount <= 0 or price_per_unit < 0:
        raise WorksCityError(key="works-city-zero-haul")

    hours = catalog.recipes.mass_of(type_key) * amount / constants[R.WORKS_HAUL_KG_PER_HOUR]
    labor = labor_tariff(constants, hours * MINUTES_PER_HOUR)
    return await _post(
        session,
        constants,
        city,
        by,
        body,
        WorkOrderKind.FUEL_DELIVERY,
        node,
        offer=int(money(price_per_unit) * amount),
        labor=labor,
        payload={"node": str(node.id), "type_key": type_key, "amount": amount, "left": amount},
    )


async def cancel_city_order(
    session: AsyncSession,
    city: City,
    by: Identity,
    body: Body,
    order_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Withdraw the city's order. Returns (to the treasury, to the fund).

    The unpaid remainder goes back where it came from: the city's part to the
    treasury, the fund's to the fund. Withdrawing also revokes the licence the
    order carried -- repair and construction on the plot close with it.
    """
    moment = now or datetime.now(UTC)
    await town.require_at_hall(session, body, city)
    await town.require(session, by.id, city, Power.TREASURY)
    order = (
        (await session.execute(select(WorkOrder).where(WorkOrder.id == order_id).with_for_update()))
        .scalars()
        .one_or_none()
    )
    if order is None or order.city_id != city.id:
        raise WorksCityError(key="works-city-no-such-order")
    if order.state is not WorkOrderState.OPEN:
        raise WorksCityError(key="works-city-order-closed")
    #: Work already under way is somebody's materials already in the walls:
    #: repair and construction write them off at the order (D-145). Letting
    #: the city withdraw now would hand it the house and the escrow both --
    #: "post, wait for a stranger's timber, revoke" must not be a strategy.
    node = None if order.node_id is None else await session.get(Node, order.node_id)
    if node is not None and await _work_under_way(session, order.kind, node):
        raise WorksCityError(key="works-city-work-under-way")

    city_left = int(order.payload["city_part"]) - int(order.payload["city_paid"])
    fund_left = int(order.payload["fund_part"]) - int(order.payload["fund_used"])
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    if city_left > 0:
        await ledger.transfer(
            session,
            Reason.ESCROW_RELEASE,
            debit=escrow.id,
            credit=(await town.treasury(session, city)).id,
            amount=city_left,
            memo={"возврат эскроу городу": str(order.id)},
        )
    if fund_left > 0:
        await ledger.transfer(
            session,
            Reason.ESCROW_RELEASE,
            debit=escrow.id,
            credit=(await works.fund_account(session)).id,
            amount=fund_left,
            memo={"возврат эскроу фонду": str(order.id)},
        )
    order.state = WorkOrderState.CANCELLED
    order.cancelled_at = moment
    await session.flush()
    await events.record(
        session,
        EventKind.WORKS_ORDER_CANCELLED,
        actor_identity_id=by.id,
        order_id=str(order.id),
        order_kind=order.kind.value,
        returned=city_left + fund_left,
    )
    return city_left, fund_left
