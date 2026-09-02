# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Invention, knowledge carriers, library shelves and the batch queue (D-209).

What is checked is the decision, not the plumbing:

* an attempt with the right composition opens the recipe and starts the
  first batch; a wrong one burns what was laid out and says nothing else;
* what is laid out must be in the hands **before** the answer, or a guess
  costs nothing when wrong and learns for free when right;
* a carrier is written by whoever knows the recipe, read without being spent,
  and wiped back into a blank;
* a library holds what was put into it: what is not on the shelf cannot be
  copied here, and a contributed carrier stays for good with the giver's name;
* one body works one batch; the second waits its turn; leaving freezes the
  running one with its time and frees the machine; coming back resumes it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.commands.views import _shelf
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, jobs, library, market, travel, world
from src.models.craft import BatchState, CraftBatch
from src.models.identity import Knowledge, KnowledgeKind
from src.models.inventory import Item
from src.models.job import Job
from src.models.world import Surface
from src.units import amount_float

BENCH = "workbench"
FORGE = "forge"
WOOD = "wood"
BEAM = "shaft_support"
BARREL = "handle"
ROPE = "rope"
HANDLE = "handle"
INGOT = "iron_ingot"
NAILS = "nails"
CARRIER = "recorded_recipe"
BLANK = "recipe_blank"


async def _yard(session: AsyncSession, *, machine: str | None = BENCH, name: str = "Мастер"):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.yard.{stamp}", "Двор", area_m2=100)
    identity = await world.create_identity(session, f"{name}-{stamp}")
    body = await world.print_body(session, identity, node)
    if machine is not None:
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, machine, quality=60, origin="тест")
    return node, identity, body


async def _give(session: AsyncSession, body, type_key: str, quantity: float, quality=60):
    pocket = await world.body_container(session, body)
    return await world.grant_item(
        session, pocket, type_key, amount=quantity, quality=quality, origin="тест"
    )


async def _held(session: AsyncSession, body, type_key: str) -> float:
    pocket = await world.body_container(session, body)
    rows = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == type_key)
            )
        )
        .scalars()
        .all()
    )
    return sum(amount_float(item.amount) for item in rows)


def _norm(catalog: Catalog, name: str) -> dict[str, float]:
    return dict(catalog.recipes.recipe(name).amounts)


# --- invention ---------------------------------------------------------------


