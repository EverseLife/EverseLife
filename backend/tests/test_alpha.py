# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The alpha's debug tools (D-229).

Checked is what keeps two levers from becoming a hole in the world:

* a printed thing arrives with a named ground and without a maker's mark: the
  journal can say afterwards what the world did not earn (pillar P1);
* hurrying moves the **term**, not the work: the ordinary journal handler
  finishes it, so a hurried result is the honest one;
* the term lives in two places -- the job and the passage, the job and the
  batch -- and both move together, or the client draws a countdown for a road
  already walked;
* the world's own terms are not this lever's business: only what this body is
  doing moves;
* and the race the whole thing is built around -- the worker claims jobs by
  the same `run_at` we write to.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import (
    alpha,
    craft,
    death,
    estate,
    explore,
    farm,
    jobs,
    ruins,
    ship,
    storage,
    travel,
    world,
)
from src.models.craft import BatchState, CraftBatch
from src.models.estate import Building
from src.models.event import Event, EventKind
from src.models.identity import Body, Identity
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.travel import Travel
from src.models.world import Layer, Surface
from src.units import amount_float

ORE = "Железная руда"
BENCH = "Верстак"
MAKE = "Рукоять"
WOOD = "Дерево"
#: A liquid and something to keep it in: liquids live only in vessels (D-230).
FUEL = "Ракетное топливо"
CANISTER = "Канистра"


async def _body(session: AsyncSession) -> Body:
    """A body standing on a planet: a survey looks for a place on one, so the
    node needs a parent even where the test only cares about the term."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    here = await world.create_node(
        session, f"terra.here.{stamp}", "Здесь", area_m2=100, layer=Layer.PLANET, parent=planet
    )
    identity = await world.create_identity(session, f"Тэрн-{stamp}")
    return await world.print_body(session, identity, here)


async def _walker(session: AsyncSession):
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.here.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.there.{stamp}", "Там", area_m2=100)
    await travel.connect(session, here, there, base_seconds=3600, surface=Surface.ROAD)
    identity = await world.create_identity(session, f"Ходок-{stamp}")
    body = await world.print_body(session, identity, here)
    return there, body


async def _master(session: AsyncSession):
    """A workshop with a bench, and a master who knows what to make on it."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.shop.{stamp}", "Двор", area_m2=200)
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, BENCH, quality=60, origin="сценарий теста")
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.learn(session, identity, MAKE)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, WOOD, amount=50, quality=60, origin="сценарий теста")
    return node, body


async def _carried(session: AsyncSession, body: Body) -> list[Item]:
    where = await world.body_container(session, body)
    return list(
        (await session.execute(select(Item).where(Item.container_id == where.id))).scalars()
    )


# --- printing a thing --------------------------------------------------------


