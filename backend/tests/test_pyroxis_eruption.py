# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The eruption: fire, ways and veins (D-197, D-233).

What the shaken ground does, mirrored from the rooms of `engine/plates/`:
the open fields burn to the last chest, ways break and are laid without
cutting the planet in two, a walker on a breaking way dies with the pocket,
veins move out from under the faces and never onto the plateau. The clock
that times all of it lives in `test_pyroxis_clock.py`.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from pyroxis_kit import _dweller, _surface
from src.constants import Constants
from src.engine import plates, ship, travel, world
from src.models.event import Event, EventKind
from src.models.identity import BodyState
from src.models.job import Job, JobKind
from src.models.ship import Ship
from src.models.travel import Travel
from src.models.world import Edge, Layer, Planet, Surface, Vein

# --- the ground moves ---------------------------------------------------------


async def test_an_eruption_redraws_ways_and_moves_veins(
    session: AsyncSession, constants: Constants
) -> None:
    """The measure against a staked claim (D-197): the vein leaves by itself,
    and the map it was on stops being worth anything."""
    plateau, fields = await _surface(session, count=4)
    for field in fields:
        await world.create_vein(session, field, "tungsten", richness=70, remaining=1000)
    before = {
        field.id: await session.scalar(select(Vein.id).where(Vein.node_id == field.id).limit(1))
        for field in fields
    }
    await session.commit()

    moved = 0
    for attempt in range(12):
        moved += await plates._move_veins(
            session, constants, random.Random(attempt), fields, now=datetime.now(UTC)
        )
        if moved:
            break
    assert moved, "жила обязана уезжать, иначе точку можно застолбить навсегда"
    after = {
        field.id: await session.scalar(select(Vein.id).where(Vein.node_id == field.id).limit(1))
        for field in fields
    }
    assert after != before


async def test_the_plateau_is_never_shaken(session: AsyncSession, constants: Constants) -> None:
    """The one place anything stands on is the one place the planet leaves
    alone (D-197). A base is not taken away here -- its meaning is."""
    plateau, _ = await _surface(session, count=4)
    for attempt in range(20):
        shaken = await plates._choose(session, constants, random.Random(attempt))
        assert plateau.id not in {node.id for node in shaken}


async def test_the_ground_under_a_ship_is_outside_the_draw(
    session: AsyncSession, constants: Constants
) -> None:
    """Pulling the rock out from under a docked hull would kill a crew by an
    event rather than by a mistake (D-233)."""
    _, fields = await _surface(session, count=3)
    moored = fields[0]
    owner = await world.create_identity(session, f"Капитан-{uuid.uuid4().hex[:6]}")
    hull = await world.create_node(
        session,
        f"ship.{uuid.uuid4().hex[:6]}",
        "Корабль",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.SPACE,
    )
    connector = await world.create_node(
        session,
        f"{hull.key}.connector",
        "Коннектор",
        planet=Planet.PYROXIS,
        area_m2=20,
        layer=Layer.LOCATION,
        parent=hull,
        properties={ship.ABOARD: True},
    )
    session.add(
        Ship(
            name="Вахта",
            owner_identity_id=owner.id,
            node_id=hull.id,
            connector_node_id=connector.id,
            docked_node_id=moored.id,
            berth=1,
        )
    )
    await session.flush()

    for attempt in range(20):
        shaken = await plates._choose(session, constants, random.Random(attempt))
        assert moored.id not in {node.id for node in shaken}


async def test_what_lies_in_the_open_burns(session: AsyncSession, constants: Constants) -> None:
    """There is no warehouse in the fields, and that is the point: hauling is
    part of the work here (D-197)."""
    _, fields = await _surface(session)
    field = fields[0]
    await world.grant_item(
        session,
        await world.node_container(session, field),
        "coal",
        amount=40,
        quality=60,
        origin="тест",
    )
    burnt = await plates._burn(session, [field])
    assert burnt > 0
    assert await world.contents(session, await world.node_container(session, field)) == ()


