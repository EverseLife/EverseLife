# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Pyroxis: landing on bare rock, and the ground that will not stay put
(D-197, D-233).

Checked is what the planet **is**, and none of it is decoration:

* there is no spaceport and there cannot be one -- a ship sets down in any node
  of the surface, by the same single edge, and the only infrastructure of the
  place is its own hull;
* an eruption redraws the ways and moves the veins, and leaves the nodes and
  everything built on them alone: a base is not taken away, its meaning is --
  the vein it was put up for walks off three passes away;
* what lies under the open sky burns, and it is announced before it burns;
* **a node with people or property in it is never sealed**, but a way may break
  under somebody walking it -- and that ends in death with the pocket lost.
  Losing oneself is allowed here; being walled in is not;
* the ground a ship stands on is outside the draw: a crew is killed by its own
  mistakes, never by an event.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.engine import plates, ship, travel, world
from src.models.event import Event, EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.travel import Travel
from src.models.world import Edge, Layer, Node, Planet, Surface, Vein


async def _pyroxis(session: AsyncSession) -> Node:
    return await world.create_node(
        session,
        "pyroxis",
        "Пироксис",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.SPACE,
        properties={ship.OPEN_LANDING: True, "пекло": True},
    )


async def _surface(
    session: AsyncSession, count: int = 3, *, chain: bool = True
) -> tuple[Node, list[Node]]:
    """The plateau and a few fields around it, connected like the seed's.

    `chain=False` leaves the seed's own shape: a star, every field hanging on
    the plateau alone. That is the state a fresh world is in, and the state in
    which no way out of a field may go at all.
    """
    sphere = await _pyroxis(session)
    stamp = uuid.uuid4().hex[:6]
    plateau = await world.create_node(
        session,
        f"pyroxis.{stamp}.anvil",
        "Плато",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.PLANET,
        parent=sphere,
        properties={plates.ANVIL: True},
    )
    fields = []
    for number in range(count):
        field = await world.create_node(
            session,
            f"pyroxis.{stamp}.field.{number}",
            f"Поле {number}",
            planet=Planet.PYROXIS,
            area_m2=5000,
            layer=Layer.PLANET,
            parent=sphere,
        )
        await travel.connect(session, plateau, field, base_seconds=900, surface=Surface.TRAIL)
        fields.append(field)
    #: The fields are neighbours of each other too, or an eruption would have
    #: nowhere to move a vein to.
    if chain:
        for one, other in zip(fields, fields[1:], strict=False):
            await travel.connect(session, one, other, base_seconds=900, surface=Surface.TRAIL)
    return plateau, fields


async def _dweller(session: AsyncSession, node: Node) -> Body:
    identity = await world.create_identity(session, f"Вахтовик-{uuid.uuid4().hex[:6]}")
    return await world.print_body(session, identity, node)


# --- landing ------------------------------------------------------------------


async def test_every_node_of_the_surface_is_a_landing_site(
    session: AsyncSession, constants: Constants
) -> None:
    """Nothing is built on Pyroxis (D-230), so there is no yard to aim at -- and
    the planet takes a ship anywhere on its ground instead (D-233)."""
    plateau, fields = await _surface(session)
    landings = {node.key for node in await ship.open_landings(session)}
    assert plateau.key in landings
    assert {field.key for field in fields} <= landings

    #: The planet's own node is where it stands in the sky, not a place to put
    #: a hull down on -- and both answers say so, or a flight would be offered
    #: by one and refused by the other.
    assert "pyroxis" not in landings
    assert await ship.lands_anywhere(session, plateau)
    sphere = await session.scalar(select(Node).where(Node.key == "pyroxis"))
    assert sphere is not None
    assert not await ship.lands_anywhere(session, sphere)

    #: And every one of them is a destination: there is no beacon to go out.
    lit = {node.key for node in await ship.lit_ports(session, constants)}
    assert plateau.key in lit


