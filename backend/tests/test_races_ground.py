# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on the same ground.

One of the race files (see `test_races.py` for the family's method): here the
contested thing is the place itself -- a field the eruption burns while
somebody carries a sack out of it, a face closed by a death and by the moving
ground in the same second, a ruin room two scouts open at once, a node's
properties two writers stamp together, a sown strip two harvests reap at
once. The invariant must hold whichever side wins, and neither side may die
of a deadlock.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import current, current_catalog
from src.engine import travel, world
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node, Surface, Vein

ORE = "iron_ore"


async def test_the_eruption_does_not_burn_what_was_carried_out(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window before an eruption is the whole licence for the burning
    (D-197, P6), and somebody using it must not be robbed by the fire anyway.

    The carry-out goes **first** and holds its row: the fire waits for the row
    to be taken, so it meets a sack already moving.

    With the lock the fire waits at that row, rereads it after the commit and
    finds the sack in a pocket -- not in the node -- so there is nothing here
    to burn. Without it the fire reads the sack where it still was, queues its
    delete behind the same row, and takes it **out of the player's hands** the
    moment the carry-out lands: the one place it was safe.
    """
    from src.engine import plates, storage
    from src.models.world import Layer, Planet

    #: **A handshake, not a pause.** The window this test needs is the one
    #: between taking the row and committing, and `_slow` on `pick` does not
    #: open it: the pause lands after `pick` returns, while the checks that
    #: run *before* `move_stack` reaches the row -- presence, the node, the
    #: door, the relic, the carry limit -- take longer than any head start the
    #: fire can be given by guesswork. The fire then took the row first, burnt
    #: the sack and the carry-out found nothing to move. So the fire waits for
    #: the row to be taken instead of waiting a number of milliseconds.
    took_the_row = asyncio.Event()
    carrying = world.move_stack

    async def held(*args, **kwargs):
        moved = await carrying(*args, **kwargs)
        took_the_row.set()
        await asyncio.sleep(0.2)
        return moved

    monkeypatch.setattr(world, "move_stack", held)
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session,
        "pyroxis",
        "Пироксис",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.SPACE,
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    sack = await world.grant_item(
        session,
        await world.node_container(session, field),
        ORE,
        amount=10,
        quality=60,
        origin="тест",
    )
    field_id, body_id, sack_id = field.id, body.id, sack.id
    await session.commit()

    async def erupt() -> None:
        #: The carry-out is inside its transaction and holding the row -- not
        #: probably, but by construction.
        await took_the_row.wait()
        async with factory() as db, db.begin():
            place = await db.get(Node, field_id)
            assert place is not None
            burnt = await plates._burn(db, [place])
            assert burnt == 0, "огонь сжёг то, что уже уносили"

    async def carry() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            thing = await db.get(Item, sack_id)
            assert mine is not None and thing is not None
            await storage.pick(db, current(), current_catalog(), mine, thing)

    outcome = await asyncio.gather(erupt(), carry(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        left = await db.get(Item, sack_id)
        assert left is not None, "вынесенное сгорело в руках"
        pocket = await world.body_container(db, await db.get(Body, body_id))
        assert left.container_id == pocket.id, "вынесенное сгорело в руках"


async def test_the_eruption_does_not_burn_what_was_taken_out_of_a_chest(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same robbery as above, through the door of a chest.

    A chest burns with what is in it, or its goods would outlive the place they
    lay in. But its inside is a second container, and a lock on the things
    lying on the ground says nothing about it: `storage.take` locks the thing,
    not the chest. Without the lock **inside** the box the delete queues behind
    that take and lands the moment it commits -- out of the player's hands.

    On the wild ground of Pyroxis anybody may open anybody's chest
    (`station.may_build` gives the wild to everyone), so this is not a corner:
    it is the ordinary way a sack leaves a field before an eruption.
    """
    from src.engine import plates, storage
    from src.models.world import Layer, Planet

    _slow(monkeypatch, storage, "take")
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, "pyroxis", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    chest = await world.grant_item(
        session,
        await world.node_container(session, field),
        "chest",
        quality=60,
        origin="тест",
    )
    box = await storage.inside(session, chest)
    sack = await world.grant_item(session, box, ORE, amount=10, quality=60, origin="тест")
    field_id, body_id, chest_id, sack_id = field.id, body.id, chest.id, sack.id
    await session.commit()

    async def erupt() -> None:
        await asyncio.sleep(0.05)
        async with factory() as db, db.begin():
            place = await db.get(Node, field_id)
            assert place is not None
            await plates._burn(db, [place])

    async def carry() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            crate = await db.get(Item, chest_id)
            thing = await db.get(Item, sack_id)
            assert mine is not None and crate is not None and thing is not None
            await storage.take(db, current(), current_catalog(), mine, crate, thing)

    outcome = await asyncio.gather(erupt(), carry(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        left = await db.get(Item, sack_id)
        assert left is not None, "вынесенное из сундука сгорело в руках"
        pocket = await world.body_container(db, await db.get(Body, body_id))
        assert left.container_id == pocket.id, "вынесенное из сундука сгорело в руках"
        #: And the chest itself is gone with the field: what stayed in it burned.
        assert await db.get(Item, chest_id) is None


async def test_death_and_leaving_one_face_do_not_both_carry_the_haul(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One face, two ways out of it, and one haul.

    A rift takes the miner while the ground moves the vein: `death.die` closes
    the face and leaves the ore in the node, `plates._close_faces` closes it
    and carries the ore into a pocket. Both are right on their own. Together
    they are two transactions over one haul, and the session row is the gate
    both take **first**: whoever wins it plays the whole story out while the
    loser waits at it holding nothing the winner could want.

    What goes red without the gate is the **order**: one side holds the pocket
    and waits for the session, the other holds the session and waits for the
    pocket (`leave` carries the haul into it), and the database kills one of
    them. So the assertion that actually catches it is the one about neither
    call raising -- the count of the ore is the invariant that must hold
    afterwards, not the thing the lock buys.
    """
    from src.engine import death, mining, plates
    from src.engine.mining import face as mining_face
    from src.models.mining import MiningSession, Pace, SessionState
    from src.models.world import Layer, Planet

    #: **A handshake, not a pause**, like the carry-out tests above: the
    #: eruption must arrive while the death holds the gate -- by construction,
    #: not after a guessed number of milliseconds. A cold connection costs
    #: some seventy milliseconds to open, so a guessed head start loses often
    #: enough for the eruption to win the gate, carry the haul into the pocket
    #: and hand it to the salvage -- a legal outcome, but not the branch this
    #: test pins. `session_container` is the first thing `abandon` asks after
    #: taking the session row, and `face` binds it into its own globals
    #: (D-252 split), so the patch lands where the closers look it up while
    #: the fixture's call below goes through the package door to the original.
    holds_the_gate = asyncio.Event()
    asking = mining_face.session_container

    async def held(*args, **kwargs):
        container = await asking(*args, **kwargs)
        holds_the_gate.set()
        await asyncio.sleep(0.2)
        return container

    monkeypatch.setattr(mining_face, "session_container", held)
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, "pyroxis", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    near = await world.create_node(
        session,
        f"pyroxis.{stamp}.near",
        "Соседнее поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    await travel.connect(session, field, near, base_seconds=900, surface=Surface.TRAIL)
    vein = await world.create_vein(session, field, ORE, richness=70, remaining=1000)
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        ORE,
        amount=9,
        quality=60,
        origin="тест",
    )
    #: And the long branch of `die`: a pocket with something in it, and a heap
    #: of the same goods already lying in the node. Then the death, past the
    #: gate, locks the pocket and lays its salvage into that heap --
    #: `stack_up` takes the node's things under a lock -- the very rows the
    #: eruption's `leave` would carry the haul into. With an empty pocket the
    #: death skips all of that and the test walks a branch where the order
    #: cannot be wrong.
    await world.grant_item(
        session,
        await world.body_container(session, body),
        ORE,
        amount=4,
        quality=60,
        origin="тест",
    )
    await world.grant_item(
        session,
        await world.node_container(session, field),
        ORE,
        amount=2,
        quality=60,
        origin="тест",
    )
    field_id, body_id, vein_id, face_id = field.id, body.id, vein.id, face.id
    await session.commit()

    async def dies() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            assert mine is not None
            await death.die(db, current(), mine, cause="rift")

    async def ground_moves() -> None:
        #: The death is past its gate and inside the face -- not probably,
        #: but by construction.
        await holds_the_gate.wait()
        async with factory() as db, db.begin():
            rock = await db.get(Vein, vein_id)
            assert rock is not None
            await plates._close_faces(db, current(), rock, now=datetime.now(UTC))

    outcome = await asyncio.gather(dies(), ground_moves(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        #: Nine units were mined, and nine units exist -- wherever they ended up.
        here = await world.contents(
            db, await world.node_container(db, await db.get(Node, field_id))
        )
        pocket = await world.contents(
            db, await world.body_container(db, await db.get(Body, body_id))
        )
        total = sum(
            float(thing.amount) / 1000 for thing in [*here, *pocket] if thing.type_key == ORE
        )
        #: Nine in the face, two lying in the node, and of the four in the
        #: pocket whatever the salvage roll kept -- never more than fifteen and
        #: never less than the eleven that were never on the body.
        assert 11 <= total <= 15, f"добытое размножилось или пропало: {total}"


async def test_death_and_the_burning_ground_close_one_face_without_a_deadlock(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second arm of the same ABBA: the fire against the death.

    `plates.erupted` burns the yards and only later closes the faces, while a
    death lays what the face and the pocket held into those same yards. In the
    old order -- the yards first, the session rows only inside `_close_faces` --
    the job held the burning heaps and waited for the gate the death held, and
    the death held its gate and waited for a heap the fire held. The pre-lock
    of the veins and the sessions **before the first flame** (mirrored here
    from `erupted`) makes the gate the meeting point: whoever comes second
    waits at it holding nothing the winner wants.

    The handshake rides on `stack_up`: the first heap the death lays into the
    yard comes after its gate, so the fire that waits for it arrives with the
    death provably past the gate and holding the heap -- by construction, not
    after a guessed number of milliseconds. On the old order of `die` the same
    handshake fired from the salvage, **before** the gate, and the test died
    of the very ABBA it now pins shut.
    """
    from src.engine import death, mining, plates
    from src.models.mining import MiningSession, Pace, SessionState
    from src.models.world import Layer, Planet

    laid_a_heap = asyncio.Event()
    laying = world.stack_up

    async def held(*args, **kwargs):
        heap = await laying(*args, **kwargs)
        laid_a_heap.set()
        await asyncio.sleep(0.2)
        return heap

    monkeypatch.setattr(world, "stack_up", held)
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, "pyroxis", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    vein = await world.create_vein(session, field, ORE, richness=70, remaining=1000)
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        ORE,
        amount=9,
        quality=60,
        origin="тест",
    )
    #: A pocket with goods and a heap of the same ore in the yard: the death
    #: then lays both hauls into the field -- the twins `stack_up` locks are
    #: the very rows the fire takes first.
    await world.grant_item(
        session,
        await world.body_container(session, body),
        ORE,
        amount=4,
        quality=60,
        origin="тест",
    )
    await world.grant_item(
        session,
        await world.node_container(session, field),
        ORE,
        amount=2,
        quality=60,
        origin="тест",
    )
    field_id, body_id, vein_id, face_id = field.id, body.id, vein.id, face.id
    await session.commit()

    async def dies() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            assert mine is not None
            await death.die(db, current(), mine, cause="rift")

    async def ground_burns() -> None:
        #: The death is past its gate and laying heaps -- not probably, but
        #: by construction.
        await laid_a_heap.wait()
        async with factory() as db, db.begin():
            place = await db.get(Node, field_id)
            rock = await db.get(Vein, vein_id)
            assert place is not None and rock is not None
            #: The eruption's own order, mirrored from `plates.erupted`: the
            #: veins, then the sessions at the faces -- the gate -- and only
            #: then the fire and the closing.
            await db.execute(
                select(Vein).where(Vein.node_id == place.id).order_by(Vein.id).with_for_update()
            )
            await db.execute(
                select(MiningSession)
                .join(Vein, Vein.id == MiningSession.vein_id)
                .where(Vein.node_id == place.id, MiningSession.state == SessionState.ACTIVE)
                .order_by(MiningSession.id)
                .with_for_update(of=MiningSession)
            )
            await plates._burn(db, [place])
            await plates._close_faces(db, current(), rock, now=datetime.now(UTC))

    outcome = await asyncio.gather(dies(), ground_burns(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        here = await world.contents(
            db, await world.node_container(db, await db.get(Node, field_id))
        )
        pocket = await world.contents(
            db, await world.body_container(db, await db.get(Body, body_id))
        )
        total = sum(
            float(thing.amount) / 1000 for thing in [*here, *pocket] if thing.type_key == ORE
        )
        #: The death won the gate, so everything it laid down was lying under
        #: the open sky when the fire came -- and what lies in a shaken field
        #: burns with it, to the last unit (D-197).
        assert total == 0, f"уложенное в поле должно сгореть с полем: {total}"


async def test_two_scouts_do_not_open_one_room_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A city of the Forerunners is worked out like a vein (D-232), and the
    count of what is open is its remainder.

    Two scouts come back in the same second, in different workers. Without the
    lock on the city's row both read "nothing opened yet", both write one, and
    one of the two rooms is free -- the city outlives its own stock.
    """
    from src.engine import ruins
    from src.models.world import Layer, Planet

    _slow(monkeypatch, ruins, "_fill")
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session,
        f"aurora.{stamp}.sphere",
        "Аврора",
        planet=Planet.AURORA,
        area_m2=1,
        layer=Layer.SPACE,
    )
    city = await world.create_node(
        session,
        f"aurora.{stamp}",
        "Город Предтеч",
        planet=Planet.AURORA,
        area_m2=1,
        layer=Layer.PLANET,
        parent=sphere,
        properties={ruins.PRECURSOR: True, ruins.KIND: "столица"},
    )
    hall = await world.create_node(
        session,
        f"aurora.{stamp}.hall",
        "Зал",
        planet=Planet.AURORA,
        area_m2=600,
        layer=Layer.CITY,
        parent=city,
        properties={ruins.PRECURSOR: True, ruins.DEPTH: 1},
    )
    city_id, hall_id = city.id, hall.id
    await session.commit()

    async def open_one(seed: int) -> None:
        async with factory() as db, db.begin():
            where = await db.get(Node, hall_id)
            assert where is not None
            await ruins.open_room(db, constants, random.Random(seed), where, who=None)

    await asyncio.gather(open_one(1), open_one(2))

    async with factory() as db:
        again = await db.get(Node, city_id)
        assert again is not None
        assert ruins.opened(again) == 2, "две вскрытые двери — две, а не одна"


async def test_two_marks_on_one_node_do_not_erase_each_other(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """`Node.properties` is one JSONB dict rewritten whole (review of D-238).

    A founder stamps the gate while a scout's return bumps the counter. Each
    builds its new dict from what it read at the start; without the reread
    under the row lock (`props._held`) the slower writer's snapshot is stale
    and its rewrite silently erases the faster one's key.
    """
    from src.engine import props
    from src.engine.explore import FOUND_HERE

    node = await world.create_node(
        session, f"terra.marks.{uuid.uuid4().hex[:6]}", "Перекрёсток", area_m2=100
    )
    node_id = node.id
    await session.commit()

    async def flag() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Node, node_id)
            assert own is not None
            #: The stale snapshot is loaded by the `get` above; the pause lets
            #: the counter commit inside the window a plain rewrite loses.
            await asyncio.sleep(0.2)
            await props.stamp(db, own, {travel.EXIT: True})

    async def count() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Node, node_id)
            assert own is not None
            await props.bump(db, own, FOUND_HERE)

    await asyncio.gather(flag(), count())

    async with factory() as db:
        again = await db.get(Node, node_id)
        assert again is not None
        held = again.properties or {}
        assert held.get(travel.EXIT) is True, "печать ворот стёрта счётчиком разведки"
        assert int(held.get(FOUND_HERE, 0)) == 1, "счётчик разведки стёрт печатью ворот"


async def test_two_harvests_of_one_strip_reap_it_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strip's state, its fertility and the sown fund are remainders
    (CLAUDE.md).

    Two harvests of one sown field in the same second: without the plot's
    row lock both read SOWN, both hand out the crop and the seed fund, and
    both write fertility from the same read value -- the harvest doubles out
    of thin air. The lock lives in the command's door
    (`api.commands.farm._plot`), so the race goes through it, not around it.

    The body's own lock (`_alive`) is bypassed on purpose: through the full
    command path it happens to serialise two sockets of one farmer before
    they reach the plot, and the remainder would then be guarded by a
    coincidence of somebody else's lock -- exactly what the quality bar
    forbids. The plot's door must hold on its own, body lock or no body
    lock, and this test pins that and nothing wider.
    """
    from src.api.commands.farm import _plot
    from src.constants import registry as R
    from src.engine import breed, farm
    from src.models.farm import PlotState
    from src.units import PERCENT
    from src.units import amount as to_units

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.strip.{stamp}",
        "Хутор",
        area_m2=200,
        properties={"water": "river", "fertility": 55},
    )
    who = await world.create_identity(session, f"Фермер-{stamp}")
    body = await world.print_body(session, who, node)
    node.owner_identity_id = who.id
    await session.flush()

    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    plot.state = PlotState.PLOWED
    await session.flush()
    #: Exactly the sowing norm, so the sack is spent whole and the pocket is
    #: empty when the race starts: whatever lies in it afterwards, the
    #: harvests put there.
    cultivar = await breed.landrace(session, catalog, "spelt")
    pocket = await world.body_container(session, body)
    seeds = await breed.seed_lot(
        session, catalog, pocket.id, cultivar, constants[R.FARM_SEED_RATE] * 10.0, PERCENT
    )
    await farm.sow(session, constants, catalog, body, plot, seeds)
    plant = catalog.plants.by_id("spelt")
    #: Every round done, so the crop is a full one and provably nonzero.
    plot.care_credits = int(plant.cycle_days)
    await session.flush()
    ripeness = farm.ripe_at(constants, plot, plant)
    plot_id, body_id, pocket_id = plot.id, body.id, pocket.id
    await session.commit()

    #: The pause goes between the state check and the writes: the pocket is
    #: the first thing harvest asks for once the checks have passed.
    _slow(monkeypatch, world, "body_container")

    async def reap() -> float:
        async with factory() as db, db.begin():
            own = await db.get(Body, body_id)
            assert own is not None
            with contextlib.suppress(farm.WrongState):
                strip = await _plot(db, {"plot": str(plot_id)})
                return await farm.harvest(
                    db, current(), current_catalog(), own, strip, now=ripeness
                )
            return 0.0

    taken = await asyncio.gather(reap(), reap())

    got = [one for one in taken if one]
    assert len(got) == 1, f"обе жатвы прошли по одному посеву: {taken}"

    async with factory() as db:
        things = (
            (await db.execute(select(Item).where(Item.container_id == pocket_id))).scalars().all()
        )
        reaped = sum(int(thing.amount) for thing in things if thing.type_key == plant.gives)
        fund = sum(int(thing.amount) for thing in things if thing.type_key == plant.seed)
        assert reaped == to_units(got[0]), "урожай в кармане — ровно одна жатва"
        #: The engine's seed formula verbatim (D-257): full care and full
        #: strength multiply by one, so only the soil share is left to mirror.
        soil = min(55 / plant.requires.fertility, constants[R.FARM_SOIL_SHARE_CAP] / PERCENT)
        assert fund == to_units(
            constants[R.FARM_SEED_RATE] * 10.0 * constants[R.FARM_SEED_RETURN] * soil
        ), "семенной фонд отложен один раз"


async def test_two_bumps_of_one_counter_lose_neither(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A counter in the properties map is a remainder like ore in a vein:
    two increments from the same snapshot would both write the same number."""
    from src.engine import props
    from src.engine.explore import FOUND_HERE

    node = await world.create_node(
        session, f"terra.count.{uuid.uuid4().hex[:6]}", "Развилка", area_m2=100
    )
    node_id = node.id
    await session.commit()

    async def one(delay: float) -> None:
        async with factory() as db, db.begin():
            own = await db.get(Node, node_id)
            assert own is not None
            await asyncio.sleep(delay)
            await props.bump(db, own, FOUND_HERE)

    await asyncio.gather(one(0.0), one(0.1))

    async with factory() as db:
        again = await db.get(Node, node_id)
        assert again is not None
        assert int((again.properties or {}).get(FOUND_HERE, 0)) == 2, (
            "две разведки — два, а не одно"
        )
