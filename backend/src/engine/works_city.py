# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""City orders on the works board, and the treasury as a borrower (D-248, wave 3).

Where road orders are physics -- the engine sees a sagged edge itself -- what
to repair, what to build and which station to supply is **politics**: the city
decides, holding the TREASURY power, standing in its own administration. The
state does not choose what the city needs; it subsidises the labour on what
the city chose.

## Who pays what

The fund buys no goods (D-002). Every non-labour cost -- materials the worker
walls in, fuel they pour -- is the **city's offer**, a sum the city names when
posting. The labour tariff is the engine's public formula, split by
`works.city_cofinance`: the city fronts its share, the fund adds the rest.
Both parts are escrowed at posting -- an order the money is not set aside for
does not go up.

City orders are claimless like road orders: taking one is doing the work, and
the engine pays whoever it verified. Repair and construction on the city's own
plot are licensed by the open order itself -- the order **is** the permission,
revocable by withdrawing the order. Fuel is poured through the ordinary
station mechanic and paid per unit as it lands.

Split out of `engine/works.py` before it crossed the size bar, the way
`seed.py` was cut (D-243).

## The treasury as a borrower

The city may borrow from the CB for its works: at the key rate, with no
margin and no risk premium -- a city cannot mark itself up -- on the same
credit line its citizens' loans occupy (`bank.debt_to_turnover_cap`). There
is no forced collection from a treasury: the city's discipline is the line
itself, occupied until repaid.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import bank, energy, events, ledger, works
from src.engine import city as town
from src.engine.errors import Refusal
from src.engine.estate.building import build_minutes, buildings_of, kinds
from src.engine.estate.upkeep import missing_share, repair_minutes, repairing
from src.models.bank import Loan, LoanState
from src.models.city import City, Power
from src.models.event import EventKind
from src.models.identity import Body, Identity
from src.models.job import Job, JobKind, JobState
from src.models.ledger import AccountKind, LedgerAccount
from src.models.ledger import PostingReason as Reason
from src.models.works import WorkOrder, WorkOrderKind, WorkOrderState
from src.models.world import Node
from src.units import AMOUNT_SCALE, MINUTES_PER_HOUR, PERCENT, SCALE_MAX, money, money_str

#: Fuel orders count in units split like every amount; a tail below the last
#: digit of the scale is representation noise, not undelivered fuel.
_EPS = 1 / AMOUNT_SCALE


class WorksCityError(Refusal):
    pass


def labor_tariff(constants: Constants, minutes: float) -> int:
    """The labour part of an order, minor units: hours at the public rate."""
    return money(minutes / MINUTES_PER_HOUR * constants[R.WORKS_HOUR_RATE])


def split_labor(constants: Constants, labor: int) -> tuple[int, int]:
    """(city's share, fund's share) of the labour tariff, by `works.city_cofinance`."""
    city_part = int(labor * constants[R.WORKS_CITY_COFINANCE] / PERCENT)
    return city_part, labor - city_part


