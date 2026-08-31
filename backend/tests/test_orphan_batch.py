# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A batch whose job died is swept away and gives back what went in (D-217).

The batch is the one work whose end lives entirely in a journal job. While the
job is there everything holds. When it disappears -- retries exhausted on a
defect, a hand in the database, a job that never got queued -- nothing happens
at all: the batch stays "running" for ever and its master counts as busy for
ever with it (D-211), materials already written off.

That is not hypothetical. It was found on the live world: a batch of a thousand
ingots died on a defect long since fixed, and its master could take up nothing
for nine days. Nobody noticed -- the worker was silent, the interface said "work
in progress", and only asking the engine directly told the truth.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import craft, occupation, world
from src.models.craft import BatchState, CraftBatch
from src.models.estate import Building
from src.models.event import EventKind
from src.models.inventory import Item
from src.models.job import Job, JobState

BENCH = "workbench"
MAKE = "handle"
WOOD = "wood"


async def _shop(session: AsyncSession):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.orphan.{stamp}", "Двор", area_m2=200)
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, BENCH, quality=60, origin="тест")
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.learn(session, identity, MAKE)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, WOOD, amount=50, quality=60, origin="тест")
    return node, identity, body


async def _held(session: AsyncSession, body, name: str) -> float:
    from src.units import amount_float

    pocket = await world.body_container(session, body)
    rows = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == name)
            )
        )
        .scalars()
        .all()
    )
    return sum(amount_float(row.amount) for row in rows)


async def _kill_job(session: AsyncSession, batch: CraftBatch) -> Job:
    """What a defect does after the retries run out."""
    job = (
        await session.execute(select(Job).where(Job.dedup_key == f"craft.batch:{batch.id}"))
    ).scalar_one()
    job.state = JobState.FAILED
    await session.flush()
    return job


async def test_a_batch_whose_job_died_is_swept_and_pays_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _shop(session)
    before = await _held(session, body, WOOD)
    batch = await craft.start(session, constants, catalog, body, MAKE, 2)
    spent = dict(batch.spent)
    assert spent, "партия обязана помнить, что списала"
    assert await _held(session, body, WOOD) == pytest.approx(before - spent[WOOD])

    await _kill_job(session, batch)
    assert await craft.sweep_orphans(session) == 1

    assert batch.state is BatchState.CANCELLED, "отменена, а не «сделана»"
    #: Вложенное вернулось мастеру, стоящему у станка.
    assert await _held(session, body, WOOD) == pytest.approx(before)
    #: И станок свободен: половина работы не держит верстак вечно.
    bench = (
        await session.execute(
            select(Item).where(
                Item.container_id == (await world.node_container(session, node)).id,
                Item.type_key == BENCH,
            )
        )
    ).scalar_one()
    assert bench.busy_body_id is None


async def test_the_master_is_free_again(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The point of the whole rule: a dead job must not paralyse a living body."""
    _, _, body = await _shop(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 1)
    await _kill_job(session, batch)

    with pytest.raises(occupation.Busy):
        await occupation.require_free(session, body)

    await craft.sweep_orphans(session)
    await occupation.require_free(session, body)


async def test_a_healthy_batch_is_left_alone(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """State is checked, not time: a job still waiting its hour is not a corpse."""
    _, _, body = await _shop(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 1)

    assert await craft.sweep_orphans(session) == 0
    assert batch.state is BatchState.RUNNING


async def test_a_waiting_batch_is_not_an_orphan(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A frozen or queued batch has no job **by design** (D-209).

    Sweeping it would be the breakage, not the tidying: the master stepped away
    and their work is waiting for them, materials and all.
    """
    _, _, body = await _shop(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 1)
    await craft.freeze(session, body)
    assert batch.state is BatchState.WAITING

    assert await craft.sweep_orphans(session) == 0
    assert batch.state is BatchState.WAITING, "замороженная партия ждёт мастера, а не уборки"


async def test_what_comes_back_is_written_into_the_journal(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«Куда делось сырьё» разбирают по журналу, а не по памяти."""
    _, identity, body = await _shop(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 2)
    await _kill_job(session, batch)
    await craft.sweep_orphans(session)

    from src.models.event import Event

    said = (
        (await session.execute(select(Event).where(Event.kind == EventKind.CRAFT_ABANDONED)))
        .scalars()
        .all()
    )
    assert len(said) == 1
    payload = said[0].payload
    assert payload["output"] == MAKE
    assert payload["returned"][WOOD] > 0
    assert said[0].actor_identity_id == identity.id


async def test_the_return_lands_at_the_machine_when_the_master_left(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Matter does not travel after whoever walked away (D-209).

    The batch is frozen by leaving, so this one is orphaned by hand: the state
    the rule reacts to is «идёт, а задания нет», however it came about.
    """
    node, _, body = await _shop(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 2)
    await _kill_job(session, batch)

    other = await world.create_node(
        session, f"terra.away.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=50
    )
    body.node_id = other.id
    await session.flush()
    #: Карман едет с телом, и в нём лежит остаток исходного запаса. Речь не о
    #: нём: важно, что возврат в карман не попал.
    in_pocket = await _held(session, body, WOOD)

    await craft.sweep_orphans(session)

    from src.units import amount_float

    yard = await world.node_container(session, node)
    lying = (
        (
            await session.execute(
                select(Item).where(Item.container_id == yard.id, Item.type_key == WOOD)
            )
        )
        .scalars()
        .all()
    )
    assert sum(amount_float(row.amount) for row in lying) == pytest.approx(batch.spent[WOOD]), (
        "возврат остался у станка"
    )
    assert await _held(session, body, WOOD) == pytest.approx(in_pocket), (
        "материя не поехала за тем, кто ушёл"
    )
