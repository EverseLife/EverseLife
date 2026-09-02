# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The batch's end: the journal job that lands the make, the repair or the
recycling -- and where the yield reaches, vessels included.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import events, goods, liquid
from src.engine import world as world_engine
from src.engine.craft._base import (
    CraftError,
)
from src.engine.craft._internal import (
    _num,
    _pieces,
    _release,
    _wear_station,
)
from src.engine.craft.batch.work import _target
from src.engine.craft.method_of_making import procedure
from src.engine.craft.queue import wake, wake_node
from src.engine.jobs import handler
from src.engine.world import body_container, node_container
from src.models.craft import BatchKind, BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, Item
from src.models.job import Job, JobKind
from src.models.world import Node
from src.units import (
    PERCENT,
    amount,
    amount_float,
)


@handler(JobKind.CRAFT_BATCH)
async def finish(session: AsyncSession, job: Job) -> None:
    """Work is done: products, a repaired thing, or a handful of materials."""
    batch = await session.get(CraftBatch, uuid.UUID(job.payload["batch"]))
    if batch is None:  # pragma: no cover -- a job without a batch is a bug
        raise CraftError(key="craft-job-without-batch", job=str(job.id))
    if batch.state is not BatchState.RUNNING:
        #: The job may have repeated after a failure -- no second batch comes of
        #: it. Or the batch froze while the master was away (D-209): the job of
        #: the frozen run finds nothing to finish, the resumed run has its own.
        return
    if job.payload.get("run", batch.runs) != batch.runs:
        #: A job of an earlier run, fired after the batch was frozen and resumed:
        #: it would finish the work ahead of time. Only the current run's job counts.
        return

    constants, catalog = current(), current_catalog()
    body = await session.get(Body, batch.body_id, with_for_update=True)
    node = await session.get(Node, batch.node_id)
    if body is None or node is None:  # pragma: no cover
        raise CraftError(key="craft-batch-dangling", batch=str(batch.id))

    #: The master stands at the machine -- takes it themselves; left or died --
    #: the output stays at the machine. Matter does not vanish with whoever ordered it.
    at_bench = body.state is BodyState.ALIVE and body.node_id == batch.node_id
    #: A station built in place never enters the hands (D-268): it stands on
    #: the floor of the place it was made in, master present or not.
    if catalog.recipes.built(batch.output):
        at_bench = False
    where = await body_container(session, body) if at_bench else await node_container(session, node)

    if batch.kind is BatchKind.REPAIR:
        made = await _finish_repair(session, constants, batch)
    elif batch.kind is BatchKind.RECYCLE:
        made = await _finish_recycle(session, constants, catalog, batch, where)
    else:
        made = await _finish_make(session, constants, catalog, batch, body, where, job.run_at)

    await _wear_station(session, constants, batch)
    #: The work is over -- the machine is free and waits for the next (D-150).
    await _release(session, batch.station_item_id)

    batch.state = BatchState.DONE
    batch.finished_at = job.run_at
    await session.flush()

    await events.record(
        session,
        EventKind.CRAFT_FINISHED,
        actor_identity_id=body.identity_id,
        node_id=batch.node_id,
        batch_id=str(batch.id),
        work=batch.kind.value,
        output=batch.output,
        units=amount_float(batch.units),
        quality=made,
    )
    #: The master's hands and the machine are free: the next work of theirs
    #: takes its turn, and whoever waited for this machine gets it (D-209).
    await wake(session, body, now=job.run_at)
    await wake_node(session, node, now=job.run_at)


