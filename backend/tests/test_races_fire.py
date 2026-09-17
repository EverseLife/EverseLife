# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once, and one of them is the fire.

Split off from `test_races_ground.py` (see `test_races.py` for the family's
method) when it stood at 799 lines with the quality bar at 800 and this pair
still to grow -- by subject as much as by length: the fire is the one racer
that takes matter **out** of the world (D-197), so whoever is mid-carry does
not lose a contest, they lose the goods. The ground's other races -- a ruin
room two scouts open at once, a node's properties two writers stamp together,
a sown strip two harvests reap -- stayed there.

Both tests are the same robbery, off the ground and out of a chest standing on
it, and neither gives anybody a head start. The carry-out goes first, holds
what it took, and waits until the fire **provably** stands on one of those rows
(`automat_kit._until_blocked_by`) before it commits.

A fixed pause fails at this in both directions, and the two failures look
nothing alike. Give the fire a head start and on a busy machine it wins the
start outright: it commits the chest's deletion, the carry-out finds nothing
to open and the test falls over on a setup assert, having never run the race
-- two full `-n 2` runs of four did exactly that on 2026-09-13. Hold the rows
for a fixed pause instead and the other failure is silent: a busy machine
releases them before the fire arrives, the fire finds an empty box and passes
on the very code the test exists to catch. The handshake closes both, because
it is the fire's own waiting that releases the carry-out.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from gone_kit import _lifting
from src.constants import current, current_catalog
from src.engine import world
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Layer, Node, Planet
from src.units import amount_float

ORE = "iron_ore"


async def _a_field_on_pyroxis(session: AsyncSession) -> tuple[Node, Body]:
    """A node of the wild ground with somebody standing on it.

    Pyroxis, because the eruption is its (D-197) and because on wild ground
    anybody may open anybody's chest (`station.may_build` gives the wild to
    everyone) -- so the chest test below is not a corner.
    """
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
    return field, await world.print_body(session, who, field)


async def test_the_eruption_does_not_burn_what_was_carried_out(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
) -> None:
    """The window before an eruption is the whole licence for the burning
    (D-197, P6), and somebody using it must not be robbed by the fire anyway.

    The lift goes **first** and holds the sack's row (`gone_kit._lifting`, the
    same side the floor's races send in); the fire is let out of the gate only
    once the row is taken, and waits at it.

    With the lock the fire rereads the row after the commit and finds the sack
    in a pocket -- not in the node -- so there is nothing here to burn. Without
    it the fire reads the sack where it still was, queues its delete behind the
    same row, and takes it **out of the player's hands** the moment the lift
    lands: the one place it was safe.
    """
    from src.engine import plates

    field, body = await _a_field_on_pyroxis(session)
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

    carried = asyncio.Event()

    async def erupt() -> None:
        #: The lift is inside its transaction and holding the row -- not
        #: probably, but by construction. The bound is not the race's timing:
        #: it is there so that a handshake that never comes fails this test
        #: instead of hanging the run, which has no `pytest-timeout`.
        await asyncio.wait_for(carried.wait(), 30)
        async with factory() as db, db.begin():
            place = await db.get(Node, field_id)
            assert place is not None
            burnt = await plates._burn(db, [place])
            assert burnt == 0, "огонь сжёг то, что уже уносили"

    outcome = await asyncio.gather(
        erupt(),
        _lifting(factory, current(), current_catalog(), body_id, sack_id, held=carried),
        return_exceptions=True,
    )
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        left = await db.get(Item, sack_id)
        assert left is not None, "вынесенное сгорело в руках"
        pocket = await world.body_container(db, await db.get(Body, body_id))
        assert left.container_id == pocket.id, "вынесенное сгорело в руках"


@pytest.mark.parametrize(
    "through_the_door", [True, False], ids=["door-locks-the-chest", "sack-lock-alone"]
)
async def test_the_eruption_does_not_burn_what_was_taken_out_of_a_chest(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    through_the_door: bool,
) -> None:
    """The same robbery one floor down: out of a chest standing in the field.

    A chest burns with what is in it, or its goods would outlive the place they
    lay in -- and its inside is a second container, so the fire walks into the
    box after the ground (`world.destroy`). Two locks stand between it and the
    sack, and the two cases here are the two carriers that meet one each.

    **Through the door** the chest's row goes first (`storage._allowed`), so
    the fire queues out in the yard and opens an empty box after the commit.
    That is how every door into a box behaves today, and it is why the lock
    **inside** the box is never reached in the ordinary way -- the reason
    `world.destroy` calls it the second line.

    **A carrier that holds only the sack** is the shape the second line is kept
    for: it goes through `world.move_stack`, the one funnel every move in the
    world comes through, which takes the stack's row and never touches the box
    that holds it. The fire then walks into the box while the sack is still in
    it and meets that lock alone -- a fresh `SELECT ... FOR UPDATE` that drops
    the sack the moment the carry-out commits. Take the lock away and the
    delete queues behind the carry-out's own update instead and lands on a sack
    already in the hands.

    No door reaches the box that way as of today (`world.destroy` names the
    survey), so this second case is the growth, not a path a player walks.
    When a real one appears it replaces this carrier and nothing else changes.
    """
    from src.engine import plates, storage

    field, body = await _a_field_on_pyroxis(session)
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

    took_the_row = asyncio.Event()

    async def erupt() -> None:
        await asyncio.wait_for(took_the_row.wait(), 30)
        async with factory() as db, db.begin():
            place = await db.get(Node, field_id)
            assert place is not None
            await plates._burn(db, [place])

    async def carry(blaze: asyncio.Task[None]) -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            thing = await db.get(Item, sack_id)
            assert mine is not None and thing is not None
            if through_the_door:
                crate = await db.get(Item, chest_id)
                assert crate is not None
                await storage.take(db, current(), current_catalog(), mine, crate, thing)
            else:
                pocket = await world.body_container(db, mine)
                await world.move_stack(db, thing, pocket, amount_float(thing.amount))
            took_the_row.set()
            #: Held until the fire provably stands on one of these rows -- the
            #: chest's in the first case, the sack's in the second. `unless`
            #: is the fire itself: on the code this catches it would walk
            #: straight through instead, and the test must say that rather
            #: than wait out the poll.
            stood = await _until_blocked_by(factory, db, unless=blaze)
            assert stood, "огонь прошёл мимо, не встав ни на один замок"

    blaze = asyncio.create_task(erupt())
    outcome = await asyncio.gather(blaze, carry(blaze), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        left = await db.get(Item, sack_id)
        assert left is not None, "вынесенное из сундука сгорело в руках"
        pocket = await world.body_container(db, await db.get(Body, body_id))
        assert left.container_id == pocket.id, "вынесенное из сундука сгорело в руках"
        #: And the chest itself is gone with the field: what stayed in it burned.
        assert await db.get(Item, chest_id) is None
