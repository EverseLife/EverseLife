# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""One body, one occupation (D-211, D-310).

Checked is what the rule was introduced for -- three advances on one pair of
hands. Before it a body could hold a search on the empty land, a plot under
the plough and a night's sleep at the same hour, and D-209 let a batch run
through that sleep on top. D-310 closed the last three that slipped past it:
a house going up, a house coming down, a surface being laid.

* a second occupation is refused, and the refusal names the first one;
* the plough holds the hands even when the plot is somebody's whole day away:
  the check is about the body, not the node;
* sleep is the one thing a batch does not refuse -- it freezes with the master
  and goes on when they wake;
* the queue of D-209 survives: a second batch is a place in the queue, not a
  second occupation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from explore_kit import _camp, _pin, _reach, _step
from src import i18n
from src.api.commands.craft import _craft_resume
from src.api.commands.views import _batches
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import (
    craft,
    estate,
    explore,
    farm,
    forage,
    jobs,
    occupation,
    places,
    rest,
    road,
    travel,
    world,
)
from src.models.craft import BatchState, CraftBatch
from src.models.farm import PlotState
from src.models.identity import Body, Identity
from src.models.job import JobState
from src.models.world import Planet, Surface

INGOT = "iron_ingot"
NAILS = "nails"
FORGE = "forge"


async def _yard(session: AsyncSession, *, area: float = 400, fertility: float = 55):
    """A wild plot with room to forage on and soil to plough."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.busy.{stamp}",
        "Хутор",
        area_m2=area,
        properties={"wild": True, "water": "river", "fertility": fertility},
    )
    identity = await world.create_identity(session, f"Работник-{stamp}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


async def _forge(session: AsyncSession, node) -> None:
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, FORGE, quality=60, origin="сценарий теста")


async def _give(session: AsyncSession, body, type_key: str, quantity: float) -> None:
    pocket = await world.body_container(session, body)
    await world.grant_item(
        session, pocket, type_key, amount=quantity, quality=60, origin="сценарий теста"
    )


# --- the refusals ------------------------------------------------------------


async def test_sleep_is_refused_while_the_search_goes(
    session: AsyncSession, constants: Constants
) -> None:
    """The very case reported: foraging and sleeping at the same hour."""
    _, _, body = await _yard(session)
    body.stamina = body.stamina.__class__("50")
    await forage.start(session, constants, body)

    #: By the key and what it names, not by the sentence: the wording belongs
    #: to the locale now (D-251 wave III).
    with pytest.raises(occupation.Busy) as refusal:
        await rest.sleep(session, constants, body)
    assert refusal.value.key == "occupation-busy"
    #: The quoted half is a message of its own now (wave IV): the refusal
    #: names it, and the words appear only where the language is known.
    quoted = refusal.value.inner["what"][0]
    assert quoted.key == "doing-forage-searching"

    #: Ended the search -- the bed is free again.
    await forage.stop(session, body)
    await rest.sleep(session, constants, body)
    assert body.sleeping_since is not None


async def test_plot_work_and_the_search_do_not_combine(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, body = await _yard(session)
    body.stamina = body.stamina.__class__("50")
    await forage.start(session, constants, body)

    with pytest.raises(occupation.Busy):
        await farm.mark(session, constants, body, name="грядка", area=10)


async def test_the_plough_holds_the_hands_until_it_is_done(
    session: AsyncSession, constants: Constants
) -> None:
    """A plough is the body's work, wherever the body then wanders."""
    _, _, body = await _yard(session)
    plot = await farm.mark(session, constants, body, name="грядка", area=50)
    await farm.plow(session, constants, body, plot)
    assert plot.state is PlotState.PLOWING

    doing = await occupation.current(session, body)
    assert doing is not None and doing.kind == occupation.PLOT

    with pytest.raises(occupation.Busy):
        await forage.start(session, constants, body)
    with pytest.raises(occupation.Busy):
        await rest.sleep(session, constants, body)


