"""Transit between nodes (D-045, D-107).

Checked is what makes the map a graph rather than a grid:

* nobody walks in a straight line: no edge -- no path;
* surface decides time, and offroad is pricier than a road;
* a transit takes time and arrives **by a journal job**, not by a check on
  read: closed the tab -- you still arrive;
* while walking you are absent: everything in-person is closed.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, jobs, market, mining, travel, world
from src.models.identity import Body
from src.models.travel import TravelState
from src.models.world import Surface


async def _two_nodes(session: AsyncSession, *, surface: Surface = Surface.ROAD, seconds=30):
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.here.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.there.{stamp}", "Там", area_m2=100)
    await travel.connect(session, here, there, base_seconds=seconds, surface=surface)
    identity = await world.create_identity(session, f"Ходок-{stamp}")
    body = await world.print_body(session, identity, here)
    return here, there, body


# --- graph -------------------------------------------------------------------


async def test_no_walking_in_straight_line(session: AsyncSession, constants: Constants) -> None:
    """No edge -- no path. Bridges, passes and ambushes rest on this."""
    here, _, body = await _two_nodes(session)
    far_away = await world.create_node(session, "terra.faraway", "Далеко", area_m2=100)
    with pytest.raises(travel.NoEdge):
        await travel.depart(session, constants, body, far_away)


async def test_edge_undirected(session: AsyncSession, constants: Constants) -> None:
    """The road is the same both ways, and no second row is needed for that."""
    here, there, _ = await _two_nodes(session)
    from_here = await travel.exits(session, constants, here)
    from_there = await travel.exits(session, constants, there)
    assert [path.key for path in from_here] == [there.key]
    assert [path.key for path in from_there] == [here.key]


async def test_offroad_slower_than_road(session: AsyncSession, constants: Constants) -> None:
    """Surface decides both time and the very possibility to drive through (D-107)."""
    _, _, body_by_road = await _two_nodes(session, surface=Surface.ROAD)
    road = (await travel.exits(session, constants, await _node(session, body_by_road)))[0]

    _, _, body_offroad = await _two_nodes(session, surface=Surface.TRAIL)
    trail = (await travel.exits(session, constants, await _node(session, body_offroad)))[0]

    _, _, body_by_highway = await _two_nodes(session, surface=Surface.PAVED)
    highway = (await travel.exits(session, constants, await _node(session, body_by_highway)))[0]

    assert trail.seconds > road.seconds > highway.seconds
    assert trail.seconds == pytest.approx(road.seconds * constants[R.ROAD_TRAIL_MULTIPLIER])


# --- transit -----------------------------------------------------------------


async def test_transit_takes_time(session: AsyncSession, constants: Constants) -> None:
    """The road costs time -- otherwise geography disappears along with the hauler."""
    here, there, body = await _two_nodes(session, seconds=30)
    transit = await travel.depart(session, constants, body, there)
    await session.commit()

    assert transit.arrives_at > transit.started_at
    assert body.node_id == here.id, "тело ещё не там: телепорта нет"
    assert transit.state is TravelState.GOING


async def test_arrival_happens_by_job(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """Closed the tab -- you still arrive: the arrival must be in the journal."""
    async with factory() as session, session.begin():
        _, there, body = await _two_nodes(session, seconds=30)
        transit = await travel.depart(session, constants, body, there)
        term, body_id, there_id = transit.arrives_at, body.id, there.id

    #: Before the deadline the job is not taken.
    assert await jobs.run_one(factory, now=term - timedelta(seconds=5)) is None
    job = await jobs.run_one(factory, now=term)
    assert job is not None and job.kind == "travel.leg"

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert body.node_id == there_id
        going = await travel.current(session, body)
        assert going is None, "переход закрыт"


async def test_autopath_builds_route_and_walks_itself(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """A click on a far node: the first leg is walked now, the tail as a plan,
    and each leg's arrival itself sends the body into the next (D-045)."""
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        a = await world.create_node(session, f"terra.pa.{stamp}", "А", area_m2=100)
        b = await world.create_node(session, f"terra.pb.{stamp}", "Б", area_m2=100)
        c = await world.create_node(session, f"terra.pc.{stamp}", "В", area_m2=100)
        await travel.connect(session, a, b, base_seconds=30)
        await travel.connect(session, b, c, base_seconds=30)
        identity = await world.create_identity(session, f"Путник-{stamp}")
        body = await world.print_body(session, identity, a)

        transit = await travel.depart(session, constants, body, c)
        assert transit.to_node_id == b.id, "первый отрезок — в соседа по маршруту"
        assert transit.plan == [str(c.id)], "хвост маршрута лежит планом"
        term1, body_id, b_id, c_id = transit.arrives_at, body.id, b.id, c.id

    #: The first leg arrived -- the second set out by itself, without a click.
    job = await jobs.run_one(factory, now=term1)
    assert job is not None and job.kind == "travel.leg"

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert body.node_id == b_id, "тело в промежуточном узле"
        further = await travel.current(session, body)
        assert further is not None and further.to_node_id == c_id
        assert not further.plan, "последний отрезок — без хвоста"
        term2 = further.arrives_at

    job = await jobs.run_one(factory, now=term2)
    assert job is not None

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert body.node_id == c_id, "дошёл до конца маршрута"
        assert await travel.current(session, body) is None


