# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once, and one of them is the fire.

Split off from `test_races_ground.py` (see `test_races.py` for the family's
method), which had grown past the length the quality bar allows one file. The
ground's other races -- a ruin room two scouts open at once, a node's
properties two writers stamp together, a sown strip two harvests reap -- stayed
there; what an eruption burns lives here.

The fire is a racer unlike the rest: it takes matter **out** of the world
(D-197), so whoever is mid-carry does not lose a contest, they lose the goods.
Both tests below are the same robbery from two doors -- off the ground, and out
of a chest standing on it -- and both are handshakes rather than head starts:
the fire waits for the carry-out to be holding its row, because a fire that
wins the start burns the goods before the carry-out has read them and the race
is never run at all.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import current, current_catalog
from src.db.base import forget
from src.engine import world
from src.engine.errors import Refusal
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node

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
        await asyncio.wait_for(took_the_row.wait(), timeout=5)
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


@pytest.mark.parametrize(
    "the_door_locks_the_chest", [True, False], ids=["door-locks-the-chest", "fire-lock-alone"]
)
async def test_the_eruption_does_not_burn_what_was_taken_out_of_a_chest(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
    the_door_locks_the_chest: bool,
) -> None:
    """The same robbery as above, through the door of a chest.

    A chest burns with what is in it, or its goods would outlive the place they
    lay in -- and its inside is a second container, so the fire walks into the
    box after the ground (`world.destroy`). Somebody taking a sack out of that
    box meanwhile is doing exactly what the window is for, and the delete must
    not land on the sack the moment it reaches a pocket.

    Two locks stand between the fire and that robbery, and either one alone
    holds it off -- which is why the second case here takes the first away:
    with both in place the fire never gets far enough to need the second, and
    a lock nothing leans on is a lock nothing keeps honest.

    The door takes the **chest's** row before a word that counts is read off it
    (`storage._allowed`), so the fire queues at the ground and opens an empty
    box after the commit. That one is pinned by the carry-limit races (D-146),
    not here; the second case rereads the chest **without** locking it, and the
    fire then walks in while the sack is still inside. What holds it off there
    is the lock the fire takes on what lies **inside** the box, which drops the
    sack from a fresh `SELECT ... FOR UPDATE` the moment the carry-out commits.
    Take both away and the delete queues behind the carry-out's own update and
    lands on a sack already in the hands.

    On the wild ground of Pyroxis anybody may open anybody's chest
    (`station.may_build` gives the wild to everyone), so this is not a corner:
    it is the ordinary way a sack leaves a field before an eruption.
    """
    from src.engine import plates, storage
    from src.models.world import Layer, Planet

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

    #: **A handshake, not a pause**, for the reason the first test of this file
    #: writes out -- and here a head start had already cost the race itself.
    #: The fire used to wait 50 ms; on a machine loaded by other runs it took
    #: the field first, and the carry-out then found no chest to open at all
    #: (`db.get(Item, chest_id)` came back None) and never reached
    #: `storage.take`. Two of four full `-n 2` runs failed that way on
    #: 2026-09-13, and the file passed three times out of three alone. `_slow`
    #: on `take` could not save it: its pause lands after `take` has returned,
    #: while everything this race is about happens inside. So the fire waits
    #: for the row **inside the box** to be taken instead.
    took_the_row = asyncio.Event()
    #: Patched on the package `world` rather than on `storage`: since 0830ec2
    #: every door takes a thing's row through the one helper, and `storage`
    #: reads it off the package at each call. `world.move_stack` reaches its
    #: own copy as a module global one floor down (`world.things`) and is not
    #: touched here -- which is what this wants: the carry-out still holds the
    #: sack's row through the move, and only the **chest**'s lock is in
    #: question.
    holding = world.lock_thing

    async def held(db: AsyncSession, thing: Item, *, gone: type[Refusal]) -> None:
        if thing.id != sack_id:  # the chest's row, taken first by `_allowed`
            if the_door_locks_the_chest:
                await holding(db, thing, gone=gone)
            else:
                #: The same reread without the lock, so that exactly one guard
                #: goes and the fire is left to lean on its own. No stand-in
                #: for `lock_thing`'s `thing-gone`: the chest is read before
                #: the event lets the fire out of the gate, so it cannot be
                #: gone by now. Move the handshake earlier and this needs the
                #: translation too, or a bare `InvalidRequestError` will read
                #: like a defect of the door.
                await db.refresh(thing)
                forget(db)
            return
        await holding(db, thing, gone=gone)
        took_the_row.set()
        #: Wide enough for the fire to reach its lock, and no part of the
        #: ordering: that is the event's business. Measured at some 165 ms of
        #: real waiting with three runs on the machine at once, so the margin
        #: is thin-ish and the overshoot is **quiet**: a fire that arrives
        #: after the commit finds an empty box and passes without racing
        #: anything. Deliberate -- the alternative is a wall-clock assertion,
        #: which trades a silent non-race for a flake, and a flaky race test
        #: is what this file was fixed to stop being.
        await asyncio.sleep(0.2)

    monkeypatch.setattr(world, "lock_thing", held)

    async def erupt() -> None:
        #: The carry-out is inside its transaction and holding the sack's row
        #: -- not probably, but by construction.
        await asyncio.wait_for(took_the_row.wait(), timeout=5)
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
