# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The battery: energy that stands where it is spent (D-071, D-179).

Split out of `engine/energy.py`, which crossed the length a file should have
when the ship's life support learned to draw charge (D-233). The seam is the
one that was already there: `energy` is about the **grid** -- a city pool that
fills by the hour, a tariff, a bill -- and this is about a **thing**, a cell
that is charged in a city and carried off to be spent where no grid reaches.

That is the whole of it, and it is why a ship needs it: away from a city, and
aboard a hull above all, a machine runs on the cells standing beside it. No
pool, no tariff and no bill -- the energy was bought when the battery was
charged.

Energy is perishable on purpose: a cell leaks `energy.battery_selfdischarge` a
day, so charge cannot be stockpiled for years and stays constant demand rather
than treasure.

`engine.energy` re-exports every name here, so `energy.charge_of` goes on
reading as it always did.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, ledger, stock, travel, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node, is_aboard
from src.units import (
    ENERGY_PER_TARIFF_UNIT,
    PERCENT,
    ROUND_CHARGE,
    SECONDS_PER_HOUR,
    amount_float,
    money,
    on_grid,
    step,
)

#: The class of the cell (D-215). Behaviour binds to the class, so a second
#: kind of battery is a line in the vault.
BATTERY = "battery"

#: Generators that need neither river, wind nor fuel (D-288). They live with
#: the cells rather than with the grid: their one job is to charge the
#: batteries within reach where no pool exists -- aboard a hull above all,
#: and on airless ground -- and they feed no city (`tick_offgrid`).
SOLAR = "solar_panel"
ISOTOPE = "isotope_generator"


class BatteryError(Refusal):
    pass


class NotBattery(BatteryError):
    """Not a battery: energy does not lie in a sack."""


def _grid():
    """The city grid, imported late.

    `energy` re-exports everything here, so it imports this module at its top;
    the pool, the tariff and their two refusals travel back the other way. One
    edge of the cycle, and this is it.
    """
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    return energy


def capacity(constants: Constants) -> float:
    return constants[R.ENERGY_BATTERY_CAPACITY]


def charge_of(constants: Constants, item: Item, *, now: datetime | None = None) -> float:
    """Battery charge with self-discharge -- by elapsed time.

    Energy is a perishable commodity: it cannot be stockpiled for years, and
    that makes it constant demand rather than treasure.
    """
    if item.charge is None:
        return 0.0
    moment = now or datetime.now(UTC)
    countdown = item.charged_at or item.created_at
    #: A day here is planetary, like all other terms of the world (D-008).
    hours_per_day = constants[R.TIME_DAY_TERRA]
    days = max(0.0, (moment - countdown).total_seconds() / SECONDS_PER_HOUR / hours_per_day)
    leaked = capacity(constants) * constants[R.ENERGY_BATTERY_SELFDISCHARGE] / PERCENT
    return max(0.0, float(item.charge) - leaked * days)


#: The grid the charge column keeps (`Numeric(12, 3)`). A charge is put on it
#: before it is stored, so what the engine believes a cell holds is what the
#: row holds -- otherwise Postgres rounds the write and the two drift apart.
_STEP = step(ROUND_CHARGE)


def _on_grid(charge: float | Decimal) -> Decimal:
    """The charge as the column will keep it, to the nearest thousandth.

    Not downwards: a charge reached through floats sits a hair below itself --
    sixteen arrives as `15.999999999999998` -- and flooring that would shave a
    thousandth off every write. The rounding is the one Postgres would have
    done anyway; putting it here is what makes the stored row and the engine's
    idea of it the same number, which is what the drain then measures against.
    """
    return max(Decimal(0), on_grid(charge, ROUND_CHARGE))