async def open_city_order(
    session: AsyncSession, kind: WorkOrderKind, node: Node, *, for_update: bool = False
) -> WorkOrder | None:
    stmt = select(WorkOrder).where(
        WorkOrder.kind == kind,
        WorkOrder.state == WorkOrderState.OPEN,
        WorkOrder.node_id == node.id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


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


# --- payouts ------------------------------------------------------------------


async def _pay_share(
    session: AsyncSession,
    constants: Constants,
    order: WorkOrder,
    identity_id: uuid.UUID,
    fraction: float,
    *,
    now: datetime,
) -> int:
    """Pay `fraction` of the order to the worker. Returns what reached them.

    The city's share goes in full -- it is the price of goods, not a subsidy.
    The fund's share is clipped by the worker's daily cap; the clipped rest is
    not the worker's and returns to the fund at once. Cumulative sums are kept
    against rounding drift: each step pays `int(part * done) - paid so far`.
    """
    done_before = float(order.payload.get("done", 0.0))
    done_now = min(1.0, done_before + fraction)

    city_part = int(order.payload["city_part"])
    fund_part = int(order.payload["fund_part"])
    city_paid = int(order.payload["city_paid"])
    fund_used = int(order.payload["fund_used"])
    city_due = int(city_part * done_now) - city_paid
    fund_due = int(fund_part * done_now) - fund_used
    if done_now >= 1.0:
        city_due = city_part - city_paid
        fund_due = fund_part - fund_used

    #: The recipient's row serialises the cap read across the worker's orders
    #: -- the same lock, in the same place, as the road payout takes.
    recipient = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    await session.execute(
        select(LedgerAccount.id).where(LedgerAccount.id == recipient.id).with_for_update()
    )
    cap = money(constants[R.WORKS_PLAYER_DAILY_CAP])
    allowance = max(0, cap - await works.paid_today(session, identity_id, now=now))
    fund_pay = max(0, min(fund_due, allowance))

    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    payment = max(0, city_due) + fund_pay
    if payment > 0:
        await ledger.transfer(
            session,
            Reason.WORKS_PAYOUT,
            debit=escrow.id,
            credit=recipient.id,
            amount=payment,
            memo={"госзаказ": str(order.id)},
        )
    clipped = fund_due - fund_pay
    if clipped > 0:
        await ledger.transfer(
            session,
            Reason.ESCROW_RELEASE,
            debit=escrow.id,
            credit=(await works.fund_account(session)).id,
            amount=clipped,
            memo={"остаток сверх дневного потолка": str(order.id)},
        )
    order.payload = {
        **order.payload,
        "done": done_now,
        "city_paid": city_paid + max(0, city_due),
        "fund_used": fund_used + fund_due,
    }
    await session.flush()
    await events.record(
        session,
        EventKind.WORKS_PAID,
        actor_identity_id=identity_id,
        order_id=str(order.id),
        order_kind=order.kind.value,
        amount=payment,
        clipped=clipped,
    )
    return payment


async def _finish(session: AsyncSession, order: WorkOrder, identity_id: uuid.UUID, now: datetime):
    order.state = WorkOrderState.DONE
    order.done_by = identity_id
    order.done_at = now
    await session.flush()


async def pay_repair_order(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    identity_id: uuid.UUID | None,
    *,
    now: datetime | None = None,
) -> int:
    """Pay the open repair order after the engine saw the houses whole again."""
    moment = now or datetime.now(UTC)
    order = await open_city_order(session, WorkOrderKind.BUILDING_REPAIR, node, for_update=True)
    if order is None or identity_id is None:
        return 0
    houses = await buildings_of(session, node)
    if not houses or any(float(house.condition) < SCALE_MAX for house in houses):
        return 0
    paid = await _pay_share(session, constants, order, identity_id, 1.0, now=moment)
    await _finish(session, order, identity_id, moment)
    return paid


async def pay_build_order(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    building,
    identity_id: uuid.UUID | None,
    *,
    now: datetime | None = None,
) -> int:
    """Pay the open construction order if the finished house is the one ordered."""
    moment = now or datetime.now(UTC)
    order = await open_city_order(session, WorkOrderKind.BUILDING_BUILD, node, for_update=True)
    if order is None or identity_id is None:
        return 0
    wanted = order.payload
    fits = (
        building.kind == wanted.get("building_kind")
        and building.floors == int(wanted.get("floors", 0))
        and float(building.footprint_m2) >= float(wanted.get("footprint", 0))
    )
    if not fits:
        #: A different house went up on the ordered plot: not the work that
        #: was bought. The order stays for the one that was.
        return 0
    paid = await _pay_share(session, constants, order, identity_id, 1.0, now=moment)
    await _finish(session, order, identity_id, moment)
    return paid


async def pay_fuel_delivery(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    type_key: str,
    poured: float,
    identity_id: uuid.UUID | None,
    *,
    now: datetime | None = None,
) -> int:
    """Pay per unit landed in the station. The order closes when filled."""
    moment = now or datetime.now(UTC)
    order = await open_city_order(session, WorkOrderKind.FUEL_DELIVERY, node, for_update=True)
    if order is None or identity_id is None:
        return 0
    if order.payload.get("type_key") != type_key:
        return 0
    left = float(order.payload.get("left", 0.0))
    take = min(poured, left)
    if take <= 0:
        return 0
    total = float(order.payload["amount"])
    #: The pour that empties the order pays the whole remainder, not its
    #: float-drifted fraction: three thirds must sum to one, and a tail of
    #: minor units must not stay on the escrow of a DONE order for ever.
    closing = (left - take) <= _EPS
    fraction = 1.0 if closing else take / total
    paid = await _pay_share(session, constants, order, identity_id, fraction, now=moment)
    order.payload = {**order.payload, "left": 0.0 if closing else left - take}
    await session.flush()
    if closing:
        await _finish(session, order, identity_id, moment)
    return paid


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


async def _work_under_way(session: AsyncSession, kind: WorkOrderKind, node: Node) -> bool:
    """Whether a pending repair or construction job holds this order's plot.

    Fuel is not here on purpose: a delivery is paid per pour as it lands, so
    withdrawal loses nobody anything already done.
    """
    if kind is WorkOrderKind.BUILDING_REPAIR:
        return await repairing(session, node)
    if kind is WorkOrderKind.BUILDING_BUILD:
        rows = (
            (
                await session.execute(
                    select(Job).where(
                        Job.kind == JobKind.BUILD_FINISH.value,
                        Job.state == JobState.PENDING,
                    )
                )
            )
            .scalars()
            .all()
        )
        return any(job.payload.get("node") == str(node.id) for job in rows)
    return False


async def licensed(session: AsyncSession, kind: WorkOrderKind, node: Node) -> bool:
    """Whether an open city order licenses this work on this plot (D-248).

    The order is the permission: the city posted it holding the TREASURY
    power, and withdrawing it takes the permission back with the escrow.
    """
    return await open_city_order(session, kind, node) is not None


# --- the treasury as a borrower (D-248) ---------------------------------------


async def borrow_for_works(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    amount: float,
    *,
    now: datetime | None = None,
) -> Loan:
    """The city borrows from the CB: key rate, no margin, on its own line.

    No margin and no premium -- a city cannot mark itself up -- and no
    collateral, like every loan here (D-173): the limit is the line, and the
    line is turnover. Money goes reserve-first, the shortfall is printed,
    exactly as a citizen's loan does.
    """
    moment = now or datetime.now(UTC)
    await town.require_at_hall(session, body, city)
    await town.require(session, by.id, city, Power.TREASURY)
    total = money(amount)
    if total <= 0:
        raise WorksCityError(key="works-city-loan-not-positive")
    #: The line check is read-then-insert, and the line is the treasury
    #: loan's only brake: two rulers borrowing at once must not both see it
    #: free. The city row serialises them; the loser rereads a line that
    #: already carries the winner's loan.
    await session.execute(select(City.id).where(City.id == city.id).with_for_update())
    _, _, free = await bank.city_line(session, constants, city, now=moment)
    if total > free:
        raise WorksCityError(
            key="works-city-line-exhausted",
            money=money_str(free),
            cap=constants[R.BANK_DEBT_TO_TURNOVER_CAP],
        )

    rate_value = await bank.key_rate(session, constants)
    reserve_treasury = await bank.reserve_account(session)
    have = await ledger.balance(session, reserve_treasury.id)
    printed = max(0, total - have)
    if printed > 0:
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            Reason.GENESIS,
            debit=genesis.id,
            credit=reserve_treasury.id,
            amount=printed,
            memo={"печать под кредит казне": city.name},
        )
    await ledger.transfer(
        session,
        Reason.LOAN,
        debit=reserve_treasury.id,
        credit=(await town.treasury(session, city)).id,
        amount=total,
        memo={"кредит казне": city.name},
    )
    loan = Loan(
        identity_id=None,
        principal=total,
        outstanding=total,
        rate=rate_value,
        city_id=city.id,
        margin=0,
        printed=printed,
        taken_at=moment,
        accrued_at=moment,
        serviced_at=moment,
    )
    session.add(loan)
    await session.flush()
    await events.record(
        session,
        EventKind.LOAN_TAKEN,
        actor_identity_id=by.id,
        loan_id=str(loan.id),
        amount=total,
        rate=rate_value,
        printed=printed,
        city=city.name,
        treasury_loan=True,
    )
    return loan


async def repay_for_works(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    loan: Loan,
    amount: float | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Repay the treasury's loan from the treasury. Interest goes to the
    reserve whole: there is no margin to keep."""
    await town.require_at_hall(session, body, city)
    await town.require(session, by.id, city, Power.TREASURY)
    if loan.identity_id is not None or loan.city_id != city.id:
        raise WorksCityError(key="works-city-not-treasury-loan")
    return await bank.repay(
        session,
        constants,
        by,
        loan,
        amount,
        from_account=await town.treasury(session, city),
        now=now,
    )


async def treasury_loans(session: AsyncSession, city: City) -> list[Loan]:
    return list(
        (
            await session.execute(
                select(Loan).where(
                    Loan.city_id == city.id,
                    Loan.identity_id.is_(None),
                    Loan.state == LoanState.OPEN,
                )
            )
        )
        .scalars()
        .all()
    )