async def test_a_batch_refuses_the_search_and_the_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A batch moves while the master stands here (D-209) -- so the hands are taken."""
    node, identity, body = await _yard(session)
    await _forge(session, node)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 10)

    batch = await craft.start(session, constants, catalog, body, NAILS, 2)
    assert batch.state is BatchState.RUNNING

    with pytest.raises(occupation.Busy):
        await forage.start(session, constants, body)
    with pytest.raises(occupation.Busy):
        await farm.mark(session, constants, body, name="грядка", area=10)


# --- what the client is told --------------------------------------------------


async def test_the_list_names_the_work_and_how_long_is_left(
    session: AsyncSession, constants: Constants
) -> None:
    """ "Дела" is the one place everything running is seen and stopped (D-211)."""
    _, _, body = await _yard(session)
    plot = await farm.mark(session, constants, body, name="Северная", area=50)
    await farm.plow(session, constants, body, plot)

    doings = await occupation.all_of(session, body)
    assert [doing.kind for doing in doings] == [occupation.PLOT]
    line = doings[0]
    #: A key, not a word: the title is derived from the kind, and the sentence
    #: carries the plot's name as an argument (D-251 wave IV).
    assert line.title == "doing-plot"
    assert line.says.key == "doing-plot-what"
    #: Four strips are four lines: each must name its own.
    assert line.says.params["plot"] == "Северная"
    said = i18n.render(line.says.key, line.says.params, locale=i18n.DEFAULT_LOCALE)
    assert "Северная" in said, said
    assert line.until is not None

    #: The deadline is told as a distance, not as a stamp: an ISO string in a
    #: refusal was unreadable, and the world counts a day of its own length.
    #: Quoted as a message with numbers, not as a phrase (wave IV): how many
    #: words "2 ч 5 мин" is, and in which forms, is the language's business.
    with pytest.raises(occupation.Busy) as refusal:
        await occupation.require_free(session, body)
    assert refusal.value.key == "occupation-busy"
    assert refusal.value.params["term"] == "true", "у вспашки есть срок"

    left = refusal.value.inner["left"][0]
    assert left == occupation.left_to_say(line.until)
    assert left.key == "time-left"
    assert set(left.params) == {"hours", "minutes"}, "часы и минуты числами"

    #: And what the reader actually gets out of it.
    said = i18n.render(left.key, left.params, locale=i18n.DEFAULT_LOCALE)
    assert "T" not in said and "+00:00" not in said, said
    assert "ещё" in said or "меньше минуты" in said, said


async def test_a_sleeping_body_has_one_line_and_no_clock(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, body = await _yard(session)
    body.stamina = body.stamina.__class__("50")
    await rest.sleep(session, constants, body)

    doings = await occupation.all_of(session, body)
    assert [(d.kind, d.title, d.until) for d in doings] == [
        (occupation.SLEEP, "doing-sleep", None)
    ], "сон кончается решением, а не сроком"


# --- sleep and the bench -----------------------------------------------------


async def test_sleep_freezes_the_batch_and_waking_sets_it_going(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Lying down is stepping away from the bench (D-211, amending D-209)."""
    node, identity, body = await _yard(session)
    await _forge(session, node)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 10)

    moment = datetime.now(UTC)
    batch = await craft.start(session, constants, catalog, body, NAILS, 2, now=moment)
    left_before = (batch.ready_at - moment).total_seconds()

    body.stamina = body.stamina.__class__("50")
    await rest.sleep(session, constants, body, now=moment)
    await session.refresh(batch)
    assert batch.state is BatchState.WAITING, "спящий не работает"
    assert batch.ready_at is None
    assert float(batch.remaining_seconds) == pytest.approx(left_before, abs=1)

    #: The machine went free while the master slept: nothing holds it.
    assert await craft.present(session, body, node.id) is False

    later = moment + timedelta(hours=1)
    await rest.wake(session, constants, body, now=later)
    await session.refresh(batch)
    assert batch.state is BatchState.RUNNING
    #: The work left is the work left: the hour of sleep did not do any of it.
    assert (batch.ready_at - later).total_seconds() == pytest.approx(left_before, abs=1)


