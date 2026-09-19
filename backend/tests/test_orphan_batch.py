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

What comes back is exactly what was written off, landed the way the batch's own
yield would land: a coin as the coin it was -- fineness, minter's mark, no
quality (D-016) -- a liquid poured into a vessel (D-230), and what the hands
cannot hold beside the machine (D-265). The races of the sweep against a master
walking away are `test_races_orphan.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import coin, craft, gear, occupation, storage, world
from src.models.craft import BatchState, CraftBatch
from src.models.estate import Building
from src.models.event import EventKind
from src.models.inventory import Item
from src.models.job import Job, JobState
from src.units import amount_float

BENCH = "workbench"
MAKE = "handle"
WOOD = "wood"
COMPOST = "compost"
WASTE = "organic_waste"
WATER = "water"
CANISTER = "canister"
ORE = "iron_ore"
GOLD = "gold_coin"
GOLD_METAL = "refined_gold"
IRON = "iron_ingot"


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
    assert await craft.sweep_orphans(session) == (1, 0)

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

    assert await craft.sweep_orphans(session) == (0, 0)
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

    assert await craft.sweep_orphans(session) == (0, 0)
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


async def _lying(session: AsyncSession, node, name: str) -> list[Item]:
    yard = await world.node_container(session, node)
    rows = await session.execute(
        select(Item).where(Item.container_id == yard.id, Item.type_key == name)
    )
    return list(rows.scalars().all())


async def test_a_liquid_comes_back_poured(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Compost drinks water out of the canister in the hands; swept away, the
    batch pours it back in -- never loose into the hands, where a liquid
    cannot be (D-230)."""
    _, identity, body = await _shop(session)
    await world.learn(session, identity, COMPOST)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, WASTE, amount=50, quality=60, origin="test")
    canister = await world.grant_item(session, pocket, CANISTER, quality=60, origin="test")
    inside = await storage.inside(session, canister)
    await world.grant_item(session, inside, WATER, amount=50, quality=60, origin="test")
    batch = await craft.start(session, constants, catalog, body, COMPOST, 1)
    assert batch.spent[WATER] > 0

    await _kill_job(session, batch)
    assert await craft.sweep_orphans(session) == (1, 0)

    assert await _held(session, body, WATER) == 0, "no loose water in the hands"
    poured = sum(
        amount_float(thing.amount)
        for thing in await storage.content(session, canister)
        if thing.type_key == WATER
    )
    assert poured == pytest.approx(50), "the water is back in the canister"


async def test_what_the_hands_cannot_hold_falls_at_the_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The hands filled up while the batch ran: the giveback past the carry
    limit falls beside the machine (D-265), as the batch's yield would have --
    and not one piece of wood is lost on the way."""
    node, _, body = await _shop(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 2)
    pocket = await world.body_container(session, body)
    room = constants[R.INVENTORY_CARRY_MASS] - await gear.load_of(session, constants, catalog, body)
    await world.grant_item(
        session, pocket, ORE, amount=room / gear.mass_of(catalog, ORE, 1), origin="test"
    )

    await _kill_job(session, batch)
    await craft.sweep_orphans(session)

    load = await gear.load_of(session, constants, catalog, body)
    assert load <= constants[R.INVENTORY_CARRY_MASS] + 1e-6
    fallen = sum(amount_float(row.amount) for row in await _lying(session, node, WOOD))
    assert fallen > 0, "what did not fit lies at the machine"
    assert await _held(session, body, WOOD) + fallen == pytest.approx(50)


async def _minter(session: AsyncSession, constants: Constants, catalog: Catalog, count: int):
    """A mint in the yard and a minter holding `count` coins of their own."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.mint.{stamp}", "Mint", area_m2=100)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "coin_station", quality=60, origin="test")
    identity = await world.create_identity(session, f"Minter-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, GOLD_METAL, amount=10, quality=60, origin="test")
    await world.grant_item(session, pocket, IRON, amount=2, quality=55, origin="test")
    await world.learn(session, identity, GOLD)
    minting = await coin.mint(session, constants, catalog, body, GOLD, count)
    job = (
        await session.execute(select(Job).where(Job.dedup_key == f"craft.batch:{minting.id}"))
    ).scalar_one()
    await craft.finish(session, job)
    (stack,) = await _gold(session, pocket)
    return node, identity, body, stack


async def _gold(session: AsyncSession, container) -> list[Item]:
    rows = await session.execute(
        select(Item).where(Item.container_id == container.id, Item.type_key == GOLD)
    )
    return list(rows.scalars().all())


async def test_an_abandoned_melt_gives_back_the_very_coins(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Two of six coins go under the die, and the melt's job dies. They come
    back as the coins they were -- the minter's mark, the fineness, no quality
    (D-016) -- and fold back into the four left in the hands (D-214), rather
    than lying beside them as money nobody minted."""
    node, identity, body, stack = await _minter(session, constants, catalog, 6)
    mark = (stack.maker_identity_id, stack.made_at, stack.made_node_id)
    fineness = float(stack.fineness)
    assert mark[0] == identity.id and mark[2] == node.id

    melt = await coin.melt(session, constants, catalog, body, stack, 2)
    await _kill_job(session, melt)
    assert await craft.sweep_orphans(session) == (1, 0)

    (back,) = await _gold(session, await world.body_container(session, body))
    assert amount_float(back.amount) == 6, "one stack again, whole"
    assert back.quality is None, "a coin has no quality"
    assert (back.maker_identity_id, back.made_at, back.made_node_id) == mark
    assert float(back.fineness) == fineness


async def test_a_melt_left_behind_gives_its_coins_back_at_the_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The whole stack went under the die and the minter walked away: the coins
    lie at the press, the stack is gone, and the mark came back anyway -- the
    melt kept it, not the stack (D-217)."""
    node, identity, body, stack = await _minter(session, constants, catalog, 3)
    made_at = stack.made_at
    melt = await coin.melt(session, constants, catalog, body, stack, 3)
    await _kill_job(session, melt)
    away = await world.create_node(
        session, f"terra.away.{uuid.uuid4().hex[:6]}", "Away", area_m2=50
    )
    body.node_id = away.id
    await session.flush()

    await craft.sweep_orphans(session)

    assert await _gold(session, await world.body_container(session, body)) == []
    (back,) = await _lying(session, node, GOLD)
    assert amount_float(back.amount) == 3
    assert back.quality is None
    assert back.maker_identity_id == identity.id
    assert back.made_at == made_at
    assert back.made_node_id == node.id
    assert float(back.fineness) == constants[R.COIN_DEFAULT_FINENESS]


async def test_an_abandoned_mint_gives_back_metal_not_money(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The mint's batch carries a fineness too, and it belongs to the coins it
    would have made: the metal it gives back is metal -- with the quality it
    went in with and no fineness -- and nobody's mark is invented for it."""
    _, _, body, _ = await _minter(session, constants, catalog, 1)
    minting = await coin.mint(session, constants, catalog, body, GOLD, 5)
    await _kill_job(session, minting)
    await craft.sweep_orphans(session)

    pocket = await world.body_container(session, body)
    metal = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == GOLD_METAL)
            )
        )
        .scalars()
        .all()
    )
    assert sum(amount_float(row.amount) for row in metal) == pytest.approx(10 - 0.9)
    assert all(row.fineness is None and row.quality is not None for row in metal)
    assert all(row.maker_identity_id is None for row in metal)


async def test_the_sweep_frees_only_its_own_hold_on_the_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The orphan's job died after its hour, and by then the bench's stamp had
    lapsed: `_pick_station` counts that as free and gave the bench to a second
    master. Closing the first batch frees the bench only while it is still
    the first master's -- freed unconditionally, the second master's bench
    went to a third."""
    node, _, body = await _shop(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 1)
    yard = await world.node_container(session, node)
    bench = (
        await session.execute(
            select(Item).where(Item.container_id == yard.id, Item.type_key == BENCH)
        )
    ).scalar_one()
    assert bench.busy_body_id == body.id
    bench.busy_until = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    other_identity = await world.create_identity(session, f"Second-{uuid.uuid4().hex[:8]}")
    other = await world.print_body(session, other_identity, node)
    await world.learn(session, other_identity, MAKE)
    await world.grant_item(
        session, await world.body_container(session, other), WOOD, amount=10, origin="test"
    )
    await craft.start(session, constants, catalog, other, MAKE, 1)
    assert bench.busy_body_id == other.id

    await _kill_job(session, batch)
    assert await craft.sweep_orphans(session) == (1, 0)

    await session.refresh(bench)
    assert bench.busy_body_id == other.id, "the second master keeps the bench"
