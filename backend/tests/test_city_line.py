# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The land within the line (D-356): whatever lies inside a city's outline is
the city's, and the city sells it.

The owner, 2026-09-19: nodes inside the city could not be bought, and where
the city is and where it is not is decided by its border. The border is the
outline the map draws (`src.outline`, pinned against the map's own copy in
`test_outline.py`); these are the engine's readings of it -- what it takes,
what it lets go, what draws it, and what the window, the purchase and the
allotment say over the ground it covers. The races are in
`test_races_city_line.py`.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from city_line_kit import _events, _head, _node, _town
from estate_kit import _buyer
from src import globe, seed_once
from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import estate, facet, mapshot, places, sight, tick, travel, world
from src.engine.city import land as city_land
from src.engine.city import lookup
from src.models.catchup import CatchUpStep
from src.models.estate import Building, BuildSite, SiteState
from src.models.event import EventKind
from src.models.farm import Plot
from src.models.world import COVERED, PLOT, Layer, Node, Planet, Surface, only_covered


async def test_the_land_within_the_line_is_the_city_s(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A find inside the outline is the city's -- a plot, marked as covered; one
    beyond it stays nobody's. Asked again, the line takes nothing twice."""
    city, *_ = await _town(session, constants, catalog)
    inside = await _node(session, constants, "inside", 10, 10)
    beyond = await _node(session, constants, "beyond", 300, 0)
    assert await lookup.of_node(session, inside) is None

    assert await town.cover(session, constants, city) == (1, 0)
    assert inside.owner_city_id == city.id
    assert inside.properties[COVERED] is True and inside.properties[PLOT] is True
    assert (await lookup.of_node(session, inside)).id == city.id, "законы читают то же правило"
    assert beyond.owner_city_id is None and COVERED not in beyond.properties

    told = await _events(session, EventKind.LAND_COVERED, inside)
    assert len(told) == 1
    #: A nameless find is told by the keys of its ground, never by the
    #: vault's word (D-332 item 4): the digest names it in the reader's language.
    said = facet.told_of(constants, inside)
    assert {key: told[0].payload.get(key) for key in said} == said
    assert "node" not in told[0].payload
    assert told[0].payload["city"] == city.name

    assert await town.cover(session, constants, city) == (0, 0), "второй раз брать нечего"


async def test_a_covered_plot_is_sold_and_then_draws_the_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The window prices a covered find and the purchase takes it; bought, it
    is land with paper on it, and such land draws the city's line."""
    from src.api.commands.look import _look

    city, home, *_ = await _town(session, constants, catalog)
    inside = await _node(session, constants, "inside", 10, 10)
    await town.cover(session, constants, city)
    homes = {city.id: home.id}
    by_key = {home.id: home.key}

    #: Covered and nobody's: the city's land, but not the line's frame.
    frame, _ = await town.frame_of(session, city)
    assert inside.id not in {node.id for node in frame}
    assert mapshot.territory_key(inside, homes, by_key) is None, "покрытое не рисует черту"

    identity, body = await _buyer(session, inside, city=city)
    seen = (await _look({"identity_id": identity.id}, session, {}))["look"]
    assert seen["node"]["price"] > 0, "окно называет цену"
    deed = await estate.buy(session, constants, catalog, body, inside)
    assert deed.node_id == inside.id and inside.owner_identity_id == identity.id

    frame, _ = await town.frame_of(session, city)
    assert inside.id in {node.id for node in frame}, "земля с бумагой рисует черту"
    assert mapshot.territory_key(inside, homes, by_key) == home.key

    #: And pays for the ground like any plot of the rings (D-236).
    assert await estate.land_tax_of(session, constants, catalog, inside) > 0
    await estate.levy_land_tax(session, constants, catalog)
    assert await _events(session, EventKind.LAND_TAXED, inside), "налог дошёл и сюда"


async def test_the_line_drawing_back_lets_go_only_empty_land_nobody_holds(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A covered find the line no longer reaches is wild again if nothing is on
    it. One with a holder is the frame's; one with a house on it stays the
    city's and joins the frame -- and draws the line round itself."""
    city, *_ = await _town(session, constants, catalog)
    covered = {COVERED: True, PLOT: True}
    stray = await _node(session, constants, "stray", 0, -300, properties=covered)
    held = await _node(session, constants, "held", 300, 0, properties=covered)
    built = await _node(session, constants, "built", -300, 0, properties=covered)
    for node in (stray, held, built):
        node.owner_city_id = city.id
    holder = await world.create_identity(session, f"Держатель-{uuid.uuid4().hex[:6]}")
    held.owner_identity_id = holder.id
    session.add(Building(node_id=built.id, area_m2=40, footprint_m2=40))
    await session.flush()

    assert await town.cover(session, constants, city) == (0, 1)
    assert stray.owner_city_id is None
    assert COVERED not in stray.properties and PLOT not in stray.properties
    assert stray.center_steps is None
    assert len(await _events(session, EventKind.LAND_UNCOVERED, stray)) == 1

    assert held.owner_city_id == city.id and held.properties[COVERED] is True
    assert built.owner_city_id == city.id, "земля с домом не дичает"
    assert COVERED not in built.properties and built.properties[PLOT] is True
    frame, _ = await town.frame_of(session, city)
    assert built.id in {node.id for node in frame}, "и рисует черту"


async def test_a_highway_takes_a_plot_and_a_covered_end_into_the_frame(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What a highway takes is a plot, sold like a ring's (D-356 item 4); and
    a covered node a highway reaches is the highway's, drawing the line --
    held or not, so a holder handing it back does not leave it to the line."""
    city, home, gate, *_ = await _town(session, constants, catalog)
    far = await _node(session, constants, "far", 90, 0)
    way = await travel.connect(session, gate, far, base_seconds=60, surface=Surface.PAVED)
    taken = await city_land.annex_by_way(session, constants, way)
    assert taken is not None and taken[1].id == far.id
    assert far.owner_city_id == city.id
    assert far.properties[PLOT] is True and COVERED not in far.properties
    assert await estate.sale_refusal(session, constants, far) is None, "взятое трактом продаётся"

    inside = await _node(session, constants, "inside", 10, 10)
    bought = await _node(session, constants, "bought", -10, 10)
    await town.cover(session, constants, city)
    assert only_covered(inside) and only_covered(bought)
    holder = await world.create_identity(session, f"Держатель-{uuid.uuid4().hex[:6]}")
    bought.owner_identity_id = holder.id
    await session.flush()
    for node in (inside, bought):
        way = await travel.connect(session, home, node, base_seconds=60, surface=Surface.PAVED)
        assert await city_land.annex_by_way(session, constants, way) is None, "земля уже города"
        assert node.owner_city_id == city.id and COVERED not in node.properties, node.key
        assert node.properties[PLOT] is True
        assert not await _events(session, EventKind.LAND_ANNEXED, node), "из рук в руки ничего"
    frame, _ = await town.frame_of(session, city)
    assert inside.id in {node.id for node in frame}


async def test_the_window_prices_only_what_the_purchase_sells(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city's own outskirts are empty and still not for sale (D-282): the
    window says no price rather than a price the purchase refuses."""
    from src.api.commands.look import _look

    city, _, gate, north, _ = await _town(session, constants, catalog)
    refusal = await estate.sale_refusal(session, constants, gate)
    assert refusal is not None and refusal.key == "estate-land-not-a-plot"
    identity, _ = await _buyer(session, gate, city=city)
    seen = (await _look({"identity_id": identity.id}, session, {}))["look"]
    assert "price" not in seen["node"], "своя локация города без цены"

    identity, _ = await _buyer(session, north, city=city)
    seen = (await _look({"identity_id": identity.id}, session, {}))["look"]
    assert seen["node"]["price"] > 0, "участок кольца — с ценой"


async def test_somebody_else_s_work_keeps_a_find_off_the_list_and_one_s_own_does_not(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A bed marked or a house begun while the ground was wild (D-198) is its
    maker's work: nobody else buys the ground from under it -- and the maker
    buys the ground under his own work like any other."""
    city, *_ = await _town(session, constants, catalog)
    bed = await _node(session, constants, "bed", 10, 10)
    site = await _node(session, constants, "site", -10, 10)
    farmer = await world.create_identity(session, f"Пахарь-{uuid.uuid4().hex[:6]}")
    stranger = await world.create_identity(session, f"Чужой-{uuid.uuid4().hex[:6]}")
    session.add(
        Plot(node_id=bed.id, owner_identity_id=farmer.id, name="Полоса", area_m2=10, fertility=50)
    )
    session.add(
        BuildSite(
            node_id=site.id,
            owner_identity_id=farmer.id,
            footprint_m2=10,
            kind="wood",
            state=SiteState.GATHERING,
        )
    )
    await session.flush()
    await town.cover(session, constants, city)
    assert bed.owner_city_id == city.id and site.owner_city_id == city.id
    for node in (bed, site):
        refusal = await estate.sale_refusal(session, constants, node, buyer=stranger.id)
        assert refusal is not None and refusal.key == "estate-land-not-vacant", node.key
        assert await estate.sale_refusal(session, constants, node, buyer=farmer.id) is None


async def test_the_city_hands_out_only_empty_land(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The allotment asks what the sale asks (D-356): a covered vein is not
    handed out, somebody's beds are not handed to somebody else -- and a
    settler may be given the ground under his own beds."""
    city, home, *_ = await _town(session, constants, catalog)
    head, head_body = await _head(session, city, home)
    vein = await _node(session, constants, "vein", 10, 10)
    await world.create_vein(session, vein, "iron_ore", richness=50, remaining=1000)
    bed = await _node(session, constants, "bed", -10, 10)
    farmer = await world.create_identity(session, f"Пахарь-{uuid.uuid4().hex[:6]}")
    stranger = await world.create_identity(session, f"Чужой-{uuid.uuid4().hex[:6]}")
    session.add(
        Plot(node_id=bed.id, owner_identity_id=farmer.id, name="Полоса", area_m2=10, fertility=50)
    )
    await session.flush()
    await town.cover(session, constants, city)

    for node, to in ((vein, stranger), (bed, stranger)):
        with pytest.raises(town.CityError) as refused:
            await town.allot(session, head, city, node, to, body=head_body)
        assert refused.value.key == "city-land-not-vacant", node.key
        assert node.owner_identity_id is None
    await town.allot(session, head, city, bed, farmer, body=head_body)
    assert bed.owner_identity_id == farmer.id


async def test_city_land_is_public_wherever_it_hangs(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Land a city holds is seen by everybody, dark (D-097): it is inside the
    city's walls, wherever it hangs."""
    city, home, *_ = await _town(session, constants, catalog)
    inside = await _node(session, constants, "inside", 10, 10)
    wild = await _node(session, constants, "wild", 300, 0)
    await town.cover(session, constants, city)
    nodes = list((await session.execute(select(Node))).scalars().all())
    public = sight._public(nodes, [], {home.id})
    assert inside.id in public
    assert wild.id not in public


async def test_the_tick_takes_up_what_the_line_covers(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Whatever way of changing a frame forgets to ask the line, the tick asks it."""
    city, *_ = await _town(session, constants, catalog)
    inside = await _node(session, constants, "inside", 10, 10)
    assert await tick._lines(session, datetime.now(UTC)) == {"land_covered": 1, "land_uncovered": 0}
    assert inside.owner_city_id == city.id


async def test_a_scout_s_find_asks_only_the_lines_that_reach_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A find changes no frame: only a line whose raster reaches its point is
    asked, and a way between two nodes of one frame asks that frame's line."""
    city, home, gate, *_ = await _town(session, constants, catalog)
    inside = await _node(session, constants, "inside", 10, 10)
    far = places.geo_of(await _node(session, constants, "far", 5000, 0))
    near = places.geo_of(inside)
    assert far is not None and near is not None

    assert await town.cover_near(session, constants, home.planet, far) == (0, 0)
    assert inside.owner_city_id is None, "далёкая находка черту не спрашивает"
    assert await town.cover_near(session, constants, home.planet, near) == (1, 0)
    assert inside.owner_city_id == city.id

    other = await _node(session, constants, "other", -10, -10)
    lone = await _node(session, constants, "lone", 5000, 5000)
    await town.cover_way(session, constants, home, lone)
    assert other.owner_city_id is None, "путь в дичь не улица"
    await town.cover_way(session, constants, home, gate)
    assert other.owner_city_id == city.id, "улица между узлами каркаса спрашивает черту"


async def test_two_lines_over_one_node_the_elder_city_s_takes_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Where two cities' lines cross, the node goes to the city founded first
    -- the one whose line was there first -- whichever tick asks (D-356)."""
    elder, *_ = await _town(session, constants, catalog)
    #: A lone node eleven metres past the elder's gate: its own land reaches
    #: six, and the elder's gate reaches twelve and a half.
    younger_home = await _node(session, constants, "younger", 36, 0)
    younger = await town.found(session, catalog, younger_home, f"Младший-{uuid.uuid4().hex[:6]}")
    younger_home.owner_city_id = younger.id
    between = await _node(session, constants, "between", 32, 0)
    #: Founded in one transaction, the two share a moment; the order is set.
    founded = datetime(2026, 9, 1, tzinfo=UTC)
    elder.created_at = founded
    younger.created_at = founded + timedelta(days=1)
    await session.flush()

    await town.cover_all(session, constants)
    assert between.owner_city_id == elder.id


async def test_another_city_s_node_never_moves(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A line covering land a city holds already takes nothing (D-332)."""
    city, *_ = await _town(session, constants, catalog)
    lonely = await _node(session, constants, "lonely", 5000, 5000)
    other = await town.found(session, catalog, lonely, f"Другой-{uuid.uuid4().hex[:8]}")
    theirs = await _node(session, constants, "theirs", 10, 10)
    theirs.owner_city_id = other.id
    await session.flush()
    await town.cover(session, constants, city)
    assert theirs.owner_city_id == other.id


async def test_the_capital_takes_the_oil_field_and_leaves_the_first_axe_wild(
    session: AsyncSession,
) -> None:
    """In the world the vault lays, the capital's line covers the oil field
    beside its gate and leaves the coal pit and the floodplain -- where a
    newcomer makes the first axe by hand (D-196) -- nobody's."""
    from src.seed import seed

    await seed(session)
    keys = ("terra.oilfield", "terra.coal", "terra.floodplain", "terra.capital.core")
    nodes = {
        node.key: node
        for node in (await session.execute(select(Node).where(Node.key.in_(keys)))).scalars()
    }
    capital = nodes["terra.capital.core"].owner_city_id
    assert capital is not None
    assert nodes["terra.oilfield"].owner_city_id == capital
    assert nodes["terra.oilfield"].properties[COVERED] is True
    assert nodes["terra.coal"].owner_city_id is None
    assert nodes["terra.floodplain"].owner_city_id is None


async def test_land_a_highway_took_before_the_rule_becomes_a_plot_once(
    session: AsyncSession,
    constants: Constants,
) -> None:
    """A find a highway took before D-356 has no plot mark; the deploy marks it
    once (`seed_once.TAKEN_LAND_IS_PLOTS`), and never again after that."""
    from src.seed import seed

    await seed(session)
    capital = await session.scalar(select(Node).where(Node.key == "terra.capital.core"))
    sphere = await session.get(Node, capital.parent_id)
    lat, lon = places.geo_of(capital)
    per_deg = globe.radius_m(constants, Planet.TERRA) * math.pi / 180

    async def find(name: str, north_m: float) -> Node:
        """A find north of the capital, as a highway would have left it."""
        return await world.create_node(
            session,
            f"terra.{name}.{uuid.uuid4().hex[:8]}",
            "",
            area_m2=100,
            layer=Layer.PLANET,
            parent=sphere,
            properties={
                places.PLACE: {places.PLACE_LAT: lat + north_m / per_deg, places.PLACE_LON: lon}
            },
        )

    taken = await find("taken", 60)
    taken.owner_city_id = capital.owner_city_id
    await session.execute(
        delete(CatchUpStep).where(CatchUpStep.step == seed_once.TAKEN_LAND_IS_PLOTS)
    )
    await session.flush()

    await seed(session)
    await session.refresh(taken)
    assert taken.properties.get(PLOT) is True

    later = await find("later", 90)
    later.owner_city_id = capital.owner_city_id
    await session.flush()
    await seed(session)
    await session.refresh(later)
    assert PLOT not in (later.properties or {}), "второй деплой шаг не повторяет"


async def test_a_second_deploy_founds_no_city_on_the_planet_s_sphere(
    session: AsyncSession,
) -> None:
    """Since D-330 the capital stands on its core, which hangs on the sphere.

    The catch-up took the core's parent for the capital: at the first deploy
    of a world laid after D-330 it founded a city on Terra's sphere, flagged
    it the capital, and wrote every find of the planet to it as its built-up
    area -- and a city over the whole planet kept every find out of every
    line (D-356).
    """
    from src.models.city import City
    from src.seed import seed

    await seed(session)
    find = await world.create_node(
        session, f"terra.wild.{uuid.uuid4().hex[:8]}", "", area_m2=100, layer=Layer.PLANET,
        parent=await session.get(Node, (await session.scalar(
            select(Node).where(Node.key == "terra.capital.core"))).parent_id),
    )  # fmt: skip
    await session.flush()
    await seed(session)

    cities = list((await session.execute(select(City))).scalars().all())
    homes = [await session.get(Node, city.node_id) for city in cities]
    assert all(home.layer is Layer.PLANET for home in homes), [home.key for home in homes]
    assert [city.name for city in cities if city.capital] == ["Столица Терры"]
    await session.refresh(find)
    assert find.owner_city_id is None, "находка вдали от столицы ничья"


async def test_a_ruin_within_the_line_is_the_city_s_and_never_a_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A Forerunner ruin inside the line or at the end of a highway is the
    city's land and not ground to divide (D-356, D-232): neither sold nor
    handed out -- its root, its hall and the rooms under the root alike."""
    from src.engine.death import PRECURSOR

    city, _, gate, *_ = await _town(session, constants, catalog)
    ruin = await _node(session, constants, "ruin", 10, 10, properties={PRECURSOR: True})
    room = await _node(session, constants, "room", -10, 10, parent=ruin)
    await town.cover(session, constants, city)
    for node in (ruin, room):
        assert node.owner_city_id == city.id and node.properties[COVERED] is True, node.key
        assert PLOT not in node.properties, node.key
        refusal = await estate.sale_refusal(session, constants, node)
        assert refusal is not None and refusal.key == "estate-land-ruin", node.key

    hall = await _node(session, constants, "hall", 90, 0, properties={PRECURSOR: True})
    way = await travel.connect(session, gate, hall, base_seconds=60, surface=Surface.PAVED)
    await city_land.annex_by_way(session, constants, way)
    assert hall.owner_city_id == city.id and PLOT not in hall.properties


async def test_the_one_off_step_marks_no_land_of_a_city_on_the_sphere(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """In a world the old catch-up hurt, every find of Terra was written to a
    city founded on the sphere (D-356 item 10). None of it is highway land,
    and the one-off step makes none of it a plot to sell."""
    from src.seed import seed

    await seed(session)
    capital = await session.scalar(select(Node).where(Node.key == "terra.capital.core"))
    sphere = await session.get(Node, capital.parent_id)
    hurt = await town.found(session, catalog, sphere, f"Терра-{uuid.uuid4().hex[:6]}")
    find = await world.create_node(
        session, f"terra.hurt.{uuid.uuid4().hex[:8]}", "", area_m2=100, layer=Layer.PLANET,
        parent=sphere,
    )  # fmt: skip
    find.owner_city_id = hurt.id
    await session.execute(
        delete(CatchUpStep).where(CatchUpStep.step == seed_once.TAKEN_LAND_IS_PLOTS)
    )
    await session.flush()

    await seed(session)
    await session.refresh(find)
    assert PLOT not in (find.properties or {}), "земля города на сфере — не участок"
