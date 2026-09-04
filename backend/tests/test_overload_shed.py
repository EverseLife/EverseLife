# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The limit fell under a load nobody moved: what no longer fits falls (D-306).

Nothing arrived. The frame came off, or was drained, or was worn through to
nothing -- and the load that stood on the lift it gave has to come down. Here
each road to a fallen limit is walked, what may never fall is held back (what
is worn, what was just taken off, the air a body breathes), and the fall is
raced against the hands it moves things out of.

The other half of the family -- a thing arriving into hands too full for it --
is `test_overload.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from overload_kit import (
    BACKPACK,
    EXO,
    ORE,
    SUIT,
    _charged,
    _ground,
    _held,
    _hold,
    _lying,
    _told,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, storage, wear, world
from src.models.event import EventKind
from src.models.gear import Equipped
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float


async def test_taking_the_frame_off_drops_what_it_was_carrying(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The limit falls with the exoskeleton, and the load falls with it (D-265).

    The hole the rule was written against, from the other side: nothing
    arrives, the limit simply shrinks. Wear the frame, fill the hands to its
    limit, take it off -- and before this the ore stayed in the pocket and
    walked out of the node. "An overloaded body does not exist any more"
    (D-265, D-268) was true only of the doors things came in through.
    """
    node, _, body = await _ground(session)
    base = constants[R.INVENTORY_CARRY_MASS]
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, exo)
    assert await gear.capacity(session, constants, catalog, body) > base, "каркас поднимает"
    #: A hundred kilograms of ore: nothing a bare pair of hands could hold.
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= base + 1e-6, f"после снятия каркаса тело несёт {carries} при пределе {base}"
    assert await _lying(session, node, ORE) > 0, "лишнее легло под ноги"
    assert await _held(session, body, "exoskeleton") == 1, "снятый каркас остаётся в руках"


async def test_the_lighter_frame_drops_what_the_heavier_carried(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One slot, two frames: putting on the weaker one is the same shrinking
    limit, and the previous frame comes off by itself (`gear.equip`)."""
    node, _, body = await _ground(session)
    heavy = await _hold(session, body, "heavy_exoskeleton", 1)
    light = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, heavy)
    await _hold(session, body, ORE, 250 / catalog.recipes.mass_of(ORE))

    await gear.equip(session, constants, catalog, body, light)

    carries = await gear.load_of(session, constants, catalog, body)
    limit = await gear.capacity(session, constants, catalog, body)
    assert carries <= limit + 1e-6, f"тело несёт {carries} при пределе {limit}"
    assert await _lying(session, node, ORE) > 0


async def test_what_is_worn_never_falls(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The shedding strips nobody: a pack on the back is on the body, not in
    the hands, and taking one thing off must not silently take another."""
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    pack = await _hold(session, body, "sturdy_backpack", 1)
    await gear.equip(session, constants, catalog, body, exo)
    await gear.equip(session, constants, catalog, body, pack)
    await _hold(session, body, ORE, 200 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    worn = await gear.equipped(session, body)
    assert "back" in worn and worn["back"].id == pack.id, "рюкзак остался на теле"
    assert await _lying(session, node, "sturdy_backpack") == 0


async def test_dressing_that_lowers_nothing_drops_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Only the act that lowered the limit sheds (D-306).

    A suit is put on over a load already past the limit -- the world puts one
    there past every door, and until the "before and after" test the dressing
    answered for an overload it had not caused: the cylinder in the hands hit
    the ground and the wearer suffocated in a suit they had just donned.
    """
    node, _, body = await _ground(session)
    suit = await _hold(session, body, "heatproof_suit", 1)
    #: Past the limit before a finger is lifted: nothing here arrived by a door.
    await _hold(session, body, ORE, 60 / catalog.recipes.mass_of(ORE))
    before = await gear.load_of(session, constants, catalog, body)
    assert before > await gear.capacity(session, constants, catalog, body)

    await gear.equip(session, constants, catalog, body, suit)

    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(before)
    assert await _lying(session, node, ORE) == 0, "надетое ничего не опустило -- ничего и не упало"


async def test_a_drained_frame_drops_what_it_was_carrying(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No charge, no lift, and no load above the bare hands either (D-306).

    The frame is powered by a cell in the hands, so taking it off is only one
    of the ways the lift goes. The tick is the sweep that answers all of them:
    it finds the wearer without a charge, whatever the road to it.
    """
    node, _, body = await _ground(session)
    base = constants[R.INVENTORY_CARRY_MASS]
    exo = await _hold(session, body, "exoskeleton", 1)
    cell = await _charged(session, body, 0.5)
    await gear.equip(session, constants, catalog, body, exo)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    assert await gear.load_of(session, constants, catalog, body) > base

    moment = datetime.now(UTC)
    await gear.wear_exoskeletons(session, constants, catalog, hours=100, now=moment)

    assert float(cell.charge) == 0, "тик выпил ячейку до дна"
    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= base + 1e-6, f"без заряда тело несёт {carries} при пределе {base}"
    assert await _lying(session, node, ORE) > 0


async def test_a_charged_frame_drops_nothing_at_the_tick(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """While the cell holds, the frame lifts and the tick is a drink, not a fall."""
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body, 500)
    await gear.equip(session, constants, catalog, body, exo)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    before = await gear.load_of(session, constants, catalog, body)

    await gear.wear_exoskeletons(session, constants, catalog, hours=1, now=datetime.now(UTC))

    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(before)
    assert await _lying(session, node, ORE) == 0


async def test_the_cell_put_down_is_the_same_lost_lift(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A cell laid on the ground lowers the limit exactly as a drained one does,
    and the sweep does not ask by which road the charge left the hands (D-306).

    Without this the fix would have been a fix of one click: wear, load, put
    the cell down, walk off with the load and pick the cell up later.
    """
    node, _, body = await _ground(session)
    base = constants[R.INVENTORY_CARRY_MASS]
    exo = await _hold(session, body, "exoskeleton", 1)
    cell = await _charged(session, body, 500)
    await gear.equip(session, constants, catalog, body, exo)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    #: Out of the hands and onto the ground, by the ordinary door.
    await storage.drop(session, constants, catalog, body, cell)
    await gear.wear_exoskeletons(session, constants, catalog, hours=1, now=datetime.now(UTC))

    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= base + 1e-6, f"аккумулятор на земле -- тело несёт {carries}"
    assert await _lying(session, node, ORE) > 0


async def test_a_full_vessel_falls_by_what_it_holds(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A vessel weighs its fill, and so it must fall by its fill (D-230).

    The falls used to read the tare alone: a plastic canister of two and a half
    kilograms holding forty sorted as the lightest thing in the hands and, once
    it did fall, was subtracted from the excess as two and a half. The ore went
    first and kept going -- the hands were emptied of everything and the load
    was still over.
    """
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, exo)
    can = await _hold(session, body, "plastic_canister", 1)
    inside = await storage.inside(session, can)
    await world.grant_item(
        session, inside, "water", amount=40 / catalog.recipes.mass_of("water"), origin="тест"
    )
    await _hold(session, body, ORE, 20 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    #: The heaviest thing in the hands was the canister, fill and all.
    assert await _held(session, body, "plastic_canister") == 0, "полная канистра ушла первой"
    assert await _lying(session, node, "plastic_canister") == 1
    #: And the ore was not emptied out after it: the excess was already paid.
    assert await _held(session, body, ORE) > 0, "руду сняли ровно на избыток"
    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= constants[R.INVENTORY_CARRY_MASS] + 1e-6


async def test_gear_heavier_than_bare_hands_takes_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What is worn is a floor the shedding cannot go under (D-306).

    A heavy frame and a suit weigh more than the bare limit together, so a
    wearer whose cell runs dry is over it with empty hands. Emptying the pocket
    would take the food, the tool and the air cylinder and leave the body over
    anyway -- every tick, for ever, and on Pyroxis that is a death by suffocation
    for a state the numbers should not allow. Nothing is taken; the log shouts.
    """
    node, _, body = await _ground(session)
    frame = await _hold(session, body, "heavy_exoskeleton", 1)
    suit = await _hold(session, body, "heatproof_suit", 1)
    await gear.equip(session, constants, catalog, body, frame)
    await gear.equip(session, constants, catalog, body, suit)
    tank = await _hold(session, body, "oxygen_tank", 1)
    worn = catalog.recipes.mass_of("heavy_exoskeleton") + catalog.recipes.mass_of("heatproof_suit")
    assert worn > constants[R.INVENTORY_CARRY_MASS], "снаряжение тяжелее голых рук"

    #: No cell at all: the frame lifts nothing, and the limit is the bare hands.
    await gear.wear_exoskeletons(session, constants, catalog, hours=1, now=datetime.now(UTC))

    assert await _held(session, body, "oxygen_tank") == 1, "баллон остался в руках"
    assert await _lying(session, node, "oxygen_tank") == 0
    assert tank.container_id == (await world.body_container(session, body)).id


async def test_the_frame_just_taken_off_stays_in_the_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What was just taken off does not fall (D-306).

    `unequip` deletes the worn record before it settles, so without a word the
    frame is no longer "worn" and becomes the heaviest thing in the hands --
    and lands on the ground, out of reach of the very hands that could not
    lift what it was lifting.
    """
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, exo)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    assert await _held(session, body, "exoskeleton") == 1, "снятый каркас остался в руках"
    assert await _lying(session, node, "exoskeleton") == 0
    assert await _lying(session, node, ORE) > 0, "упала руда, а не каркас"


async def test_undressing_answers_for_its_own_excess_and_no_more(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The overload a door found is not the door's to answer (D-306).

    The world puts things into the hands past every door -- the harvest does
    it to this day -- so a body can already be over the limit when a pack comes
    off. Taking the pack off adds its own few kilograms and takes those; the
    fifty somebody else's door let in stay where they were.
    """
    node, _, body = await _ground(session)
    pack = await _hold(session, body, "sturdy_backpack", 1)
    await gear.equip(session, constants, catalog, body, pack)
    await _hold(session, body, ORE, 80 / catalog.recipes.mass_of(ORE))
    before = await gear.load_of(session, constants, catalog, body)
    over = before - await gear.capacity(session, constants, catalog, body)
    assert over > 0, "тело уже за пределом, и не этой дверью"

    await gear.unequip(session, constants, catalog, body, "back")

    #: The pack stopped lightening the load, and that -- and only that -- fell:
    #: the felt load is where it was, and the fifty it found stayed in the hands.
    left = await gear.load_of(session, constants, catalog, body)
    assert left == pytest.approx(before, abs=0.5), f"упало лишнее: было {before}, стало {left}"
    fell = await _lying(session, node, ORE) * catalog.recipes.mass_of(ORE)
    assert fell < 15, f"с рудой ушло {fell} кг вместо облегчения рюкзака"
    assert await _held(session, body, ORE) > 0, "руки не вычищены"


async def test_the_air_a_body_breathes_never_falls(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Where there is no air, the cylinder is not what falls (D-233, D-306).

    The suit is worn and safe; the air is a bottle in the hands, and a heavy
    one. Dropped for the sake of the carry limit it is a death sentence carried
    out while the player is offline -- the tick that took it would suffocate
    them on the next pass. Nothing but air goes into a breathing cylinder, so
    the exception carries no ore.
    """
    from src.engine import oxygen
    from src.models.world import Layer, Planet

    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session,
        f"pyroxis.sky.{stamp}",
        "Пироксис",
        area_m2=1,
        planet=Planet.PYROXIS,
        layer=Layer.SPACE,
        properties={oxygen.AIRLESS: True},
    )
    node = await world.create_node(
        session,
        f"pyroxis.field.{stamp}",
        "Плато",
        area_m2=400,
        planet=Planet.PYROXIS,
        layer=Layer.PLANET,
        parent=sphere,
    )
    identity = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, identity, node)

    suit = await _hold(session, body, "heatproof_suit", 1)
    await gear.equip(session, constants, catalog, body, suit)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, exo)
    bottle = await _hold(session, body, "oxygen_tank", 1)
    inside = await storage.inside(session, bottle)
    await world.grant_item(session, inside, "oxygen", amount=10, origin="тест")
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    assert await _held(session, body, "oxygen_tank") == 1, "баллон остался в руках"
    assert await _lying(session, node, "oxygen_tank") == 0
    assert await _lying(session, node, ORE) > 0, "упала руда"


async def test_the_tick_and_a_hand_over_one_heap_lose_nothing(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep moves a living player's stacks, so it races their own hands.

    The tick sheds a drained wearer while the player puts the same heap down
    themselves. Whichever wins, matter is conserved and the hands end within
    the limit: `world.move_stack` locks and rereads each stack, so the amount
    is never counted twice.

    It does **not** hold that the loser moves nothing: the fall reads the
    pocket before it takes the body's row, so a heap that left the hands in
    between is still carried to the yard it already lies in, and `item.fell`
    names a fall that did not happen. That is a defect of the shedding itself
    (D-265, D-306) and older than this path, which is why the assertions below
    speak of matter and of the limit, and not of the journal.
    """
    _slow(monkeypatch, gear, "carried_mass")
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await gear.equip(session, constants, catalog, body, exo)
    #: No cell at all: the frame is a frame, and the sweep will find it so.
    ore = await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    body_id, node_id, ore_id = body.id, node.id, ore.id
    whole = await _held(session, body, ORE)
    await session.commit()

    async def sweep() -> None:
        async with factory() as db, db.begin():
            await gear.wear_exoskeletons(db, constants, catalog, hours=1, now=datetime.now(UTC))

    async def by_hand() -> None:
        async with factory() as db, db.begin():
            who = await db.get(Body, body_id)
            stack = await db.get(Item, ore_id)
            assert who is not None and stack is not None
            await storage.drop(db, constants, catalog, who, stack)

    outcomes = await asyncio.gather(sweep(), by_hand(), return_exceptions=True)
    raised = [one for one in outcomes if isinstance(one, Exception)]
    assert not raised, raised

    async with factory() as db:
        who = await db.get(Body, body_id)
        field = await db.get(Node, node_id)
        assert who is not None and field is not None
        assert await _held(db, who, ORE) + await _lying(db, field, ORE) == pytest.approx(whole), (
            "руда не удвоилась и не пропала"
        )
        limit = await gear.capacity(db, constants, catalog, who)
        assert await gear.load_of(db, constants, catalog, who) <= limit + 1e-6


async def test_a_frame_worn_to_nothing_drops_what_it_carried(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Wear is the third road to a fallen limit, and the one no sweep walks.

    The frame did not come off and was not drained: it ended. The tick finds
    its wearers by joining the slot to the thing, so a thing that has ceased
    to exist takes its wearer out of the join at the very moment the limit
    falls -- and the slot row, which carries no foreign key on the thing,
    outlives it and points at nothing. Nobody is left to answer, so the fall
    is answered where the thing dies (D-305, D-306).
    """
    node, _, body = await _ground(session)
    bare = constants[R.INVENTORY_CARRY_MASS]
    frame = await _hold(session, body, EXO, 1)
    await _charged(session, body, 500)
    await gear.equip(session, constants, catalog, body, frame)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    assert await gear.load_of(session, constants, catalog, body) > bare

    assert await wear.spend(session, constants, frame, 1000, cause="wearing")

    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= bare + 1e-6, f"каркас кончился, а тело несёт {carries} при пределе {bare}"
    assert await _lying(session, node, ORE) > 0, "лишнее легло под ноги"


async def test_a_frame_worn_to_nothing_leaves_the_slot_free(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The slot row goes with the thing, though nothing forces it to.

    D-305 does not ask for this: a row whose thing is gone matches no reader's
    join, and `equip` clears a stale one itself when the slot is next filled.
    It goes because the death is the one moment the world knows the thing is
    dead -- and a table holding only rows that can still be true is the
    cheaper one to read.
    """
    _, _, body = await _ground(session)
    frame = await _hold(session, body, EXO, 1)
    await _charged(session, body, 500)
    await gear.equip(session, constants, catalog, body, frame)

    await wear.spend(session, constants, frame, 1000, cause="wearing")

    left = await session.scalar(
        select(func.count()).select_from(Equipped).where(Equipped.body_id == body.id)
    )
    assert left == 0, "слот помнит вещь, которой больше нет"
    spare = await _hold(session, body, EXO, 1)
    await gear.equip(session, constants, catalog, body, spare)
    assert await gear.equipped(session, body) != {}, "освободившийся слот принял новый каркас"


async def test_the_frame_that_ended_does_not_fall_itself(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The order the whole answer hangs on: the load settles **after** the
    thing is gone.

    Settled before, the dying frame still weighs in `load_of`, inflates the
    excess by its own mass and is itself a candidate for the fall -- so more
    matter than owed goes to the ground and the journal names a thing that is
    not lying there.
    """
    node, identity, body = await _ground(session)
    frame = await _hold(session, body, EXO, 1)
    await _charged(session, body, 500)
    await gear.equip(session, constants, catalog, body, frame)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    await wear.spend(session, constants, frame, 1000, cause="wearing")

    assert await _lying(session, node, EXO) == 0, "погибшая вещь не лежит на земле"
    said = await _told(session, identity.id, EventKind.ITEM_FELL)
    assert said, "падение всё же было"
    assert all(one.payload["type_key"] != EXO for one in said), (
        "журнал назвал упавшим то, чего на земле нет"
    )


async def test_a_pack_worn_to_nothing_drops_what_it_lightened(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A pack raises no limit -- it lightens the load -- and ending it stops
    lightening. Nothing was moved and the hands are over the limit all the same.
    """
    node, _, body = await _ground(session)
    bare = constants[R.INVENTORY_CARRY_MASS]
    spec = constants[R.INVENTORY_PACK][catalog.recipes.resolve(BACKPACK)]
    room, factor = float(spec["capacity"]), float(spec["factor"])
    relief, tare = room * (1 - factor), catalog.recipes.mass_of(BACKPACK)
    #: A tripwire, not a given: the test only says anything while the pack
    #: lightens by more than it weighs. The day a pack grows heavier than its
    #: own relief, this says so instead of passing on an empty scenario.
    assert relief > tare + 1, "предусловие сценария: облегчение больше собственной тары"

    pack = await _hold(session, body, BACKPACK, 1)
    await gear.equip(session, constants, catalog, body, pack)
    #: Filled to the pack's ceiling: within the limit while it is on the back,
    #: over it the moment the relief goes.
    await _hold(session, body, ORE, (bare + relief - tare - 1) / catalog.recipes.mass_of(ORE))
    assert await gear.load_of(session, constants, catalog, body) <= bare + 1e-6

    await wear.spend(session, constants, pack, 1000, cause="wearing")

    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= bare + 1e-6, f"рюкзак кончился, а тело несёт {carries} при пределе {bare}"
    assert await _lying(session, node, ORE) > 0, "то, что легчил рюкзак, легло под ноги"


async def test_a_frame_ending_and_a_hand_over_one_heap_lose_nothing(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wear moves a living player's stacks too, so it races their own hands.

    The daily wear finishes the frame in the tick's session while the player
    puts the same heap down in theirs. The order is the one `_fall` already
    takes -- the body's row, then the stacks it moves -- so neither side moves
    what the other has taken, and neither waits on the other for ever.
    """
    _slow(monkeypatch, gear, "carried_mass")
    node, _, body = await _ground(session)
    frame = await _hold(session, body, EXO, 1)
    await _charged(session, body, 500)
    await gear.equip(session, constants, catalog, body, frame)
    ore = await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    body_id, node_id, ore_id, frame_id = body.id, node.id, ore.id, frame.id
    whole = await _held(session, body, ORE)
    await session.commit()

    async def ends() -> None:
        async with factory() as db, db.begin():
            worn = await db.get(Item, frame_id)
            assert worn is not None
            await wear.spend(db, constants, worn, 1000, cause="wearing")

    async def by_hand() -> None:
        async with factory() as db, db.begin():
            who = await db.get(Body, body_id)
            stack = await db.get(Item, ore_id)
            assert who is not None and stack is not None
            await storage.drop(db, constants, catalog, who, stack)

    outcomes = await asyncio.gather(ends(), by_hand(), return_exceptions=True)
    raised = [one for one in outcomes if isinstance(one, Exception)]
    assert not raised, raised

    async with factory() as db:
        who = await db.get(Body, body_id)
        field = await db.get(Node, node_id)
        assert who is not None and field is not None
        assert await _held(db, who, ORE) + await _lying(db, field, ORE) == pytest.approx(whole), (
            "руда не удвоилась и не пропала"
        )
        limit = await gear.capacity(db, constants, catalog, who)
        assert await gear.load_of(db, constants, catalog, who) <= limit + 1e-6


async def test_the_daily_wear_and_the_sweep_take_the_bodies_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """Two sweeps over the same wearers, in transactions of their own.

    Tick steps do not share a transaction (`tick.tick_step`), so the daily
    wear runs beside the charge sweep -- and both now take body rows: the
    sweep to shed a drained wearer, the wear to shed one whose frame ended.
    Taken in two orders that is a deadlock, so both take them in id order.

    **A smoke test, and it says so rather than pretending.** A deadlock needs
    the two to interleave at one particular instant, and no delay makes that a
    certainty; drop the `order_by` and this may well still pass. What it does
    hold is the rest: the two sweeps over the same bodies raise nothing,
    conserve the matter and leave every pair of hands within its limit.
    """
    floor = constants[R.QUALITY_SCALE].min
    ids = []
    for _ in range(4):
        _, _, body = await _ground(session)
        frame = await _hold(session, body, EXO, 1)
        await _charged(session, body, 500)
        await gear.equip(session, constants, catalog, body, frame)
        await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
        #: On its last day: the daily step finishes it and settles the load.
        frame.condition = Decimal(str(floor + 0.01))
        ids.append(body.id)
    await session.flush()
    whole = sum([await _held(session, await session.get(Body, one), ORE) for one in ids])
    await session.commit()

    async def daily() -> None:
        async with factory() as db, db.begin():
            await wear.daily_gear_wear(db, constants, catalog)

    async def sweep() -> None:
        async with factory() as db, db.begin():
            await gear.wear_exoskeletons(db, constants, catalog, hours=1, now=datetime.now(UTC))

    outcomes = await asyncio.gather(daily(), sweep(), return_exceptions=True)
    raised = [one for one in outcomes if isinstance(one, Exception)]
    assert not raised, raised

    async with factory() as db:
        bare = constants[R.INVENTORY_CARRY_MASS]
        for one in ids:
            who = await db.get(Body, one)
            assert who is not None
            carries = await gear.load_of(db, constants, catalog, who)
            assert carries <= bare + 1e-6, f"тело несёт {carries} при пределе {bare}"
        lay = await db.scalar(
            select(func.coalesce(func.sum(Item.amount), 0)).where(Item.type_key == ORE)
        )
        assert amount_float(int(lay or 0)) == pytest.approx(whole), "руда не удвоилась и не пропала"


async def test_a_suit_that_lifts_nothing_drops_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Only the difference a death makes falls, never an overload it found.

    A suit raises no limit and lightens no load: ending, it takes away its own
    weight and nothing else, so the hands are lighter than before and there is
    nothing for this door to answer for. The overload it found came in by
    another door -- the harvest goes into the hands past every check (OQ-110)
    -- and that door answers for it, not this one (D-306).
    """
    node, _, body = await _ground(session)
    bare = constants[R.INVENTORY_CARRY_MASS]
    suit = await _hold(session, body, SUIT, 1)
    await gear.equip(session, constants, catalog, body, suit)
    #: Past every door, the way a harvest arrives: the hands are already over.
    await _hold(session, body, ORE, 2 * bare / catalog.recipes.mass_of(ORE))
    over = await gear.load_of(session, constants, catalog, body)
    assert over > bare, "сценарий: тело уже за пределом до смерти вещи"

    assert await wear.spend(session, constants, suit, 1000, cause="wearing")

    assert await _lying(session, node, ORE) == 0, "чужой перегруз этой двери не принадлежит"
    left = await gear.load_of(session, constants, catalog, body)
    assert left == pytest.approx(over - catalog.recipes.mass_of(SUIT)), (
        "ушла ровно масса самой вещи"
    )


async def test_a_stale_slot_row_settles_nobody(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A slot row outlives the thing leaving the hands, on purpose (D-305).

    So the row alone may not be believed: the wearer is whose pocket the thing
    lies in. Wearing out a pack the world has already taken out of these hands
    must not drop this body's ore -- it stopped lightening the moment it left,
    and that fall, if it was owed, was owed then and by that door.
    """
    node, _, body = await _ground(session)
    pack = await _hold(session, body, BACKPACK, 1)
    await gear.equip(session, constants, catalog, body, pack)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    #: What the world takes goes straight, past `move_stack` and its refusal --
    #: the way a dead body's things reach the yard (`death.py`).
    yard = await world.node_container(session, node)
    pack.container_id = yard.id
    await session.flush()
    held = await _held(session, body, ORE)

    assert await wear.spend(session, constants, pack, 1000, cause="wearing")

    assert await _held(session, body, ORE) == pytest.approx(held), (
        "рюкзак, которого нет в руках, не роняет чужую ношу"
    )
