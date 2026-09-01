# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Craft and quality (D-092, D-133).

Checked is not "the function computes a number" but what the system was written for:

* input amounts are taken **from data**, not from imagination (D-133);
* the quality forecast is shown as an exact number before materials are spent;
* the ceiling is set by the weakest link -- machine or tool;
* for a mix the proportion depends on raw-material quality, an assembly has none at all;
* a batch runs as a journal job and completes exactly once;
* matter is not created: the input is written off, losses went to the sink.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, jobs, wear, world
from src.engine.craft import Procedure
from src.models.craft import BatchState, CraftBatch
from src.models.identity import Body, KnowledgeKind
from src.models.inventory import Item
from src.units import amount_float

#: The first real processing step of the ladder: an iron ingot is smelted by
#: an operation, without a recipe (20-systems/03-crafting), while nails already require knowledge.
INGOT = "iron_ingot"
NAILS = "nails"
STEEL = "steel"
BENCH = "workbench"
FORGE = "forge"
FURNACE = "smelting_furnace"


async def _workshop(
    session: AsyncSession,
    *,
    #: Nails are forged: the tests' example recipe lives at the forge (machine rebalance).
    machine: str | None = FORGE,
    machine_quality: float = 60,
    library: bool = False,
):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.workshop.{stamp}",
        "workshop",
        area_m2=100,
        properties={"library": library},
    )
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    if machine is not None:
        yard = await world.node_container(session, node)
        await world.grant_item(
            session, yard, machine, quality=machine_quality, origin="сценарий теста"
        )
    if library:
        #: A library holds what was put into it (D-209): the test's one is a
        #: capital's -- the whole catalog on the shelf.
        from src.constants import current_catalog
        from src.engine import library as shelf

        await shelf.stock(
            session, node, (recipe.type_key for recipe in current_catalog().recipes.recipes)
        )
    return node, identity, body


async def _give(session: AsyncSession, body, type_key: str, quantity: float, quality: float):
    container = await world.body_container(session, body)
    return await world.grant_item(
        session,
        container,
        type_key,
        amount=quantity,
        quality=quality,
        origin="сценарий теста",
    )


async def _in_inventory(session: AsyncSession, body, type_key: str) -> float:
    container = await world.body_container(session, body)
    result = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == container.id, Item.type_key == type_key
        )
    )
    return amount_float(int(result or 0))


# --- method of making ---------------------------------------------------------


def test_input_amounts_taken_from_data(catalog: Catalog) -> None:
    """Amounts are never taken from imagination (D-133)."""
    recipe = catalog.recipes.recipe(NAILS)
    method = craft.procedure(catalog, NAILS)
    assert method.per_unit == {INGOT: recipe.amounts[INGOT]}
    assert method.needs_recipe, "рецепт требует знания"


def test_operation_without_recipe_needs_no_knowledge(catalog: Catalog) -> None:
    """Smelting is the boundary between "mined" and "made", and it is open to all."""
    method = craft.procedure(catalog, INGOT)
    assert not method.needs_recipe
    assert method.station == FURNACE
    assert set(method.per_unit) == {"iron_ore", "coal"}


def test_every_recipe_is_made_by_hand_or_at_a_real_machine(catalog: Catalog) -> None:
    """A station is either the one word for "by hand" or a thing that exists (D-216).

    This is the guard that was missing. The vault used to carry a second word
    for emptiness -- «Стройка», a leftover of the recipe kind D-106 abolished --
    and the engine quietly understood it while the client did not: eighteen
    recipes, the workshop and the bioprinter and the road surface among them,
    were offered to the player nowhere at all. Nothing failed, nothing was
    logged, and the whole "found your own city" branch was dead for two months.

    So the invariant is asserted where both sides can see it: **the list of
    words meaning "no machine" is exactly one long**, and every other station
    is an item somebody can actually put in a node.
    """
    from src.constants.catalog import ItemKind

    assert craft.BENCHLESS == (craft.HANDS,), (
        "второе имя пустоты — синоним, о котором узнаёт не вся система"
    )

    book = catalog.recipes
    for recipe in book.recipes:
        station = recipe.station
        if station is None or station in craft.BENCHLESS:
            continue
        made = book.recipe(station)
        assert made.kind is ItemKind.STATION, (
            f"«{recipe.name}» делается на «{station}», а это не рабочая станция"
        )


