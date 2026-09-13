# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A thing under the knife (D-346): what taking apart may take, and when.

Checked is what the decision was written against:

* the doors that carry a thing out of the hands -- another pair of hands, the
  floor, the counter, a machine or a rig put up, a composition laid out --
  refuse a thing a batch is taking apart, in words, and a fall under a
  shrunken limit takes it last;
* the end takes apart only what still lies in the master's hands and is not
  worn: a thing the world moved meanwhile stays whole where it is, and
  nothing is paid for it;
* the rule is a batch at work and a place together, so a batch that waits, one
  that is over, or one whose thing the world took pins nothing;
* the return and the hours go by the amount named, and a thing takes one
  work at a time.

The races of the same rule -- a hand against the start and against the end --
are `test_races_recycle.py`.
"""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, gear, jobs, market, overload, station, storage, world
from src.engine.craft._base import CraftError, NotEnough, TooBig
from src.models.craft import BatchState, CraftBatch
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float

HAMMER = "hammer"
STEEL = "steel"
HANDLE = "handle"
WOOD = "wood"
FIBER = "fiber"
FLAX = "flax"
BENCH = "workbench"
BACKPACK = "simple_backpack"
TERMINAL = "market_terminal"


async def _master(session: AsyncSession, *machines: str):
    """A yard with the machines named and a master standing in it."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.knife.{stamp}", "Двор", area_m2=200)
    yard = await world.node_container(session, node)
    for machine in machines:
        await world.grant_item(session, yard, machine, quality=70, origin="сценарий теста")
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    return node, body


async def _hold(session: AsyncSession, body, what: str, qty: float = 1, quality: float = 80):
    pocket = await world.body_container(session, body)
    return await world.grant_item(
        session, pocket, what, amount=qty, quality=quality, origin="сценарий теста"
    )


async def _passer_by(session: AsyncSession, node):
    identity = await world.create_identity(session, f"Прохожий-{uuid.uuid4().hex[:8]}")
    return await world.print_body(session, identity, node)


async def _held(session: AsyncSession, body_id, what: str) -> float:
    body = await session.get(Body, body_id)
    pocket = await world.body_container(session, body)
    rows = (
        await session.execute(
            select(Item).where(Item.container_id == pocket.id, Item.type_key == what)
        )
    ).scalars()
    return sum(amount_float(row.amount) for row in rows)


# --- the doors ---------------------------------------------------------------