async def test_the_fire_empties_a_chest_inside_a_chest(
    session: AsyncSession, constants: Constants
) -> None:
    """Matter leaves the world by one door, or it leaves it half-way (D-197).

    A chest goes inside a chest (`storage.admits` allows it), and unpacking one
    level deep would delete the inner chest while its own container went on
    holding goods with no owner: invisible, unreachable, alive for ever in a
    place that no longer exists.
    """
    from src.engine import storage
    from src.models.inventory import Container, Item

    _, fields = await _surface(session, count=1)
    yard = await world.node_container(session, fields[0])
    outer = await world.grant_item(session, yard, "chest", quality=60, origin="тест")
    inner = await world.grant_item(
        session, await storage.inside(session, outer), "chest", quality=60, origin="тест"
    )
    deep = await world.grant_item(
        session,
        await storage.inside(session, inner),
        "coal",
        amount=5,
        quality=60,
        origin="тест",
    )
    deep_id, inner_id = deep.id, inner.id
    burnt = await plates._burn(session, [fields[0]])

    assert burnt > 0
    assert await session.get(Item, deep_id) is None, "уголь в сундуке в сундуке пережил поле"
    assert (
        await session.scalar(
            select(Container.id).where(Container.owner_id.in_([deep_id, inner_id]))
        )
    ) is None, "контейнер остался без владельца"
    assert await world.contents(session, yard) == ()


async def test_the_planet_stays_one_graph(session: AsyncSession, constants: Constants) -> None:
    """A break that would cut anything off is cancelled (D-197, P6).

    Checked by **reachability**, not by counting ways out: a node with two ways
    that both lead into one dead end is as walled in as a node with none, and
    that is exactly the case a degree count calls safe. And an empty field cut
    loose for ever would be the same wrong done to the map instead of a person
    (D-007).
    """
    plateau, fields = await _surface(session, count=2, chain=False)
    ways = await plates._adjacency(session)
    anchor = await plates._anchor(session)
    assert anchor == plateau.id, "мерить планету следует от наковальни"

    #: The star the seed lays: every field hangs on the plateau alone, so no way
    #: out of any of them may go.
    for field in fields:
        assert not plates._may_lose(ways, field.id, plateau.id, anchor)

    #: Give a field a second way round, and the first one may go: nothing is
    #: cut off by losing it.
    await travel.connect(session, fields[0], fields[1], base_seconds=900, surface=Surface.TRAIL)
    ways = await plates._adjacency(session)
    assert plates._may_lose(ways, fields[0].id, plateau.id, anchor)

    #: A place already standing apart -- an old node nobody laid a trail to --
    #: does not make every way on the planet unbreakable: it was unreachable
    #: before the break and is unreachable after it, and the eruptions go on.
    apart = await world.create_node(
        session,
        f"pyroxis.apart.{uuid.uuid4().hex[:8]}",
        "Ничей выход",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
    )
    ways = await plates._adjacency(session)
    assert apart.id not in plates._connected(ways, anchor)
    assert plates._may_lose(ways, fields[0].id, plateau.id, anchor)