async def test_the_queue_of_batches_survives(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A second batch is a place in the queue, not a second occupation (D-209)."""
    node, identity, body = await _yard(session)
    await _forge(session, node)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 20)

    first = await craft.start(session, constants, catalog, body, NAILS, 2)
    second = await craft.start(session, constants, catalog, body, NAILS, 2)
    assert first.state is BatchState.RUNNING
    assert second.state is BatchState.WAITING
    assert [batch.id for batch in await craft.waiting(session, body)] == [second.id]


async def test_a_waiting_work_is_not_taken_up_behind_another_occupation(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A waiting batch is no occupation, so its master may start a search
    beside it -- and taking it up is starting it again (D-211): no wake gives
    it the free forge while the search goes, and the hand that asks is told
    what the body is at."""
    node, identity, body = await _yard(session)
    await _forge(session, node)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 10)
    batch = await craft.start(session, constants, catalog, body, NAILS, 2)
    assert await craft.freeze(session, body) is batch
    body.stamina = body.stamina.__class__("50")
    await forage.start(session, constants, body)

    assert await craft.wake(session, body) is None
    await session.refresh(batch)
    assert batch.state is BatchState.WAITING, "the forge is free, the hands are not"
    #: And the window says so, rather than that no forge is free.
    (seen,) = await _batches(session, identity.id)
    assert seen["waiting"] == "busy"
    with pytest.raises(occupation.Busy) as refusal:
        await _craft_resume({"identity_id": identity.id}, session, {})
    assert refusal.value.inner["what"][0].key == "doing-forage-searching"

    #: The search over, the same hand takes the work up.
    await forage.stop(session, body)
    answer = await _craft_resume({"identity_id": identity.id}, session, {})
    assert answer["batch"] == str(batch.id)


