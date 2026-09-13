# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton at work without the player (D-339): the advance that
wears the machine, walks its cursor, does the one action due and pays for the
hours in lubricant and energy -- and the tick that brings those minutes.

One advance does at most one action: an action holds the machine for the
minutes a hand's would (`busy_until`), and the shortest of them is longer than
a tick. What is due is read free from the beds' clocks (`plan.py`) and judged
again under each bed's lock by the hands (`hands.py`).

**Energy is promised before the action and drawn after it.** The promise
counts what the pool holds, what the owner's purse covers at the tariff
(D-135) and what this tick has promised already -- the same pool, the same
purse -- priced the way the bill will be, one sum per source and owner. The
draw takes the pool **last**, as a bench takes its stacks before the pool it
draws (`craft/batch/work.py`). What a draw comes up short by -- a bench took
the pool between the promise and the draw -- is a debt on the machine's row
(`energy_owed`), and the next action waits until the debt is promised with
the minute's own energy: a machine never works on credit twice.

Lock order, per machine: the machine's row, the yard's stacks (lubricant and
water, one query), the plot (skipped if held), the storage named for the
action and its stacks. At the end of the tick every pool it draws, in one
query by id, then the purses. The tick walks the machines by their node, so
two passes over two yards take the yards the same way round.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.engine import (
    automat,
    battery,
    energy,
    events,
    farm,
    ledger,
    liquid,
    station,
    stock,
    wear,
    world,
)
from src.engine.agro._base import FAULT, NO_LUBE, NO_PLOTS, NO_POWER, NOT_ENTITLED
from src.engine.agro.hands import Done, Shift
from src.engine.agro.plan import BY_AMOUNT, TROUBLE_WORK, Bed, Due, beds_of, plan, walk
from src.models.agro import FieldAutomat
from src.models.energy import EnergyPool
from src.models.event import EventKind
from src.models.inventory import Container, ContainerKind, Item
from src.models.ledger import AccountKind
from src.models.world import Node, is_aboard
from src.units import (
    ENERGY_PER_TARIFF_UNIT,
    ROUND_QUALITY,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    money,
    on_grid,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bill:
    """Energy a machine owes this tick, drawn after every machine has worked.

    Ids, not rows: a later machine's savepoint rolling back expires what it
    touched, and the bill is paid after all of them.
    """

    row_id: uuid.UUID
    owner_identity_id: uuid.UUID
    node_id: uuid.UUID
    #: The grid the energy comes from, or the hull or node whose own cells give it.
    source_id: uuid.UUID
    energy: float


@dataclass
class Promises:
    """What the tick has promised: energy by source, and by source and owner,
    with each source's tariff and each owner's purse read once."""

    by_source: dict[uuid.UUID, float] = field(default_factory=dict)
    by_bill: dict[tuple[uuid.UUID, uuid.UUID], float] = field(default_factory=dict)
    tariffs: dict[uuid.UUID, float] = field(default_factory=dict)
    purses: dict[uuid.UUID, int] = field(default_factory=dict)

    def copy(self) -> Promises:
        return Promises(
            dict(self.by_source), dict(self.by_bill), dict(self.tariffs), dict(self.purses)
        )

    def spent(self, owner: uuid.UUID) -> int:
        """What this owner is promised to pay, priced as the bills will be: one sum per source."""
        return sum(
            money(energy_ / ENERGY_PER_TARIFF_UNIT * self.tariffs.get(source, 0.0))
            for (source, who), energy_ in self.by_bill.items()
            if who == owner
        )


async def advance(
    session: AsyncSession,
    constants: Constants,
    row: FieldAutomat,
    *,
    catalog: Catalog | None = None,
    now: datetime | None = None,
    bills: list[Bill] | None = None,
    promises: Promises | None = None,
) -> int:
    """Bring the machine up to "now": wear, the cursor walked, the one action
    due done, and the hours paid in lubricant and energy. Returns the actions
    done (nought or one).

    With `bills` the energy is written down for the caller to draw after every
    machine has worked (the tick), against `promises` it shares with the other
    machines; without, it is drawn here, after the action. None of what stops
    the machine is an error (D-120): no energy, no lubricant, no water, a full
    store -- the enterprise's obligations, shown as the word it stands with.
    """
    moment = now or datetime.now(UTC)
    #: The row is the transaction's: the tick and an owner reprogramming race
    #: for the cursor and the stamp. A no-op when the caller already holds it.
    await session.refresh(row, with_for_update=True)
    hours = (moment - row.counted_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return 0
    book = catalog or current_catalog()
    machine = await session.get(Item, row.item_id)
    node = await session.get(Node, row.node_id)
    if machine is None:
        #: Gone -- burnt or taken apart. The row goes, and the bunker's
        #: contents would vanish with it: they fall to the yard instead.
        await _gone(session, row, node)
        return 0
    #: Wear by the clock, worked or stood (D-120), by the Terran day like the
    #: rig it is modelled on and like its own fallow and weeding (D-008).
    if await wear.spend(
        session,
        constants,
        machine,
        constants[R.AGRO_WEAR_PER_DAY] * hours / farm.day_hours(constants),
        cause="field_automat_work",
    ):
        await _gone(session, row, node)
        return 0
    yard = None if node is None else await world.node_container(session, node)
    if node is None or yard is None or machine.container_id != yard.id or not machine.installed:
        #: Taken down or carried off: a machine works only where it stands.
        return await _idle(session, row, row.trouble, moment)
    #: The owner's right to the node, asked every time: land sold from under
    #: the machine stops it -- the seller neither pays for it nor takes from
    #: chests that are the buyer's now, and the buyer may set it anew.
    owner = row.owner_identity_id
    if owner is None or not await station.may_build_as(session, owner, node):
        return await _idle(session, row, NOT_ENTITLED, moment)

    beds = await beds_of(session, constants, book, row, moment)
    if not beds:
        #: Nothing given to it: it idles, and idling draws nothing.
        return await _idle(session, row, NO_PLOTS, moment)
    walk(constants, row, beds, moment)

    #: The yard's stacks the hours may touch, in ONE query and one lock order
    #: (stock.py): the lubricant always, the water only where there is no river.
    lube_names = set(world.station_names(automat.LUBE))
    wanted = set(lube_names)
    if not world.has_place(node, world.WATER):
        wanted.add(farm.WATER)
    by_name: dict[str, list[Item]] = {}
    for stack in await liquid.locked_stacks(session, book, yard, tuple(wanted)):
        by_name.setdefault(stack.type_key, []).append(stack)

    #: A machine with a programme and plots is on the whole time: holding a
    #: setpoint is work (D-339 p. 8). The lubricant caps the hours; the energy
    #: of those hours and any debt left from a short draw are promised first.
    lube = [stack for name in sorted(lube_names) for stack in by_name.get(name, [])]
    lube_rate = constants[R.AUTO_LUBE_PER_HOUR]
    have = sum(amount_float(stack.amount) for stack in lube)
    worked = min(hours, have / lube_rate) if lube_rate > 0 else hours
    short = NO_LUBE if amount(lube_rate * worked) < amount(lube_rate * hours) else None
    need = worked * constants[R.AGRO_ENERGY_PER_HOUR] + float(row.energy_owed)
    promised = promises if promises is not None else Promises()
    source: uuid.UUID | None = None
    if need > 0:
        source = await _promise(session, constants, node, owner, need, promised, moment)
        if source is None:
            short, worked, need = NO_POWER, 0.0, 0.0

    done = 0
    if short is not None:
        trouble: str | None = short
    elif row.busy_until is not None and row.busy_until > moment:
        #: Busy with the last action: the word it stood with stays.
        trouble = row.trouble
    else:
        done, trouble = await _work(
            session, constants, book, row, node, yard, beds, by_name, moment
        )

    if lube_rate > 0 and worked > 0:
        await stock.consume(session, lube, amount(lube_rate * worked))
    if source is not None and need > 0:
        if bills is not None:
            bills.append(Bill(row.id, owner, node.id, source, need))
            row.energy_owed = Decimal(0)
        else:
            left = await _draw(session, constants, owner, node, need, moment)
            row.energy_owed = on_grid(left, ROUND_QUALITY)
            if left > 0:
                trouble = NO_POWER
    await _stand(session, row, trouble)
    row.counted_at = moment
    await session.flush()
    return done


async def _work(
    session: AsyncSession,
    constants: Constants,
    book: Catalog,
    row: FieldAutomat,
    node: Node,
    yard: Container,
    beds: list[Bed],
    by_name: dict[str, list[Item]],
    now: datetime,
) -> tuple[int, str | None]:
    """Do the first action due that goes. Returns the actions done and the word to stand with."""
    epoch = await world.epoch(session)
    shift = Shift(
        session, constants, book, row, node, yard.id, by_name.get(farm.WATER, []), epoch, now
    )
    due, planned = plan(shift, beds)
    failures: list[str] = []
    #: What a resource was found short for: the area of the smallest bed it
    #: failed, or None when it fails whatever the bed. A shortage of seeds for
    #: a big strip must not starve a small one of the same culture; a store
    #: that is not there is not there for any.
    short_for: dict[tuple[str, ...], float | None] = {}
    tried: set[str] = set()
    done = 0
    for entry in due:
        if entry.key in short_for:
            smallest = short_for[entry.key]
            if smallest is None or entry.area >= smallest:
                continue
        outcome = await entry.act()
        if outcome is None:
            #: Nothing to do after all, or the bed was held this minute: not a try.
            continue
        tried.add(entry.work)
        if isinstance(outcome, Done):
            row.busy_until = now + timedelta(minutes=outcome.minutes)
            done = 1
            break
        failures.append(outcome)
        short_for[entry.key] = entry.area if outcome in BY_AMOUNT else None
    if failures:
        return done, failures[0]
    if planned is not None:
        return done, planned
    return done, _kept(row.trouble, due, tried)


def _kept(was: str | None, due: list[Due], tried: set[str]) -> str | None:
    """The last word, kept while its work is still due and was not tried again.

    An action done this minute is no news about the work the word is about: a
    watering done while the sowing stays short of seeds must not blink the
    word off, or the journal would tell it again after every watering.
    """
    held_back = TROUBLE_WORK.get(was or "", frozenset())
    waiting = {entry.work for entry in due}
    return was if held_back & waiting and not held_back & tried else None


async def _promise(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    owner: uuid.UUID,
    need: float,
    promises: Promises,
    now: datetime,
) -> uuid.UUID | None:
    """Promise this much energy, or refuse. Returns the source promised from.

    Read without a lock, as a forecast reads (`energy.affordable`): the pool
    as it stands and the owner's purse at its tariff, less what this tick has
    promised them already and priced as the bill will be -- or, where no grid
    reaches, the cells of the node or of the whole hull aboard (D-071, D-288).
    """
    grid = await energy.grid_node(session, node)
    if grid is None:
        cells = node.parent_id if is_aboard(node) and node.parent_id is not None else node.id
        left = await battery.charge_in(session, constants, node, now=now)
        if left - promises.by_source.get(cells, 0.0) < need:
            return None
        promises.by_source[cells] = promises.by_source.get(cells, 0.0) + need
        return cells
    pool = await energy.pool_of(session, constants, node, create=False)
    if pool is None or float(pool.stored) - promises.by_source.get(grid.id, 0.0) < need:
        return None
    promises.tariffs[grid.id] = float(pool.tariff)
    after = promises.copy()
    after.by_bill[(grid.id, owner)] = after.by_bill.get((grid.id, owner), 0.0) + need
    price = after.spent(owner)
    if price > 0:
        if owner not in promises.purses:
            account = await ledger.find_account(session, AccountKind.IDENTITY, owner)
            promises.purses[owner] = (
                0 if account is None else await ledger.balance(session, account.id)
            )
        if promises.purses[owner] < price:
            return None
    promises.by_bill = after.by_bill
    promises.by_source[grid.id] = promises.by_source.get(grid.id, 0.0) + need
    return grid.id


async def _draw(
    session: AsyncSession,
    constants: Constants,
    owner_identity_id: uuid.UUID,
    node: Node,
    energy_: float,
    now: datetime,
) -> float:
    """Draw this energy through the family's door (D-253). Returns what was not drawn."""
    rate = constants[R.AGRO_ENERGY_PER_HOUR]
    if rate <= 0:  # pragma: no cover -- a machine that draws nothing owes nothing
        return 0.0
    hours = energy_ / rate
    powered = await automat.draw_energy(
        session, constants, owner_identity_id, node, hours, rate, now=now, purpose="field_automat"
    )
    #: Short only by what the pool can show: a last thousandth is not a debt.
    left = max(0.0, energy_ - powered * rate)
    return 0.0 if amount(left) <= 0 else left


async def _idle(
    session: AsyncSession, row: FieldAutomat, trouble: str | None, now: datetime
) -> int:
    """The machine stands through these hours: nothing worked, nothing drawn."""
    await _stand(session, row, trouble)
    row.counted_at = now
    await session.flush()
    return 0


async def _stand(session: AsyncSession, row: FieldAutomat, trouble: str | None) -> None:
    """Write the word the machine stands with; tell the owner when a new one comes.

    The journal line names the place; why the machine stands is its window's
    word, where the owner can act on it (D-339 p. 11).
    """
    if trouble == row.trouble:
        return
    row.trouble = trouble
    if trouble is None:
        return
    node = await session.get(Node, row.node_id)
    await events.record(
        session,
        EventKind.AGRO_STALLED,
        actor_identity_id=row.owner_identity_id,
        node_id=row.node_id,
        node=None if node is None else node.name,
        machine=str(row.item_id),
        trouble=trouble,
    )


async def _gone(session: AsyncSession, row: FieldAutomat, node: Node | None) -> None:
    """The machine is no more: its bunker falls to the yard, and the row goes."""
    hold = (
        await session.execute(
            select(Container).where(
                Container.kind == ContainerKind.STORAGE, Container.owner_id == row.item_id
            )
        )
    ).scalar_one_or_none()
    if hold is not None and node is not None:
        yard = await world.node_container(session, node)
        await session.execute(
            update(Item).where(Item.container_id == hold.id).values(container_id=yard.id)
        )
        await session.execute(delete(Container).where(Container.id == hold.id))
    await session.delete(row)
    await session.flush()


async def _take(session: AsyncSession, row_id: uuid.UUID) -> FieldAutomat | None:
    """The machine's row for this tick, or None when its owner holds it right now."""
    return (
        await session.execute(
            select(FieldAutomat)
            .where(FieldAutomat.id == row_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def tick_fields(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Advance every field automaton of the world. Returns the actions done.

    Each machine works in a savepoint of its own. One the database turned away
    this minute -- a lock waited too long -- is simply tried next minute. One
    whose programme or bed the vault has since broken stands with the word
    `fault` and its clock moved on: it neither stops the world's fields nor
    runs up a debt of wear and lubricant to be paid at once when mended. A
    machine its owner holds right now is skipped rather than waited for. The
    energy of all of them is drawn at the end.
    """
    moment = now or datetime.now(UTC)
    ids = (
        (
            await session.execute(
                select(FieldAutomat.id).order_by(FieldAutomat.node_id, FieldAutomat.id)
            )
        )
        .scalars()
        .all()
    )
    bills: list[Bill] = []
    promises = Promises()
    done = 0
    for row_id in ids:
        mine: list[Bill] = []
        before = promises.copy()
        try:
            async with session.begin_nested():
                row = await _take(session, row_id)
                if row is None:
                    continue
                done += await advance(
                    session, constants, row, now=moment, bills=mine, promises=promises
                )
        except DBAPIError:
            #: The database's no, not the machine's fault: next minute.
            promises = before
            log.warning("field automat %s: the database refused this minute", row_id)
            continue
        except Exception:  # noqa: BLE001 -- one machine must not stop the world's fields
            promises = before
            await _fault(session, row_id, moment)
            continue
        bills += mine
    await _pay(session, constants, bills, moment)
    return done


async def _fault(session: AsyncSession, row_id: uuid.UUID, now: datetime) -> None:
    """A machine whose advance failed: the word `fault`, the clock moved on, told once."""
    async with session.begin_nested():
        row = await _take(session, row_id)
        if row is None:
            log.exception("field automat %s: the advance failed; the row is held", row_id)
            return
        if row.trouble != FAULT:
            log.exception("field automat %s: the advance failed; it stands with a fault", row_id)
        await _idle(session, row, FAULT, now)


async def _pay(
    session: AsyncSession, constants: Constants, bills: list[Bill], now: datetime
) -> None:
    """Draw the tick's energy: every pool first, in one query by id, then one
    draw per source and owner. A draw that comes up short leaves each machine
    of the group its share as a debt, and the word `no_power`."""
    if not bills:
        return
    grids = sorted({bill.source_id for bill in bills})
    #: Taken together and in one order, before any purse: a pool taken, then a
    #: purse, then another pool would meet a bench that holds the second pool
    #: and reaches for the same purse.
    await session.execute(
        select(EnergyPool)
        .where(EnergyPool.node_id.in_(grids))
        .order_by(EnergyPool.id)
        .with_for_update()
    )
    grouped: dict[tuple[str, str], list[Bill]] = {}
    for bill in bills:
        grouped.setdefault((str(bill.source_id), str(bill.owner_identity_id)), []).append(bill)
    for key in sorted(grouped):
        group = grouped[key]
        node = await session.get(Node, group[0].node_id)
        if node is None:  # pragma: no cover -- a node is never deleted
            continue
        total = sum(bill.energy for bill in group)
        left = await _draw(session, constants, group[0].owner_identity_id, node, total, now)
        if left <= 0:
            continue
        for bill in group:
            row = await session.get(FieldAutomat, bill.row_id)
            if row is None:
                continue
            row.energy_owed = on_grid(left * bill.energy / total, ROUND_QUALITY)
            await _stand(session, row, NO_POWER)
    await session.flush()