async def _finish_make(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    batch: CraftBatch,
    body: Body,
    where: Container,
    moment: datetime,
) -> list[float]:
    """The batch: products with a mark and a quality spread around the promised value."""
    #: The seed comes from the batch: a job retry after a failure gives the
    #: same thing, not a new roll. Spread is a property of the batch, not the
    #: worker's luck.
    noise = random.Random(str(batch.id))
    scale = constants[R.QUALITY_SCALE]
    spread = float(batch.spread)
    units = amount_float(batch.units)

    #: A coin has no quality at all: fineness describes it, and it comes off the
    #: batch together with the minter's mark (D-016).
    from src.engine import coin  # noqa: PLC0415 -- lazy: breaks the import cycle with coin

    coin_ = coin.is_coin(catalog, batch.output)

    #: Food gets a shelf life at making: cooked from the pot spoils
    #: `cook.spoilage_multiplier` times faster, dry at the base speed. An
    #: operation's output (ingot, gravel) has no recipe at all -- and that is
    #: normal, not a reason to drop the batch: smelting runs without a recipe
    #: (20-systems/03).
    try:
        recipe = catalog.recipes.recipe(batch.output)
    except ConstantError:
        recipe = None
    spoils_at = None
    if recipe is not None and recipe.food:
        from src.engine import food  # noqa: PLC0415 -- lazy: breaks the import cycle with food

        spoils_at = (
            food.cooked_spoils_at(constants, now=moment)
            if batch.flavor is not None
            else moment + timedelta(hours=food.shelf_hours(constants, rate=1))
        )

    made: list[float] = []
    #: What arrived in the hands, for the carry rule below (D-265): judged
    #: once for the whole yield, not piece by piece.
    arrived: list[Item] = []
    for piece in _pieces(catalog, batch.output, units):
        quality = scale.clamp(float(batch.quality) + noise.uniform(-spread, spread))
        made.append(float(batch.fineness) if coin_ else quality)
        fresh = Item(
            container_id=where.id,
            type_key=batch.output,
            amount=amount(piece),
            quality=None if coin_ else _num(quality),
            fineness=batch.fineness,
            maker_identity_id=body.identity_id,
            made_at=moment,
            made_node_id=batch.node_id,
            spoils_at=spoils_at,
            flavor=batch.flavor,
            roles_filled=batch.roles_filled,
            recipe_key=batch.recipe_key,
            #: A station built in place stands where it was made (D-268); a
            #: portable one left at the bench lies there as cargo until somebody
            #: puts it up (D-278).
            installed=catalog.recipes.built(batch.output),
        )
        session.add(fresh)
        #: Loose output joins a stack it is indistinguishable from (D-214) --
        #: which in practice means an earlier batch of the same hour that came
        #: out at exactly the same quality. The spread usually sees to it that
        #: it did not, and then the stacks stay apart, as they should.
        await world_engine.stack_up(session, fresh)
        #: A liquid is poured, not handed over (D-230): into the vessels in
        #: the master's hands, then into those at the machine. What fits
        #: nowhere is spilled -- and said so, because matter that vanished in
        #: silence is a bug report waiting to happen.
        within = await _vessels_reach(session, batch, where)
        spilled = await liquid.settle(session, catalog, fresh, within)
        if spilled > 0:
            await events.record(
                session,
                EventKind.STORAGE_SPILLED,
                actor_identity_id=body.identity_id,
                node_id=batch.node_id,
                type_key=batch.output,
                amount=spilled,
            )
        elif len(within) > 1 and not liquid.is_liquid(catalog, batch.output):
            arrived.append(fresh)
    if arrived:
        #: Paid into the master's hands past the carry limit, the yield falls
        #: underfoot (D-265): a station is not carried off because it was
        #: made rather than picked up. Liquids are in vessels already.
        from src.engine import overload  # noqa: PLC0415 -- lazy: cycle via storage, estate

        await overload.settle_load(session, constants, catalog, body, arrived)
    return made


async def _vessels_reach(
    session: AsyncSession, batch: CraftBatch, where: Container
) -> list[Container]:
    """Where a liquid output may be poured: the hands first when the master is
    at the machine, then the place itself. Away from the bench the hands are
    out of reach, and only what stands at the machine takes it."""
    yard = await node_container(session, await session.get(Node, batch.node_id))
    if where.id == yard.id:
        return [yard]
    return [where, yard]


async def _finish_repair(
    session: AsyncSession, constants: Constants, batch: CraftBatch
) -> list[float]:
    """Repair: condition came back, the ceiling dropped."""
    item = await _target(session, batch)
    scale = constants[R.QUALITY_SCALE]
    #: `quality.repair_ceiling_loss` is given negative -- we add rather than
    #: subtract: the sign belongs to the vault, not the engine.
    cap = scale.clamp(float(item.condition_cap) + constants[R.QUALITY_REPAIR_CEILING_LOSS])
    item.condition_cap = _num(cap)
    item.condition = _num(cap)
    await session.flush()
    return [cap]


async def _finish_recycle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    batch: CraftBatch,
    where: Container,
) -> list[float]:
    """Recycling: the thing is gone, and not all materials came back."""
    from src.engine import coin  # noqa: PLC0415 -- lazy: breaks the import cycle with coin

    #: A coin melts by its fineness, not by the recipe norm: a spoiled one has
    #: exactly as much metal as was put into it (D-016).
    if coin.is_coin(catalog, batch.output):
        return await coin.finish_melt(session, constants, catalog, batch, where)

    item = await _target(session, batch)
    proc = procedure(catalog, batch.output)
    scale = constants[R.QUALITY_SCALE]

    carryover = constants[R.QUALITY_RECYCLE_CARRYOVER] / PERCENT
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    quality = scale.max if item.quality is None else float(item.quality)
    back = scale.clamp(quality * carryover)

    returned: list[float] = []
    for name, per_unit in proc.per_unit.items():
        #: What comes back comes back whole (D-212): a fifth of an ingot is not
        #: an ingot, and taking a thing apart cannot mint one out of rounding.
        given = amount(goods.whole(name, per_unit * share, catalog=catalog))
        if given <= 0:
            continue
        back_into = Item(container_id=where.id, type_key=name, amount=given, quality=_num(back))
        session.add(back_into)
        await world_engine.stack_up(session, back_into)
        returned.append(back)

    await events.record(
        session,
        EventKind.ITEM_CONSUMED,
        item_id=str(item.id),
        type_key=item.type_key,
        cause="recycled",
    )
    await session.delete(item)
    await session.flush()
    return returned