async def test_the_console_shows_the_planet_and_not_every_field_of_it(
    session: AsyncSession, constants: Constants
) -> None:
    """A planet one lands anywhere on is one line of the console (D-233).

    Its fields differ in nothing the console can show -- same hours, same fuel,
    same class -- and their number grows with every field a scout opens: six
    identical rows today, sixty later, in a socket answer sent every time the
    console is opened (D-225).

    Asked of a hull **in orbit** over Pyroxis, because that is where the pad is
    chosen at all now (D-245): from the ground there is one move and it is the
    climb, and between worlds one goes orbit to orbit.
    """
    from src.engine.ship.view import profile
    from src.models.ship import Ship

    plateau, fields = await _surface(session, count=6)
    #: `_surface` has already laid the planet: the orbit hangs under that one.
    sphere = await session.get(Node, plateau.parent_id)
    assert sphere is not None
    orbit = await world.create_node(
        session,
        ship.orbit_key(Planet.PYROXIS),
        "Околопланетная орбита Пироксиса",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.SPACE,
        parent=sphere,
        properties={ship.ORBIT_NODE: True},
    )
    owner = await world.create_identity(session, f"Капитан-{uuid.uuid4().hex[:6]}")
    hull = await world.create_node(
        session,
        f"ship.{uuid.uuid4().hex[:6]}",
        "Корабль",
        area_m2=1,
        planet=Planet.PYROXIS,
        layer=Layer.SPACE,
    )
    connector = await world.create_node(
        session,
        f"{hull.key}.connector",
        "Коннектор",
        area_m2=20,
        planet=Planet.PYROXIS,
        layer=Layer.LOCATION,
        parent=hull,
        properties={ship.ABOARD: True},
    )
    hulk = Ship(
        name="Вахта",
        owner_identity_id=owner.id,
        node_id=hull.id,
        connector_node_id=connector.id,
        docked_node_id=orbit.id,
    )
    session.add(hulk)
    await session.flush()

    console = await profile(session, constants, current_catalog(), hulk)
    assert console["stage"] == "orbit"
    assert len(console["landings"]) == 1, "консоль перечисляет планету, а не каждое её поле"
    row = console["landings"][0]
    #: And it says so, so the client knows a node picker belongs here.
    assert row["anywhere"] is True
    assert row["node"] in {plateau.key, *(field.key for field in fields)}
    #: A name and nothing else: what a descent costs is a fact about the planet,
    #: and it is sent once beside the list rather than copied into every field
    #: of it (D-225, D-245).
    assert set(row) == {"node", "name", "anywhere"}
    #: This hull has no engines at all, so the price is offered and unreachable
    #: rather than hidden: "не отрывается" is an answer, and a missing row is not.
    assert set(console["descent"]) == {"hours", "fuel", "needs", "reachable"}
    assert console["descent"]["reachable"] is False
    #: The name is the planet's own, not the field the row happens to carry:
    #: the hull comes down where the roll puts it (D-235).
    assert row["name"] == sphere.name


async def test_a_landing_without_a_port_falls_where_the_rock_allows(
    session: AsyncSession, constants: Constants
) -> None:
    """A planet with no ports takes a ship into a node of its own choosing
    (D-233, D-235).

    There is nothing to prefer: no piers, no berths, no lit beacons. So the
    node is rolled at the landing rather than picked in the console -- one sets
    down where the rock allows. Seeded by the job, so a flight that failed and
    is retried puts the hull in the same place instead of teleporting it across
    the planet on the second attempt.
    """
    from src.engine.ship.flight import _somewhere_on

    plateau, fields = await _surface(session, count=6)
    ground = {plateau.id, *(field.id for field in fields)}

    #: The same job always lands in the same place.
    twice = set()
    for _ in range(2):
        twice.add((await _somewhere_on(session, plateau, dice=random.Random("job-1"))).id)
    assert len(twice) == 1, "повтор рейса не должен переносить корабль"

    #: And across many flights the whole surface is used, not one node.
    where = set()
    for attempt in range(40):
        landed = await _somewhere_on(session, plateau, dice=random.Random(f"job-{attempt}"))
        assert landed.id in ground, "сели мимо планеты"
        where.add(landed.id)
    assert len(where) > 1, "садятся всегда в один узел — это не жеребьёвка"


async def test_ground_without_a_planet_property_takes_nobody(
    session: AsyncSession, constants: Constants
) -> None:
    """Landing anywhere is a property of the **planet** (D-233), not a hole in
    the rule: on Terra a ship still needs a yard."""
    wild = await world.create_node(
        session, f"terra.wild.{uuid.uuid4().hex[:6]}", "Пустошь", area_m2=100, layer=Layer.PLANET
    )
    assert not await ship.lands_anywhere(session, wild)
    assert wild.key not in {node.key for node in await ship.open_landings(session)}


