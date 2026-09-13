# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton at work without the player (D-339): the advance that
wears the machine, walks its cursor, does the one action due and pays for the
hours in lubricant and energy -- and the tick that brings those minutes.

One advance does at most one action: an action holds the machine for the
minutes a hand's would (`busy_until`), and the shortest of them is longer than
a tick. What is due is read free from the beds' clocks (`plan.py`) and judged
again under each bed's lock by the hands (`hands.py`).

**Energy is the automat family's (D-253, `automat/bill.py`).** It is promised
before the action -- what the supply holds and what the owner's purse pays at
the tariff (D-135), less what this pass has promised already -- and drawn after
every machine has worked, supply by supply in the family's one lock order. A
machine is promised all its minute or none of it. What happens between the
promise and the draw is the family's rule too (the owner, 2026-09-13,
`20-systems/12-energy.md`): a pool a bench emptied meanwhile leaves the work
done and bills only what the pool gave; a purse emptied meanwhile is not
forgiven -- the pass runs again with it empty, and that owner's machines on
the tariff stand and lose the minute.

Lock order, per machine: the machine's row, the yard's stacks (lubricant and
water, one query), the plot (skipped if held), the storage named for the
action and its stacks. At the end of the pass the pools, then the cells, then
the purses (`bill.pay`). The tick walks the machines by their node, so two
passes over two yards take the yards the same way round.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.db.base import forget
from src.engine import (
    automat,
    events,
    farm,
    liquid,
    station,
    stock,
    wear,
    world,
)
from src.engine.agro._base import FAULT, NO_LUBE, NO_PLOTS, NO_POWER, NOT_ENTITLED
from src.engine.agro.hands import Done, Shift
from src.engine.agro.plan import BY_AMOUNT, TROUBLE_WORK, Bed, Due, beds_of, plan, walk
from src.engine.automat import bill as energy_bill
from src.models.agro import FieldAutomat
from src.models.event import EventKind
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import SECONDS_PER_HOUR, amount, amount_float

log = logging.getLogger(__name__)


#: What the bill names the energy for (`finance.posting` details).
PURPOSE = "field_automat"


class PurseMoved(Exception):
    """A purse the promise found full could not pay the draw: the minute goes back."""

    def __init__(self, owners: set[uuid.UUID]) -> None:
        super().__init__(owners)
        self.owners = owners