def test_mining_does_not_pretend_to_be_craft(catalog: Catalog) -> None:
    """An operation that spends nothing takes matter from the world -- that is not craft.

    Ore goes by its own mechanic (vein and pickaxe), and cannot be taken by batch.
    """
    with pytest.raises(craft.Unmakeable):
        craft.procedure(catalog, "iron_ore")


def test_place_extraction_goes_as_batch(catalog: Catalog) -> None:
    """Felling is place extraction (D-177): without inputs, but tied to a node."""
    method = craft.procedure(catalog, "wood")
    assert method.place == "woods"
    assert method.inputs == ()
    assert "axe" in method.tools
    assert not method.needs_recipe


def test_wood_is_felled_by_a_named_way(catalog: Catalog) -> None:
    """The way is named, not guessed (D-196), and felling wants an axe.

    Deadwood by hand is no longer an operation: what lies on the ground is
    found by foraging (D-210), and the forest window keeps only the axe.
    """
    felling = craft.procedure(catalog, "wood", way="logging")
    assert "axe" in felling.tools
    assert felling.place == "woods"
    with pytest.raises(craft.Unmakeable):
        craft.procedure(catalog, "wood", way="Сбор валежника")


def test_unknown_way_is_refused_and_the_real_ones_are_named(catalog: Catalog) -> None:
    """A made-up way is not silently replaced by any other -- and the refusal
    says which ways do make the thing.

    The resolver has just computed them, and a refusal that only says "not this
    way" leaves the asker guessing: an AI citizen (D-224) spent ten minutes
    trying `forge` and `smelt` at a catalog that says «Рубка дерева».
    """
    #: Asserted by key and by what the refusal carries, not by the sentence:
    #: the wording belongs to the locale now, and a translation must not fail a
    #: test about the rules (D-251 wave III).
    with pytest.raises(craft.Unmakeable) as refusal:
        craft.procedure(catalog, "wood", way="Телекинез")
    assert refusal.value.key == "craft-unknown-way"
    assert refusal.value.params["way"] == "Телекинез"
    assert refusal.value.params["known"] == "true"
    assert "logging" in refusal.value.params["ways"]


def test_stone_axe_ladder_needs_no_tools(catalog: Catalog, constants: Constants) -> None:
    """The whole ladder from bare hands to the first axe (D-196, D-210).

    The raw of the first axe is found by foraging on empty land, and every
    step after that is handwork: if any of them asks for a machine or a tool,
    the world cannot be started from scratch.
    """
    from src.engine import forage

    #: The whole surface, not one place's share: what the ladder needs is
    #: that these lie about somewhere at all (D-254 binds each to its mark).
    found = forage.whole_table(constants)
    for output in ("wood", "stone", "flax"):
        assert output in found, f"{output} должен находиться собирательством"

    for output in ("fiber", "rope", "stone_axe"):
        step = craft.procedure(catalog, output)
        assert step.station is None, f"{output} должен собираться руками"

    axe = craft.procedure(catalog, "stone_axe")
    assert set(axe.inputs) == {"stone", "wood", "rope"}


async def test_felling_asks_for_the_right_place(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Trees are felled where the forest is: on a bare plot there is nothing to fell (D-177)."""
    stamp = uuid.uuid4().hex[:8]
    bare = await world.create_node(
        session, f"terra.bare.{stamp}", "Пустырь", area_m2=10_000, properties={}
    )
    identity = await world.create_identity(session, f"Новичок-{stamp}")
    body = await world.print_body(session, identity, bare)

    with pytest.raises(craft.CraftError):
        await craft.start(session, constants, catalog, body, "wood", 1, way="logging")


async def test_a_tool_from_the_node_is_refused_by_name_of_what_tool_means(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`tool` is a thing in the hands; the machine standing here is taken by the
    engine. The refusal says so, because an AI citizen (D-224) read the id of
    the smelter off the place and sent it here twenty-four times running.
    """
    stamp = uuid.uuid4().hex[:8]
    forest = await world.create_node(
        session, f"terra.forest.{stamp}", "Лес", area_m2=10_000, properties={"woods": True}
    )
    identity = await world.create_identity(session, f"Дровосек-{stamp}")
    body = await world.print_body(session, identity, forest)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "axe", quality=60, origin="сценарий теста")
    yard = await world.node_container(session, forest)
    standing = await world.grant_item(session, yard, "smelting_furnace", quality=70, origin="тест")

    with pytest.raises(craft.NoTool) as refusal:
        await craft.start(
            session,
            constants,
            catalog,
            body,
            "wood",
            1,
            way="logging",
            tool_item_id=standing.id,
        )
    #: By key, not by wording: the sentence lives in the locale (D-251 wave III).
    assert refusal.value.key == "craft-tool-not-in-hands"