async def test_nothing_grows_where_the_ground_bakes(
    session: AsyncSession, constants: Constants
) -> None:
    """A grove on a lava field would be a property nobody could explain
    (D-231, D-233): the search does not offer what the planet cannot hold."""
    from src.engine import explore

    _, fields = await _surface(session, count=1)
    offered = await explore.possible(session, fields[0])
    assert explore.VEIN in offered and explore.SITE in offered
    assert explore.FOREST not in offered


# --- the ground moves ---------------------------------------------------------


async def test_an_eruption_redraws_ways_and_moves_veins(
    session: AsyncSession, constants: Constants
) -> None:
    """The measure against a staked claim (D-197): the vein leaves by itself,
    and the map it was on stops being worth anything."""
    plateau, fields = await _surface(session, count=4)
    for field in fields:
        await world.create_vein(session, field, "Вольфрам", richness=70, remaining=1000)
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
        "Уголь",
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
    outer = await world.grant_item(session, yard, "Сундук", quality=60, origin="тест")
    inner = await world.grant_item(
        session, await storage.inside(session, outer), "Сундук", quality=60, origin="тест"
    )
    deep = await world.grant_item(
        session,
        await storage.inside(session, inner),
        "Уголь",
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
    await world.grant_item(session, pocket, "Уголь", amount=10, quality=60, origin="тест")

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
        await world.create_vein(session, field, "Вольфрам", richness=70, remaining=1000)
        await world.grant_item(
            session,
            await world.node_container(session, field),
            "Уголь",
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
    vein = await world.create_vein(session, fields[0], "Вольфрам", richness=70, remaining=1000)
    body = await _dweller(session, fields[0])
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await session_container(session, face),
        "Вольфрам",
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
    assert [thing.type_key for thing in pocket] == ["Вольфрам"], "добытое осталось в забое"


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
        await world.create_vein(session, field, "Вольфрам", richness=70, remaining=1000)

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
    vein = await world.create_vein(session, fields[0], "Вольфрам", richness=70, remaining=1000)
    body = await _dweller(session, fields[0])
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        "Вольфрам",
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
    assert [thing.type_key for thing in pocket] == ["Вольфрам"]

    #: And under an eruption. The **only** vein left in the node is the
    #: worked-out one with somebody still sitting at it, so the move cannot
    #: quietly pick an easier neighbour and leave the case untested.
    vein.node_id = fields[1].id
    await session.flush()
    other = await world.create_vein(session, fields[0], "Медная руда", richness=70, remaining=1000)
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
    vein = await world.create_vein(session, fields[0], "Вольфрам", richness=70, remaining=1000)
    body = await _dweller(session, fields[0])
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        "Вольфрам",
        amount=6,
        quality=70,
        origin="тест",
    )

    await death.die(session, constants, body, cause="тест", now=datetime.now(UTC))

    assert face.state is SessionState.LEFT and face.ended_at is not None
    lying = await world.contents(session, await world.node_container(session, fields[0]))
    assert [thing.type_key for thing in lying] == ["Вольфрам"], "добытое пропало вместе с телом"


async def test_the_planets_clock_queues_itself(session: AsyncSession, constants: Constants) -> None:
    """An eruption is the planet's weather, not an event of the server (D-197):
    the world puts its own next one in the journal."""
    await plates.ensure_scheduled(session, now=datetime.now(UTC))
    queued = await session.scalar(select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1))
    assert queued is not None
    period = constants[R.PYROXIS_ERUPTION_PERIOD]
    ahead = (queued.run_at - datetime.now(UTC)).total_seconds() / 3600 / 24
    assert period.min - 1 <= ahead <= period.max + 1

    #: Asked twice, queued once: the clock must neither be lost nor doubled.
    await plates.ensure_scheduled(session, now=datetime.now(UTC))
    both = (
        (await session.execute(select(Job).where(Job.kind == JobKind.PLATES_WARN.value)))
        .scalars()
        .all()
    )
    assert len(both) == 1