async def test_a_scout_back_on_a_known_place_takes_up_the_work_waiting_there(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The run ends with the scout standing on the find (D-327), and a work of
    theirs waiting there goes on (D-209). The run is over, but its job is the
    one being run -- still pending to whoever asks what the body is at -- and
    must not be counted against it."""
    async with factory() as session, session.begin():
        sphere, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, catalog, camp)
        aim = _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.0)
        point = explore.point_of(
            constants, Planet.TERRA, explore.cell_of(constants, Planet.TERRA, aim)
        )
        shop = await world.create_node(
            session,
            f"terra.shop.{uuid.uuid4().hex[:6]}",
            "Shop",
            planet=Planet.TERRA,
            area_m2=60,
            parent=sphere,
            properties=_pin(point),
        )
        await _forge(session, shop)
        who = await session.get(Identity, scout.identity_id)
        assert who is not None
        await world.learn(session, who, NAILS)
        await _give(session, scout, INGOT, 10)
        #: The work was begun at the shop and left there; the scout sets out
        #: from the camp. Moved by hand: the road between is not the point.
        scout.node_id = shop.id
        await session.flush()
        batch = await craft.start(session, constants, catalog, scout, NAILS, 2)
        assert await craft.freeze(session, scout) is batch
        scout.node_id = camp.id
        await session.flush()
        run = await explore.survey(session, constants, scout, point)
        #: Out in the field the work reads as left behind, though the engine
        #: keeps the scout in the camp until the run ends (D-327).
        (seen,) = await _batches(session, scout.identity_id)
        assert seen["waiting"] == "away"
        term, scout_id, shop_id, batch_id = run.run_at, scout.id, shop.id, batch.id

    await jobs.run_due(factory, limit=10, now=term)

    async with factory() as session:
        back = await session.get(Body, scout_id)
        assert back is not None and back.node_id == shop_id, "the run found the shop"
        taken = await session.get(CraftBatch, batch_id)
        assert taken is not None and taken.state is BatchState.RUNNING


async def test_the_queue_is_seen_through_orders(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The client draws the queue from the `orders` command (D-209): the running
    batch and the one behind it, each with its node named.

    Pinned because the listing broke at the node's name and nobody's batch
    reached the screen for a week: the reply was a refusal, and the sidebar
    kept the empty list it had. Handwork on purpose -- the case reported.
    """
    import src.api.session  # noqa: F401, PLC0415 -- registers the commands
    from src.api.registry import COMMANDS

    node, identity, body = await _yard(session)
    await world.learn(session, identity, "fiber")
    await _give(session, body, "flax", 10)
    first = await craft.start(session, constants, catalog, body, "fiber", 1)
    second = await craft.start(session, constants, catalog, body, "fiber", 1)

    answer = await COMMANDS["orders"].run({"identity_id": identity.id}, session, {})
    rows = {row["id"]: row for row in answer["orders"]["batches"]}
    assert set(rows) == {str(first.id), str(second.id)}
    assert rows[str(first.id)]["state"] == "running"
    assert rows[str(first.id)]["started_at"] and rows[str(first.id)]["ready_at"]
    assert rows[str(second.id)]["state"] == "waiting"
    assert rows[str(second.id)]["waiting"] == "queued"
    assert rows[str(second.id)]["node"] == node.name
    assert rows[str(first.id)]["station"] is None


# --- the works on land (D-310) ------------------------------------------------


async def _woodland(session: AsyncSession, *, area: float = 400):
    """A forest of one's own: there is timber to fell and room to build on."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.woods.{stamp}",
        "Бор",
        area_m2=area,
        properties={"woods": True},
    )
    identity = await world.create_identity(session, f"Хозяин-{stamp}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


async def _materials(session: AsyncSession, constants: Constants, body, area: float) -> None:
    """What a house of this size takes, and one unit over."""
    for name, quantity in estate.estimate(
        constants, footprint=area, floors=1, kind=estate.kinds(constants)[0]
    ).items():
        await _give(session, body, name, quantity + 1)


async def test_a_build_holds_the_hands_and_says_so(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The case reported: felling went on beside a house going up (D-310).

    The house is put up by a body, and a body already at something has none to
    spare -- so the felling is refused, and the refusal names the build.
    """
    node, _, body = await _woodland(session)
    await _materials(session, constants, body, 20.0)
    await _give(session, body, "axe", 1)

    await estate.construct(session, constants, body, node, 20.0)

    doing = await occupation.current(session, body)
    assert doing is not None and doing.kind == occupation.BUILD
    assert doing.title == "doing-build"
    assert doing.until is not None, "у стройки есть срок"

    with pytest.raises(occupation.Busy) as refusal:
        await craft.start(session, constants, catalog, body, "wood", 1, way="logging")
    assert refusal.value.key == "occupation-busy"
    assert refusal.value.inner["what"][0].key == "doing-build-what"


async def test_a_build_is_drawn_in_the_list_from_anywhere(
    session: AsyncSession, constants: Constants
) -> None:
    """Walking off the yard used to leave the house invisible: the plot looked
    empty, the sack was empty, and nothing said why.
    """
    node, _, body = await _woodland(session)
    await _materials(session, constants, body, 20.0)
    await estate.construct(session, constants, body, node, 20.0)

    #: The body goes elsewhere -- the work is its own, not the plot's.
    away = await world.create_node(
        session, f"terra.away.{uuid.uuid4().hex[:8]}", "Тропа", area_m2=100
    )
    body.node_id = away.id
    await session.flush()

    doings = await occupation.all_of(session, body)
    assert [(one.kind, one.says.key) for one in doings] == [(occupation.BUILD, "doing-build-what")]


async def test_a_second_build_is_refused(session: AsyncSession, constants: Constants) -> None:
    """Two houses at once, even on two plots: one pair of hands (D-310)."""
    node, identity, body = await _woodland(session)
    await _materials(session, constants, body, 20.0)
    await _materials(session, constants, body, 20.0)
    await estate.construct(session, constants, body, node, 20.0)

    second = await world.create_node(
        session, f"terra.plot.{uuid.uuid4().hex[:8]}", "Второй участок", area_m2=400
    )
    second.owner_identity_id = identity.id
    body.node_id = second.id
    await session.flush()

    with pytest.raises(occupation.Busy):
        await estate.construct(session, constants, body, second, 20.0)


async def test_taking_a_house_apart_holds_the_hands(
    session: AsyncSession, constants: Constants
) -> None:
    """Demolition is building's own shape in reverse, and it holds the same hands."""
    node, _, body = await _woodland(session)
    await _materials(session, constants, body, 20.0)
    job = await estate.construct(session, constants, body, node, 20.0)
    await estate.finish_build(session, job)
    #: The worker closes a finished job; a pending one would read as a build
    #: still going on -- which is exactly what this file now tests for.
    job.state = JobState.DONE
    await session.flush()

    await estate.demolish(session, constants, body, node)
    doing = await occupation.current(session, body)
    assert doing is not None and doing.kind == occupation.DEMOLISH
    assert doing.title == "doing-demolish" and doing.until is not None

    with pytest.raises(occupation.Busy):
        await forage.start(session, constants, body)


async def test_laying_a_surface_holds_the_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A road is laid by a body too (D-158, D-310) -- and it is not the road walked."""
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.rda.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.rdb.{stamp}", "Там", area_m2=100)
    edge = await travel.connect(session, here, there, base_seconds=600)
    identity = await world.create_identity(session, f"Дорожник-{stamp}")
    body = await world.print_body(session, identity, here)
    await _give(session, body, "road_paving", constants[R.ROAD_SURFACE_PER_EDGE])

    await road.lay(session, constants, catalog, body, edge)

    doing = await occupation.current(session, body)
    assert doing is not None and doing.kind == occupation.PAVING, (
        "укладка — своё дело, а не «путь»: по этому id клиент их и различает"
    )
    assert doing.title == "doing-paving" and doing.until is not None
    #: Laying, not topping up: one job kind carries both, and the window
    #: offered them under two different words.
    assert doing.says.params["mend"] == "false"
    said = i18n.render(doing.says.key, doing.says.params, locale=i18n.DEFAULT_LOCALE)
    assert "укладка" in said, said

    with pytest.raises(occupation.Busy):
        await forage.start(session, constants, body)


async def test_topping_up_a_road_is_not_called_laying(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The same job kind, two works, two words (D-158).

    A worker who pressed "подсыпать" and read "идёт укладка покрытия" was being
    told about somebody else's work.
    """
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.mnd.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.mne.{stamp}", "Там", area_m2=100)
    edge = await travel.connect(session, here, there, base_seconds=600, surface=Surface.ROAD)
    edge.condition = edge.condition.__class__("40")
    identity = await world.create_identity(session, f"Дорожник-{stamp}")
    body = await world.print_body(session, identity, here)
    await _give(session, body, "road_paving", constants[R.ROAD_SURFACE_PER_EDGE])

    await road.lay(session, constants, catalog, body, edge, mend=True)

    doing = await occupation.current(session, body)
    assert doing is not None and doing.kind == occupation.PAVING
    assert doing.says.params["mend"] == "true"
    said = i18n.render(doing.says.key, doing.says.params, locale=i18n.DEFAULT_LOCALE)
    assert "подсыпка" in said, said


def test_every_kind_owes_a_word_in_every_language() -> None:
    """A kind added without its one-word title shows the player the key."""
    for kind in occupation.KINDS:
        for locale in i18n.LOCALES:
            said = i18n.render(f"doing-{kind}", locale=locale)
            assert said and said != f"doing-{kind}", (kind, locale)


async def test_a_site_is_not_started_by_busy_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The other door into a build (D-266) keeps the same rule as the one-motion one."""
    node, _, body = await _woodland(session)
    site = await estate.lay_site(session, constants, body, node, 20)
    for name, quantity in site.needed.items():
        await _give(session, body, name, quantity)
        await estate.contribute_to_site(session, constants, catalog, body, site, name, quantity)

    await forage.start(session, constants, body)
    strength = float(body.stamina)
    with pytest.raises(occupation.Busy):
        await estate.start_site(session, constants, body, site)
    #: And the strength is untouched: the check stands before the price.
    assert float(body.stamina) == strength

    await forage.stop(session, body)
    await estate.start_site(session, constants, body, site)
    doing = await occupation.current(session, body)
    assert doing is not None and doing.kind == occupation.BUILD


async def test_the_builder_may_sleep_under_the_rising_roof(
    session: AsyncSession, constants: Constants
) -> None:
    """The works on land hold the hands, not the night (D-310).

    They run by their own clock -- the house rises whether its builder is on
    the plot, on the road or asleep -- so lying down neither stops them nor
    earns anything the waking hours would not have. Refusing it would kill the
    builder: the vault's own example house is twenty-three days of building,
    and even a hut of twenty metres outlasts a night.
    """
    node, _, body = await _woodland(session)
    await _materials(session, constants, body, 20.0)
    await estate.construct(session, constants, body, node, 20.0)
    body.stamina = body.stamina.__class__("10")

    #: What the build forbids is unchanged -- checked awake, because a sleeper
    #: is refused everything in person by the door before this one (D-091).
    with pytest.raises(occupation.Busy):
        await forage.start(session, constants, body)

    await rest.sleep(session, constants, body)
    assert body.sleeping_since is not None

    #: Both are running now, and the list says so: the sleeper is not idle land.
    kinds = {doing.kind for doing in await occupation.all_of(session, body)}
    assert kinds == {occupation.BUILD, occupation.SLEEP}