def test_dishes_wait_for_cooking(catalog: Catalog) -> None:
    """Roles arrive together with cooking on E2 (D-119), and pretending is not allowed."""
    with pytest.raises(craft.Unmakeable):
        craft.procedure(catalog, "soup")


def test_time_grows_with_processing_depth(catalog: Catalog, constants: Constants) -> None:
    """The main value knob of the ladder: the deeper the processing, the longer (D-133)."""
    nails = craft.step_hours(catalog, catalog.recipes.recipe(NAILS))
    pickaxe = craft.step_hours(catalog, catalog.recipes.recipe("steel_pickaxe"))
    assert pickaxe > nails > 0

    #: The own step of the first processing level is exactly `craft.time_per_unit`.
    #: Fiber, not rope: since D-196 the flax goes raw -> fiber -> rope, and rope
    #: became the second level -- which is exactly what the ladder should charge for.
    fiber = craft.step_hours(catalog, catalog.recipes.recipe("fiber"))
    assert fiber * 60 == pytest.approx(constants[R.CRAFT_TIME_PER_UNIT], rel=0.05)

    rope = craft.step_hours(catalog, catalog.recipes.recipe("rope"))
    assert rope > fiber, "верёвка теперь глубже волокна"


def test_broken_machine_works_slowly(catalog: Catalog, constants: Constants) -> None:
    method = craft.procedure(catalog, NAILS)
    scale = constants[R.QUALITY_SCALE]
    fast = craft.batch_minutes(constants, method, 1, scale.max)
    slow = craft.batch_minutes(constants, method, 1, scale.min)
    k = constants[R.CRAFT_STATION_SPEED_K]
    assert slow / fast == pytest.approx(k.max / k.min)


# --- quality -----------------------------------------------------------------


def _mix(catalog: Catalog) -> Procedure:
    """Steel: pig iron, coal, limestone -- fifteen mixes out of one hundred and twelve."""
    return craft.procedure(catalog, STEEL)


def test_poor_ore_needs_more_flux(constants: Constants, catalog: Catalog) -> None:
    """For a mix the optimum depends on raw-material quality (D-092)."""
    mix = _mix(catalog)
    bonus = mix.inputs[1]
    poor = craft.optimal_amounts(constants, mix, 1, 20)
    pure_ = craft.optimal_amounts(constants, mix, 1, 90)
    assert poor[bonus] > pure_[bonus]
    #: This does not concern the base: it is what is being processed.
    assert poor[mix.inputs[0]] == pure_[mix.inputs[0]]


def test_assembly_has_no_proportions(constants: Constants, catalog: Catalog) -> None:
    """A workbench is a log and a rope, nothing in between (D-092)."""
    assembly = craft.procedure(catalog, NAILS)
    for_poor = craft.optimal_amounts(constants, assembly, 1, 10)
    for_pure = craft.optimal_amounts(constants, assembly, 1, 90)
    assert for_poor == for_pure


def test_miss_costs_more_and_spreads_more(constants: Constants, catalog: Catalog) -> None:
    """Bad smelting eats more and gives an unpredictable ingot."""
    exact = craft.waste_share(constants, 1.0)
    miss = craft.waste_share(constants, 0.0)
    assert exact == constants[R.CRAFT_WASTE_SHARE]
    assert miss == constants[R.CRAFT_WASTE_BAD_RATIO]
    assert craft.spread_of(constants, 1.0) < craft.spread_of(constants, 0.0)