async def test_two_processes_of_one_deploy_start_one_clock(
    session: AsyncSession, constants: Constants
) -> None:
    """A release starts its processes minutes apart, and each asks the planet
    for its weather.

    Counted from the moment of the **ask**, the two would land on two different
    hours, both would pass the dedup key, and the planet would carry two
    independent chains of eruptions -- each queueing its own next one, and the
    ground shaking twice as often after every release, for ever. Counted from
    the start of the day, the two compute the same hour and the key makes one
    job of them.

    Asked of `schedule` rather than of `ensure_scheduled`: the guard above it
    would hide the arithmetic, and it is the arithmetic that is wrong here.
    """
    morning = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    await plates.schedule(session, constants, after=morning)
    await plates.schedule(session, constants, after=morning + timedelta(minutes=37))
    queued = (
        (await session.execute(select(Job).where(Job.kind == JobKind.PLATES_WARN.value)))
        .scalars()
        .all()
    )
    assert len(queued) == 1, "у планеты одни часы, а не по одним на процесс"


async def test_a_deploy_the_next_day_does_not_start_a_second_chain(
    session: AsyncSession, constants: Constants
) -> None:
    """The day changes, and the arithmetic alone stops covering us: a fresh day
    gives a fresh hour and a fresh key. What holds then is the chain already
    running -- a warning is pending, so there is nothing to start."""
    await plates.ensure_scheduled(session, now=datetime(2026, 9, 1, 9, 0, tzinfo=UTC))
    await plates.ensure_scheduled(session, now=datetime(2026, 9, 2, 9, 0, tzinfo=UTC))
    queued = (
        (await session.execute(select(Job).where(Job.kind == JobKind.PLATES_WARN.value)))
        .scalars()
        .all()
    )
    assert len(queued) == 1


async def test_a_second_already_used_does_not_stop_the_weather(
    session: AsyncSession, constants: Constants
) -> None:
    """The dedup key is unique across every state, so a **finished** warning of
    that same second refuses the new one.

    Two opposite reasons hide behind one refusal: usually it is the other
    process of the same deploy, a second ahead, and its job is the one we
    wanted. But a corpse holding the second is the other, and swallowing that
    refusal would stop the planet's weather until somebody restarted the world.
    """
    day = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    await plates.schedule(session, constants, after=day)
    first = await session.scalar(select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1))
    assert first is not None
    #: The chain ran and finished. The second it sat on stays taken for ever.
    first.state = JobState.DONE
    await session.flush()

    await plates.schedule(session, constants, after=day)
    alive = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.PLATES_WARN.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(alive) == 1, "секунда, занятая покойником, не должна выключать погоду"
    assert alive[0].run_at > first.run_at


async def test_a_chain_that_died_does_not_stop_the_weather(
    session: AsyncSession, constants: Constants
) -> None:
    """Only a **pending** warning counts as a running chain (D-197).

    A warning that failed all its attempts is not a chain: taken for one, it
    would switch the planet's weather off for ever -- the ground would never
    move again, and the whole measure against a staked claim would quietly go
    with it.
    """
    await plates.ensure_scheduled(session, now=datetime(2026, 9, 1, 9, 0, tzinfo=UTC))
    dead = await session.scalar(select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1))
    assert dead is not None
    dead.state = JobState.FAILED
    await session.flush()

    await plates.ensure_scheduled(session, now=datetime(2026, 9, 3, 9, 0, tzinfo=UTC))
    alive = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.PLATES_WARN.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(alive) == 1, "погибшая цепочка не должна выключать погоду планеты"


async def test_the_warning_outlives_the_second_it_was_said_in(
    session: AsyncSession, constants: Constants
) -> None:
    """The window is six hours wide, and the signal is an event -- and an event
    reaches whoever is connected in the second it is written (`api.push`).

    Somebody logging in ten minutes into the window would otherwise stand on
    ground about to move and read nothing about it. The place carries the
    warning while it stands, so `look` shows it (D-197, P6, D-225: the client
    cannot derive an announced hour from anything it already has).
    """
    from src.api.commands.look import _look

    #: Eight fields against at most `pyroxis.nodes_shifted` shaken: a quiet one
    #: is then certain, and the half of the contract about the **absent** key is
    #: checked on every run rather than on the runs where the dice were kind.
    _, fields = await _surface(session, count=8)
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
    shaken = {uuid.UUID(one) for one in coming.payload["nodes"]}

    for field in fields:
        said = await plates.shaking(session, field)
        assert (said is not None) == (field.id in shaken)
        if said is not None:
            assert said == pytest.approx(coming.run_at, abs=timedelta(seconds=1))

    #: And it is on the look of somebody standing there, not only in the second
    #: the signal was written.
    where = next(field for field in fields if field.id in shaken)
    body = await _dweller(session, where)
    seen = (await _look({"identity_id": body.identity_id}, session, {}))["look"]
    assert seen["node"]["shaking_at"] == coming.run_at.isoformat()

    #: A quiet field says nothing at all: an absent key, not a null.
    calm = next(field for field in fields if field.id not in shaken)
    quiet = await _dweller(session, calm)
    seen = (await _look({"identity_id": quiet.identity_id}, session, {}))["look"]
    assert "shaking_at" not in seen["node"]


