"""Transport and convoy (D-107, D-129, D-157).

Checked is what transport was introduced for at all:

* cargo rides **in the hold**, not in hands: the carry limit is got around by
  a wagon, not a worn backpack;
* offroad lets no vehicle through at all -- the road is a precondition of trade;
* the convoy follows the body by itself and wears per leg, the more the fuller the hold;
* a broken convoy stops, and the cargo stays lying where it stopped.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import death, gear, jobs, transport, travel, world
from src.models.identity import Body
from src.models.inventory import Item
from src.models.travel import Harness
from src.models.world import Node, Surface

#: What we haul: raw material with a mass of a kilogram per unit (`inventory.mass_by_kind`).
CARGO = "Железная руда"
CART = "Повозка"
BARROW = "Тачка"


async def _convoy(
    session: AsyncSession,
    *,
    surface: Surface = Surface.ROAD,
    seconds: float = 600,
    vehicle: str = CART,
):
    """Two nodes, a body and a vehicle standing nearby."""
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.tha.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.thb.{stamp}", "Там", area_m2=100)
    await travel.connect(session, here, there, base_seconds=seconds, surface=surface)
    identity = await world.create_identity(session, f"Возчик-{stamp}")
    body = await world.print_body(session, identity, here)
    yard = await world.node_container(session, here)
    cart = await world.grant_item(
        session, yard, vehicle, amount=1, origin="сценарий теста"
    )
    return here, there, body, cart


async def _to_hands(session: AsyncSession, body: Body, qty: float) -> Item:
    """Put cargo into the hands. In the game that is several trips: the hand is small (D-146)."""
    pocket = await world.body_container(session, body)
    return await world.grant_item(
        session, pocket, CARGO, amount=qty, origin="сценарий теста"
    )


# --- harness -----------------------------------------------------------------


async def test_harness_to_what_is_nearby(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body, cart = await _convoy(session)
    await transport.harness(session, constants, catalog, body, cart)
    harnessed = await transport.harnessed(session, body)
    assert harnessed is not None and harnessed.id == cart.id


async def test_cannot_harness_to_grain_sack(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A vehicle is `kind: vehicle` from the vault, not everything that is heavy."""
    _, _, body, _ = await _convoy(session)
    sack = await _to_hands(session, body, 1)
    with pytest.raises(transport.NotVehicle):
        await transport.harness(session, constants, catalog, body, sack)