async def test_printed_thing_lands_in_hand(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Into the hands and nowhere else: chests and machines have holders and
    doors, and a tool that walked past those would test another world."""
    body = await _body(session)
    await alpha.spawn(session, constants, catalog, body, type_key=ORE, amount=5)

    carried = await _carried(session, body)
    assert [(item.type_key, amount_float(item.amount)) for item in carried] == [(ORE, 5.0)]


async def test_printed_thing_names_its_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Matter out of nowhere is still matter with a ground in the journal (P1):
    `origin = 'alpha'` is what finds every thing the world did not earn."""
    body = await _body(session)
    item = await alpha.spawn(session, constants, catalog, body, type_key=ORE, amount=1)

    events = list((await session.execute(select(Event).order_by(Event.id))).scalars())
    #: `item.created` carries the ground and no actor: the thing was made by
    #: nobody, and saying otherwise would be a maker's mark by another name.
    arrival = [
        event
        for event in events
        if event.kind == EventKind.ITEM_CREATED.value
        and event.payload.get("item_id") == str(item.id)
    ]
    assert [event.payload.get("origin") for event in arrival] == [alpha.ORIGIN]
    assert arrival[0].actor_identity_id is None

    #: Who asked is on the second event, and it points at the same thing --
    #: that pair is the whole trail.
    asked = [event for event in events if event.kind == EventKind.ALPHA_SPAWNED.value]
    assert [event.actor_identity_id for event in asked] == [body.identity_id]
    assert asked[0].payload["item_id"] == str(item.id)


async def test_printed_thing_carries_no_maker_mark(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A maker's mark is a claim about work that was not done, and the market
    and reputation read it."""
    body = await _body(session)
    item = await alpha.spawn(session, constants, catalog, body, type_key=ORE, amount=1)
    assert item.maker_identity_id is None


async def test_unknown_name_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    body = await _body(session)
    with pytest.raises(alpha.NoSuchThing):
        await alpha.spawn(session, constants, catalog, body, type_key="Философский камень")


async def test_quality_is_checked_against_the_vault_scale(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The scale is content (`quality.scale`); a tool that knew better than the
    vault would be a second copy of it."""
    body = await _body(session)
    scale = constants[R.QUALITY_SCALE]
    with pytest.raises(alpha.AlphaError):
        await alpha.spawn(session, constants, catalog, body, type_key=ORE, quality=scale.max + 1)
    made = await alpha.spawn(session, constants, catalog, body, type_key=ORE, quality=scale.max)
    assert float(made.quality) == scale.max


async def test_nothing_is_printed_by_a_zero_amount(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    body = await _body(session)
    with pytest.raises(alpha.AlphaError):
        await alpha.spawn(session, constants, catalog, body, type_key=ORE, amount=0)


async def test_a_printed_liquid_is_poured_into_the_vessel_in_hand(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A liquid is poured, not handed over (D-230). Printed into the bare hands
    it would be matter this world has no place for: `pour` moves liquids
    between vessels, so a stack outside one could never reach a tank at all."""
    body = await _body(session)
    pocket = await world.body_container(session, body)
    canister = await world.grant_item(session, pocket, CANISTER, origin="тест")

    made = await alpha.spawn(session, constants, catalog, body, type_key=FUEL, amount=3)

    held = await storage.inside(session, canister)
    assert made.container_id == held.id, "жидкость осталась в ладонях"
    assert amount_float(made.amount) == 3.0
    assert not [item for item in await _carried(session, body) if item.type_key == FUEL]


async def test_a_printed_liquid_without_a_vessel_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Refused whole rather than spilled: spilling is the honest end of work
    already done, and here no work was done."""
    body = await _body(session)
    with pytest.raises(alpha.AlphaError):
        await alpha.spawn(session, constants, catalog, body, type_key=FUEL, amount=3)
    assert not [item for item in await _carried(session, body) if item.type_key == FUEL]


# --- hurrying a term ---------------------------------------------------------


async def test_survey_term_comes_up_to_now(session: AsyncSession, constants: Constants) -> None:
    """The survey's term lives in the job alone -- the simplest of the three."""
    body = await _body(session)
    job = await explore.survey(session, constants, body)
    assert job.run_at > datetime.now(UTC)

    moved = await alpha.hurry(session, body.identity_id, body)
    assert moved == (JobKind.EXPLORE_SURVEY.value,)
    assert job.run_at <= datetime.now(UTC), "срок разведки остался в будущем"


async def test_hurried_survey_is_finished_by_the_ordinary_handler(
    session: AsyncSession,
    constants: Constants,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The lever moves the term; the work is done by the handler the world runs
    anyway. There is no second path where a find could come out differently."""
    body = await _body(session)
    await explore.survey(session, constants, body)
    await alpha.hurry(session, body.identity_id, body)
    await session.commit()

    ran = await jobs.run_one(factory)
    assert ran is not None and ran.kind == JobKind.EXPLORE_SURVEY.value
    assert ran.state is JobState.DONE


async def test_passage_term_moves_with_its_job(session: AsyncSession, constants: Constants) -> None:
    """Two clocks on one road: the job fires by `run_at`, the client draws the
    bar by `arrives_at`. Moving one alone leaves a countdown for a walked road."""
    there, body = await _walker(session)
    passage = await travel.depart(session, constants, body, there)
    was = passage.arrives_at

    moved = await alpha.hurry(session, body.identity_id, body)
    assert moved == (JobKind.TRAVEL_LEG.value,)

    job = (
        await session.execute(
            select(Job).where(Job.body_id == body.id, Job.kind == JobKind.TRAVEL_LEG.value)
        )
    ).scalar_one()
    fresh = await session.get(Travel, passage.id)
    assert fresh.arrives_at < was
    assert fresh.arrives_at == job.run_at, "часы разошлись: задание и переход о разном"


async def test_world_own_terms_are_left_alone(session: AsyncSession, constants: Constants) -> None:
    """Only what this body is doing moves. The daily tick, meters and spoilage
    are the world's business -- pulling those forward would falsify a session,
    not speed it up."""
    body = await _body(session)
    await explore.survey(session, constants, body)
    meter = await jobs.enqueue(
        session,
        JobKind.UTILITY_METER,
        datetime.now(UTC) + timedelta(days=1),
        payload={},
        dedup_key=f"utility.meter:{uuid.uuid4()}",
        body_id=body.id,
    )
    was = meter.run_at

    moved = await alpha.hurry(session, body.identity_id, body)
    assert moved == (JobKind.EXPLORE_SURVEY.value,)
    assert meter.run_at == was, "рычаг дотянулся до счётчика мира"


async def _who(session: AsyncSession, identity_id) -> Identity:
    found = await session.get(Identity, identity_id)
    assert found is not None
    return found


async def _printing_ground(session: AsyncSession, catalog: Catalog):
    """The Forerunners' own printer, and a body standing at it to die there.

    The eternal one on purpose: it asks neither energy nor iron nor money
    (D-028), so the test is about the term and nothing else -- and twelve hours
    is the term in question.
    """
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.core.{stamp}", "Ядро", area_m2=200)
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    await ruins.grant_relic(session, node, death.PRINTER, origin="тест: наследие Предтеч")
    identity = await world.create_identity(session, f"Смертный-{stamp}")
    return node, await world.print_body(session, identity, node)


async def test_somebody_elses_term_is_not_touched(
    session: AsyncSession, constants: Constants
) -> None:
    """The lever reaches this body's work, not the neighbour's."""
    mine = await _body(session)
    theirs = await _body(session)
    job = await explore.survey(session, constants, theirs)
    was = job.run_at

    assert await alpha.hurry(session, mine.identity_id, mine) == ()
    assert job.run_at == was


async def test_body_print_comes_up_to_now_without_a_body(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Twelve hours at the Forerunners' printer is the longest term in the
    world, and the one who is waiting it out has no body to filter jobs by --
    the body is what is being made. So the print is reached by the identity in
    the payload, and the lever works from the cloud, where there is nothing to
    do but wait."""
    node, body = await _printing_ground(session, catalog)
    identity_id = body.identity_id
    await death.die(session, constants, body, cause="обвал")
    job = await death.order(session, constants, catalog, await _who(session, identity_id), node)
    was = job.run_at
    assert was > datetime.now(UTC)

    moved = await alpha.hurry(session, identity_id, None)
    assert moved == (JobKind.BODY_PRINT.value,)
    assert job.run_at <= datetime.now(UTC), "срок печати остался в будущем"
    assert job.run_at < was


async def test_somebody_elses_print_is_not_hurried(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The identity in the payload is the whole of the guarantee: without it the
    lever would finish the queue, not one's own term in it."""
    node, body = await _printing_ground(session, catalog)
    theirs = body.identity_id
    await death.die(session, constants, body, cause="обвал")
    job = await death.order(session, constants, catalog, await _who(session, theirs), node)
    was = job.run_at
    mine = await _body(session)

    assert await alpha.hurry(session, mine.identity_id, mine) == ()
    assert job.run_at == was


async def test_keel_term_comes_up_to_now(session: AsyncSession, constants: Constants) -> None:
    """The longest wait in the game is reachable, or a ship cannot be looked at
    in one session at all: `ship.foundation_hours` is eight hours per node."""
    stamp = uuid.uuid4().hex[:8]
    port = await world.create_node(session, f"terra.port.{stamp}", "Космодром", area_m2=400)
    session.add(Building(node_id=port.id, area_m2=400))
    await session.flush()
    await world.grant_item(
        session, await world.node_container(session, port), "Космическая верфь", origin="тест"
    )
    identity = await world.create_identity(session, f"Корабел-{stamp}")
    body = await world.print_body(session, identity, port)
    await world.grant_item(
        session, await world.body_container(session, body), "Основа узла корабля", origin="тест"
    )
    job = await ship.found(session, constants, body, "Странник")
    assert job.run_at > datetime.now(UTC)

    moved = await alpha.hurry(session, body.identity_id, body)
    assert moved == (JobKind.SHIP_KEEL.value,)
    assert job.run_at <= datetime.now(UTC), "срок закладки остался в будущем"


async def _stocked(session: AsyncSession, constants: Constants, body: Body) -> None:
    """The materials for twenty metres, into the pocket. Called again after a
    build, because building spends them and mending asks for more."""
    pocket = await world.body_container(session, body)
    for goods, per_metre in estate.composition(constants, estate.kinds(constants)[0]).items():
        await world.grant_item(
            session, pocket, goods, amount=float(per_metre) * 20 + 1, quality=60, origin="тест"
        )
    await session.flush()


async def _yard(session: AsyncSession, constants: Constants, name: str):
    """A plot of one's own, with the materials for twenty metres in the pocket."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity = await world.create_identity(session, f"{name}-{stamp}")
    body = await world.print_body(session, identity, plot)
    plot.owner_identity_id = identity.id
    await _stocked(session, constants, body)
    return body, plot


async def _raised(session: AsyncSession, constants: Constants, name: str):
    """A plot with the house already standing on it: what is demolished and repaired."""
    body, plot = await _yard(session, constants, name)
    raising = await estate.construct(session, constants, body, plot, 20.0)
    await estate.finish_build(session, raising)
    #: The handler is run by hand here, so the row is closed by hand too -- the
    #: worker would have done it, and a job left pending would be hurried along
    #: with the one the test is about.
    raising.state = JobState.DONE
    await _stocked(session, constants, body)
    return body, plot


async def _building(session: AsyncSession, constants: Constants, name: str) -> Job:
    body, plot = await _yard(session, constants, name)
    return await estate.construct(session, constants, body, plot, 20.0)


async def _demolishing(session: AsyncSession, constants: Constants, name: str) -> Job:
    body, plot = await _raised(session, constants, name)
    return await estate.demolish(session, constants, body, plot)


async def _repairing(session: AsyncSession, constants: Constants, name: str) -> Job:
    body, plot = await _raised(session, constants, name)
    #: Nothing to mend on a whole house: the walls are worn down first.
    for house in await estate.buildings_of(session, plot):
        house.condition = Decimal("50")
    await session.flush()
    return await estate.repair(session, constants, body, plot)


async def _ploughing(session: AsyncSession, constants: Constants, name: str) -> Job:
    """A furrow on nobody's fertile ground: the field is open there (D-198)."""
    stamp = uuid.uuid4().hex[:6]
    field = await world.create_node(
        session,
        f"terra.field.{stamp}",
        "Пойма",
        area_m2=400,
        layer=Layer.PLANET,
        properties={"вода": "река", "плодородие": 55},
    )
    identity = await world.create_identity(session, f"{name}-{stamp}")
    body = await world.print_body(session, identity, field)
    plot = await farm.mark(session, constants, body, name=f"Делянка-{stamp}", area=100)
    await farm.plow(session, constants, body, plot)
    return (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.FARM_PLOW.value,
                Job.body_id == body.id,
                Job.state == JobState.PENDING,
            )
        )
    ).scalar_one()


@pytest.mark.parametrize(
    ("kind", "start"),
    [
        (JobKind.BUILD_FINISH, _building),
        (JobKind.BUILD_DEMOLISH, _demolishing),
        (JobKind.BUILD_REPAIR, _repairing),
        (JobKind.FARM_PLOW, _ploughing),
    ],
)
async def test_journal_works_come_up_to_now_and_only_this_body_s(
    session: AsyncSession, constants: Constants, kind: JobKind, start
) -> None:
    """A house is a working day and a furrow is hours, and the lever must reach
    every one of those works -- but only the ones this body started. The safety
    of the whole thing rests on the jobs being queued with a `body_id`, and that
    lives in four different modules, so all four are pinned."""
    job = await start(session, constants, "Хозяин")
    theirs = await start(session, constants, "Сосед")
    was = theirs.run_at
    assert job.run_at > datetime.now(UTC)

    mine = await session.get(Body, job.body_id)
    moved = await alpha.hurry(session, mine.identity_id, mine)
    assert moved == (kind.value,)
    assert job.run_at <= datetime.now(UTC), "срок работ остался в будущем"
    assert theirs.run_at == was, "рычаг дотянулся до чужой стройки"


async def test_nothing_to_hurry_is_not_a_refusal(session: AsyncSession) -> None:
    """Standing still is an answer, not an error: the widget's button is
    pressed by a tester who has not started anything yet."""
    body = await _body(session)
    assert await alpha.hurry(session, body.identity_id, body) == ()


async def test_batch_term_and_bench_clock_move_together(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Two clocks move on a batch -- the job and `ready_at` -- and the bench's
    booking deliberately does not.

    `_pick_station` counts a machine free once its stamp has passed (D-150).
    Pulling `busy_until` up would hand the bench to another master for the
    second before the handler runs, and our handler's `_release` would then
    wipe their booking: two batches on one machine.
    """
    _, body = await _master(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 2)
    was = batch.ready_at
    bench = await session.get(Item, batch.station_item_id)
    assert bench is not None and bench.busy_until == was

    moved = await alpha.hurry(session, body.identity_id, body)
    assert moved == (JobKind.CRAFT_BATCH.value,)

    job = (
        await session.execute(
            select(Job).where(Job.body_id == body.id, Job.kind == JobKind.CRAFT_BATCH.value)
        )
    ).scalar_one()
    fresh = await session.get(CraftBatch, batch.id)
    assert fresh.ready_at < was
    assert fresh.ready_at == job.run_at, "часы разошлись: задание и партия о разном"
    assert bench.busy_until == was, "бронь станка снята досрочно: верстак можно перехватить"


async def test_hurried_batch_is_finished_by_the_ordinary_handler(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The lever moves the term; the products are made by the handler the world
    runs anyway, and the bench comes free with them."""
    _, body = await _master(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 2)
    await alpha.hurry(session, body.identity_id, body)
    await session.commit()

    ran = await jobs.run_one(factory)
    assert ran is not None and ran.kind == JobKind.CRAFT_BATCH.value
    assert ran.state is JobState.DONE

    done = await session.get(CraftBatch, batch.id, populate_existing=True)
    assert done.state is BatchState.DONE
    made = [item for item in await _carried(session, body) if item.type_key == MAKE]
    assert made, "партия доведена, а изделия нет"


async def test_a_frozen_batch_has_no_term_to_hurry(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A batch frozen while the master was away holds the work left, not a term
    (D-209), and each run has its own job. Hurrying the leftover would move a
    term nobody is waiting on -- and the handler ignores it by run number
    anyway, so the batch would simply never finish."""
    _, body = await _master(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 2)
    job = (
        await session.execute(
            select(Job).where(Job.body_id == body.id, Job.kind == JobKind.CRAFT_BATCH.value)
        )
    ).scalar_one()
    was = job.run_at

    await craft.freeze(session, body)
    assert (await session.get(CraftBatch, batch.id)).state is BatchState.WAITING

    assert await alpha.hurry(session, body.identity_id, body) == ()
    assert job.run_at == was, "срок замороженного запуска всё-таки двинули"


async def test_a_leftover_run_is_not_hurried_beside_the_live_one(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Frozen and resumed, the batch has two jobs: the leftover of run one and
    the live one of run two (D-209). The handler refuses the leftover by its
    run number, so hurrying it would move a term nobody waits on -- and the
    batch would then hang on a job that finishes nothing."""
    _, body = await _master(session)
    batch = await craft.start(session, constants, catalog, body, MAKE, 2)
    await craft.freeze(session, body)
    await craft.wake(session, body)
    live = (await session.get(CraftBatch, batch.id)).runs
    assert live > 1, "партия не перезапустилась: проверять нечего"

    jobs_of_batch = {
        job.payload.get("run"): job
        for job in (
            await session.execute(
                select(Job).where(
                    Job.body_id == body.id,
                    Job.kind == JobKind.CRAFT_BATCH.value,
                    Job.state == JobState.PENDING,
                )
            )
        ).scalars()
    }
    assert set(jobs_of_batch) >= {1, live}, f"ожидались задания обоих запусков: {jobs_of_batch}"
    stale_was = jobs_of_batch[1].run_at

    assert await alpha.hurry(session, body.identity_id, body) == (JobKind.CRAFT_BATCH.value,)
    assert jobs_of_batch[1].run_at == stale_was, "двинули срок брошенного запуска"
    assert jobs_of_batch[live].run_at < stale_was


def test_both_kinds_tell_the_client_what_to_reread() -> None:
    """An event with empty `touches` is delivered and changes nothing on the
    client (D-226). The widget would still look right -- it rereads the world
    after its own action -- but a second tab of the same player would see
    neither the printed thing nor the pulled-up term."""
    from src.api.push import touches_of

    assert touches_of(EventKind.ALPHA_SPAWNED.value) == ("inventory", "doings")
    assert touches_of(EventKind.ALPHA_HURRIED.value) == ("inventory", "doings")


# --- the race the lever is built around --------------------------------------


async def test_term_the_worker_already_took_is_skipped(
    session: AsyncSession,
    constants: Constants,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The worker claims pending jobs by `run_at` under `FOR UPDATE SKIP
    LOCKED` -- the very column this lever writes to.

    Without the same lock the two overlap: the worker is carrying the job out
    while the lever moves its term underneath, and the handler then stamps its
    result by a moment that was rewritten mid-flight. With it, a job already
    being run is skipped -- there is nothing left to hurry.
    """
    body = await _body(session)
    job = await explore.survey(session, constants, body)
    body_id, job_id, term = body.id, job.id, job.run_at
    await session.commit()

    #: The claim runs at a moment past the term, as the worker would; the
    #: lever runs before it, as a player pressing the button would.
    started = asyncio.Event()

    async def claim() -> None:
        async with factory() as db, db.begin():
            taken = await jobs._claim(db, term + timedelta(seconds=1), "worker/test")
            assert taken is not None and taken.id == job_id
            started.set()
            #: Hold the row as a running handler would, long enough for the
            #: lever to reach it and have to decide.
            await asyncio.sleep(0.3)

    async def rush() -> tuple[str, ...]:
        await started.wait()
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            return await alpha.hurry(db, mine, now=term - timedelta(minutes=1))

    _, moved = await asyncio.gather(claim(), rush())
    assert moved == (), "рычаг влез в задание, которое воркер уже выполнял"

    fresh = await session.get(Job, job_id, populate_existing=True)
    assert fresh.run_at == term, "срок переписан под работающим обработчиком"


async def test_a_claimed_passage_is_skipped_with_its_own_clock(
    session: AsyncSession,
    constants: Constants,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The same race on the branch that takes a second lock.

    A passage is where the lever locks two rows in a row -- the job, then the
    travel -- so the order matters here and not on a survey. A job the worker
    already holds must leave **both** clocks alone: moving `arrives_at` under
    a running arrival would stamp the traveller as arriving at a moment the
    handler never saw.
    """
    there, body = await _walker(session)
    passage = await travel.depart(session, constants, body, there)
    body_id, passage_id, term = body.id, passage.id, passage.arrives_at
    await session.commit()

    started = asyncio.Event()

    async def claim() -> None:
        async with factory() as db, db.begin():
            taken = await jobs._claim(db, term + timedelta(seconds=1), "worker/test")
            assert taken is not None and taken.kind == JobKind.TRAVEL_LEG.value
            started.set()
            await asyncio.sleep(0.3)

    async def rush() -> tuple[str, ...]:
        await started.wait()
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            return await alpha.hurry(db, mine, now=term - timedelta(minutes=1))

    _, moved = await asyncio.gather(claim(), rush())
    assert moved == (), "рычаг влез в переход, который воркер уже выполнял"

    fresh = await session.get(Travel, passage_id, populate_existing=True)
    assert fresh.arrives_at == term, "часы перехода переписаны под обработчиком"


async def test_two_levers_at_once_leave_one_term(
    session: AsyncSession,
    constants: Constants,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two clicks in the same instant -- two transactions on one row. The lock
    serialises them; the second finds the term already up to now and moves
    nothing, rather than both writing their own moment."""
    body = await _body(session)
    await explore.survey(session, constants, body)
    body_id = body.id
    await session.commit()

    async def rush() -> tuple[str, ...]:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            return await alpha.hurry(db, mine.identity_id, mine)

    outcomes = await asyncio.gather(rush(), rush())
    assert sorted(len(one) for one in outcomes) == [0, 1], f"срок двинули дважды: {outcomes}"