async def test_right_composition_opens_recipe_and_starts_batch(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The exact composition of the timber at the workbench: the recipe is
    opened with the discoverer's mark, and the laid-out wood becomes the first batch."""
    _, identity, body = await _yard(session)
    await _give(session, body, WOOD, 50)
    await _give(session, body, ROPE, 10)

    result = await craft.invent(
        session, constants, catalog, body, _norm(catalog, BEAM), 2, station=BENCH
    )
    assert result.success and result.learned == (BEAM,)
    assert result.batch is not None and result.batch.output == BEAM
    assert result.batch.state is BatchState.RUNNING

    known = (
        await session.execute(
            select(Knowledge).where(Knowledge.identity_id == identity.id, Knowledge.key == BEAM)
        )
    ).scalar_one()
    assert known.kind is KnowledgeKind.RECIPE and known.discovered is True


async def test_a_recipe_opens_even_when_the_prototype_is_short(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Laid out exactly the norm: waste is taken on top of it (D-133), so the
    hands are short by the waste and the prototype does not start.

    The recipe still opens -- the guess was right -- and the answer says why
    there is no batch. It says it by key: the note is assembled at the edge in
    the reader's language, like a refusal (D-251 wave III). Until then the
    refusal was turned into a string here, and once refusals became keys that
    string was empty.
    """
    _, _, body = await _yard(session)
    norm = _norm(catalog, BEAM)
    #: Twenty units, not one: the waste on a counted input is dust until it
    #: gathers into a piece (`goods.spent`), and on one beam it never does.
    for name, per_unit in norm.items():
        await _give(session, body, catalog.recipes.resolve(name), per_unit * 20)

    result = await craft.invent(session, constants, catalog, body, norm, 20, station=BENCH)
    assert result.success and result.learned == (BEAM,)
    assert result.batch is None
    assert result.note_key == "craft-not-enough"
    assert result.note_args["goods"] in {catalog.recipes.resolve(name) for name in norm}


async def test_wrong_composition_burns_what_was_laid_out(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No recipe of the workbench takes wood and iron: everything laid out burns
    (`invent.material_loss`), nothing is learned, and the answer gives no hint."""
    _, identity, body = await _yard(session)
    await _give(session, body, WOOD, 20)
    await _give(session, body, INGOT, 5)

    result = await craft.invent(
        session, constants, catalog, body, {WOOD: 4, INGOT: 1}, 2, station=BENCH
    )
    assert not result.success and result.batch is None
    #: The note is a key now, not a sentence (D-251 wave III): the words are
    #: assembled at the edge, in the language of whoever asked.
    assert result.note_key == "craft-invent-failed"
    #: A random share within `invent.material_loss` burns -- of each kind its
    #: own. Both kinds here are counted (D-212), so the share is rounded up to
    #: whole pieces: at least one of each burns, never more than was laid out.
    for name, laid in ((WOOD, 8.0), (INGOT, 2.0)):
        burned = result.burned[name]
        assert burned == int(burned), "штучное горит штуками"
        assert 1 <= burned <= laid
    assert await _held(session, body, WOOD) == pytest.approx(20 - result.burned[WOOD])
    assert await _held(session, body, INGOT) == pytest.approx(5 - result.burned[INGOT])
    assert (
        not (await session.execute(select(Knowledge).where(Knowledge.identity_id == identity.id)))
        .scalars()
        .all()
    )


async def test_amounts_tell_recipes_apart(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Timber and handle are both wood at the workbench: the amount per unit is
    what names the recipe (D-209), so the vault keeps them apart."""
    pail, barrel = _norm(catalog, BEAM), _norm(catalog, BARREL)
    assert set(pail) == set(barrel) == {WOOD}
    assert pail != barrel

    _, _, body = await _yard(session)
    await _give(session, body, WOOD, 50)
    await _give(session, body, ROPE, 10)
    result = await craft.invent(session, constants, catalog, body, barrel, 1, station=BENCH)
    assert result.learned == (BARREL,)


async def test_laid_out_must_be_in_hands_before_anything(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A guess with materials one does not own is refused up front -- whether the
    composition is right or wrong: otherwise it would be a free oracle."""
    _, identity, body = await _yard(session)
    with pytest.raises(craft.NotEnough):
        await craft.invent(
            session, constants, catalog, body, _norm(catalog, BEAM), 1, station=BENCH
        )
    with pytest.raises(craft.NotEnough):
        await craft.invent(session, constants, catalog, body, {WOOD: 4, INGOT: 1}, 1, station=BENCH)
    assert (
        not (await session.execute(select(Knowledge).where(Knowledge.identity_id == identity.id)))
        .scalars()
        .all()
    )


async def test_too_many_kinds_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The search space stays surveyable: no more than `invent.max_ingredients` kinds."""
    _, _, body = await _yard(session)
    cap = int(constants[R.INVENT_MAX_INGREDIENTS])
    names = [WOOD, INGOT, "stone", "clay", "rope", "cloth", "resin", "coal"][: cap + 1]
    for name in names:
        await _give(session, body, name, 5)
    #: By key, not by wording: the sentence lives in the locale (D-251 wave III).
    with pytest.raises(craft.CraftError) as refused:
        await craft.invent(
            session, constants, catalog, body, dict.fromkeys(names, 1.0), 1, station=BENCH
        )
    assert refused.value.key == "craft-too-many-ingredients"
    assert refused.value.params["max"] == constants[R.INVENT_MAX_INGREDIENTS]


async def test_known_recipe_is_not_invented_again(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, identity, body = await _yard(session)
    await world.learn(session, identity, BEAM)
    await _give(session, body, WOOD, 20)
    await _give(session, body, ROPE, 5)
    with pytest.raises(craft.CraftError) as refused:
        await craft.invent(
            session, constants, catalog, body, _norm(catalog, BEAM), 1, station=BENCH
        )
    assert refused.value.key == "craft-already-known"
    assert refused.value.params["recipe"] == BEAM


async def test_operation_is_not_invented(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ore and coal at the furnace is smelting -- on the list for everyone. The
    attempt is refused rather than burned: a trap is not a rule."""
    _, _, body = await _yard(session, machine="smelting_furnace")
    await _give(session, body, "iron_ore", 10)
    await _give(session, body, "coal", 10)
    with pytest.raises(craft.Unmakeable) as refused:
        await craft.invent(
            session,
            constants,
            catalog,
            body,
            {"iron_ore": 4, "coal": 1},
            1,
            station="smelting_furnace",
        )
    assert refused.value.key == "craft-known-operation"
    assert refused.value.params["operation"] == "iron_smelting"
    assert await _held(session, body, "iron_ore") == 10


async def test_invention_needs_the_machine_here(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _yard(session, machine=None)
    await _give(session, body, WOOD, 20)
    await _give(session, body, ROPE, 5)
    with pytest.raises(craft.NoStation):
        await craft.invent(
            session, constants, catalog, body, _norm(catalog, BEAM), 1, station=BENCH
        )


# --- carriers ----------------------------------------------------------------


async def _written_carrier(session: AsyncSession, catalog: Catalog, body, recipe: str) -> Item:
    """A carrier with a recipe on it, granted straight into the hands."""
    pocket = await world.body_container(session, body)
    item = await world.grant_item(session, pocket, CARRIER, quality=70, origin="тест")
    item.recipe_key = recipe
    await session.flush()
    return item


async def test_carrier_is_written_only_by_who_knows(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Writing needs the recipe of the carrier itself and the recipe going onto
    it; the batch and then the item carry the recipe's name."""
    _, identity, body = await _yard(session, machine=None)
    await world.learn(session, identity, CARRIER)
    await _give(session, body, BLANK, 3)

    with pytest.raises(craft.NotLearned):
        await craft.start(session, constants, catalog, body, CARRIER, 1, recipe_key=NAILS)
    with pytest.raises(craft.CraftError) as refused:
        await craft.start(session, constants, catalog, body, CARRIER, 1)
    assert refused.value.key == "craft-write-needs-recipe"

    await world.learn(session, identity, NAILS)
    batch = await craft.start(session, constants, catalog, body, CARRIER, 1, recipe_key=NAILS)
    assert batch.recipe_key == NAILS
    assert batch.state is BatchState.RUNNING


async def test_written_carrier_arrives_with_recipe(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    async with factory() as session, session.begin():
        _, identity, body = await _yard(session, machine=None)
        await world.learn(session, identity, CARRIER)
        await world.learn(session, identity, NAILS)
        #: One recipe takes exactly one blank: writing has no waste (D-209).
        await _give(session, body, BLANK, 1, quality=80)
        batch = await craft.start(session, constants, catalog, body, CARRIER, 1, recipe_key=NAILS)
        ready, body_id = batch.ready_at, body.id
        #: The carrier is the blank, one write poorer -- no spread, no ceiling.
        assert float(batch.quality) == pytest.approx(80 - constants[R.CARRIER_WRITE_WEAR])
        assert float(batch.spread) == 0

    assert await jobs.run_one(factory, now=ready) is not None

    async with factory() as session:
        body = await session.get(type(body), body_id)
        pocket = await world.body_container(session, body)
        made = (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == CARRIER)
            )
        ).scalar_one()
        assert made.recipe_key == NAILS
        assert float(made.quality) == pytest.approx(80 - constants[R.CARRIER_WRITE_WEAR])
        assert market.goods_key(made) == f"{CARRIER}: {NAILS}"
        #: The blank is gone whole -- no 1.05 of it.
        assert await _held(session, body, BLANK) == 0


async def test_writing_time_follows_the_blank_quality(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Good memory writes in a blink, worn memory takes minutes: a straight line
    from `carrier.write_seconds.max` at quality 0 to `.min` at 100."""
    span = constants[R.CARRIER_WRITE_SECONDS]
    assert craft.write_seconds(constants, 100) == pytest.approx(span.min)
    assert craft.write_seconds(constants, 0) == pytest.approx(span.max)
    assert craft.write_seconds(constants, 50) == pytest.approx((span.min + span.max) / 2)

    _, identity, body = await _yard(session, machine=None)
    await world.learn(session, identity, CARRIER)
    await world.learn(session, identity, NAILS)
    await _give(session, body, BLANK, 1, quality=1)
    plan = await craft.plan(session, constants, catalog, body, CARRIER, 1, recipe_key=NAILS)
    assert plan.minutes * 60 == pytest.approx(craft.write_seconds(constants, 1))
    assert plan.waste == 0 and plan.consumes == {BLANK: 1}


async def test_dead_blank_is_not_written_on(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Worn to zero, the memory is dead: it is skipped, and with nothing else in
    the hands the write is refused by name."""
    _, identity, body = await _yard(session, machine=None)
    await world.learn(session, identity, CARRIER)
    await world.learn(session, identity, NAILS)
    await _give(session, body, BLANK, 1, quality=0)
    with pytest.raises(craft.Unmakeable) as refused:
        await craft.plan(session, constants, catalog, body, CARRIER, 1, recipe_key=NAILS)
    assert refused.value.key == "craft-blank-dead"
    #: Nothing live in the hands at all, so the refusal says only that.
    assert refused.value.params["live"] == "false"

    #: A live one beside it is taken; the dead one stays.
    await _give(session, body, BLANK, 1, quality=40)
    plan = await craft.plan(session, constants, catalog, body, CARRIER, 1, recipe_key=NAILS)
    assert plan.quality == pytest.approx(40 - constants[R.CARRIER_WRITE_WEAR])


async def test_reading_learns_and_keeps_the_carrier(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One carrier teaches many: reading costs stamina, not the carrier."""
    _, identity, body = await _yard(session, machine=None)
    item = await _written_carrier(session, catalog, body, NAILS)
    before = float(body.stamina)

    learned = await craft.read_carrier(session, catalog, body, item)
    assert learned is not None and learned.key == NAILS
    assert float(body.stamina) == pytest.approx(before - constants[R.CRAFT_COPY_STAMINA])
    assert await session.get(Item, item.id) is not None

    #: Known already -- nothing paid, nothing rewritten.
    again = float(body.stamina)
    assert await craft.read_carrier(session, catalog, body, item) is None
    assert float(body.stamina) == pytest.approx(again)


async def test_wiping_returns_a_blank(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _yard(session, machine=None)
    item = await _written_carrier(session, catalog, body, NAILS)
    blank = await craft.wipe_carrier(session, catalog, body, item)
    assert blank.id == item.id
    assert blank.type_key == BLANK and blank.recipe_key is None
    #: Erasing wears the memory (D-209).
    assert float(blank.quality) == pytest.approx(70 - constants[R.CARRIER_WIPE_WEAR])


async def test_carriers_are_different_goods_per_recipe(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """On the counter "Рецепт: Гвозди" and "Рецепт: Шахтная крепь" are two positions:
    loading one does not move the other."""
    node, identity, body = await _yard(session, machine="market_terminal")
    nails = await _written_carrier(session, catalog, body, NAILS)
    await _written_carrier(session, catalog, body, BEAM)

    moved = await market.load(session, constants, body, market.goods_key(nails), 1)
    assert moved == 1
    assert await _held(session, body, CARRIER) == 1
    stall = await market.stall(session, node, identity.id)
    on_counter = (
        await session.execute(select(Item).where(Item.container_id == stall.id))
    ).scalar_one()
    assert on_counter.recipe_key == NAILS


# --- library shelves ---------------------------------------------------------


async def _library(session: AsyncSession):
    node, identity, body = await _yard(session, machine=world.LIBRARY, name="Читатель")
    return node, identity, body


async def test_only_what_is_on_the_shelf_can_be_copied(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A library holds what was put into it (D-068): an empty one teaches nothing."""
    node, _, body = await _library(session)
    with pytest.raises(craft.NoLibrary) as refused:
        await craft.copy_recipe(session, catalog, body, NAILS)
    assert refused.value.key == "craft-library-lacks"
    assert refused.value.params["recipe"] == NAILS

    await library.stock(session, node, [NAILS])
    learned = await craft.copy_recipe(session, catalog, body, NAILS)
    assert learned is not None and learned.key == NAILS


async def test_contributed_carrier_stays_with_the_name(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Given cannot be ungiven: the carrier is the library's now, the giver's
    name is on the entry, and anyone standing here may copy the recipe."""
    node, identity, body = await _library(session)
    item = await _written_carrier(session, catalog, body, NAILS)

    entry = await library.contribute(session, catalog, body, item)
    assert entry.recipe == NAILS and entry.contributor_identity_id == identity.id
    assert await session.get(Item, item.id) is None
    assert await library.has(session, node, NAILS)
    names = await library.contributors(session, [entry])
    assert names[identity.id] == identity.name

    #: Somebody else, same room, may take it now.
    other = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    guest = await world.print_body(session, other, node)
    assert (await craft.copy_recipe(session, catalog, guest, NAILS)) is not None

    #: A second copy of the same recipe is not swallowed.
    again = await _written_carrier(session, catalog, body, NAILS)
    with pytest.raises(library.AlreadyThere):
        await library.contribute(session, catalog, body, again)
    assert await session.get(Item, again.id) is not None


async def test_shelf_names_the_pioneer_beside_the_contributor(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The shelf says both names (D-259): who brought the carrier and who
    first opened the recipe. A founding entry carries no pioneer key at
    all (D-225) -- nobody ever opened it."""
    node, _, _ = await _library(session)
    await library.stock(session, node, [NAILS, ROPE])
    discoverer = await world.create_identity(session, f"Пионер-{uuid.uuid4().hex[:6]}")
    await world.learn(session, discoverer, NAILS, discovered=True)

    shelf = {entry["recipe"]: entry for entry in await _shelf(session, node)}
    assert shelf[NAILS]["pioneer"] == discoverer.name
    assert "pioneer" not in shelf[ROPE]


async def test_only_a_written_carrier_goes_to_the_shelf(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _library(session)
    blank = await _give(session, body, BLANK, 1)
    with pytest.raises(library.NotACarrier):
        await library.contribute(session, catalog, body, blank)


async def test_no_library_no_contribution(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _yard(session, machine=None)
    item = await _written_carrier(session, catalog, body, NAILS)
    with pytest.raises(library.NotHere):
        await library.contribute(session, catalog, body, item)


# --- the queue: one body, one work, at the machine ---------------------------


async def _forge_master(session: AsyncSession):
    node, identity, body = await _yard(session, machine=FORGE)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 50)
    return node, identity, body


async def test_second_work_waits_its_turn(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One body works one batch: the second is paid for now but moves later."""
    _, _, body = await _forge_master(session)
    held = await _held(session, body, INGOT)
    first = await craft.start(session, constants, catalog, body, NAILS, 2)
    second = await craft.start(session, constants, catalog, body, NAILS, 3)

    assert first.state is BatchState.RUNNING and first.ready_at is not None
    assert second.state is BatchState.WAITING and second.ready_at is None
    assert second.remaining_seconds is not None and float(second.remaining_seconds) > 0
    assert second.station_item_id is None
    #: Both are written off: the queue is not a way to reserve for nothing.
    assert await _held(session, body, INGOT) < held - 4


async def test_queue_advances_when_work_ends(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    async with factory() as session, session.begin():
        _, _, body = await _forge_master(session)
        first = await craft.start(session, constants, catalog, body, NAILS, 1)
        second = await craft.start(session, constants, catalog, body, NAILS, 1)
        ready, first_id, second_id = first.ready_at, first.id, second.id

    job = await jobs.run_one(factory, now=ready)
    assert job is not None

    async with factory() as session:
        done = await session.get(CraftBatch, first_id)
        nxt = await session.get(CraftBatch, second_id)
        assert done.state is BatchState.DONE
        assert nxt.state is BatchState.RUNNING
        assert nxt.run_started_at == ready
        assert nxt.ready_at is not None and nxt.ready_at > ready
        assert nxt.station_item_id is not None


async def _road(session: AsyncSession, node):
    there = await world.create_node(
        session, f"terra.there.{uuid.uuid4().hex[:8]}", "Там", area_m2=100
    )
    await travel.connect(session, node, there, base_seconds=30, surface=Surface.ROAD)
    return there


async def test_leaving_freezes_and_frees_the_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The master walks away: the batch keeps its time left, the forge is free
    for the next person, and the frozen run's job will do nothing."""
    node, _, body = await _forge_master(session)
    there = await _road(session, node)
    moment = datetime.now(UTC)
    batch = await craft.start(session, constants, catalog, body, NAILS, 5, now=moment)
    machine = await session.get(Item, batch.station_item_id)
    planned = (batch.ready_at - moment).total_seconds()

    later = moment + timedelta(seconds=planned / 2)
    await travel.depart(session, constants, body, there, now=later)

    await session.refresh(batch)
    assert batch.state is BatchState.WAITING
    assert batch.ready_at is None and batch.station_item_id is None
    assert float(batch.remaining_seconds) == pytest.approx(planned / 2, abs=1)
    await session.refresh(machine)
    assert machine.busy_body_id is None

    #: The job of the frozen run finds nothing to finish.
    job = (
        await session.execute(select(Job).where(Job.dedup_key == f"craft.batch:{batch.id}"))
    ).scalar_one()
    job.run_at = moment + timedelta(seconds=planned)
    await craft.finish(session, job)
    await session.refresh(batch)
    assert batch.state is BatchState.WAITING


async def test_coming_back_resumes_where_it_stopped(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    async with factory() as session, session.begin():
        node, _, body = await _forge_master(session)
        there = await _road(session, node)
        moment = datetime.now(UTC)
        batch = await craft.start(session, constants, catalog, body, NAILS, 5, now=moment)
        planned = (batch.ready_at - moment).total_seconds()
        away = moment + timedelta(seconds=planned / 2)
        going = await travel.depart(session, constants, body, there, now=away)
        batch_id, body_id, arrives = batch.id, body.id, going.arrives_at

    #: Arrived there: nothing of theirs is here, the batch stays frozen.
    assert await jobs.run_one(factory, now=arrives) is not None
    async with factory() as session, session.begin():
        frozen = await session.get(CraftBatch, batch_id)
        assert frozen.state is BatchState.WAITING
        body = await session.get(type(body), body_id)
        back = await travel.depart(session, constants, body, node, now=arrives)
        home = back.arrives_at

    #: Back at the forge: the batch goes on with exactly the time it had left.
    assert await jobs.run_one(factory, now=home) is not None
    async with factory() as session:
        resumed = await session.get(CraftBatch, batch_id)
        assert resumed.state is BatchState.RUNNING
        assert resumed.runs == 2
        assert resumed.run_started_at == home
        assert (resumed.ready_at - home).total_seconds() == pytest.approx(planned / 2, abs=1)
        assert resumed.station_item_id is not None
        #: The resumed run has a job of its own.
        again = (
            await session.execute(select(Job).where(Job.dedup_key == f"craft.batch:{batch_id}:2"))
        ).scalar_one()
        assert again.run_at == resumed.ready_at


async def test_frozen_batch_waits_for_a_free_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Somebody took the only forge while the master was away: the batch waits
    with the reason, and goes on when the forge frees up."""
    node, _, body = await _forge_master(session)
    there = await _road(session, node)
    moment = datetime.now(UTC)
    batch = await craft.start(session, constants, catalog, body, NAILS, 5, now=moment)
    await travel.depart(session, constants, body, there, now=moment)

    other_id = await world.create_identity(session, f"Другой-{uuid.uuid4().hex[:6]}")
    other = await world.print_body(session, other_id, node)
    await world.learn(session, other_id, NAILS)
    await _give(session, other, INGOT, 10)
    theirs = await craft.start(session, constants, catalog, other, NAILS, 1, now=moment)
    assert theirs.state is BatchState.RUNNING

    #: Turned back: on the spot again, but the forge is taken -- still waiting.
    await travel.turn_back(session, body, now=moment)
    await session.refresh(batch)
    assert batch.state is BatchState.WAITING
    assert await craft.wake(session, body, now=moment) is None

    #: The other's work ends -- the machine frees, and the frozen batch takes it.
    job = (
        await session.execute(select(Job).where(Job.dedup_key == f"craft.batch:{theirs.id}"))
    ).scalar_one()
    job.run_at = theirs.ready_at
    await craft.finish(session, job)
    await session.refresh(batch)
    assert batch.state is BatchState.RUNNING


# --- choosing the quality that goes into the work (D-058) --------------------


async def test_chosen_tier_feeds_the_batch(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Without a word the worst ingots go into the nails; with the tier named,
    only that tier goes -- and too little of it is a refusal, not a fallback."""
    _, identity, body = await _forge_master(session)
    #: `_forge_master` gave 50 ingots at 60; add a poor stack and a fine one.
    await _give(session, body, INGOT, 5, quality=25)
    await _give(session, body, INGOT, 5, quality=85)
    poor = market.tier_of(constants, 25)
    fine = market.tier_of(constants, 85)

    silent = await craft.plan(session, constants, catalog, body, NAILS, 1)
    chosen = await craft.plan(session, constants, catalog, body, NAILS, 1, tiers={INGOT: fine})
    assert silent.quality < chosen.quality
    #: The forge is a cap (D-267); under it the material is the chosen 85, not the poor 25.
    assert chosen.quality == pytest.approx(min(chosen.ceiling, 85), abs=1)

    with pytest.raises(craft.NotEnough):
        await craft.plan(session, constants, catalog, body, NAILS, 20, tiers={INGOT: poor})


async def test_market_load_by_tier(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Loading names the tier: the good stack goes to the counter, the poor stays home."""
    node, identity, body = await _yard(session, machine="market_terminal")
    await _give(session, body, INGOT, 3, quality=25)
    await _give(session, body, INGOT, 3, quality=85)
    fine = market.tier_of(constants, 85)
    await market.load(session, constants, body, INGOT, 2, tier=fine)
    stall = await session.execute(
        select(Item).where(Item.container_id == (await market.stall(session, node, identity.id)).id)
    )
    counter = stall.scalars().all()
    assert len(counter) == 1 and float(counter[0].quality) == 85
    assert await _held(session, body, INGOT) == 4
