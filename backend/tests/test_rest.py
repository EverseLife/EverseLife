# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Hibernation (D-091).

Checked is what sleep is built this way for:

* recovery is computed by the time actually slept -- no tick needed;
* at home (with a bed) exactly `body.hibernation_home_k` times faster;
* no sleeping in advance: the ceiling is `body.stamina_max`, a full one has no reason to lie down;
* a sleeper is unavailable for in-person actions -- that is how sleep pays.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import chat, mining, rest, travel, world
from src.models.chat import Utterance


async def _tired(session: AsyncSession, *, stamina: float = 40, bed: bool = False):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.camp.{stamp}", "Привал", area_m2=100)
    identity = await world.create_identity(session, f"Усталый-{stamp}")
    body = await world.print_body(session, identity, node)
    body.stamina = Decimal(str(stamina))
    if bed:
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, rest.BED, quality=50, origin="тест")
    await session.flush()
    return node, body


async def test_sleep_restores_over_time(session: AsyncSession, constants: Constants) -> None:
    """Credited on waking, by actual hours -- offline and without a tick."""
    _, body = await _tired(session, stamina=40)
    lay_down = datetime.now(UTC)
    await rest.sleep(session, constants, body, now=lay_down)

    returned = await rest.wake(session, constants, body, now=lay_down + timedelta(hours=2))
    await session.commit()

    assert returned == pytest.approx(2 * constants[R.BODY_HIBERNATION_RATE])
    assert float(body.stamina) == pytest.approx(40 + returned)
    assert body.sleeping_since is None


async def test_snatched_sleep_does_not_outpace_the_vault(
    session: AsyncSession, constants: Constants
) -> None:
    """Lying down and getting up in a loop restores no faster than the rate.

    Stamina keeps hundredths and Postgres rounds a half **up**, so a sleep
    worth half of one used to be credited a whole one. At
    `body.hibernation_rate` that is a sleep of two and a quarter seconds, and
    nothing throttles the pair of commands -- so the loop paid about twice the
    rate the vault sets. Alone in this family it ran the player's way, which
    is what made it worth doing.
    """
    _, body = await _tired(session, stamina=40)
    started = datetime.now(UTC)
    #: A sleep worth half a hundredth: the old rounding gave a whole one.
    nap = timedelta(hours=0.005 / constants[R.BODY_HIBERNATION_RATE])
    naps, moment = 200, started

    for _ in range(naps):
        await rest.sleep(session, constants, body, now=moment)
        moment += nap
        await rest.wake(session, constants, body, now=moment)
        #: Through the row: the whole defect lives in the round trip.
        await session.flush()
        await session.refresh(body, ["stamina"])

    worth = constants[R.BODY_HIBERNATION_RATE] * (naps * nap) / timedelta(hours=1)
    gained = float(body.stamina) - 40
    #: Never more than the hours were worth. Snatched sleep may give less --
    #: a nap too short to credit a hundredth credits nothing -- but it must
    #: never give more, which is what doubled the rate.
    assert gained <= worth
    #: And never less than nothing: waking does not take strength away. The
    #: floor is on the way in, and a floor can only ever be pointed the wrong
    #: way once.
    assert gained >= 0


async def test_a_night_of_odd_length_is_credited_to_the_hundredth(
    session: AsyncSession, constants: Constants
) -> None:
    """An ordinary sleep loses nothing to the arithmetic.

    The credit is floored, and a figure reached through floats lands an ulp
    below itself: from forty, half an hour and nine seconds at eight an hour
    comes to 44.019999999999996 and not the 44.02 it is worth. Floored raw
    that is a hundredth taken from an honest night -- the shortfall the others
    in this family avoid by keeping their sliver, and this one has nowhere to
    put one. A hundred and sixty-two durations under six hours do it.
    """
    _, body = await _tired(session, stamina=40)
    lay_down = datetime.now(UTC)
    await rest.sleep(session, constants, body, now=lay_down)

    nap = timedelta(seconds=1809)
    returned = await rest.wake(session, constants, body, now=lay_down + nap)
    worth = constants[R.BODY_HIBERNATION_RATE] * nap / timedelta(hours=1)

    #: Within the hundredth the column can hold, and never above the worth --
    #: compared a billionth wide, since both sides are float sums and the
    #: quantity under test is a hundredth.
    assert returned <= worth + 1e-9
    assert returned > worth - 0.01


async def test_faster_at_home(session: AsyncSession, constants: Constants) -> None:
    """The bed is the home while there are no own buildings (E3)."""
    _, in_field = await _tired(session, stamina=10)
    _, at_home = await _tired(session, stamina=10, bed=True)
    lay_down = datetime.now(UTC)

    await rest.sleep(session, constants, in_field, now=lay_down)
    await rest.sleep(session, constants, at_home, now=lay_down)
    assert not in_field.sleeping_home
    assert at_home.sleeping_home

    hour = lay_down + timedelta(hours=1)
    plain = await rest.wake(session, constants, in_field, now=hour)
    with_bed = await rest.wake(session, constants, at_home, now=hour)
    assert with_bed == pytest.approx(plain * constants[R.BODY_HIBERNATION_HOME_K])


async def test_cannot_sleep_in_advance(session: AsyncSession, constants: Constants) -> None:
    """The ceiling is `body.stamina_max`; a full one has no reason to lie down."""
    _, almost_full = await _tired(session, stamina=constants[R.BODY_STAMINA_MAX] - 1)
    lay_down = datetime.now(UTC)
    await rest.sleep(session, constants, almost_full, now=lay_down)
    await rest.wake(session, constants, almost_full, now=lay_down + timedelta(hours=50))
    assert float(almost_full.stamina) == constants[R.BODY_STAMINA_MAX]

    with pytest.raises(rest.NotTired):
        await rest.sleep(session, constants, almost_full)


async def test_sleeper_unavailable_for_in_person(
    session: AsyncSession, constants: Constants
) -> None:
    """Overslept -- the lot got bought: that is how hibernation pays (D-091)."""
    node, body = await _tired(session)
    vein = await world.create_vein(session, node, "iron_ore", richness=60, remaining=1000)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="сценарий теста")
    adjacent = await world.create_node(
        session, f"terra.next.{uuid.uuid4().hex[:6]}", "Рядом", area_m2=50
    )
    await travel.connect(session, node, adjacent, base_seconds=10)

    await rest.sleep(session, constants, body)

    with pytest.raises(travel.Asleep):
        await mining.start(session, constants, body, vein)
    with pytest.raises(travel.Asleep):
        await travel.depart(session, constants, body, adjacent)
    with pytest.raises(travel.Asleep):
        await chat.say(session, constants, body, "сплю и говорю", kind=Utterance.SPEECH)
    with pytest.raises(travel.Asleep):
        #: A sleeper does not lie down a second time -- they are already lying.
        await rest.sleep(session, constants, body)

    #: Waking up is always allowed -- that is the exit.
    await rest.wake(session, constants, body)
    await mining.start(session, constants, body, vein)


async def test_no_lying_down_en_route(session: AsyncSession, constants: Constants) -> None:
    node, body = await _tired(session)
    adjacent = await world.create_node(
        session, f"terra.far.{uuid.uuid4().hex[:6]}", "Даль", area_m2=50
    )
    await travel.connect(session, node, adjacent, base_seconds=600)
    await travel.depart(session, constants, body, adjacent)
    with pytest.raises(travel.InTransit):
        await rest.sleep(session, constants, body)