async def test_foreign_harness_not_hijacked(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    here, _, body, cart = await _convoy(session)
    await transport.harness(session, constants, catalog, body, cart)

    neighbour_id = await world.create_identity(session, f"Сосед-{uuid.uuid4().hex[:6]}")
    neighbour = await world.print_body(session, neighbour_id, here)
    with pytest.raises(transport.AlreadyHarnessed):
        await transport.harness(session, constants, catalog, neighbour, cart)


async def test_unharnessing_leaves_convoy_with_cargo(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Abandoning a loaded convoy is a normal move of the game, not an engine error."""
    _, _, body, cart = await _convoy(session)
    await transport.harness(session, constants, catalog, body, cart)
    cargo = await _to_hands(session, body, 20)
    await transport.load(session, constants, catalog, body, cargo)

    await transport.unharness(session, body)
    assert await transport.harnessed(session, body) is None
    assert await transport.cargo_mass(session, catalog, cart) == pytest.approx(20)


# --- hold --------------------------------------------------------------------


async def test_cargo_rides_in_hold_not_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """This is what it was all made for: the hands limit is got around by a wagon."""
    _, _, body, cart = await _convoy(session)
    await transport.harness(session, constants, catalog, body, cart)

    hands_limit = await gear.capacity(session, constants, catalog, body)
    #: More than can be carried -- and all of it rides in the hold.
    cargo = await _to_hands(session, body, hands_limit * 3)
    carried = await transport.load(session, constants, catalog, body, cargo)

    assert carried == pytest.approx(hands_limit * 3)
    assert await gear.load_of(session, catalog, body) == pytest.approx(0), (
        "погруженное больше не в руках"
    )
    assert await transport.cargo_mass(session, catalog, cart) > hands_limit


async def test_hold_is_not_elastic(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body, cart = await _convoy(session, vehicle=BARROW)
    await transport.harness(session, constants, catalog, body, cart)
    limit = transport.capacity(constants, BARROW)
    cargo = await _to_hands(session, body, limit + 1)
    with pytest.raises(transport.Overloaded):
        await transport.load(session, constants, catalog, body, cargo)


async def test_unloading_hits_hands_limit(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One takes from the hold by hand: the carry limit does not go anywhere (D-146)."""
    _, _, body, cart = await _convoy(session)
    await transport.harness(session, constants, catalog, body, cart)
    cargo = await _to_hands(session, body, 100)
    await transport.load(session, constants, catalog, body, cargo)

    in_hold = (await transport.cargo_items(session, cart))[0]
    with pytest.raises(gear.Overloaded):
        await transport.unload(session, constants, catalog, body, in_hold)

    #: A handful at a time is fine, and that is an honest price: the hand is small, the wagon big.
    qty = await transport.unload(session, constants, catalog, body, in_hold, 10)
    assert qty == pytest.approx(10)
    assert await gear.load_of(session, catalog, body) == pytest.approx(10)


# --- road (D-107) ------------------------------------------------------------


async def test_offroad_does_not_let_convoy_through(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The road is a precondition of trade, not a convenience."""
    _, there, body, cart = await _convoy(session, surface=Surface.TRAIL)
    await transport.harness(session, constants, catalog, body, cart)
    with pytest.raises(transport.Impassable):
        await travel.depart(session, constants, body, there)

    #: A walker passes the same trail: the ban is on the vehicle, not the person.
    await transport.unharness(session, body)
    assert await travel.depart(session, constants, body, there) is not None


async def test_heavy_needs_highway(constants: Constants) -> None:
    """A light one goes by road, a heavy one only by paved (D-107)."""
    #: A spaceship is no longer a vehicle (D-201): it is a subgraph, not a
    #: thing in a node. A boat took its place as the heavy example. Vehicles
    #: are recognised by thing class or by the exact table word (D-215) --
    #: substring matching is gone, so the name is the word itself.
    light, heavy = CART, "Судно"
    assert not transport.heavy(constants, light)
    assert transport.heavy(constants, heavy)
    assert transport.passable(constants, Surface.ROAD, light)
    assert not transport.passable(constants, Surface.ROAD, heavy)
    assert transport.passable(constants, Surface.PAVED, heavy)
    assert not transport.passable(constants, Surface.TRAIL, light)


async def test_convoy_goes_faster_and_spends_no_strength(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A wagon carries both time and body: `transport.speed_k` and `stamina_k`."""
    _, there, on_foot, _ = await _convoy(session)
    walking = await travel.depart(session, constants, on_foot, there)
    walking_seconds = (walking.arrives_at - walking.started_at).total_seconds()
    before = float(on_foot.stamina)
    assert float(on_foot.stamina) < constants[R.BODY_STAMINA_MAX]

    _, there_, carter, cart = await _convoy(session)
    await transport.harness(session, constants, catalog, carter, cart)
    strength_before = float(carter.stamina)
    by_convoy = await travel.depart(session, constants, carter, there_)
    convoy_seconds = (by_convoy.arrives_at - by_convoy.started_at).total_seconds()

    assert convoy_seconds == pytest.approx(
        walking_seconds / transport.speed(constants, CART), rel=1e-3
    )
    assert float(carter.stamina) == pytest.approx(strength_before), "везёт транспорт, а не ноги"
    assert before < constants[R.BODY_STAMINA_MAX]


async def test_convoy_route_built_over_passable(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A walker takes the trail shortcut, the convoy goes around by road."""
    stamp = uuid.uuid4().hex[:8]
    a = await world.create_node(session, f"terra.ra.{stamp}", "А", area_m2=100)
    b = await world.create_node(session, f"terra.rb.{stamp}", "Б", area_m2=100)
    c = await world.create_node(session, f"terra.rc.{stamp}", "В", area_m2=100)
    #: The direct trail is short but closed to the convoy; the road via B is longer.
    await travel.connect(session, a, c, base_seconds=60, surface=Surface.TRAIL)
    await travel.connect(session, a, b, base_seconds=300, surface=Surface.ROAD)
    await travel.connect(session, b, c, base_seconds=300, surface=Surface.ROAD)

    on_foot = await travel.route(session, constants, a.id, c.id)
    assert on_foot == [c.id], "пешему тропа короче"

    by_convoy = await travel.route(session, constants, a.id, c.id, vehicle=CART)
    assert by_convoy == [b.id, c.id], "обоз идёт кругом по дороге"


async def test_convoy_has_nowhere_if_only_trail(
    session: AsyncSession, constants: Constants
) -> None:
    stamp = uuid.uuid4().hex[:8]
    a = await world.create_node(session, f"terra.sa.{stamp}", "А", area_m2=100)
    b = await world.create_node(session, f"terra.sb.{stamp}", "Б", area_m2=100)
    await travel.connect(session, a, b, base_seconds=60, surface=Surface.TRAIL)
    with pytest.raises(travel.NoRoute):
        await travel.route(session, constants, a.id, b.id, vehicle=CART)


# --- convoy on the road ------------------------------------------------------


async def test_convoy_arrives_with_cargo(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The vehicle and hold move after the body -- by a journal job, like the body."""
    async with factory() as session, session.begin():
        _, there, body, cart = await _convoy(session)
        await transport.harness(session, constants, catalog, body, cart)
        cargo = await _to_hands(session, body, 30)
        await transport.load(session, constants, catalog, body, cargo)
        transit = await travel.depart(session, constants, body, there)
        term, body_id, there_id, cart_id = (
            transit.arrives_at, body.id, there.id, cart.id,
        )

    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        body = await session.get(Body, body_id)
        cart = await session.get(Item, cart_id)
        there = await session.get(Node, there_id)
        yard = await world.node_container(session, there)
        assert body.node_id == there_id
        assert cart.container_id == yard.id, "повозка приехала за телом"
        assert await transport.cargo_mass(session, catalog, cart) == pytest.approx(30)
        assert await transport.harnessed(session, body) is not None


async def test_convoy_wears_per_leg(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A full hold wears more than an empty one: it is not air that is hauled (D-129)."""
    async def drive(cargo_: float) -> float:
        async with factory() as session, session.begin():
            _, there, body, cart = await _convoy(session)
            await transport.harness(session, constants, catalog, body, cart)
            if cargo_:
                await transport.load(
                    session, constants, catalog, body,
                    await _to_hands(session, body, cargo_),
                )
            transit = await travel.depart(session, constants, body, there)
            term, cart_id = transit.arrives_at, cart.id
        assert await jobs.run_one(factory, now=term) is not None
        async with factory() as session:
            cart = await session.get(Item, cart_id)
            return float(cart.condition)

    empty = await drive(0)
    loaded = await drive(transport.capacity(constants, CART))
    assert empty < constants[R.QUALITY_SCALE].max, "переход изнашивает"
    assert loaded < empty, "полный трюм изнашивает сильнее пустого"


async def test_breakdown_stops_convoy_and_drops_cargo(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A breakdown is a stop, not a loss of cargo (D-157)."""
    async with factory() as session, session.begin():
        stamp = uuid.uuid4().hex[:8]
        a = await world.create_node(session, f"terra.wa.{stamp}", "А", area_m2=100)
        b = await world.create_node(session, f"terra.wb.{stamp}", "Б", area_m2=100)
        c = await world.create_node(session, f"terra.wc.{stamp}", "В", area_m2=100)
        await travel.connect(session, a, b, base_seconds=300)
        await travel.connect(session, b, c, base_seconds=300)
        identity = await world.create_identity(session, f"Возчик-{stamp}")
        body = await world.print_body(session, identity, a)
        yard = await world.node_container(session, a)
        cart = await world.grant_item(
            session, yard, CART, amount=1, origin="сценарий теста"
        )
        #: The wagon on its last legs: the next leg finishes it.
        cart.condition = Decimal("0.5")
        await transport.harness(session, constants, catalog, body, cart)
        await transport.load(
            session, constants, catalog, body, await _to_hands(session, body, 25)
        )

        transit = await travel.depart(session, constants, body, c)
        assert transit.plan, "маршрут из двух отрезков"
        term, body_id, b_id, cart_id = transit.arrives_at, body.id, b.id, cart.id

    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        body = await session.get(Body, body_id)
        assert body.node_id == b_id, "обоз встал там, где сломался"
        assert await session.get(Item, cart_id) is None, "разбитая повозка кончилась"
        assert await transport.harnessed(session, body) is None, "упряжка распалась"
        assert await travel.current(session, body) is None, "маршрут прерван"

        yard = await world.node_container(session, await session.get(Node, b_id))
        lies = (
            await session.execute(select(Item).where(Item.container_id == yard.id))
        ).scalars().all()
        assert [thing.type_key for thing in lies] == [CARGO], "груз остался в узле"


async def test_death_unharnesses(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The dead pull nothing, and the convoy stays standing with the cargo."""
    _, _, body, cart = await _convoy(session)
    await transport.harness(session, constants, catalog, body, cart)
    await transport.load(
        session, constants, catalog, body, await _to_hands(session, body, 10)
    )

    await death.die(session, constants, body, cause="сценарий теста")
    assert await transport.harnessed(session, body) is None
    assert (
        await session.execute(select(Harness).where(Harness.item_id == cart.id))
    ).scalar_one_or_none() is None
    assert await transport.cargo_mass(session, catalog, cart) == pytest.approx(10)