async def test_a_thing_under_the_knife_is_not_handed_over(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The hole itself: handed over mid-batch, the hammer used to be taken
    apart in the friend's hands and paid out as steel to the master."""
    node, body = await _master(session, "forge")
    hammer = await _hold(session, body, HAMMER)
    friend = await _passer_by(session, node)
    await craft.recycle(session, constants, catalog, body, hammer)

    with pytest.raises(world.TakenApart):
        await storage.hand(session, constants, catalog, body, friend, hammer)
    assert await _held(session, body.id, HAMMER) == 1, "молоток остался на верстаке"
    assert await _held(session, friend.id, HAMMER) == 0


async def test_a_thing_under_the_knife_is_not_put_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The floor is the same door: `move_stack` asks once for every surface."""
    _, body = await _master(session, "forge")
    hammer = await _hold(session, body, HAMMER)
    await craft.recycle(session, constants, catalog, body, hammer)

    with pytest.raises(world.TakenApart):
        await storage.drop(session, constants, catalog, body, hammer)


async def test_the_counter_says_why_it_takes_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The counter picks stacks by name, not by id: with one hammer under the
    knife it loads nothing and says why; with a spare it loads the spare."""
    _, body = await _master(session, "forge", TERMINAL)
    hammer = await _hold(session, body, HAMMER)
    await craft.recycle(session, constants, catalog, body, hammer)

    with pytest.raises(world.TakenApart):
        await market.load(session, constants, body, HAMMER, 1)

    spare = await _hold(session, body, HAMMER, quality=40)
    assert await market.load(session, constants, body, HAMMER, 1) == pytest.approx(1.0)
    await session.refresh(hammer)
    pocket = await world.body_container(session, body)
    assert hammer.container_id == pocket.id, "на прилавок ушёл запасной"
    assert (await session.get(Item, spare.id)).container_id != pocket.id


async def test_a_bench_under_the_knife_is_not_put_up(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Putting a machine up out of the hands writes its place directly, past
    `move_stack`, and asks the rule itself."""
    _, body = await _master(session)
    bench = await _hold(session, body, BENCH)
    await craft.recycle(session, constants, catalog, body, bench)

    with pytest.raises(world.TakenApart):
        await station.place(session, catalog, body, bench)


async def test_a_thing_under_the_knife_is_not_material(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The third stack-picker (`craft._stock`): a guess laying out one hammer
    must not burn the hammer on the bench."""
    _, body = await _master(session, "forge")
    hammer = await _hold(session, body, HAMMER)
    await craft.recycle(session, constants, catalog, body, hammer)

    with pytest.raises(NotEnough):
        await craft.invent(session, constants, catalog, body, {HAMMER: 1}, 1, station="forge")
    assert await _held(session, body.id, HAMMER) == 1


async def test_a_rig_under_the_knife_is_not_put_up(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The rig's own door writes the place directly as well."""
    from src.engine import rig

    node, body = await _master(session, "workshop")
    vein = await world.create_vein(session, node, "iron_ore", richness=60, remaining=100_000)
    machine = await _hold(session, body, "drilling_rig")
    await craft.recycle(session, constants, catalog, body, machine)

    with pytest.raises(world.TakenApart):
        await rig.place(session, body, machine, vein)


async def test_a_thing_under_the_knife_falls_last(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A limit that shrank sheds the heaviest first -- and the hammer is the
    heaviest thing here. It stays, and the lighter stacks go instead."""
    node, body = await _master(session, "forge")
    hammer = await _hold(session, body, HAMMER)
    heaviest = catalog.recipes.mass_of(HAMMER)
    #: Four stacks, each lighter than the hammer, over the bare hands together.
    for name in (WOOD, "iron_ore", "coal", "limestone"):
        pieces = math.floor((heaviest - 0.5) / catalog.recipes.mass_of(name))
        await _hold(session, body, name, pieces)
    await craft.recycle(session, constants, catalog, body, hammer)
    assert (
        await gear.load_of(session, constants, catalog, body) > constants[R.INVENTORY_CARRY_MASS]
    ), "сценарий: руки перегружены"

    assert await overload.shed(session, constants, catalog, body) > 0, "лишнее упало"
    assert await _held(session, body.id, HAMMER) == 1, "разбираемое падает последним"


async def test_a_thing_under_the_knife_is_no_floor_for_the_load(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A stack heavier than bare hands, put under the knife: were it a floor
    the shedding does not go under, nothing would fall at all and the body
    would walk off over the limit. Everything else falls, then as much of the
    stack as the limit asks -- and the end takes apart what is left of it."""
    share = constants[R.CRAFT_RECYCLE_RETURN] / 100
    per = craft.procedure(catalog, HANDLE).per_unit[WOOD]
    limit = constants[R.INVENTORY_CARRY_MASS]
    unit = catalog.recipes.mass_of(HANDLE)
    async with factory() as session, session.begin():
        _, body = await _master(session, BENCH)
        pieces = math.ceil((limit + 6) / unit)
        handles = await _hold(session, body, HANDLE, pieces)
        await _hold(session, body, "iron_ore", 5 / catalog.recipes.mass_of("iron_ore"))
        work = await craft.recycle(session, constants, catalog, body, handles)

        assert await overload.shed(session, constants, catalog, body) > 0
        assert await gear.load_of(session, constants, catalog, body) <= limit + 1e-6
        assert await _held(session, body.id, "iron_ore") == 0, "сначала падает всё остальное"
        left = await _held(session, body.id, HANDLE)
        assert 0 < left < pieces, "потом — столько разбираемого, сколько велит предел"
        term, body_id = work.ready_at, body.id

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        assert await _held(session, body_id, HANDLE) == 0
        assert await _held(session, body_id, WOOD) == math.floor(per * share * left)


# --- the end -----------------------------------------------------------------


async def test_the_end_takes_apart_nothing_the_world_moved(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The world's own hand does not ask (death, a burnt yard): the thing
    leaves the pocket past every door. Then it is pinned no more, and the end
    finds nothing to take apart -- the thing stays whole, no steel is paid."""
    async with factory() as session, session.begin():
        node, body = await _master(session, "forge")
        hammer = await _hold(session, body, HAMMER)
        friend = await _passer_by(session, node)
        work = await craft.recycle(session, constants, catalog, body, hammer)
        term, ids = work.ready_at, (body.id, friend.id, hammer.id, work.id)

    body_id, friend_id, hammer_id, batch_id = ids
    async with factory() as session, session.begin():
        thing = await session.get(Item, hammer_id)
        #: As `death` spills a pocket: the place written directly.
        thing.container_id = (await world.node_container(session, node)).id
        await session.flush()
        assert await world.taken_apart(session, [thing]) == frozenset(), "мир забрал — не держит"
        passer = await session.get(Body, friend_id)
        await storage.pick(session, constants, catalog, passer, thing)

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        batch = await session.get(CraftBatch, batch_id)
        assert batch.state is BatchState.DONE
        assert await _held(session, friend_id, HAMMER) == 1, "молоток цел у того, кто поднял"
        assert await _held(session, body_id, STEEL) == 0, "за чужой молоток сталь не платят"


async def test_a_batch_that_is_over_pins_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Cancelled is over as surely as done (D-217): the thing is free again,
    to be put on or taken apart anew. `gear.equip` used to ask its own
    question and held such a pack off the back for ever."""
    _, body = await _master(session, BENCH)
    pack = await _hold(session, body, BACKPACK)
    work = await craft.recycle(session, constants, catalog, body, pack)
    with pytest.raises(gear.Unmade):
        await gear.equip(session, constants, catalog, body, pack)
    work.state = BatchState.CANCELLED
    await session.flush()

    assert await world.taken_apart(session, [pack]) == frozenset()
    await gear.equip(session, constants, catalog, body, pack)
    assert (await gear.equipped(session, body))["back"].id == pack.id


async def _run_out(factory: async_sessionmaker[AsyncSession], term, batch_id) -> None:
    """Every job there is, up to the end of the batch named -- which only gets
    its hour once the batch ahead of it has finished and woken it."""
    later = term
    while (done := await jobs.run_one(factory, now=later)) is not None:
        later = max(later, done.run_at)
        async with factory() as session:
            waiting = await session.get(CraftBatch, batch_id)
            if waiting.ready_at is not None:
                later = max(later, waiting.ready_at)


async def test_a_waiting_batch_holds_nothing_and_takes_apart_nothing_gone(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A batch queued behind another is not at the bench: its thing may be
    handed over, and when its turn comes the end finds nothing to take apart.
    Held for a batch that waits, a thing could be held for ever -- a waiting
    batch whose machine was taken down never ends, and it cannot be cancelled."""
    async with factory() as session, session.begin():
        node, body = await _master(session, "forge")
        first = await _hold(session, body, HAMMER)
        second = await _hold(session, body, HAMMER, quality=60)
        friend = await _passer_by(session, node)
        going = await craft.recycle(session, constants, catalog, body, first)
        queued = await craft.recycle(session, constants, catalog, body, second)
        assert going.state is BatchState.RUNNING and queued.state is BatchState.WAITING

        assert await storage.hand(session, constants, catalog, body, friend, second) == 1
        term, body_id, friend_id, queued_id = going.ready_at, body.id, friend.id, queued.id

    await _run_out(factory, term, queued_id)

    share = constants[R.CRAFT_RECYCLE_RETURN] / 100
    one = math.floor(craft.procedure(catalog, HAMMER).per_unit[STEEL] * share)
    async with factory() as session:
        assert (await session.get(CraftBatch, queued_id)).state is BatchState.DONE
        assert await _held(session, friend_id, HAMMER) == 1, "отданный молоток цел"
        assert await _held(session, body_id, STEEL) == one, "сталь — только за свой молоток"


async def test_what_was_put_on_while_waiting_is_not_taken_apart(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Worn is on the body, not on the bench (D-305): a pack put on while its
    batch waited stays on the back when the batch ends."""
    async with factory() as session, session.begin():
        _, body = await _master(session, BENCH)
        first = await _hold(session, body, BACKPACK)
        second = await _hold(session, body, BACKPACK, quality=60)
        going = await craft.recycle(session, constants, catalog, body, first)
        queued = await craft.recycle(session, constants, catalog, body, second)
        await gear.equip(session, constants, catalog, body, second)
        term, body_id, second_id, queued_id = going.ready_at, body.id, second.id, queued.id

    await _run_out(factory, term, queued_id)

    async with factory() as session:
        assert (await session.get(CraftBatch, queued_id)).state is BatchState.DONE
        worn = await gear.equipped(session, await session.get(Body, body_id))
        assert worn["back"].id == second_id, "надетый рюкзак остался на спине"


# --- how much ----------------------------------------------------------------


async def test_a_stack_is_taken_apart_by_its_amount(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Ten handles are ten handles' work and ten handles' return -- not one
    handle's share for the whole stack gone."""
    share = constants[R.CRAFT_RECYCLE_RETURN] / 100
    per = craft.procedure(catalog, HANDLE).per_unit[WOOD]
    async with factory() as session, session.begin():
        _, body = await _master(session, BENCH)
        _, other = await _master(session, BENCH)
        ten = await craft.recycle(
            session, constants, catalog, body, await _hold(session, body, HANDLE, 10)
        )
        one = await craft.recycle(
            session, constants, catalog, other, await _hold(session, other, HANDLE, 1)
        )
        assert amount_float(ten.units) == 10
        ten_took = (ten.ready_at - ten.run_started_at).total_seconds()
        one_took = (one.ready_at - one.run_started_at).total_seconds()
        assert ten_took == pytest.approx(one_took * 10, rel=1e-3), "и часы — по количеству"
        term, body_id = max(ten.ready_at, one.ready_at), body.id

    while await jobs.run_one(factory, now=term) is not None:
        pass

    async with factory() as session:
        assert await _held(session, body_id, HANDLE) == 0
        assert await _held(session, body_id, WOOD) == math.floor(per * share * 10)


async def test_a_crumb_gives_back_a_crumb(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A hundredth of fibre used to fetch a whole unit's share of flax -- forty
    times what went into it. Now it fetches a hundredth of that share."""
    share = constants[R.CRAFT_RECYCLE_RETURN] / 100
    per = craft.procedure(catalog, FIBER).per_unit[FLAX]
    async with factory() as session, session.begin():
        _, body = await _master(session)
        crumb = await _hold(session, body, FIBER, 0.01)
        work = await craft.recycle(session, constants, catalog, body, crumb)
        term, body_id = work.ready_at, body.id

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        assert await _held(session, body_id, FLAX) == pytest.approx(per * share * 0.01, abs=1e-3)


async def test_what_comes_back_past_the_limit_falls_underfoot(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Fifty recipe blanks weigh a couple of kilograms and give back a share of
    fifty circuits and fifty ingots of steel -- far more than a pair of hands
    holds. Paid into the hands, what does not fit falls at the bench (D-265),
    exactly as a batch's yield does."""
    limit = constants[R.INVENTORY_CARRY_MASS]
    async with factory() as session, session.begin():
        node, body = await _master(session, "workshop")
        blanks = await _hold(session, body, "recipe_blank", constants[R.CRAFT_BATCH_MAX])
        work = await craft.recycle(session, constants, catalog, body, blanks)
        term, body_id, node_id = work.ready_at, body.id, node.id

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert await gear.load_of(session, constants, catalog, body) <= limit + 1e-6
        yard = await world.node_container(session, await session.get(Node, node_id))
        fallen = (
            await session.execute(
                select(Item).where(
                    Item.container_id == yard.id, Item.type_key == "electric_circuit"
                )
            )
        ).scalars()
        assert sum(amount_float(row.amount) for row in fallen) > 0, "лишнее легло у станка"


async def test_a_liquid_comes_back_poured(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Bitumen gives back crude oil, and oil lies in vessels only (D-230): into
    the canister in the hands, never loose in them."""
    share = constants[R.CRAFT_RECYCLE_RETURN] / 100
    per = craft.procedure(catalog, "bitumen").per_unit["crude_oil"]
    async with factory() as session, session.begin():
        _, body = await _master(session, "distillation_column")
        canister = await _hold(session, body, "canister")
        bitumen = await _hold(session, body, "bitumen", 1)
        work = await craft.recycle(session, constants, catalog, body, bitumen)
        term, body_id, canister_id = work.ready_at, body.id, canister.id

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        assert await _held(session, body_id, "crude_oil") == 0, "россыпью в руках нефти нет"
        inside = await storage.inside(session, await session.get(Item, canister_id), create=False)
        poured = (
            await session.execute(
                select(Item).where(Item.container_id == inside.id, Item.type_key == "crude_oil")
            )
        ).scalars()
        assert sum(amount_float(row.amount) for row in poured) == pytest.approx(per * share)


async def test_a_stack_that_grew_on_the_bench_keeps_the_rest(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """While the batch waited, the handles went down on the floor and came
    back up into a pocket that meanwhile held three more of the same: the stack
    folded into eight (D-214). The end takes apart the five it was named for."""
    share = constants[R.CRAFT_RECYCLE_RETURN] / 100
    per = craft.procedure(catalog, HANDLE).per_unit[WOOD]
    async with factory() as session, session.begin():
        _, body = await _master(session, BENCH, "forge")
        going = await craft.recycle(
            session, constants, catalog, body, await _hold(session, body, HAMMER)
        )
        handles = await _hold(session, body, HANDLE, 5)
        queued = await craft.recycle(session, constants, catalog, body, handles)
        assert queued.state is BatchState.WAITING

        await storage.drop(session, constants, catalog, body, handles)
        await _hold(session, body, HANDLE, 3)
        await storage.pick(session, constants, catalog, body, handles)
        assert await _held(session, body.id, HANDLE) == 8
        term, body_id, queued_id = going.ready_at, body.id, queued.id

    await _run_out(factory, term, queued_id)

    async with factory() as session:
        assert await _held(session, body_id, HANDLE) == 3, "неназванные рукояти остались"
        assert await _held(session, body_id, WOOD) == math.floor(per * share * 5)


async def test_a_stack_larger_than_a_batch_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, body = await _master(session, BENCH)
    most = constants[R.CRAFT_BATCH_MAX]
    handles = await _hold(session, body, HANDLE, most + 1)

    with pytest.raises(TooBig):
        await craft.recycle(session, constants, catalog, body, handles)


async def test_one_work_on_a_thing_at_a_time(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Queued twice, the second taking apart found nothing and its job died;
    a repair queued behind would spend its materials on a thing gone."""
    _, body = await _master(session, "forge")
    hammer = await _hold(session, body, HAMMER)
    hammer.condition = 40
    await session.flush()
    await craft.recycle(session, constants, catalog, body, hammer)

    with pytest.raises(CraftError) as twice:
        await craft.recycle(session, constants, catalog, body, hammer)
    assert twice.value.key == "craft-already-in-work"
    with pytest.raises(CraftError) as mended:
        await craft.repair(session, constants, catalog, body, hammer)
    assert mended.value.key == "craft-already-in-work"
