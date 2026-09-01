# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat: a station that works without the player (D-253, revising D-035).

The third transition from labour to capital, after the rig (D-115) and the
field automaton (D-120), and built by the same creed: the machine loses to a
human on every measure but one -- it does not sleep.

| | Human | Automat |
|---|---|---|
| Speed | the recipe's own hours | `auto.speed_share` of that |
| Quality | by skill and inputs | not above `auto.quality_cap` |
| Needs | food and sleep | lubricant, energy, maintenance |
| Presence | constantly | only to program and to haul |

Craft remains the way to get **good things**, the automat the way to get
**a lot of average** -- word for word the rig's bargain.

## What it executes

One machine, one recipe (chains between machines are the node editor's
business, D-253 wave 5). The recipe is loaded **out of the owner's own
knowledge** (D-068, D-209): the machine is not a free library. Which recipes
a machine may take at all is the vault's, not code's:

* `auto.covers` -- station -> automat: the assembler stands in for benches,
  forges and workshops, the furnace for the smelters, the reactor for the
  chemistry. A station outside the table -- the hearth, the mint, the
  shipyards, «Руками» -- is outside automation by construction;
* `auto.barred_inputs` -- the pyroxite tier waits for its own station (OQ-106);
* stations themselves are never programmed: a station is a build, and its
  scale is set by hand (D-223).

## How it works

The worker tick advances every automat by wall time, like the rigs. An hour
of work needs `auto.lube_per_hour` of lubricant from the vessels standing in
the node (D-230: a liquid lives in a vessel; hauling lubricant is the coal
run of factories) and `auto.energy_per_hour` from the city pool, billed to
the owner at the tariff (D-135: whoever burns pays) -- or drawn from the
node's own batteries where no grid reaches (D-071). Inputs come off the
node's yard and the vessels in it; outputs land there too, a liquid poured
into vessels and **waiting in the backlog** while no vessel has room -- the
well does not spill for a forgotten canister, and neither does the reactor.

The backlog is time, not matter: inputs are consumed at payout, so work
never strands materials inside the machine. Wear runs by the clock whether
it works or stands -- an abandoned automat falls apart (`auto.wear_per_day`).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import battery, energy, events, ledger, liquid, station, stock, travel, wear, world
from src.engine.craft import HANDS, Procedure, Unmakeable, procedure
from src.engine.errors import Refusal
from src.models.automat import Automat as AutomatRow
from src.models.automat import AutomatLink
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Knowledge, KnowledgeKind
from src.models.inventory import Container, Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import (
    AMOUNT_SCALE,
    ENERGY_PER_TARIFF_UNIT,
    HOURS_PER_DAY,
    PERCENT,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    money,
)

#: The automat thing class (D-253): its members come from the vault, and the
#: node-editor window opens by this class on the client.
AUTOMAT = "automaton"

#: A piece is judged done at the world's own granularity (D-212): amounts
#: split into thousandths, and the last digit of a representation must not
#: hold a finished piece back.
_EPS = 1 / AMOUNT_SCALE

#: The lubricant thing class (D-253): what the machines drink, by the hour.
LUBE = "lube"


class AutomatError(Refusal):
    pass


class NotAnAutomat(AutomatError):
    """Not an automat: programs are loaded into the family of D-253 and nothing else."""


class NotCovered(AutomatError):
    """The recipe's station is not this automat's group (`auto.covers`)."""


class BarredInput(AutomatError):
    """The pyroxite tier is barred until its own station exists (OQ-106)."""


class NoStationBuilds(AutomatError):
    """A station is a build: its scale is set by hand, never by a machine (D-223)."""


class RecipeUnknown(AutomatError):
    """The owner does not know the recipe: the machine is not a free library (D-253)."""


class SelfLink(AutomatError):
    """A machine does not feed itself: the wire needs two ends (D-253)."""


async def of_item(session: AsyncSession, item: Item) -> AutomatRow | None:
    """The automat row of this machine, if it was ever programmed."""
    return (
        await session.execute(select(AutomatRow).where(AutomatRow.item_id == item.id))
    ).scalar_one_or_none()