async def test_the_digest_tells_what_the_place_lived_through(
    session: AsyncSession, constants: Constants
) -> None:
    """Coming back to a changed map, one must be told why it changed (D-197).

    An eruption has no actor -- it is the planet's doing -- so it is asked for
    by the **node** the body stands in, and merged into the digest by time
    rather than appended after everything the player did themselves.
    """
    from src.api.commands.world import _world_summary

    _, fields = await _surface(session, count=2)
    where = fields[0]
    body = await _dweller(session, where)
    now = datetime.now(UTC)

    #: Two of the place's own, an hour apart, and the older one written last:
    #: two lists each sorted by itself do not make one sorted list.
    #: One of the player's own, and **older** than both of the planet's: the two
    #: lists are each sorted by themselves, so only an event that has to move
    #: between them can tell a merge from a concatenation.
    session.add(
        Event(
            kind=EventKind.TRAVEL_ARRIVED.value,
            actor_identity_id=body.identity_id,
            node_id=where.id,
            at=now - timedelta(hours=3),
        )
    )
    for hours, kind in ((2, EventKind.PLATES_WARNED), (1, EventKind.PLATES_ERUPTED)):
        #: Written by hand rather than through `events.record`: the journal is
        #: append-only and refuses to have its stamp moved afterwards, and the
        #: whole question here is the order of two stamps.
        session.add(Event(kind=kind.value, node_id=where.id, at=now - timedelta(hours=hours)))
    await session.flush()

    digest = await _world_summary(
        {"identity_id": body.identity_id}, session, {"since": (now - timedelta(days=1)).isoformat()}
    )
    told = [line["kind"] for line in digest["happened"]]
    assert [one for one in told if one.startswith("plates.")] == [
        EventKind.PLATES_ERUPTED.value,
        EventKind.PLATES_WARNED.value,
    ]
    #: Newest first across **everything**, the player's own doings included:
    #: the body was printed just now and stands above an eruption of an hour ago.
    assert told[0] == EventKind.BODY_PRINTED.value
    assert told[-1] == EventKind.TRAVEL_ARRIVED.value
    assert [line["at"] for line in digest["happened"]] == sorted(
        (line["at"] for line in digest["happened"]), reverse=True
    )

    #: And nothing of a place one is not standing in.
    elsewhere = await _dweller(session, fields[1])
    quiet = await _world_summary(
        {"identity_id": elsewhere.identity_id},
        session,
        {"since": (now - timedelta(days=1)).isoformat()},
    )
    assert not [line for line in quiet["happened"] if line["kind"].startswith("plates.")]


async def test_the_signal_comes_before_the_ground_moves(
    session: AsyncSession, constants: Constants
) -> None:
    """Free, to everybody in the nodes, and ahead of the loss (P6, D-197): the
    window to walk out of is not merchandise."""
    await _surface(session, count=3)
    await plates.ensure_scheduled(session, now=datetime.now(UTC))
    warning = await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_WARN.value).limit(1)
    )
    assert warning is not None

    await plates.warned(session, warning)
    told = (
        (await session.execute(select(Event).where(Event.kind == EventKind.PLATES_WARNED.value)))
        .scalars()
        .all()
    )
    assert told, "сигнал приходит всем в затронутых узлах, и приходит заранее"
    coming = await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_ERUPT.value).limit(1)
    )
    assert coming is not None
    ahead = (coming.run_at - warning.run_at).total_seconds() / 3600
    assert ahead == pytest.approx(constants[R.PYROXIS_ERUPTION_WARNING])

    #: And the next one is already in the journal: the planet keeps its own time.
    assert await session.scalar(
        select(Job).where(Job.kind == JobKind.PLATES_WARN.value, Job.id != warning.id).limit(1)
    )
