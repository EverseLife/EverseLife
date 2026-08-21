# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""One body, one occupation (D-211).

Checked is what the rule was introduced for -- three advances on one pair of
hands. Before it a body could hold a search on the empty land, a plot under
the plough and a night's sleep at the same hour, and D-209 let a batch run
through that sleep on top.

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
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import craft, farm, forage, occupation, rest, world
from src.models.craft import BatchState
from src.models.farm import PlotState

INGOT = "Слиток железа"
NAILS = "Гвозди"
FORGE = "Кузница"


async def _yard(session: AsyncSession, *, area: float = 400, fertility: float = 55):
    """A wild plot with room to forage on and soil to plough."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.busy.{stamp}",
        "Хутор",
        area_m2=area,
        properties={"дикий": True, "вода": "река", "плодородие": fertility},
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

    with pytest.raises(occupation.Busy) as refusal:
        await rest.sleep(session, constants, body)
    assert "поиск" in str(refusal.value)

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
    """"Дела" is the one place everything running is seen and stopped (D-211)."""
    _, _, body = await _yard(session)
    plot = await farm.mark(session, constants, body, name="Северная", area=50)
    await farm.plow(session, constants, body, plot)

    doings = await occupation.all_of(session, body)
    assert [doing.kind for doing in doings] == [occupation.PLOT]
    line = doings[0]
    assert line.title == "вспашка"
    assert "Северная" in line.what, "делянок бывает четыре: строка обязана назвать свою"
    assert line.until is not None

    #: The deadline is told as a distance, not as a stamp: an ISO string in a
    #: refusal was unreadable, and the world counts a day of its own length.
    said = line.refusal()
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
        (occupation.SLEEP, "сон", None)
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