async def settle_charge(
    session: AsyncSession, constants: Constants, item: Item, *, now: datetime | None = None
) -> float:
    """Write into the battery its actual charge as of now.

    The stamp moves only when the charge does. A leak thinner than a
    thousandth has nowhere to be written, and moving the stamp over it would
    throw those hours away -- and every command that touches a cell settles
    it, so a cell in steady use would never leak at all. Left alone, the
    countdown keeps running until the leak is worth a thousandth.
    """
    moment = now or datetime.now(UTC)
    was = _on_grid(item.charge or 0)
    #: Upwards, alone among the writes here: the row must never claim a leak
    #: that has not happened yet. Rounded to the nearest, a cell would shed a
    #: whole thousandth once half of one had leaked, and the stamp below --
    #: which asks how long that drop took -- would run past the clock and be
    #: pulled back to it, doubling the leak for anyone settling often.
    charge_ = max(
        Decimal(0),
        Decimal(str(charge_of(constants, item, now=moment))).quantize(_STEP, rounding=ROUND_UP),
    )
    if charge_ == was:
        return float(charge_)
    item.charge = charge_
    item.charged_at = _leaked_through(constants, item, was - charge_, moment)
    await session.flush()
    return float(charge_)


def _leaked_through(
    constants: Constants, item: Item, dropped: Decimal, moment: datetime
) -> datetime:
    """The moment the stored drop was actually reached, not "now".

    The charge is written to the nearest thousandth, so a stamp moved to now
    would claim the whole elapsed for a drop the leak had only half earned,
    and a cell settled often would leak at twice its rate. Moving the stamp
    only as far as the drop accounts for leaves the remainder where it is --
    in the hours -- and the leak keeps its true pace however often it is read.
    """
    countdown = item.charged_at or item.created_at
    per_day = capacity(constants) * constants[R.ENERGY_BATTERY_SELFDISCHARGE] / PERCENT
    if per_day <= 0:  # pragma: no cover -- a world where cells do not leak
        return moment
    days = float(dropped) / per_day
    earned = countdown + timedelta(seconds=days * constants[R.TIME_DAY_TERRA] * SECONDS_PER_HOUR)
    #: Never past now: a cell drained to nothing stops leaking, and its stamp
    #: must not fall behind for good.
    return min(earned, moment)