async def test_a_way_breaking_under_a_walker_kills_them_with_the_pocket(
    session: AsyncSession, constants: Constants
) -> None:
    """One walked far from the ship and chose this risk (D-233).

    The pocket does not fall to the ground: it is gone, and that is a sink of
    matter the decision names out loud.
    """
    plateau, fields = await _surface(session, count=2)
    body = await _dweller(session, fields[0])
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "coal", amount=10, quality=60, origin="тест")

    edge = await session.scalar(
        select(Edge).where(
            or_(
                (Edge.node_a_id == fields[0].id) & (Edge.node_b_id == plateau.id),
                (Edge.node_a_id == plateau.id) & (Edge.node_b_id == fields[0].id),
            )
        )
    )
    assert edge is not None
    session.add(
        Travel(
            body_id=body.id,
            from_node_id=fields[0].id,
            to_node_id=plateau.id,
            edge_id=edge.id,
            arrives_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await session.flush()

    died = await plates._kill_on(session, constants, edge, now=datetime.now(UTC))
    assert died == 1
    assert body.state is BodyState.DEAD
    #: Nothing survived the fall: what a body drops on death lands in the node
    #: (D-011), and here there was no node to land in.
    assert await world.contents(session, pocket) == ()
    assert await world.contents(session, await world.node_container(session, fields[0])) == ()


async def test_the_whole_eruption_runs(session: AsyncSession, constants: Constants) -> None:
    """The job the world actually runs, from the signal to the moved ground.

    Everything below is checked piece by piece elsewhere; this is the one test
    that runs the piece the world runs -- and the one that would have caught a
    handler that raises before it does anything at all.
    """
    plateau, fields = await _surface(session, count=4)
    for field in fields:
        await world.create_vein(session, field, "tungsten", richness=70, remaining=1000)
        await world.grant_item(
            session,
            await world.node_container(session, field),
            "coal",
            amount=10,
            quality=60,
            origin="тест",
        )

    await plates.ensure_scheduled(session, now=datetime.now(UTC))
    warning = await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1)
    )
    assert warning is not None
    await plates.warned(session, warning)
    coming = await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_ERUPT.value).limit(1)
    )
    assert coming is not None

    ways_before = len(await _edges(session))
    await plates.erupted(session, coming)

    #: Told to the places it happened in, so everybody standing there rereads
    #: the ground under them.
    told = (
        (await session.execute(select(Event).where(Event.kind == EventKind.PLATES_ERUPTED.value)))
        .scalars()
        .all()
    )
    shaken = {uuid.UUID(one) for one in coming.payload["nodes"]}
    assert {row.node_id for row in told if row.node_id is not None} == shaken
    #: And exactly one tally of the whole eruption, for the journal to read --
    #: the per-node ones carry no totals, because somebody standing in one
    #: field has no business reading how much burned in another.
    whole = [row for row in told if row.node_id is None]
    assert len(whole) == 1 and whole[0].payload["veins_moved"] is not None

    #: What lay in the shaken nodes burned, and the plateau kept everything.
    for field in fields:
        left = await world.contents(session, await world.node_container(session, field))
        assert (left == ()) == (field.id in shaken)

    #: The graph is still one graph: nothing was cut loose from the plateau.
    ways = await plates._adjacency(session)
    reached = plates._connected(ways, plateau.id)
    assert {node.id for node in await plates._surface(session)} <= reached
    #: And the eruption's own tally squares with the graph: every way it says
    #: it tore is gone, every one it says it laid is there. How many ways one
    #: shaken node may lose is not a rule anywhere -- each way is rolled on its
    #: own (`plates._redraw`) and only reachability holds the roll back -- so
    #: counting breaks per node here would be asserting the dice.
    assert len(await _edges(session)) == (
        ways_before - whole[0].payload["ways_torn"] + whole[0].payload["ways_laid"]
    )


async def _edges(session: AsyncSession) -> list[Edge]:
    return list((await session.execute(select(Edge))).scalars().all())


async def test_a_moved_vein_ends_the_work_at_its_face(
    session: AsyncSession, constants: Constants
) -> None:
    """Matter is worked in person (D-044): a face two passes away is not the
    face under this pick, and the session at it ends with the ground.

    Ends the way leaving a face always ends: **the ore comes out with it.** The
    ground moved, and that is not the miner's mistake (D-143) -- a session shut
    by hand would leave the haul in a container nobody can ever open again.
    """
    from src.engine.mining import session_container
    from src.models.mining import MiningSession, Pace, SessionState

    _, fields = await _surface(session, count=2)
    vein = await world.create_vein(session, fields[0], "tungsten", richness=70, remaining=1000)
    body = await _dweller(session, fields[0])
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await session_container(session, face),
        "tungsten",
        amount=7,
        quality=70,
        origin="тест",
    )

    for attempt in range(12):
        if await plates._move_veins(
            session, constants, random.Random(attempt), [fields[0]], now=datetime.now(UTC)
        ):
            break
    else:  # pragma: no cover -- twelve rolls at one half never all miss
        pytest.fail("жила так и не уехала")

    face = await session.scalar(select(MiningSession).where(MiningSession.body_id == body.id))
    assert face is not None and face.state is SessionState.LEFT
    assert face.ended_at is not None
    pocket = await world.contents(session, await world.body_container(session, body))
    assert [thing.type_key for thing in pocket] == ["tungsten"], "добытое осталось в забое"


