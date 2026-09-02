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
from src.engine import ledger, travel, world
from src.models.city import City
from src.models.event import Event, EventKind
from src.models.identity import Body, Identity
from src.models.inventory import Item
from src.models.world import Node, Surface, Vein
from src.units import money

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


async def test_two_sessions_grow_one_landrace(
    factory: async_sessionmaker[AsyncSession],
    catalog,
) -> None:
    """A culture's base cultivar is created lazily at first need (D-057), and
    two first needs come in the same second: two foragers take one culture's
    seeds in different workers. Both select nothing, both insert -- and
    without the partial unique index (`uq_variety_authorless`) the culture
    got two authorless base rows, after which `.first()` without ORDER BY
    answered each caller with whichever row the planner met first: seed lots
    of "the same" cultivar stopped stacking and sown plots pointed apart.

    The loser must not die of the refusal either: the violation is caught
    under a savepoint (`breed._create_once`) and the winner's row is reread.
    The first session holds its insert uncommitted for the window; the second
    then provably selects nothing and queues its insert at the index until
    the winner commits.
    """
    from src.engine import breed
    from src.models.plant import Variety

    planted = asyncio.Event()

    async def first() -> uuid.UUID:
        async with factory() as db, db.begin():
            grown = await breed.landrace(db, catalog, "spelt")
            planted.set()
            await asyncio.sleep(0.2)
            return grown.id

    async def second() -> uuid.UUID:
        await planted.wait()
        async with factory() as db, db.begin():
            grown = await breed.landrace(db, catalog, "spelt")
            return grown.id

    ids = await asyncio.gather(first(), second())
    assert ids[0] == ids[1], f"две сессии — один базовый сорт: {ids}"

    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(Variety).where(
                        Variety.culture_id == "spelt",
                        Variety.author_identity_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, "базовый сорт удвоился"


async def test_two_sessions_grow_one_wild_ancestor(
    factory: async_sessionmaker[AsyncSession],
    constants,
    catalog,
) -> None:
    """The wild ancestor (D-260) is created by the same select-then-insert as
    the base cultivar, and doubles the same way -- two `forage.take` of one
    culture's wild seeds in one second. The same construction as the landrace
    test above: the winner holds its insert uncommitted through the window,
    the loser queues at the index and rereads the winner's row.
    """
    from src.engine import breed
    from src.models.plant import Variety

    planted = asyncio.Event()

    async def first() -> uuid.UUID:
        async with factory() as db, db.begin():
            grown = await breed.wild_ancestor(db, constants, catalog, "spelt")
            planted.set()
            await asyncio.sleep(0.2)
            return grown.id

    async def second() -> uuid.UUID:
        await planted.wait()
        async with factory() as db, db.begin():
            grown = await breed.wild_ancestor(db, constants, catalog, "spelt")
            return grown.id

    ids = await asyncio.gather(first(), second())
    assert ids[0] == ids[1], f"две сессии — один дикий предок: {ids}"

    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(Variety).where(
                        Variety.culture_id == "spelt",
                        Variety.author_identity_id.is_(None),
                        Variety.wild.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, "дикий предок удвоился"


async def test_two_fertilizings_of_one_strip_both_land(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
) -> None:
    """Fertility is a remainder and takes both writes, losing neither (D-264).

    Two rounds of fertilizing race through the command door: the plot lock in
    `api.commands.farm._plot` serialises them, so the second reads the
    fertility the first wrote -- 40 becomes 60, not 50 twice. The dose is
    debited from the pocket both times.
    """
    from src.api.commands.farm import _plot
    from src.constants import registry as R
    from src.engine import farm

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.dung.{stamp}",
        "Хутор",
        area_m2=200,
        properties={"water": "river", "fertility": 40},
    )
    who = await world.create_identity(session, f"Фермер-{stamp}")
    body = await world.print_body(session, who, node)
    node.owner_identity_id = who.id
    await session.flush()

    plot = await farm.mark(session, current(), body, name="тощая", area=10)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "compost", amount=20, origin="тест")
    plot_id, body_id, pocket_id = plot.id, body.id, pocket.id
    await session.commit()

    async def spread() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Body, body_id)
            assert own is not None
            strip = await _plot(db, {"plot": str(plot_id)})
            await farm.fertilize(db, current(), own, strip, "compost")

    outcome = await asyncio.gather(spread(), spread(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        from src.models.farm import Plot

        strip = await db.get(Plot, plot_id)
        assert float(strip.fertility) == pytest.approx(
            40 + 2 * current()[R.FARM_COMPOST_RECOVERY]
        ), "оба внесения легли в землю"
        left = sum(
            int(thing.amount)
            for thing in (
                (await db.execute(select(Item).where(Item.container_id == pocket_id))).scalars()
            )
            if thing.type_key == "compost"
        )
        from src.units import amount as to_units

        assert left == to_units(20) - 2 * to_units(current()[R.FARM_FERTILIZER_PER_M2] * 10), (
            "норма списана дважды"
        )


async def _a_city_location(session: AsyncSession, holder_name: str):
    """A city's own location standing in somebody's name: the state D-281 undoes.

    Not a plot -- no `plot` mark on it -- and that is the whole point: this is
    the core the city works from, handed out by an allotment that asked too
    little.
    """
    from src.engine import city as town
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET, parent=planet
    )
    core = await world.create_node(
        session, f"terra.city.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    city = await town.found(session, current_catalog(), delegate, "Столица")
    core.owner_city_id = city.id
    holder = await world.create_identity(session, f"{holder_name}-{stamp}")
    await world.print_body(session, holder, core)
    core.owner_identity_id = holder.id
    await session.flush()
    return city, core, holder


async def test_the_buyer_never_pays_for_a_location_the_city_takes_back(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """The purchase of a deed and the city taking its location back, at once.

    The catch-up runs against a live world -- the deploy raises the new backend
    beside the old one -- so the pass that returns the city's core meets people
    still playing. The one thing that must not happen is the one that costs
    real money: the buyer pays for the paper and loses the node in the same
    second.

    **The two never meet, and that is the answer rather than an ordering.**
    There is no window to widen here: `buy_deed` refuses a paper written for a
    city location before it looks at anybody's purse (D-281), so whichever
    coroutine wins the row, the money has not moved. A test of the guard from
    the side the guard exists for -- the second session running at the same
    moment -- and the reason no lock is enough on its own.
    """
    from src.engine import city as town
    from src.engine import estate
    from src.models.estate import Deed
    from src.models.ledger import AccountKind, PostingReason

    city, core, holder = await _a_city_location(session, "Захвативший")
    deed = await estate.issue_deed(session, core, holder.id)
    deed.sale_price = money(100)
    buyer = await world.create_identity(session, f"Покупатель-{uuid.uuid4().hex[:6]}")
    purse = await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=purse.id, amount=money(500)
    )
    node_id, city_id, deed_id, buyer_id = core.id, city.id, deed.id, buyer.id
    await session.commit()

    async def purchase() -> None:
        async with factory() as db, db.begin():
            paper = await db.get(Deed, deed_id)
            if paper is None:
                return
            who = await db.get(Identity, buyer_id)
            await estate.buy_deed(db, who, paper)

    async def retake() -> None:
        async with factory() as db, db.begin():
            node = await db.get(Node, node_id)
            await town.reclaim(db, node, await db.get(City, city_id))

    outcome = await asyncio.gather(purchase(), retake(), return_exceptions=True)
    unexpected = [
        one
        for one in outcome
        if isinstance(one, BaseException) and not isinstance(one, estate.NotForSale)
    ]
    assert not unexpected, outcome

    async with factory() as db:
        assert await ledger.balance(db, purse.id) == money(500), "покупатель ни за что не заплатил"
        node = await db.get(Node, node_id)
        assert node.owner_identity_id is None, "локация вернулась городу"


async def test_two_deploys_take_the_same_location_back_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two catch-up passes over one node: the title comes back once.

    A deploy retried, or two of them overlapping, runs the repair twice at the
    same moment. `reclaim` looks at the row it already holds before it locks --
    that look is what keeps a whole world's nodes from being locked at every
    deploy -- so the second pass reads a node still standing in somebody's
    name. Without the locked reread after the lock it would hand over a node
    already handed over and tell the former holder twice about one loss.
    """
    from src.engine import city as town

    _slow(monkeypatch, world, "hand_over")
    city, core, _ = await _a_city_location(session, "Захвативший")
    node_id, city_id = core.id, city.id
    await session.commit()

    async def catch_up() -> bool:
        async with factory() as db, db.begin():
            node = await db.get(Node, node_id)
            return await town.reclaim(db, node, await db.get(City, city_id))

    outcome = await asyncio.gather(catch_up(), catch_up(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome
    assert sorted(outcome) == [False, True], f"забрать можно один раз: {outcome}"

    async with factory() as db:
        told = (
            (await db.execute(select(Event).where(Event.kind == EventKind.LAND_RECLAIMED.value)))
            .scalars()
            .all()
        )
        assert len(told) == 1, "об одной потере говорят один раз"
        node = await db.get(Node, node_id)
        assert node.owner_identity_id is None