async def charge_battery(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    amount_wanted: float | None = None,
    *,
    now: datetime | None = None,
) -> float:
    """Charge a battery from the city pool at the tariff.

    In person: charge is taken in the city and by hand. The taker pays -- into
    the city treasury: there is no free energy, and zero is a tariff too (D-085).

    Both the one in hand and the one standing here as a machine are charged
    (D-179): a battery is property of the place no less than a load.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise BatteryError(key="battery-dead-charges")
    await travel.require_here(session, body)

    if item.type_key not in world.station_names(BATTERY):
        raise NotBattery(key="battery-not-a-battery", goods=item.type_key)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise BatteryError(key="battery-body-off-node")
    pocket = await world.body_container(session, body)
    yard = await world.node_container(session, node)
    if item.container_id not in (pocket.id, yard.id):
        raise BatteryError(key="battery-not-here")
    pool = await _grid().pool_of(session, constants, node, lock=True)
    if pool is None:
        raise _grid().NoGrid(key="battery-no-grid")
    await _grid().produce(session, constants, pool, now=moment)

    #: The cell's row before its charge is read and rewritten: a worn
    #: exoskeleton drinks from this very cell every tick (D-268), and a charge
    #: written over a drain the tick just committed would undo the drain.
    await session.refresh(item, with_for_update=True)
    have = await settle_charge(session, constants, item, now=moment)
    place = max(0.0, capacity(constants) - have)
    wants = place if amount_wanted is None else min(float(amount_wanted), place)
    will_give = min(wants, float(pool.stored))
    if will_give <= 0:
        raise _grid().NotEnough(key="battery-nothing-to-give", have=float(pool.stored), place=place)
    #: A pour too thin to be written is refused rather than served. The pool
    #: gives up a whole step for any positive draw, so a request under one
    #: would burn the city's energy while the cell gained nothing and the bill
    #: rounded to nothing -- free of charge, and repeatable.
    if will_give < float(step(ROUND_CHARGE)):
        raise _grid().NotEnough(key="battery-give-too-little", least=float(step(ROUND_CHARGE)))

    #: What the cell will actually hold, and therefore what is charged for.
    #: The bill used to be issued on the asked-for figure while the row kept
    #: the rounded one: the payer and the cell disagreed by up to half a
    #: thousandth every time, in whichever direction the rounding fell.
    stored = _on_grid(have + will_give)
    given = float(stored) - have

    #: The tariff is given per hundred energy -- the bill is issued by it too.
    price = money(given / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if price > 0:
        account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
        treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=account.id,
            credit=treasury.id,
            amount=price,
            memo={"энергии": given, "тариф": float(pool.tariff)},
        )

    _grid().take_from_pool(pool, given)
    item.charge = stored
    item.charged_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.ENERGY_CHARGED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        energy=given,
        paid=price,
        tariff=float(pool.tariff),
    )
    return given


async def batteries_in(session: AsyncSession, node: Node) -> list[Item]:
    """The batteries standing in this node, locked for the transaction -- and
    aboard a ship, in **every** room of the hull.

    Locked because charge is a quantity of a shared thing (CLAUDE.md): a
    machine draws from the same cells a crew member is unplugging to carry
    off. In id order across the yards, like every other write-off.

    The hull is one building (D-288): a cell in the hold feeds the machine in
    the workshop, because a ship's rooms are one delegate node's children and
    the wiring between them is not worth a picture. The rooms are read off
    the node in hand -- its siblings -- so this asks nothing of the ship
    package, which imports this module through the gear.
    """
    rooms = [node]
    if is_aboard(node) and node.parent_id is not None:
        rooms = list(
            (await session.execute(select(Node).where(Node.parent_id == node.parent_id)))
            .scalars()
            .all()
        )
    yards = [await world.node_container(session, room) for room in rooms]
    #: The cells put up in the house (D-278): one dropped on the floor is cargo
    #: and feeds nothing until somebody stands it.
    return [
        cell
        for cell in await stock.locked_stacks(
            session, [yard.id for yard in yards], world.station_names(BATTERY)
        )
        if cell.installed
    ]


async def charge_in(
    session: AsyncSession, constants: Constants, node: Node, *, now: datetime | None = None
) -> float:
    """The charge standing here, read the way a forecast must read.

    The same cells `batteries_in` gathers and by the same two rules -- put up
    rather than lying (D-278), and the whole hull rather than the one room
    (D-288) -- but nothing is locked and no yard is made for the asking: this
    answers "how much could be drunk here", and a question must not write to
    the place it asks about (CLAUDE.md). A room nobody has put anything into
    has no yard row at all, and no yard holds no charge.
    """
    rooms = [node]
    if is_aboard(node) and node.parent_id is not None:
        rooms = list(
            (await session.execute(select(Node).where(Node.parent_id == node.parent_id)))
            .scalars()
            .all()
        )
    yards = [yard for yard in [await world.node_yard(session, room) for room in rooms] if yard]
    if not yards:
        return 0.0
    cells = (
        (
            await session.execute(
                select(Item).where(
                    Item.container_id.in_([yard.id for yard in yards]),
                    Item.type_key.in_(world.station_names(BATTERY)),
                    Item.installed.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(charge_of(constants, cell, now=now) * amount_float(cell.amount) for cell in cells)


async def fill_cells(
    session: AsyncSession,
    constants: Constants,
    cells: list[Item],
    offered: float,
    *,
    now: datetime | None = None,
) -> float:
    """Put charge into these locked cells, in order, up to their capacity.

    Returns what the cells actually took. The mirror of `drain_cells`, and for
    the same generator that never had a pool to fill (D-288): a panel or an
    isotope generator off the grid charges what stands within reach, and what
    nothing takes is not kept -- free energy for export does not exist.
    """
    moment = now or datetime.now(UTC)
    if offered <= 0:
        return 0.0
    left = offered
    banked = 0.0
    for cell in cells:
        if left <= 0:
            break
        have = await settle_charge(session, constants, cell, now=moment)
        pile = amount_float(cell.amount)
        room = capacity(constants) - have
        if room <= 0 or pile <= 0:
            continue
        #: Evenly into the pile, as the drain takes evenly out of it: a stack
        #: of cells stays one stack of one charge.
        give = min(left, room * pile)
        was = Decimal(str(have))
        stored = min(
            Decimal(str(capacity(constants))),
            _on_grid(was + Decimal(str(give)) / Decimal(str(pile))),
        )
        if stored <= was:
            continue
        cell.charge = stored
        cell.charged_at = moment
        really = float((stored - was) * Decimal(str(pile)))
        left -= really
        banked += really
    if banked > 0:
        await session.flush()
    return banked


async def batteries_carried(session: AsyncSession, body: Body) -> list[Item]:
    """The batteries in this body's hands, locked for the transaction (D-268).

    The exoskeleton drinks from them by the hour, and the same cell may be
    handed over or put down in the same moment: locked in id order, like the
    yard's.
    """
    pocket = await world.body_container(session, body)
    return await stock.locked_stacks(session, pocket.id, world.station_names(BATTERY))


async def charged_carried(
    session: AsyncSession, constants: Constants, body: Body, *, now: datetime | None = None
) -> bool:
    """Whether any battery in the hands holds a charge -- what powers a worn exoskeleton."""
    moment = now or datetime.now(UTC)
    pocket = await world.body_container(session, body)
    cells = (
        await session.execute(
            select(Item).where(
                Item.container_id == pocket.id,
                Item.type_key.in_(world.station_names(BATTERY)),
            )
        )
    ).scalars()
    return any(charge_of(constants, cell, now=moment) > 0 for cell in cells)


async def drain_batteries(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    wanted: float,
    *,
    now: datetime | None = None,
) -> float:
    """Take charge out of the node's own batteries. Returns what was taken.

    The grid does not reach everywhere and is not meant to: away from a city --
    and aboard a ship above all (D-071, D-233) -- a machine runs on the cells
    standing beside it. No pool, no tariff and no bill: the energy was bought
    when the battery was charged, and it is spent where it stands.

    Takes what there is when there is not enough: the caller decides what a
    half-fed machine does, and for life support that is exactly the point --
    it makes less air rather than stopping dead.
    """
    return await drain_cells(session, constants, await batteries_in(session, node), wanted, now=now)


async def drain_carried(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    wanted: float,
    *,
    now: datetime | None = None,
) -> float:
    """Take charge out of the batteries in the hands (D-268). Returns what was taken."""
    return await drain_cells(
        session, constants, await batteries_carried(session, body), wanted, now=now
    )


async def drain_cells(
    session: AsyncSession,
    constants: Constants,
    cells: list[Item],
    wanted: float,
    *,
    now: datetime | None = None,
) -> float:
    """Take charge out of these locked cells, in order.

    Returns what the caller may spend, never more than `wanted` and never more
    than the cells actually gave up. The two can differ: the charge column
    holds a thousandth of one cell, so a stack pays in whole steps and the
    remainder is lost rather than handed on.
    """
    moment = now or datetime.now(UTC)
    if wanted <= 0:  # pragma: no cover -- callers ask for a positive draw
        return 0.0
    left = wanted
    taken = 0.0
    for cell in cells:
        if left <= 0:
            break
        have = await settle_charge(session, constants, cell, now=moment)
        #: A stack of batteries is a stack of full cells: the charge column is
        #: one cell's, and the amount says how many stand there (D-179).
        pile = amount_float(cell.amount)
        if have <= 0 or pile <= 0:
            continue
        spend = min(left, have * pile)
        #: Drawn evenly from the pile, so a stack never splits into cells of
        #: different charge -- that is what would make it two stacks.
        was = Decimal(str(have))
        rest = _on_grid(was - Decimal(str(spend)) / Decimal(str(pile)))
        #: A draw thinner than a thousandth of a cell has nowhere to be
        #: written: the row would keep the charge it started with while the
        #: caller was told it got the energy, and the next command would find
        #: the stack full again. A stack of a thousand made every draw under
        #: half a unit free. So a draw that cannot show takes the smallest
        #: step the column holds, and what left the cells is read back from
        #: the cells rather than assumed.
        if rest >= was:
            rest = max(Decimal(0), was - _STEP)
        cell.charge = rest
        cell.charged_at = moment
        #: What the cells actually gave up, and what the caller is credited,
        #: are two numbers. The stack loses a whole step per cell even for a
        #: draw thinner than that, and the caller must not be handed the
        #: overshoot: `automat` turns energy into worked hours (`taken / rate`)
        #: and would run a machine longer than the clock allows. The remainder
        #: is lost on the grid -- against the drawer, which is the safe side.
        really = float((was - rest) * Decimal(str(pile)))
        given = min(really, spend)
        left -= given
        taken += given
    if taken > 0:
        await session.flush()
    return taken


# --- generation off the grid (D-288) ---------------------------------------------


def steady_rates(constants: Constants) -> dict[str, float]:
    """Output an hour of the generators that ask for nothing, by item name."""
    return {
        **dict.fromkeys(world.station_names(SOLAR), float(constants[R.ENERGY_SOLAR_RATE])),
        **dict.fromkeys(world.station_names(ISOTOPE), float(constants[R.ENERGY_ISOTOPE_RATE])),
    }


async def tick_offgrid(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float:
    """Charge what stands off the grid: a panel or an isotope generator in a
    room no pool reaches. Returns the energy the batteries took.

    The city's generators fill the city's pool (`energy.produce`); these have
    no pool to fill where it matters most -- aboard a hull under way, on the
    black fields of Pyroxis -- and charge the batteries within reach instead:
    the whole hull's, because the hull is one building (`batteries_in`). What
    nothing takes is not kept: free energy for export does not exist. Off the
    grid is read off the room itself: a city's room is the pool's business,
    and a room that is not a city's has no pool (`energy.grid_node`).

    The generator's own `charged_at` is its stamp -- when its output was last
    settled -- the way a battery's says when its charge was; a generator holds
    no charge of its own, so the column is free for it. **No stamp is the
    start, not the beginning of time**: a panel put up today is first seen by
    this tick, stamped, and credited nothing -- otherwise its age in the bag
    would come out of the cells as free energy. Taking it down clears the
    stamp (`station.take`) for the same reason.

    The generators' rows are **locked** before the stamp is read: two ticks on
    one hull -- the very race the air is tested against -- would otherwise
    both read the same hour and bank it twice, the cells locked and the source
    of the hours not.
    """
    moment = now or datetime.now(UTC)
    rates = steady_rates(constants)
    if not rates:  # pragma: no cover -- the vault names both
        return 0.0
    rows = (
        await session.execute(
            select(Item, Node)
            .join(Container, Container.id == Item.container_id)
            .join(Node, Node.id == Container.owner_id)
            .where(
                Container.kind == ContainerKind.NODE,
                Node.layer != Layer.CITY,
                Item.installed.is_(True),
                Item.type_key.in_(tuple(rates)),
            )
            .order_by(Item.id)
            .with_for_update(of=Item)
            .execution_options(populate_existing=True)
        )
    ).all()
    #: By room, so one hull's cells are read and settled once however many
    #: panels stand on it -- and in room order, like every other pass.
    by_room: dict[uuid.UUID, tuple[Node, list[Item]]] = {}
    for generator, node in rows:
        by_room.setdefault(node.id, (node, []))[1].append(generator)
    banked = 0.0
    for _, (node, generators) in sorted(by_room.items(), key=lambda pair: pair[0]):
        made = 0.0
        for generator in generators:
            if generator.charged_at is not None:
                hours = (moment - generator.charged_at).total_seconds() / SECONDS_PER_HOUR
                made += rates[generator.type_key] * amount_float(generator.amount) * max(0.0, hours)
            generator.charged_at = moment
        if made <= 0:
            continue
        cells = await batteries_in(session, node)
        banked += await fill_cells(session, constants, cells, made, now=moment)
    if rows:
        await session.flush()
    return banked