async def test_autopath_picks_fastest_route(
    session: AsyncSession, constants: Constants
) -> None:
    """The route is computed by time with surface, not by node count."""
    stamp = uuid.uuid4().hex[:8]
    a = await world.create_node(session, f"terra.qa.{stamp}", "А", area_m2=100)
    b = await world.create_node(session, f"terra.qb.{stamp}", "Б", area_m2=100)
    c = await world.create_node(session, f"terra.qc.{stamp}", "В", area_m2=100)
    d = await world.create_node(session, f"terra.qd.{stamp}", "Г", area_m2=100)
    #: Two paths to d: a long "direct" one via c and a short detour via b.
    await travel.connect(session, a, c, base_seconds=500, surface=Surface.TRAIL)
    await travel.connect(session, c, d, base_seconds=500, surface=Surface.TRAIL)
    await travel.connect(session, a, b, base_seconds=30, surface=Surface.PAVED)
    await travel.connect(session, b, d, base_seconds=30, surface=Surface.PAVED)

    laid = await travel.route(session, constants, a.id, d.id)
    assert laid == [b.id, d.id], "быстрый тракт бьёт короткое бездорожье"


async def test_autopath_to_disconnected_node_refuses(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, body = await _two_nodes(session)
    island = await world.create_node(
        session, f"terra.isle.{uuid.uuid4().hex[:6]}", "Остров", area_m2=100
    )
    with pytest.raises(travel.NoRoute):
        await travel.depart(session, constants, body, island)


async def test_cannot_walk_two_roads_at_once(
    session: AsyncSession, constants: Constants
) -> None:
    _, there, body = await _two_nodes(session)
    await travel.depart(session, constants, body, there)
    with pytest.raises(travel.AlreadyGoing):
        await travel.depart(session, constants, body, there)


# --- while walking you are absent --------------------------------------------


async def test_no_mining_en_route(session: AsyncSession, constants: Constants) -> None:
    """Matter requires presence, and there is no presence now (D-044)."""
    here, there, body = await _two_nodes(session)
    vein = await world.create_vein(session, here, "Железная руда", richness=60, remaining=1000)
    await travel.depart(session, constants, body, there)

    with pytest.raises(travel.InTransit):
        await mining.start(session, constants, body, vein)


async def test_no_loading_or_buying_en_route(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    here, there, body = await _two_nodes(session)
    yard = await world.node_container(session, here)
    await world.grant_item(session, yard, market.TERMINAL, quality=70, origin="тест")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "Железная руда", amount=5, quality=60, origin="тест")

    await travel.depart(session, constants, body, there)
    with pytest.raises(travel.InTransit):
        await market.load(session, constants, body, "Железная руда", 5)


async def test_no_recipe_copying_en_route(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The library does not work remotely -- nor halfway to it (D-053)."""
    here, there, body = await _two_nodes(session)
    here.properties = {"library": True}
    await session.flush()

    await travel.depart(session, constants, body, there)
    with pytest.raises(travel.InTransit):
        await craft.copy_recipe(session, catalog, body, "Гвозди")


async def test_no_batch_start_en_route(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    here, there, body = await _two_nodes(session)
    yard = await world.node_container(session, here)
    await world.grant_item(session, yard, "Верстак", quality=60, origin="тест")
    identity_id = body.identity_id
    from src.models.identity import Identity

    identity = await session.get(Identity, identity_id)
    await world.learn(session, identity, "Гвозди")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "Слиток железа", amount=5, quality=60, origin="тест")

    await travel.depart(session, constants, body, there)
    with pytest.raises(travel.InTransit):
        await craft.plan(session, constants, catalog, body, "Гвозди", 1)


# --- the road costs stamina (D-147) ------------------------------------------


async def test_road_costs_stamina(
    session: AsyncSession, constants: Constants
) -> None:
    """Time is a poor price: close the tab and you have arrived. The body pays the second."""
    _, there, body = await _two_nodes(session, seconds=3600)
    before = float(body.stamina)
    await travel.depart(session, constants, body, there)
    #: An hour of road is exactly the vault rate per hour. The spend goes by
    #: time, not by transit count: otherwise a step across the quarter is pricier than crossing the
    #: steppe.
    assert float(body.stamina) == pytest.approx(
        before - constants[R.TRAVEL_STAMINA_PER_HOUR]
    )


async def test_step_across_city_costs_almost_nothing(
    session: AsyncSession, constants: Constants
) -> None:
    """Seconds of road are fractions of a unit: geography does not punish a step."""
    _, there, body = await _two_nodes(session, seconds=6)
    before = float(body.stamina)
    await travel.depart(session, constants, body, there)
    spent_ = before - float(body.stamina)
    assert 0 < spent_ < constants[R.TRAVEL_STAMINA_PER_HOUR]


async def test_no_leaving_without_strength(
    session: AsyncSession, constants: Constants
) -> None:
    """One cannot set out on a road there is not enough strength for -- like starting a batch."""
    from decimal import Decimal

    _, there, body = await _two_nodes(session, seconds=36_000)
    body.stamina = Decimal("1")
    await session.flush()
    with pytest.raises(travel.NoStrength):
        await travel.depart(session, constants, body, there)
    assert await travel.current(session, body) is None, "отказ не оставляет перехода"


async def test_with_vehicle_road_costs_body_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The vehicle carries, not the legs (D-147, D-129).

    The vehicle is **harnessed**, not in the pocket: a barrow is heavier than
    the carry limit and is never in the hands at all (D-157).
    """
    from src.engine import transport

    here, there, body = await _two_nodes(session, seconds=3600)
    yard = await world.node_container(session, here)
    barrow = await world.grant_item(session, yard, "Тачка", quality=60, origin="тест")
    await transport.harness(session, constants, catalog, body, barrow)

    before = float(body.stamina)
    await travel.depart(session, constants, body, there)
    assert float(body.stamina) == pytest.approx(before)


async def test_road_price_visible_before_leaving(constants: Constants) -> None:
    """The player must see the price before the decision, not after."""
    hour = 3600
    walking = travel.stamina_cost(constants, hour, transport=False)
    with_wagon = travel.stamina_cost(constants, hour, transport=True)
    assert walking == pytest.approx(constants[R.TRAVEL_STAMINA_PER_HOUR])
    assert with_wagon == pytest.approx(
        walking * constants[R.TRANSPORT_STAMINA_K]
    )


async def _node(session: AsyncSession, body: Body):
    from src.models.world import Node

    node = await session.get(Node, body.node_id)
    assert node is not None
    return node