async def program(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    recipe_key: str,
    *,
    now: datetime | None = None,
) -> AutomatRow:
    """Load a recipe into the machine. In person, on own ground, out of own knowledge."""
    moment = now or datetime.now(UTC)
    node = await _machine_here(session, body, item)
    proc = _programmable(constants, catalog, item, recipe_key)

    #: Out of the owner's own knowledge (D-253): choosing is loading, nothing
    #: is carried or inserted -- but the machine is not a free library, and
    #: an operation (smelting) is everyone's, as at the furnace itself.
    if proc.needs_recipe and not await _knows(session, body, proc.output):
        raise RecipeUnknown(key="auto-recipe-unknown", goods=proc.output)

    row = await of_item(session, item)
    if row is None:
        row = AutomatRow(
            item_id=item.id,
            node_id=node.id,
            owner_identity_id=body.identity_id,
            backlog=Decimal(0),
            counted_at=moment,
        )
        session.add(row)
    else:
        #: The old programme is worked to this moment first: hours lived under
        #: it must not produce under the new one.
        await advance(session, constants, row, catalog=catalog, now=moment)
        #: A change of programme drops the started piece: its inputs were
        #: never consumed (the backlog is time), so nothing is lost but time.
        row.backlog = Decimal(0)
        row.owner_identity_id = body.identity_id
        if row.node_id != node.id:
            #: The machine moved houses: its wires pointed at the old floor,
            #: and a wire between nodes is not a thing (D-047).
            await _drop_wires(session, item.id)
        row.node_id = node.id
    row.recipe_key = proc.output
    await session.flush()

    await events.record(
        session,
        EventKind.AUTOMAT_PROGRAMMED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        automat=str(row.id),
        machine=item.type_key,
        recipe=proc.output,
    )
    return row