def test_ceiling_set_by_weakest_link(constants: Constants, catalog: Catalog) -> None:
    """An excellent tool on a broken anvil gives a mediocre result."""
    assembly = craft.procedure(catalog, NAILS)
    scale = constants[R.QUALITY_SCALE]
    excellent = craft.forecast_quality(
        constants, assembly, ceiling=scale.max, material=scale.max, accuracy=1.0
    )
    mediocre = craft.forecast_quality(
        constants, assembly, ceiling=scale.mid, material=scale.max, accuracy=1.0
    )
    assert excellent == pytest.approx(scale.max)
    assert mediocre == pytest.approx(scale.mid)


def test_craft_premium_only_for_mix(constants: Constants, catalog: Catalog) -> None:
    """The master surpasses the machine by adaptivity, not by a big number (D-058)."""
    scale = constants[R.QUALITY_SCALE]
    mix = _mix(catalog)
    assembly = craft.procedure(catalog, NAILS)

    of_mix = craft.forecast_quality(
        constants, mix, ceiling=scale.mid, material=scale.max, accuracy=1.0
    )
    of_assembly = craft.forecast_quality(
        constants, assembly, ceiling=scale.mid, material=scale.max, accuracy=1.0
    )
    assert of_mix > scale.mid, "точная пропорция поднимает выше потолка станции"
    assert of_assembly == pytest.approx(scale.mid), "у сборки прибавке взяться неоткуда"
    assert of_mix - scale.mid <= constants[R.QUALITY_HAND_CRAFT_BONUS]


# --- batch -------------------------------------------------------------------


async def test_forecast_shown_as_number_before_batch(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Without an exact number the player will not connect action with result (D-092)."""
    _, identity, body = await _workshop(session)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 10, quality=80)

    forecast = await craft.plan(session, constants, catalog, body, NAILS, 3)
    assert isinstance(forecast.quality, float)
    assert forecast.minutes > 0
    assert forecast.consumes[INGOT] > 0

    #: The forecast spends nothing.
    assert await _in_inventory(session, body, INGOT) == pytest.approx(10)


async def test_batch_does_not_go_without_recipe(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """From birth the player cannot create anything."""
    _, _, body = await _workshop(session)
    await _give(session, body, INGOT, 10, quality=80)
    with pytest.raises(craft.NotLearned):
        await craft.plan(session, constants, catalog, body, NAILS, 1)


async def test_batch_does_not_go_without_machine_in_node(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The requirement "machine in place" is what makes craft city-forming."""
    _, identity, body = await _workshop(session, machine=None)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 10, quality=80)
    with pytest.raises(craft.NoStation):
        await craft.plan(session, constants, catalog, body, NAILS, 1)


async def test_short_of_inputs_batch_does_not_start(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Matter is not created under any circumstances (I1)."""
    _, identity, body = await _workshop(session)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 1, quality=80)
    with pytest.raises(craft.NotEnough):
        await craft.start(session, constants, catalog, body, NAILS, 10)


async def test_batch_over_ceiling_not_started(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, identity, body = await _workshop(session)
    await world.learn(session, identity, NAILS)
    over_measure = constants[R.CRAFT_BATCH_MAX] + 1
    with pytest.raises(craft.TooBig):
        await craft.plan(session, constants, catalog, body, NAILS, over_measure)


async def test_input_written_off_at_once_with_loss(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Waste is sink S9: there is less matter in the world than there was (D-129)."""
    _, identity, body = await _workshop(session)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 10, quality=60)

    norm = catalog.recipes.recipe(NAILS).amounts[INGOT] * 4
    batch = await craft.start(session, constants, catalog, body, NAILS, 4)
    await session.commit()

    left = await _in_inventory(session, body, INGOT)
    written_off = 10 - left
    assert written_off > norm, "потери берутся сверх нормы, а не из выхода"
    assert batch.state is BatchState.RUNNING
    assert batch.ready_at > batch.started_at


