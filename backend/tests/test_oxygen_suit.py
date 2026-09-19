# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The suit that does not come off, and the countdown a body is told of (D-343).

Checked is the rule in its two directions:

* the **connection** is a door: where only the suit breathes for a body, it
  neither comes off nor gives its slot to anything but a suit -- on the rock
  and on the road to it -- while aboard and under a sky with air it comes off
  as any gear does. There is no void to step out into above a planet any more
  (D-354): the road out of a hull into nothing breathable is the gangway down
  onto an airless world;
* the **reserve** is a countdown: the tick decides by what the settling found,
  so a bare body chokes however much air lies in its bag, the body is told
  once when the countdown starts, and a worn suit is told of the day before it
  wears through -- and then the tick finishes what the wear began.

The breathing itself is in `test_oxygen.py`, a hull and its crew in
`test_oxygen_hull.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from oxygen_kit import SUIT, _cylinder, _ground, _hull, _person, _port, _sphere, _suited
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, oxygen, travel, wear, world
from src.engine.oxygen import breath
from src.models.event import Event, EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node, Planet

BODY_SLOT = "body"
COAT = "insulated_suit"
OTHER_SUIT = "pyroxite_suit"
PACK = "sturdy_backpack"


async def _told(session: AsyncSession, body: Body, kind: EventKind) -> list[Event]:
    rows = await session.execute(
        select(Event).where(Event.kind == kind.value, Event.actor_identity_id == body.identity_id)
    )
    return list(rows.scalars().all())


async def _held(session: AsyncSession, body: Body, type_key: str) -> Item:
    pocket = await world.body_container(session, body)
    return await world.grant_item(session, pocket, type_key, quality=60, origin="тест")


async def _worn_suit(session: AsyncSession, body: Body) -> Item | None:
    return (await gear.equipped(session, body)).get(BODY_SLOT)


async def _on_the_rock(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> tuple[Node, Body]:
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis, name="Чёрное поле")
    body = await _person(session, rock)
    await _suited(session, constants, catalog, body)
    await _cylinder(session, body, 6)
    return rock, body


async def _landed_on_the_rock(session: AsyncSession, constants: Constants) -> tuple[Node, Body]:
    """A hull set down on Pyroxis, its owner aboard: the gangway runs down to
    ground with nothing to breathe (D-233) -- the one road out of a hull into
    no air, now that the sky above a planet is no node (D-354)."""
    await _sphere(session, Planet.PYROXIS, airless=True)
    pad = await _port(session, Planet.PYROXIS)
    vessel, body, _connector = await _hull(session, constants, pad)
    assert vessel.docked_node_id == pad.id
    return pad, body


# --- the connection is a door -------------------------------------------------


async def test_the_suit_does_not_come_off_on_the_rock(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Neither taken off nor pushed off its slot -- but a suit for a suit, and
    any other slot, as ever."""
    rock, body = await _on_the_rock(session, constants, catalog)
    pack = await _held(session, body, PACK)
    await gear.equip(session, constants, catalog, body, pack)

    with pytest.raises(oxygen.NoAir) as refused:
        await gear.unequip(session, constants, catalog, body, BODY_SLOT)
    assert refused.value.key == "oxygen-suit-stays-on"
    assert refused.value.params == {"node": rock.name, "suit": SUIT}
    assert await oxygen.suited(session, catalog, body), "скафандр сняли на скале"

    #: The coat is what the heat tempts one into (`frost.suit_k`), and it takes
    #: the same slot.
    coat = await _held(session, body, COAT)
    with pytest.raises(oxygen.NoAir):
        await gear.equip(session, constants, catalog, body, coat)
    worn = await _worn_suit(session, body)
    assert worn is not None and worn.type_key == SUIT, "костюм вытеснил скафандр на скале"

    other = await _held(session, body, OTHER_SUIT)
    await gear.equip(session, constants, catalog, body, other)
    worn = await _worn_suit(session, body)
    assert worn is not None and worn.type_key == OTHER_SUIT, "скафандр на скафандр не сменили"

    assert await gear.unequip(session, constants, catalog, body, "back") is not None


async def test_the_suit_comes_off_where_there_is_air(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Under a sky with air, and aboard a sealed hull: the rule is the rock's, not the suit's."""
    terra = await _sphere(session, Planet.TERRA, airless=False)
    field = await _ground(session, Planet.TERRA, terra)
    walker = await _person(session, field)
    await _suited(session, constants, catalog, walker)
    coat = await _held(session, walker, COAT)
    await gear.equip(session, constants, catalog, walker, coat)
    assert not await oxygen.suited(session, catalog, walker)

    _, crew = await _landed_on_the_rock(session, constants)
    await _suited(session, constants, catalog, crew)
    assert await gear.unequip(session, constants, catalog, crew, BODY_SLOT) is not None


async def test_on_the_road_to_the_rock_the_suit_stays_on(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The road is outside: a body's node changes only when it arrives.

    Stepping off a hull, the body still stands aboard for as long as the leg
    lasts -- and the step checked the suit when it set out. Taken off on the
    way, it would arrive on the rock bare.
    """
    rock, body = await _landed_on_the_rock(session, constants)
    await _suited(session, constants, catalog, body)
    await _cylinder(session, body, 6)
    await travel.depart(session, constants, body, rock)
    assert await travel.current(session, body) is not None

    with pytest.raises(oxygen.NoAir) as refused:
        await gear.unequip(session, constants, catalog, body, BODY_SLOT)
    #: Named as the road, not the place: the body still counts aboard, and
    #: "take it off aboard" would read as a contradiction.
    assert refused.value.key == "oxygen-suit-stays-on-road"
    assert refused.value.params["node"] == rock.name


async def test_a_step_out_and_taking_the_suit_off_do_not_both_pass(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step and the undressing share the body's row.

    Without it the step reads the suit and goes on to lay its road, while the
    undressing -- starting a moment later -- finds no road yet and the body
    still aboard, and takes the suit off: both commit, and a bare body walks
    into the void. With it one of the two waits and sees the other's result.
    """
    async with factory() as session, session.begin():
        rock, body = await _landed_on_the_rock(session, constants)
        await _suited(session, constants, catalog, body)
        await _cylinder(session, body, 6)
        body_id, rock_id = body.id, rock.id

    #: The window between the step's reading of the suit and its road.
    _slow(monkeypatch, breath, "suited", delay=0.4)

    async def step() -> None:
        async with factory() as db, db.begin():
            walker = await db.get(Body, body_id)
            target = await db.get(Node, rock_id)
            assert walker is not None and target is not None
            await travel.depart(db, constants, walker, target)

    async def undress() -> None:
        await asyncio.sleep(0.15)
        async with factory() as db, db.begin():
            walker = await db.get(Body, body_id)
            assert walker is not None
            await gear.unequip(db, constants, catalog, walker, BODY_SLOT)

    outcomes = await asyncio.gather(step(), undress(), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, Exception)]
    assert len(refused) == 1, f"прошли оба или ни один: {outcomes}"
    assert isinstance(refused[0], oxygen.NoAir), refused[0]

    async with factory() as session:
        walker = await session.get(Body, body_id)
        assert walker is not None
        going = await travel.current(session, walker)
        suited = await oxygen.suited(session, catalog, walker)
    assert not (going is not None and not suited), "голое тело вышло в пустоту"


async def test_a_change_of_dress_decides_under_the_body_row(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three tabs of one player: a coat for a coat, the suit on, a step out.

    The coat-for-coat change has nothing to refuse, and it used to let itself
    through without the body's row. Held between that answer and its own
    write, it let the suit go on and the step go out -- and then replaced
    whatever stood in the slot by then, which was the suit: a bare body on the
    road to the void. Under the row, the other two wait for it, and whatever
    order they finish in, no body is on that road without a suit.
    """
    async with factory() as session, session.begin():
        rock, body = await _landed_on_the_rock(session, constants)
        first_coat = await _held(session, body, COAT)
        await gear.equip(session, constants, catalog, body, first_coat)
        suit = await _held(session, body, SUIT)
        second_coat = await _held(session, body, COAT)
        await _cylinder(session, body, 6)
        body_id, rock_id = body.id, rock.id
        suit_id, coat_id = suit.id, second_coat.id

    door = oxygen.require_suit_kept

    async def held_at_the_coat(session, catalog_, body_, slot, *, putting_on=None):
        await door(session, catalog_, body_, slot, putting_on=putting_on)
        if putting_on is not None and putting_on.id == coat_id:
            await asyncio.sleep(1.0)

    monkeypatch.setattr(oxygen, "require_suit_kept", held_at_the_coat)

    async def dress(item_id, after: float) -> None:
        await asyncio.sleep(after)
        async with factory() as db, db.begin():
            walker = await db.get(Body, body_id)
            thing = await db.get(Item, item_id)
            assert walker is not None and thing is not None
            await gear.equip(db, constants, catalog, walker, thing)

    async def step(after: float) -> None:
        await asyncio.sleep(after)
        async with factory() as db, db.begin():
            walker = await db.get(Body, body_id)
            target = await db.get(Node, rock_id)
            assert walker is not None and target is not None
            await travel.depart(db, constants, walker, target)

    outcomes = await asyncio.gather(
        dress(coat_id, 0.0), dress(suit_id, 0.15), step(0.35), return_exceptions=True
    )
    for one in outcomes:
        assert one is None or isinstance(one, oxygen.NoAir), outcomes

    async with factory() as session:
        walker = await session.get(Body, body_id)
        assert walker is not None
        going = await travel.current(session, walker)
        suited = await oxygen.suited(session, catalog, walker)
    assert not (going is not None and not suited), f"голое тело вышло в пустоту: {outcomes}"


# --- the reserve is a countdown ------------------------------------------------


@pytest.mark.parametrize("bare", [True, False], ids=["bare-with-a-full-bag", "suited-run-dry"])
async def test_nothing_to_breathe_is_told_once_and_kills_at_the_next_settling(
    session: AsyncSession, constants: Constants, catalog: Catalog, bare: bool
) -> None:
    """Through the tick, both ways of having nothing to breathe (D-234, D-343).

    A bare body with air in the bag used to be neither charged nor choked:
    the choking asked the cylinders once more, found them full and let the
    body stand there for nothing and for ever.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    body = await _person(session, rock)
    if bare:
        await _cylinder(session, body, 100)
    else:
        await _suited(session, constants, catalog, body)
    started = datetime.now(UTC)
    body.air_at = started - timedelta(hours=1)
    await session.flush()

    assert await oxygen.tick_bodies(session, constants, catalog, now=started) == 0
    assert body.choking_since == started, "отсчёт не начался"
    assert len(await _told(session, body, EventKind.BODY_AIRLESS)) == 1, "телу не сказали"
    if bare:
        assert await oxygen.carried(session, body) == pytest.approx(100), "голое тело дышало"

    later = started + timedelta(minutes=1)
    assert await oxygen.tick_bodies(session, constants, catalog, now=later) == 1
    assert body.state is not BodyState.ALIVE, "задыхающееся тело пережило второй счёт"
    assert len(await _told(session, body, EventKind.BODY_AIRLESS)) == 1, "сказали дважды"


async def test_a_suit_worn_through_outside_is_told_the_day_before_and_then_chokes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The world takes a worn-through suit (D-305), and the countdown takes the body.

    Told the day before, by the very measure the wear will write it off with,
    and only of what is worn: the spare in the bag wears too, and nobody wears
    it. Then the suit is gone, the bag is still full, and the tick does the rest.
    """
    _, body = await _on_the_rock(session, constants, catalog)
    worn = await _worn_suit(session, body)
    assert worn is not None
    spare = await _held(session, body, SUIT)
    per_day = constants[R.WEAR_GEAR_PER_DAY]
    environment = constants[R.WEAR_ENVIRONMENT_K].get(Planet.PYROXIS.value, 1.0)
    floor = constants[R.QUALITY_SCALE].min
    #: A day and a half of wear left: tomorrow survives, the day after does not.
    for suit in (worn, spare):
        daily = wear.spent_on(constants, suit, per_day, environment=environment)
        suit.condition = Decimal(str(round(floor + 1.5 * daily, 2)))
        suit.wear_remainder = Decimal(0)
    await session.flush()

    assert await wear.daily_gear_wear(session, constants, catalog) == 0
    told = await _told(session, body, EventKind.GEAR_WEARING_OUT)
    assert [one.payload["item_id"] for one in told] == [str(worn.id)], "сказали не о надетом"
    assert told[0].payload["type_key"] == SUIT

    worn_id = worn.id
    assert await wear.daily_gear_wear(session, constants, catalog) >= 1
    assert await session.get(Item, worn_id) is None, "скафандр не истёрся"
    assert not await oxygen.suited(session, catalog, body)
    assert await oxygen.carried(session, body) > 0

    started = datetime.now(UTC)
    body.air_at = started - timedelta(minutes=1)
    await session.flush()
    assert await oxygen.tick_bodies(session, constants, catalog, now=started) == 0
    assert len(await _told(session, body, EventKind.BODY_AIRLESS)) == 1
    assert (
        await oxygen.tick_bodies(session, constants, catalog, now=started + timedelta(minutes=1))
        == 1
    )
    assert body.state is not BodyState.ALIVE, "тело без скафандра пережило второй счёт"