async def advance(
    session: AsyncSession,
    constants: Constants,
    row: FieldAutomat,
    *,
    catalog: Catalog | None = None,
    now: datetime | None = None,
    tab: energy_bill.Tab | None = None,
) -> int:
    """Bring the machine up to "now": wear, the cursor walked, the one action
    due done, and the hours paid in lubricant and energy. Returns the actions
    done (nought or one).

    With `tab` the energy is written down on the pass's tab for the caller to
    draw after every machine has worked (the tick); without, it is drawn here,
    after the action, and a purse that no longer pays raises `PurseMoved` for
    the caller to roll the advance back. None of what stops the machine is an
    error (D-120): no energy, no lubricant, no water, a full store -- the
    enterprise's obligations, shown as the word it stands with.
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
    #: of those hours is promised first, all of it or none.
    lube = [stack for name in sorted(lube_names) for stack in by_name.get(name, [])]
    lube_rate = constants[R.AUTO_LUBE_PER_HOUR]
    have = sum(amount_float(stack.amount) for stack in lube)
    worked = min(hours, have / lube_rate) if lube_rate > 0 else hours
    short = NO_LUBE if amount(lube_rate * worked) < amount(lube_rate * hours) else None
    rate = constants[R.AGRO_ENERGY_PER_HOUR]
    pass_tab = tab if tab is not None else energy_bill.Tab()
    bill: energy_bill.Bill | None = None
    if worked > 0 and rate > 0:
        bill = await energy_bill.promise(
            session, constants, row, node, worked, rate, now=moment, tab=pass_tab, purpose=PURPOSE
        )
        if bill is None or amount(bill.hours * rate) < amount(worked * rate):
            #: A minute half powered is not a minute: the setpoints hold for all
            #: of it or the machine stands.
            short, worked, bill = NO_POWER, 0.0, None
        else:
            #: Written down at once: should the work below fail, the tick takes
            #: this promise back with the machine's savepoint.
            pass_tab.add(bill)

    done = 0
    if short is not None:
        trouble: str | None = short
    elif row.busy_until is not None and row.busy_until > moment:
        #: Busy with the last action: the word it stood with stays -- unless it
        #: was the energy's or the lubricant's, and this minute had both.
        trouble = None if row.trouble in (NO_POWER, NO_LUBE) else row.trouble
    else:
        done, trouble = await _work(
            session, constants, book, row, node, yard, beds, by_name, moment
        )

    if lube_rate > 0 and worked > 0:
        await stock.consume(session, lube, amount(lube_rate * worked))
    if bill is not None and tab is None:
        refused = await energy_bill.pay(session, constants, [bill], now=moment)
        if refused:
            raise PurseMoved(refused)
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


async def _idle(
    session: AsyncSession, row: FieldAutomat, trouble: str | None, now: datetime
) -> int:
    """The machine stands through these hours: nothing worked, nothing drawn."""
    await _stand(session, row, trouble)
    row.counted_at = now
    await session.flush()
    return 0


#: The board's name for it: a command whose settling the purse refused.
idle = _idle


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

    The machines are paid for after they have worked (`bill.pay`), and a purse
    can empty in between. Whoever cannot pay does not burn (D-135): the whole
    pass goes back and runs again with that owner's purse taken as empty, as
    the automats' tick does (`automat.run.tick_automats`) -- one outcome for
    "did not pay", whichever second the money left in. A barred owner is never
    billed again, so each run bars one owner more and the runs end.
    """
    moment = now or datetime.now(UTC)
    ids = list(
        (
            await session.execute(
                select(FieldAutomat.id).order_by(FieldAutomat.node_id, FieldAutomat.id)
            )
        )
        .scalars()
        .all()
    )
    barred: set[uuid.UUID] = set()
    while True:
        try:
            async with session.begin_nested():
                return await _pass(session, constants, ids, barred, moment)
        except PurseMoved as moved:
            barred |= moved.owners
            #: What the rolled-back run remembered must not answer for the next.
            forget(session)
            log.info(
                "field automats: %d purse(s) emptied under the step, the pass runs again",
                len(moved.owners),
            )


async def _pass(
    session: AsyncSession,
    constants: Constants,
    ids: list[uuid.UUID],
    barred: set[uuid.UUID],
    moment: datetime,
) -> int:
    """One run over every machine, the energy drawn at its end.

    Each machine works in a savepoint of its own. One the database turned away
    this minute -- a lock waited too long -- is simply tried next minute. One
    whose programme or bed the vault has since broken stands with the word
    `fault` and its clock moved on: it neither stops the world's fields nor
    runs up a debt of wear and lubricant to be paid at once when mended. A
    machine its owner holds right now is skipped rather than waited for.
    """
    #: A purse that already failed a draw this minute pays nothing in the rerun.
    tab = energy_bill.Tab(purses=dict.fromkeys(barred, 0))
    done = 0
    for row_id in ids:
        owed = len(tab.bills)
        try:
            async with session.begin_nested():
                row = await _take(session, row_id)
                if row is None:
                    continue
                done += await advance(session, constants, row, now=moment, tab=tab)
        except Exception as failure:  # noqa: BLE001 -- one machine must not stop the world's fields
            tab.keep(owed)
            if _passing(failure):
                #: The database's no, not the machine's fault: next minute.
                log.warning("field automat %s: the database refused this minute", row_id)
            else:
                await _fault(session, row_id, moment)
    refused = await energy_bill.pay(session, constants, tab.bills, now=moment)
    if refused:
        raise PurseMoved(refused)
    return done


#: SQLSTATEs that say "not now" rather than "never": a deadlock, a
#: serialization failure, a lock not available, a statement cancelled by its
#: timeout. Anything else the database refuses would refuse again next minute.
_PASSING = frozenset({"40P01", "40001", "55P03", "57014"})


def _passing(failure: Exception) -> bool:
    """Whether the database turned this minute away and will not the next."""
    if not isinstance(failure, DBAPIError):
        return False
    state = getattr(failure.orig, "sqlstate", None) or getattr(failure.orig, "pgcode", None)
    return state in _PASSING


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