async def test_batch_arrives_by_job_exactly_once(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The batch's effect must be in the database, and the job idempotent."""
    async with factory() as session, session.begin():
        _, identity, body = await _workshop(session)
        await world.learn(session, identity, NAILS)
        await _give(session, body, INGOT, 10, quality=70)
        batch = await craft.start(session, constants, catalog, body, NAILS, 2)
        ready, batch_id, body_id = batch.ready_at, batch.id, body.id

    #: The batch's time has not come yet -- the job is not taken.
    assert await jobs.run_one(factory, now=ready - timedelta(minutes=1)) is None

    job = await jobs.run_one(factory, now=ready)
    assert job is not None and job.kind == "craft.batch"

    async with factory() as session:
        reloaded = await session.get(Body, body_id)
        assert reloaded is not None
        pocket = await world.body_container(session, reloaded)
        done = (
            (
                await session.execute(
                    select(Item).where(Item.container_id == pocket.id, Item.type_key == NAILS)
                )
            )
            .scalars()
            .all()
        )
        assert len(done) == 1, "гвозди складываются: одна стопка"
        assert amount_float(done[0].amount) == pytest.approx(2)
        assert done[0].maker_identity_id is not None, "клеймо обязательно (D-058)"

        batch = await session.get(CraftBatch, batch_id)
        assert batch is not None and batch.state is BatchState.DONE

        #: A repeat of the same job gives no second batch.
        await craft.finish(session, job)
        everything = (
            await session.execute(
                select(func.count())
                .select_from(Item)
                .where(Item.container_id == pocket.id, Item.type_key == NAILS)
            )
        ).scalar_one()
        assert everything == 1


async def test_recipe_machine_found_via_synonyms(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """In recipes the machine is called "Furnace", while in the node stands a "Smelting furnace".

    Without synonym resolution all chemistry and refining were unmakeable: the
    engine looked for a machine with a name that never exists in the world.
    """
    GLAZE = "glass"
    _, identity, body = await _workshop(session, machine=FURNACE, machine_quality=70)
    await world.learn(session, identity, GLAZE)
    for raw in ("quartz_sand", "coal"):
        await _give(session, body, raw, 100, quality=60)

    plan = await craft.plan(session, constants, catalog, body, GLAZE, 1)
    assert plan.ceiling == pytest.approx(70), "потолок задала стоящая в узле печь"


async def test_smelting_batch_reaches_end(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """An operation's output has no recipe -- and the batch must survive that.

    Smelting runs without a recipe (20-systems/03), so the catalog knows
    nothing about the ingot. A job that asked it about edibility dropped the
    whole batch: the input written off, no product.
    """
    async with factory() as session, session.begin():
        _, _, body = await _workshop(session, machine=FURNACE)
        await _give(session, body, "iron_ore", 20, quality=60)
        await _give(session, body, "coal", 20, quality=60)
        batch = await craft.start(session, constants, catalog, body, INGOT, 1)
        ready, body_id = batch.ready_at, body.id

    job = await jobs.run_one(factory, now=ready)
    assert job is not None and job.kind == "craft.batch"

    async with factory() as session:
        reloaded = await session.get(Body, body_id)
        pocket = await world.body_container(session, reloaded)
        ingots = (
            (
                await session.execute(
                    select(Item).where(Item.container_id == pocket.id, Item.type_key == INGOT)
                )
            )
            .scalars()
            .all()
        )
        assert ingots, "слиток вышел из печи, а не сгинул вместе с заданием"


async def test_left_machine_output_stays_at_machine(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Matter does not teleport after the master and does not vanish with them."""
    async with factory() as session, session.begin():
        node, identity, body = await _workshop(session)
        await world.learn(session, identity, NAILS)
        await _give(session, body, INGOT, 10, quality=70)
        batch = await craft.start(session, constants, catalog, body, NAILS, 2)
        ready, node_id = batch.ready_at, node.id

        far_away = await world.create_node(
            session, f"terra.away.{uuid.uuid4().hex[:8]}", "Далеко", area_m2=100
        )
        body.node_id = far_away.id

    await jobs.run_one(factory, now=ready)

    async with factory() as session:
        yard = await world.node_container(session, await _node(session, node_id))
        done = (
            (
                await session.execute(
                    select(Item).where(Item.container_id == yard.id, Item.type_key == NAILS)
                )
            )
            .scalars()
            .all()
        )
        assert len(done) == 1


async def test_wares_do_not_stack_each_with_own_quality(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Raw material stacks, products do not (04-items)."""
    async with factory() as session, session.begin():
        _, identity, body = await _workshop(session, machine=FORGE, machine_quality=80)
        await world.learn(session, identity, "iron_pickaxe")
        await _give(session, body, INGOT, 20, quality=70)
        await _give(session, body, "handle", 20, quality=70)
        batch = await craft.start(session, constants, catalog, body, "iron_pickaxe", 3)
        ready, body_id = batch.ready_at, body.id

    await jobs.run_one(factory, now=ready)

    async with factory() as session:
        reloaded = await session.get(Body, body_id)
        assert reloaded is not None
        pocket = await world.body_container(session, reloaded)
        pickaxes = (
            (
                await session.execute(
                    select(Item).where(
                        Item.container_id == pocket.id, Item.type_key == "iron_pickaxe"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(pickaxes) == 3, "каждая кирка — отдельная вещь со своим клеймом"
        assert all(amount_float(pickaxe.amount) == 1 for pickaxe in pickaxes)


async def test_machine_wears_per_batch(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Maintenance is mandatory: the machine is finite, like everything else (D-129)."""
    async with factory() as session, session.begin():
        node, identity, body = await _workshop(session)
        await world.learn(session, identity, NAILS)
        await _give(session, body, INGOT, 10, quality=70)
        batch = await craft.start(session, constants, catalog, body, NAILS, 2)
        ready, machine_id = batch.ready_at, batch.station_item_id

    await jobs.run_one(factory, now=ready)

    async with factory() as session:
        machine = await session.get(Item, machine_id)
        assert machine is not None
        #: Wear is divided by service life: a better machine also lasts longer.
        expected_ = constants[R.WEAR_STATION_PER_BATCH] / wear.life_factor(constants, 60)
        assert float(machine.condition) == pytest.approx(100 - expected_, abs=0.01)


async def test_result_stays_within_promised_spread(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Craft is not a slot machine: the promised number is the middle of the result."""
    async with factory() as session, session.begin():
        _, identity, body = await _workshop(session)
        await world.learn(session, identity, NAILS)
        await _give(session, body, INGOT, 10, quality=70)
        batch = await craft.start(session, constants, catalog, body, NAILS, 2)
        promised, spread_ = float(batch.quality), float(batch.spread)
        ready, body_id = batch.ready_at, body.id

    await jobs.run_one(factory, now=ready)

    async with factory() as session:
        reloaded = await session.get(Body, body_id)
        assert reloaded is not None
        pocket = await world.body_container(session, reloaded)
        nails = (
            (
                await session.execute(
                    select(Item).where(Item.container_id == pocket.id, Item.type_key == NAILS)
                )
            )
            .scalars()
            .first()
        )
        assert nails is not None and nails.quality is not None
        assert abs(float(nails.quality) - promised) <= spread_ + 0.01


async def test_library_does_not_work_remotely(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The Library's only restriction is geographic (D-053)."""
    _, identity, body = await _workshop(session, library=False)
    with pytest.raises(craft.NoLibrary):
        await craft.copy_recipe(session, catalog, body, NAILS)

    _, identity, body = await _workshop(session, library=True)
    knowledge = await craft.copy_recipe(session, catalog, body, NAILS)
    await session.commit()
    assert knowledge is not None
    assert knowledge.kind is KnowledgeKind.RECIPE and knowledge.key == NAILS


async def test_copying_recipe_costs_stamina(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Knowledge is free in money, but copying it is work (D-148).

    Paying with the body keeps both the freeness of knowledge and its price:
    in a day one can carry off a dozen recipes, not the whole list.
    """
    from decimal import Decimal

    _, identity, body = await _workshop(session, library=True)
    before = float(body.stamina)
    await craft.copy_recipe(session, catalog, body, NAILS)
    assert float(body.stamina) == pytest.approx(before - constants[R.CRAFT_COPY_STAMINA])

    #: What is already known is not rewritten: the same body does not pay twice.
    now_ = float(body.stamina)
    assert await craft.copy_recipe(session, catalog, body, NAILS) is None
    assert float(body.stamina) == pytest.approx(now_)

    body.stamina = Decimal("1")
    await session.flush()
    with pytest.raises(craft.NoStrength):
        await craft.copy_recipe(session, catalog, body, "rope")


async def _node(session: AsyncSession, node_id: uuid.UUID):
    from src.models.world import Node

    node = await session.get(Node, node_id)
    assert node is not None
    return node