async def test_a_vein_never_moves_onto_the_plateau(
    session: AsyncSession, constants: Constants
) -> None:
    """The plateau is never shaken (`_choose`), so a vein that moved onto it
    would never move again -- the one claim on Pyroxis nothing could ever take
    away, which is exactly what this machinery exists against (D-197).

    The plateau is also the only node the seed leaves without a vein, and that
    is not decoration: `test_seed` pins it.
    """
    plateau, fields = await _surface(session, count=3)
    for field in fields:
        await world.create_vein(session, field, "tungsten", richness=70, remaining=1000)

    for attempt in range(30):
        await plates._move_veins(
            session, constants, random.Random(attempt), fields, now=datetime.now(UTC)
        )
        on_the_anvil = await session.scalar(
            select(Vein.id).where(Vein.node_id == plateau.id).limit(1)
        )
        assert on_the_anvil is None, "жила уехала на наковальню и застряла там навсегда"


async def test_a_face_at_a_worked_out_vein_still_lets_the_miner_out(
    session: AsyncSession, constants: Constants
) -> None:
    """The last swing is the one that takes the remainder to nought, and it
    leaves the session open (D-143).

    Refusing to leave then would shut the miner in a face they can neither work
    nor walk out of, and the ore of the swing before would stay in a container
    nobody can ever open again. On Pyroxis it is worse than stuck: an eruption
    closes the faces of a vein it moves, and one abandoned session at a
    worked-out vein would roll back the whole eruption -- the burning, the
    moved veins, the redrawn ways -- until the job ran out of attempts.
    """
    from src.engine import mining
    from src.models.mining import MiningSession, Pace, SessionState

    _, fields = await _surface(session, count=2)
    vein = await world.create_vein(session, fields[0], "tungsten", richness=70, remaining=1000)
    body = await _dweller(session, fields[0])
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        "tungsten",
        amount=4,
        quality=70,
        origin="тест",
    )
    vein.remaining = 0
    await session.flush()

    #: Out by hand, and out under an eruption: both must work.
    await mining.leave(session, constants, face, now=datetime.now(UTC))
    assert face.state is SessionState.LEFT
    pocket = await world.contents(session, await world.body_container(session, body))
    assert [thing.type_key for thing in pocket] == ["tungsten"]

    #: And under an eruption. The **only** vein left in the node is the
    #: worked-out one with somebody still sitting at it, so the move cannot
    #: quietly pick an easier neighbour and leave the case untested.
    vein.node_id = fields[1].id
    await session.flush()
    other = await world.create_vein(session, fields[0], "copper_ore", richness=70, remaining=1000)
    second = await _dweller(session, fields[0])
    stuck = MiningSession(body_id=second.id, vein_id=other.id, pace=Pace.STEADY, roof=100)
    session.add(stuck)
    await session.flush()
    other.remaining = 0
    await session.flush()
    for attempt in range(12):
        if await plates._move_veins(
            session, constants, random.Random(attempt), [fields[0]], now=datetime.now(UTC)
        ):
            break
    else:  # pragma: no cover -- twelve rolls at one half never all miss
        pytest.fail("жила так и не уехала")
    assert stuck.state is SessionState.LEFT, "забой у выработанной жилы не закрылся"


async def test_a_dead_miner_leaves_the_haul_at_the_face(
    session: AsyncSession, constants: Constants
) -> None:
    """A body that cannot come back does not keep its face open (D-011).

    The ore was out of the rock and lying at the face before the body fell, so
    it stays in the node like everything else the place kept. Left ACTIVE, the
    session would hold the haul where nobody could ever reach it -- and the
    next thing to touch that session would be an eruption trying to close it.
    """
    from src.engine import death, mining
    from src.models.mining import MiningSession, Pace, SessionState

    _, fields = await _surface(session, count=2)
    vein = await world.create_vein(session, fields[0], "tungsten", richness=70, remaining=1000)
    body = await _dweller(session, fields[0])
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        "tungsten",
        amount=6,
        quality=70,
        origin="тест",
    )

    await death.die(session, constants, body, cause="тест", now=datetime.now(UTC))

    assert face.state is SessionState.LEFT and face.ended_at is not None
    lying = await world.contents(session, await world.node_container(session, fields[0]))
    assert [thing.type_key for thing in lying] == ["tungsten"], "добытое пропало вместе с телом"