async def stop(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> AutomatRow | None:
    """Take the programme off. The machine stays; the row goes with it.

    A row is the working state of a programmed machine and nothing else
    (D-253): without one the machine is a thing again -- it does not wear by
    the clock and does not cost the tick a lock. The wires stay: they are
    keyed by the machine itself, and the picture outlives the programme.
    """
    moment = now or datetime.now(UTC)
    node = await _machine_here(session, body, item)
    row = await of_item(session, item)
    if row is None:
        return None
    await advance(session, constants, row, now=moment)
    await session.delete(row)
    await session.flush()

    await events.record(
        session,
        EventKind.AUTOMAT_STOPPED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        machine=item.type_key,
    )
    return None


async def advance(
    session: AsyncSession,
    constants: Constants,
    row: AutomatRow,
    *,
    catalog: Catalog | None = None,
    now: datetime | None = None,
) -> float:
    """Advance the automat up to "now". Returns the units paid out.

    Four limiters, and any of them stops the machine: lubricant in the
    node's vessels, energy in the pool (or the batteries), inputs on the
    yard, and -- for a liquid output -- room in a vessel. None is an error:
    these are the enterprise's obligations, exactly as with the rig.
    """
    moment = now or datetime.now(UTC)
    #: The row is taken for the transaction: the tick and an owner
    #: reprogramming race for the same backlog and stamp.
    await session.refresh(row, with_for_update=True)
    hours = (moment - row.counted_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return 0.0
    book = catalog or current_catalog()

    machine = await session.get(Item, row.item_id)
    node = await session.get(Node, row.node_id)
    if machine is None:
        #: The machine is gone -- dismantled or worn to nothing. The row
        #: goes with it (the wires went with the machine itself, by CASCADE):
        #: a dead automat must not cost the tick a lock every pass.
        await session.delete(row)
        await session.flush()
        return 0.0
    #: Wear runs by the clock, worked or stood: an abandoned automat falls
    #: apart. Charged before the limiters, like the rig's -- and a machine
    #: the wear just finished does not work the window as a ghost.
    if await wear.spend(
        session,
        constants,
        machine,
        constants[R.AUTO_WEAR_PER_DAY] * hours / HOURS_PER_DAY,
        cause="automat_work",
    ):
        await session.delete(row)
        await session.flush()
        return 0.0
    if node is None or row.recipe_key is None:
        #: A row without a programme is a leftover of an older shape: the row
        #: is the working state, and a machine that works nothing has none.
        await session.delete(row)
        await session.flush()
        return 0.0
    yard = await world.node_container(session, node)
    if machine.container_id != yard.id:
        #: Carried away from its node: a machine works only where it stands.
        row.counted_at = moment
        await session.flush()
        return 0.0

    try:
        proc = procedure(book, row.recipe_key)
    except Unmakeable:  # pragma: no cover -- the vault dropped a recipe mid-world
        row.counted_at = moment
        await session.flush()
        return 0.0

    share = constants[R.AUTO_SPEED_SHARE] / PERCENT
    unit_hours = (proc.step_hours / share) if share > 0 else 0.0
    if unit_hours <= 0:
        row.counted_at = moment
        await session.flush()
        return 0.0

    #: Everything the advance will touch, taken in ONE query and one lock
    #: order (stock.py: "one query and one lock order, never two"): the
    #: lubricant and every input, off the yard and the vessels in it. Split
    #: by name after the lock -- two queries would hold id=9 while waiting
    #: for id=3 against a crafter taking them the one true way round.
    lube_rate = constants[R.AUTO_LUBE_PER_HOUR]
    lube_names = set(world.station_names(LUBE))
    every_key = lube_names | set(proc.per_unit)
    by_name: dict[str, list[Item]] = {}
    for stack in await liquid.locked_stacks(session, book, yard, tuple(every_key)):
        by_name.setdefault(stack.type_key, []).append(stack)
    lube_stacks = [stack for name in sorted(lube_names) for stack in by_name.get(name, [])]
    lube_have = sum(amount_float(stack.amount) for stack in lube_stacks)
    lube_hours = (lube_have / lube_rate) if lube_rate > 0 else hours

    #: The backlog's own inputs are still unconsumed, so the cap counts them too.
    backlog = float(row.backlog)
    units_by_inputs = math.inf
    for name, per in proc.per_unit.items():
        if per <= 0:
            continue
        have = sum(amount_float(stack.amount) for stack in by_name.get(name, []))
        units_by_inputs = min(units_by_inputs, have / per)
    input_hours = max(0.0, (units_by_inputs - backlog) * unit_hours)

    #: A liquid output waits for room (D-230): the backlog holds the worked
    #: units, and work past the room would burn lubricant for nothing.
    is_liquid_out = book.recipes.is_liquid(proc.output)
    room_units = math.inf
    if is_liquid_out:
        unit_mass = book.recipes.mass_of(proc.output)
        room = 0.0
        for vessel in await liquid.vessels_in(session, book, yard):
            room += await liquid.free_in(session, book, vessel)
        room_units = (room / unit_mass) if unit_mass > 0 else math.inf
    room_hours = max(0.0, (room_units - backlog) * unit_hours) if is_liquid_out else hours

    worked = max(0.0, min(hours, lube_hours, input_hours, room_hours))

    #: Energy caps last (D-135): from the city pool at the tariff, billed to
    #: the owner -- or from the node's own batteries where no grid reaches.
    energy_rate = constants[R.AUTO_ENERGY_PER_HOUR]
    if worked > 0 and energy_rate > 0:
        worked = await _draw_energy(session, constants, row, node, worked, energy_rate, now=moment)

    produced = 0.0
    if worked > 0:
        progress = backlog + worked / unit_hours
        cap = min(units_by_inputs, room_units)
        progress = min(progress, cap) if cap is not math.inf else progress
        if is_liquid_out or not book.recipes.counted(proc.output):
            paid = min(progress, room_units) if is_liquid_out else progress
        else:
            #: A piece is whole (D-212): the started one waits in the backlog.
            paid = float(math.floor(progress + _EPS))
        if paid > 0:
            await _pay_out(session, constants, book, row, machine, yard, proc, paid, by_name)
            produced = paid
            #: Told, not journaled (D-227), like a swing: the owner watching
            #: the floor sees the payout land without acting, and a thousand
            #: payouts a day stay out of the journal.
            if row.owner_identity_id is not None:
                await events.announce(
                    session,
                    touches=("node",),
                    identity_id=row.owner_identity_id,
                    event="automat.paid",
                    goods=proc.output,
                    made=amount_float(amount(paid)),
                )
        row.backlog = Decimal(str(max(0.0, progress - paid)))
        #: Lubricant burns for the hours worked, produced or not: the machine
        #: ran. Consumed after the payout maths so a refusal-free tick stays
        #: refusal-free.
        if lube_rate > 0:
            await stock.consume(session, lube_stacks, amount(lube_rate * worked))

    row.counted_at = moment
    await session.flush()
    return produced


async def tick_automats(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float:
    """Advance all automats of the world.

    The machine does not sleep -- that is its whole strength. Within a node
    the wires set the order (D-253 wave 5): a producer advances before the
    consumer it feeds, so a chain flows within one pass instead of lagging a
    tick per stage. A cycle of wires falls back to id order -- harmless: the
    order is a courtesy, not a correctness rule.
    """
    moment = now or datetime.now(UTC)
    rows = (await session.execute(select(AutomatRow).order_by(AutomatRow.id))).scalars().all()
    links = (await session.execute(select(AutomatLink))).scalars().all()
    made = 0.0
    for row in _chain_order(rows, links):
        made += await advance(session, constants, row, now=moment)
    return made


def _chain_order(rows: list[AutomatRow], links: list[AutomatLink]) -> list[AutomatRow]:
    """Kahn over the wires, per the whole world at once: feeders first.

    Wires are keyed by the machine items; a wire whose end has no working
    row feeds nobody and is skipped. Ties and cycles keep the incoming
    order -- the input arrives sorted by row id, and the queue preserves it.
    """
    by_item = {row.item_id: row for row in rows}
    feeds: dict[object, list[object]] = {}
    waits: dict[object, int] = {row.item_id: 0 for row in rows}
    for wire in links:
        if wire.from_item_id in by_item and wire.to_item_id in by_item:
            feeds.setdefault(wire.from_item_id, []).append(wire.to_item_id)
            waits[wire.to_item_id] += 1
    queue = [row.item_id for row in rows if waits[row.item_id] == 0]
    ordered: list[AutomatRow] = []
    while queue:
        current = queue.pop(0)
        ordered.append(by_item[current])
        for fed in feeds.get(current, []):
            waits[fed] -= 1
            if waits[fed] == 0:
                queue.append(fed)
    #: A cycle of wires: whatever Kahn could not release goes in id order.
    if len(ordered) < len(rows):
        left = {row.item_id for row in ordered}
        ordered.extend(row for row in rows if row.item_id not in left)
    return ordered


async def link(
    session: AsyncSession,
    body: Body,
    from_item: Item,
    to_item: Item,
) -> AutomatLink:
    """Wire A's output to B's input. Both machines here, both this owner's ground.

    Idempotent: the same wire drawn twice is one wire. The wire's mechanical
    meaning is the tick's order; the rest is the picture the editor draws.
    """
    if from_item.id == to_item.id:
        raise SelfLink(key="auto-link-self", goods=from_item.type_key)
    node = await _machine_here(session, body, from_item)
    await _machine_here(session, body, to_item)
    #: Idempotent under a race too: two hands drawing one wire must both
    #: succeed, not one of them crash on the unique pair (the quality bar).
    await session.execute(
        pg_insert(AutomatLink)
        .values(from_item_id=from_item.id, to_item_id=to_item.id)
        .on_conflict_do_nothing(constraint="uq_automat_link")
    )
    wire = (
        await session.execute(
            select(AutomatLink).where(
                AutomatLink.from_item_id == from_item.id,
                AutomatLink.to_item_id == to_item.id,
            )
        )
    ).scalar_one()
    await events.record(
        session,
        EventKind.AUTOMAT_LINKED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        source=str(from_item.id),
        target=str(to_item.id),
    )
    return wire


async def unlink(
    session: AsyncSession,
    body: Body,
    from_item: Item,
    to_item: Item,
) -> bool:
    """Cut the wire. Idempotent: cutting what is not there changes nothing."""
    node = await _machine_here(session, body, from_item)
    await _machine_here(session, body, to_item)
    wire = (
        await session.execute(
            select(AutomatLink).where(
                AutomatLink.from_item_id == from_item.id,
                AutomatLink.to_item_id == to_item.id,
            )
        )
    ).scalar_one_or_none()
    if wire is None:
        return False
    await session.delete(wire)
    await session.flush()
    await events.record(
        session,
        EventKind.AUTOMAT_UNLINKED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        source=str(from_item.id),
        target=str(to_item.id),
    )
    return True


async def _drop_wires(session: AsyncSession, item_id) -> None:
    """Cut every wire touching this machine: it moved houses (D-047)."""
    for wire in (
        (
            await session.execute(
                select(AutomatLink).where(
                    (AutomatLink.from_item_id == item_id) | (AutomatLink.to_item_id == item_id)
                )
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(wire)


async def view(session: AsyncSession, catalog: Catalog, body: Body) -> dict:
    """The automats standing where the body stands: machine, programme, backlog.

    A read: nothing is advanced and nothing is written (the tick does that).
    The numbers are as of the last tick, and that is what the console says.
    """
    await travel.require_here(session, body)
    rows = (
        (
            await session.execute(
                select(AutomatRow).where(AutomatRow.node_id == body.node_id).order_by(AutomatRow.id)
            )
        )
        .scalars()
        .all()
    )
    #: The wires of the floor: keyed by the machines standing here, so a
    #: wire between two unprogrammed machines is part of the picture too.
    node = await session.get(Node, body.node_id)
    yard = await world.node_container(session, node)
    here = (
        (await session.execute(select(Item.id).where(Item.container_id == yard.id))).scalars().all()
    )
    standing = set(here)
    wires = (
        (await session.execute(select(AutomatLink).where(AutomatLink.from_item_id.in_(standing))))
        .scalars()
        .all()
    )
    #: The machine's kind and place the client already has from `look` --
    #: only what it cannot derive travels (D-225): the address, the
    #: programme, the work in flight, and the wires (addressed by the same
    #: item ids the commands take).
    return {
        "machines": [
            {
                "item": str(row.item_id),
                "recipe": row.recipe_key,
                "backlog": float(row.backlog),
                "counted_at": row.counted_at.isoformat(),
            }
            for row in rows
        ],
        "links": [
            {"from": str(wire.from_item_id), "to": str(wire.to_item_id)}
            for wire in wires
            if wire.to_item_id in standing
        ],
    }


# --- internal ----------------------------------------------------------------


async def _machine_here(session: AsyncSession, body: Body, item: Item) -> Node:
    """Alive, here, entitled, and this thing is an automat standing in this node."""
    if body.state is not BodyState.ALIVE:
        raise AutomatError(key="auto-dead-works")
    await travel.require_here(session, body)
    if item.type_key not in world.station_names(AUTOMAT):
        raise NotAnAutomat(key="auto-not-an-automat", goods=item.type_key)
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise AutomatError(key="auto-body-off-node")
    yard = await world.node_container(session, node)
    if item.container_id != yard.id:
        raise AutomatError(key="auto-not-here")
    #: The same door as a chest's (D-181): whoever may dispose of the node
    #: programs its machines.
    if not await station.may_build(session, body, node):
        raise AutomatError(key="auto-not-entitled")
    return node


def _programmable(constants: Constants, catalog: Catalog, item: Item, recipe_key: str) -> Procedure:
    """The procedure, if this machine may run it at all (D-253).

    `procedure` itself refuses dishes and coins; here go the automat's own
    limits: no station builds, no pyroxite tier, and the station of the
    recipe must be this automat's group.
    """
    book = catalog.recipes
    canonical = book.resolve(recipe_key)
    #: An operation product (an ingot) has no recipe row -- and no kind to bar.
    found = next((r for r in book.recipes if r.type_key == canonical), None)
    if found is not None and found.kind is ItemKind.STATION:
        raise NoStationBuilds(key="auto-no-station-builds", goods=canonical)
    proc = procedure(catalog, canonical)
    barred = constants[R.AUTO_BARRED_INPUTS]
    for name in (*proc.inputs, canonical):
        if name in barred:
            raise BarredInput(key="auto-barred-input", goods=canonical)
    covers = constants[R.AUTO_COVERS]
    group = covers.get(proc.station or "", {})
    if item.type_key not in group:
        raise NotCovered(
            key="auto-not-covered",
            goods=item.type_key,
            station=proc.station or HANDS,
        )
    return proc


async def _knows(session: AsyncSession, body: Body, key: str) -> bool:
    stmt = select(Knowledge).where(
        Knowledge.identity_id == body.identity_id,
        Knowledge.kind == KnowledgeKind.RECIPE,
        Knowledge.key == key,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _draw_energy(
    session: AsyncSession,
    constants: Constants,
    row: AutomatRow,
    node: Node,
    worked: float,
    rate: float,
    *,
    now: datetime,
) -> float:
    """Cap the worked hours by energy and pay for them. Returns the hours.

    From the city pool at the tariff, billed to the owner (D-135: whoever
    burns pays, presence or not) -- or from the node's own batteries where no
    grid reaches (D-071): no pool, no tariff, the energy was bought when the
    battery was charged.
    """
    pool = await energy.pool_of(session, constants, node, lock=True)
    if pool is None:
        taken = await battery.drain_batteries(session, constants, node, worked * rate, now=now)
        return taken / rate
    await energy.produce(session, constants, pool, now=now)
    can_hours = float(pool.stored) / rate
    worked = min(worked, can_hours)
    if worked <= 0:
        return 0.0
    drawn = worked * rate
    price = money(drawn / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if price > 0 and row.owner_identity_id is not None:
        account = await ledger.account_for(session, AccountKind.IDENTITY, row.owner_identity_id)
        treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
        try:
            await ledger.transfer(
                session,
                PostingReason.ENERGY_BILL,
                debit=account.id,
                credit=treasury.id,
                amount=price,
                memo={"energy": drawn, "for": "automat", "tariff": float(pool.tariff)},
            )
        except ledger.InsufficientFunds:
            #: Whoever burns pays (D-135), and whoever cannot pay does not
            #: burn: the machine stands, the pool keeps its energy, and the
            #: tick survives -- an unpaid factory is an obligation broken,
            #: not a worker crash.
            return 0.0
    pool.stored = Decimal(str(float(pool.stored) - drawn))
    await session.flush()
    return worked


async def _pay_out(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    row: AutomatRow,
    machine: Item,
    yard: Container,
    proc: Procedure,
    paid: float,
    by_name: dict[str, list[Item]],
) -> None:
    """Consume the inputs for `paid` units and land the output on the yard.

    The stacks arrive already locked by the advance's single query -- asking
    again here would be the second lock order that door forbids. A liquid
    input was found without the payout knowing it reached into a canister
    (D-230). The output quality is the machine's ceiling: `auto.quality_cap`,
    lowered by wear -- the vein of the factory floor.
    """
    book = catalog.recipes
    for name, per in proc.per_unit.items():
        if per <= 0:
            continue
        await stock.consume(session, by_name.get(name, []), amount(per * paid))

    quality = min(constants[R.AUTO_QUALITY_CAP], wear.effective(constants, machine))
    fresh = Item(
        container_id=yard.id,
        type_key=proc.output,
        amount=amount(paid),
        quality=Decimal(str(quality)),
    )
    session.add(fresh)
    await session.flush()
    if book.is_liquid(proc.output):
        #: Into the vessels standing here (D-230). The room was counted under
        #: this transaction's locks; a pour that raced it anyway spills the
        #: difference with an event, exactly as a batch's liquid output does.
        spilled = await liquid.settle(session, catalog, fresh, (yard,))
        if spilled > 0:  # pragma: no cover -- a race the vessel locks make rare
            await events.record(
                session,
                EventKind.STORAGE_SPILLED,
                node_id=row.node_id,
                automat=str(row.id),
                spilled=spilled,
                goods=proc.output,
            )
    else:
        await world.stack_up(session, fresh)
