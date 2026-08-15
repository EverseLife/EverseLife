"""Exploration: the map grows on foot (D-152).

Checked is what exploration was introduced this way for:

* a run costs stamina and time, and does not start at all without strength;
* a find is a node connected by an edge to where you left from: no teleport;
* the vein's species comes from the vault (`gives` of the "Mining" operation),
  not from a list in code -- add a fifth species and it starts being found by itself;
* what is found is **nobody's**: the finder gets the right of first night, not ownership.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import explore, world
from src.models.job import Job, JobKind, JobState
from src.models.world import Edge, Layer, Node, Vein
from src.units import MINUTES_PER_HOUR, SECONDS_PER_HOUR

#: Seconds in a minute -- as many as minutes in an hour.
SECONDS_PER_MINUTE = MINUTES_PER_HOUR


async def _scout(session: AsyncSession):
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    gate = await world.create_node(
        session, f"terra.gate.{stamp}", "Выход", area_m2=80,
        layer=Layer.PLANET, parent=planet,
    )
    identity = await world.create_identity(session, f"Разведчик-{stamp}")
    body = await world.print_body(session, identity, gate)
    return planet, gate, body


async def _townsman(session: AsyncSession, catalog):
    """The body is in the city: a plot is sought from inside the city, not from the road (D-089)."""
    from src.engine import city as town

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=planet,
    )
    core = await world.create_node(
        session, f"terra.city.{stamp}.core", "Ядро", area_m2=100,
        parent=delegate, properties={"кольцо": 0},
    )
    city = await town.found(session, catalog, delegate, "Столица")
    core.owner_city_id = city.id
    await session.flush()
    identity = await world.create_identity(session, f"Горожанин-{stamp}")
    body = await world.print_body(session, identity, core)
    return city, core, body


async def _walk_over(session: AsyncSession, node: Node, *, finds: int) -> None:
    """Set the node's find count: this many times people already left from here not in vain."""
    node.properties = {**(node.properties or {}), explore.FOUND_HERE: finds}
    await session.flush()


async def _return(session: AsyncSession, body) -> None:
    """Run the run to the end -- the same way the worker would."""
    job = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.EXPLORE_SURVEY.value,
                Job.body_id == body.id,
                Job.state == JobState.PENDING,
            )
        )
    ).scalars().first()
    assert job is not None
    await explore.returned(session, job)
    job.state = JobState.DONE
    await session.flush()


async def test_run_costs_stamina(
    session: AsyncSession, constants: Constants
) -> None:
    """Paid by time in the field: a short run is cheap but not free (D-156)."""
    _, _, body = await _scout(session)
    before = float(body.stamina)
    await explore.survey(session, constants, body)
    written_off = before - float(body.stamina)
    assert written_off > 0, "разведка — работа, а не прогулка"
    assert written_off < constants[R.EXPLORE_ATTEMPT_STAMINA], (
        "минутный заход не может стоить как заход полной длины"
    )


async def test_lack_of_strength_lengthens_run_not_blocks(
    session: AsyncSession, constants: Constants
) -> None:
    """What was missing the scout sleeps off in the field and continues.

    A run in trodden surroundings takes hours and costs accordingly; a body
    with one unit leaves anyway but returns later -- by the sleep time per
    `body.hibernation_rate` -- and with zero stamina.
    """
    _, gate, body = await _scout(session)
    await _walk_over(session, gate, finds=10)
    body.stamina = Decimal("1")
    await session.flush()

    start = datetime.now(UTC)
    run = await explore.survey(session, constants, body, now=start)
    assert float(body.stamina) == 0, "всё, что было, ушло в поле"

    #: Longer than an ordinary run's ceiling: sleep time was added.
    ceiling = constants[R.EXPLORE_ATTEMPT_HOURS] * MINUTES_PER_HOUR
    was_going = (run.run_at - start).total_seconds() / SECONDS_PER_MINUTE
    assert was_going > ceiling, "дефицит сил досыпается в поле, и заход длиннее"


async def test_scout_unavailable_like_sleeper(
    session: AsyncSession, constants: Constants
) -> None:
    """Exploration is a body state: while the run goes, in-person is closed."""
    from src.engine import travel

    _, _, body = await _scout(session)
    await explore.survey(session, constants, body)
    with pytest.raises(travel.InField):
        await travel.require_here(session, body)


async def test_scout_does_not_walk_away(
    session: AsyncSession, constants: Constants
) -> None:
    """The body is in the field -- it has nowhere to walk from: it is not in the node (D-152).

    Setting out is checked by the same door as every in-person action: keeping
    a separate list of conditions for it means forgetting a line in it one day
    -- exactly how the scout went wandering across the map.
    """
    from src.engine import travel

    planet, gate, body = await _scout(session)
    adjacent = await world.create_node(
        session, f"terra.next.{uuid.uuid4().hex[:8]}", "Соседний", area_m2=100,
        layer=Layer.PLANET, parent=planet,
    )
    await travel.connect(session, gate, adjacent, base_seconds=30)

    await explore.survey(session, constants, body)
    with pytest.raises(travel.InField):
        await travel.depart(session, constants, body, adjacent)
    assert body.node_id == gate.id, "тело сдвинулось, оставаясь в разведке"

    #: Cancelled the run -- and the road is open again.
    await explore.cancel(session, body)
    await travel.depart(session, constants, body, adjacent)


async def test_cancel_returns_scout_immediately(
    session: AsyncSession, constants: Constants
) -> None:
    """Turning back is allowed: the run is cancelled, the body free, no find will come."""
    from src.engine import travel

    _, _, body = await _scout(session)
    run = await explore.survey(session, constants, body)
    await explore.cancel(session, body)

    await session.refresh(run)
    assert run.state is JobState.CANCELLED
    #: The body is in the exit node again and free for in-person actions.
    await travel.require_here(session, body)
    #: Nowhere to return from a second time.
    with pytest.raises(explore.NotOut):
        await explore.cancel(session, body)


async def test_second_run_with_same_body_does_not_go(
    session: AsyncSession, constants: Constants
) -> None:
    from src.engine import travel

    _, _, body = await _scout(session)
    await explore.survey(session, constants, body)
    with pytest.raises(travel.InField):
        await explore.survey(session, constants, body)


async def test_find_lands_on_map_as_edge(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The found node is connected by a road: nobody walks in a straight line in this world.

    A run is rolled, so what is checked is not "always found" but "if found --
    found correctly". An empty run is just as normal.
    """
    _, gate, body = await _scout(session)
    before = len((await session.execute(select(Node))).scalars().all())

    #: Several runs in a row: with `explore.find_chance` below a hundred one
    #: run may give nothing, and that is no reason to consider the mechanic broken.
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.SITE)
        await _return(session, body)

    nodes = (await session.execute(select(Node))).scalars().all()
    assert len(nodes) > before, "двенадцать заходов подряд не дали ничего"

    finds_ = [node for node in nodes if node.key.startswith("terra.wild.")]
    for find in finds_:
        assert find.layer is Layer.PLANET
        assert find.owner_identity_id is None, "найденное ничьё"
        edges = (
            await session.execute(
                select(Edge).where(
                    (Edge.node_a_id == find.id) | (Edge.node_b_id == find.id)
                )
            )
        ).scalars().all()
        assert edges, "находка без дороги — это телепорт"


async def test_distance_grows_and_road_gets_pricier(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The frontier recedes by itself: a find from a node of distance `d` lands at
    `d + 1`, and the road to it is exactly as many times longer as the vault orders (D-180).
    """
    from src.engine import travel

    _, gate, body = await _scout(session)
    #: Between runs we return to the gate: a successful one leads to the find
    #: (D-185), and here the first ring from one and the same node is checked.
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = gate.id
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.SITE)
        await _return(session, body)

    finds_ = [
        node
        for node in (await session.execute(select(Node))).scalars().all()
        if node.key.startswith("terra.wild.")
    ]
    assert finds_, "двенадцать заходов подряд не дали ничего"

    #: The city gate is distance 0, so everything found from here lands at distance 1.
    for find in finds_:
        assert travel.reach_of(find) == travel.reach_of(gate) + 1
        edge = (
            await session.execute(
                select(Edge).where(
                    (Edge.node_a_id == find.id) | (Edge.node_b_id == find.id)
                )
            )
        ).scalars().first()
        we_expect = travel.frontier_seconds(constants, travel.reach_of(find))
        assert edge.base_seconds == pytest.approx(we_expect, rel=0.01)

    #: The next ring is pricier than the previous -- that is the whole point of distance.
    steps = [travel.frontier_seconds(constants, d) for d in (1, 2, 3, 4)]
    assert steps == sorted(steps) and steps[0] < steps[-1]
    growth = constants[R.TRAVEL_FRONTIER_GROWTH]
    assert steps[1] == pytest.approx(steps[0] * growth)


async def test_scout_stays_at_find(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Found means you stand there, and the next run goes from there (D-185).

    Hence a chain: distance grows step by step, not as a star from one point.
    """
    from src.engine import travel

    _, gate, body = await _scout(session)
    given: list[int] = []
    for _ in range(14):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        were_standing = body.node_id
        await explore.survey(session, constants, body, goal=explore.SITE)
        await _return(session, body)
        if body.node_id != were_standing:
            node = await session.get(Node, body.node_id)
            assert node.key.startswith("terra.wild."), "ушли не на находку"
            given.append(travel.reach_of(node))

    assert given, "четырнадцать заходов подряд не дали ни одной находки"
    #: Each next find is farther than the previous: the frontier is pushed on foot.
    assert given == sorted(given)
    assert given[0] == travel.reach_of(gate) + 1


async def test_empty_run_leaves_in_place(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """There was nowhere to go: the node did not appear, and the scout is where they left."""
    _, gate, body = await _scout(session)
    #: Trodden surroundings give a find rarely -- here that is what is needed.
    await _walk_over(session, gate, finds=200)

    for _ in range(6):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = gate.id
        await session.flush()
        before = len((await session.execute(select(Node))).scalars().all())
        await explore.survey(session, constants, body, goal=explore.SITE)
        await _return(session, body)
        after = len((await session.execute(select(Node))).scalars().all())
        if after == before:
            assert body.node_id == gate.id, "пустой заход не двигает тело"


async def test_woods_are_found_when_asked_for(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Asked for woods -- got woods (D-191): felling needs a place that has them (D-177)."""
    _, gate, body = await _scout(session)
    groves = []
    for _ in range(20):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = gate.id
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.FOREST)
        await _return(session, body)
        node = await session.get(Node, body.node_id)
        if node.key.startswith("terra.wild."):
            groves.append(node)

    assert groves, "twenty runs for woods gave not a single grove"
    for grove in groves:
        assert grove.properties.get(explore.WOODS) is True


async def test_woods_grow_by_themselves(constants: Constants) -> None:
    """The world gets forested without asking: `explore.forest_share` of finds."""
    import random

    from src.units import PERCENT

    places = [
        explore._properties(constants, random.Random(seed), vein=False)
        for seed in range(300)
    ]
    wooded = sum(1 for place in places if place[explore.WOODS])
    share = wooded / len(places) * PERCENT
    #: A roll is a roll: the order of magnitude is checked, not an exact number.
    assert abs(share - constants[R.EXPLORE_FOREST_SHARE]) < 15


async def test_aiming_for_woods_narrows_the_chance(
    constants: Constants, catalog: Catalog
) -> None:
    """What is asked for narrows the chance by exactly the world's forest cover."""
    from src.units import PERCENT

    aim = explore._aim(constants, catalog, explore.FOREST, None)
    assert aim == pytest.approx(constants[R.EXPLORE_FOREST_SHARE] / PERCENT)
    assert aim < explore._aim(constants, catalog, explore.SITE, None)


async def test_species_taken_from_vault(
    constants: Constants, catalog: Catalog
) -> None:
    """There is no "which ores exist" list in the engine: it reads the "Mining" operation."""
    import random

    yield_ = next(
        op for op in catalog.recipes.operations if op.name == explore.MINING_OPERATION
    )
    rolled = {
        explore._resource(constants, catalog, random.Random(grain))
        for grain in range(200)
    }
    assert rolled, "порода не выбирается вовсе"
    assert rolled <= set(yield_.gives)
    #: Iron is mined faster than the rest, so it also turns up more often: the
    #: weight is the pace from `harvest.rates`, there is no second rarity table.
    assert "Железная руда" in rolled


async def test_vein_has_stock_and_richness(
    session: AsyncSession, constants: Constants
) -> None:
    """Veins are finite -- that is irrevocable (pillar P2)."""
    _, _, body = await _scout(session)
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.VEIN)
        await _return(session, body)

    veins = (await session.execute(select(Vein))).scalars().all()
    assert veins, "двенадцать заходов за жилой не дали ни одной"
    richness = constants[R.EXPLORE_VEIN_RICHNESS]
    for vein in veins:
        assert vein.remaining > 0
        assert richness.min <= float(vein.richness) <= richness.max


# --- the run's price grows with place depletion (D-156) ----------------------


async def test_first_run_takes_minutes(
    session: AsyncSession, constants: Constants
) -> None:
    """Untrodden surroundings give a find at once.

    The first location must be found in minutes: the mechanic by which the map
    grows on foot cannot open after six hours of waiting.
    """
    planet, gate, body = await _scout(session)
    gone = datetime.now(UTC)
    job = await explore.survey(session, constants, body, now=gone)

    run = constants[R.EXPLORE_ATTEMPT_MINUTES]
    minutes = (job.run_at - gone).total_seconds() / SECONDS_PER_MINUTE
    assert run.min <= minutes <= run.max


async def test_each_find_raises_next_run_price(
    session: AsyncSession, constants: Constants
) -> None:
    """The more nodes are opened from here, the pricier and rarer the next."""
    _, gate, body = await _scout(session)
    gone = datetime.now(UTC)
    fresh = await explore.survey(session, constants, body, now=gone)
    fresh_minutes = (fresh.run_at - gone).total_seconds() / SECONDS_PER_MINUTE
    fresh_chance = explore.chance(constants, gate)
    fresh_price = float(fresh.payload["chance"])
    assert fresh_price == pytest.approx(fresh_chance)

    await _walk_over(session, gate, finds=4)
    fresh.state = JobState.DONE
    await session.flush()
    body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
    explored = await explore.survey(session, constants, body, now=gone)
    explored_minutes = (explored.run_at - gone).total_seconds() / SECONDS_PER_MINUTE

    assert explored_minutes > fresh_minutes, "исхоженное место обязано стоить дороже"
    assert explore.chance(constants, gate) < fresh_chance, "и находиться реже"


async def test_duration_hits_ceiling(
    session: AsyncSession, constants: Constants
) -> None:
    """Growth is not endless: a day per run is not difficulty but a wall."""
    _, gate, body = await _scout(session)
    await _walk_over(session, gate, finds=20)
    gone = datetime.now(UTC)
    job = await explore.survey(session, constants, body, now=gone)
    hours = (job.run_at - gone).total_seconds() / SECONDS_PER_HOUR
    assert hours == pytest.approx(constants[R.EXPLORE_ATTEMPT_HOURS])
    assert float(body.stamina) == pytest.approx(
        constants[R.BODY_STAMINA_MAX] - constants[R.EXPLORE_ATTEMPT_STAMINA]
    ), "заход полной длины стоит полную цену"


async def test_chance_does_not_fall_below_floor(
    session: AsyncSession, constants: Constants
) -> None:
    """Trodden surroundings grow poorer but are not locked for good."""
    _, gate, _ = await _scout(session)
    await _walk_over(session, gate, finds=200)
    assert explore.chance(constants, gate) == pytest.approx(
        constants[R.EXPLORE_FIND_FLOOR]
    )


async def test_find_depletes_place_but_empty_run_does_not(
    session: AsyncSession, constants: Constants
) -> None:
    """The count grows from successes: bad luck does not punish twice.

    A successful run leads the scout to the find (D-185), so between runs the
    body returns to the gate -- otherwise the new node would already be
    depleting, while we check exactly the count of the original place.
    """
    _, gate, body = await _scout(session)
    before = explore.found_here(gate)
    finds = 0
    for _ in range(6):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        body.node_id = gate.id
        await session.flush()
        await explore.survey(session, constants, body)
        node_count = len((await session.execute(select(Node))).scalars().all())
        await _return(session, body)
        if len((await session.execute(select(Node))).scalars().all()) > node_count:
            finds += 1
    assert finds, "шесть заходов по нехоженому месту не дали ничего"
    assert explore.found_here(gate) == before + finds


async def test_fresh_find_explored_again_cheaply(
    session: AsyncSession, constants: Constants
) -> None:
    """The border moves: the map grows in breadth, not as a star from the birthplace."""
    _, gate, body = await _scout(session)
    await _walk_over(session, gate, finds=6)
    body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
    await explore.survey(session, constants, body)
    await _return(session, body)

    finds_ = [
        node
        for node in (await session.execute(select(Node))).scalars().all()
        if node.key.startswith("terra.wild.")
    ]
    if not finds_:
        pytest.skip("заход по исхоженному месту не дал находки — это норма")
    new = finds_[0]
    assert explore.chance(constants, new) > explore.chance(constants, gate)
    assert explore.found_here(new) == 0


async def test_forecast_shows_price_before_leaving(
    session: AsyncSession, constants: Constants
) -> None:
    """A price that cannot be seen in advance reads as engine randomness."""
    _, gate, body = await _scout(session)
    fresh = await explore.outlook(session, constants, body)
    assert fresh is not None
    run = constants[R.EXPLORE_ATTEMPT_MINUTES]
    assert fresh["minutes"] == {"min": run.min, "max": run.max}
    assert fresh["chance"] == pytest.approx(constants[R.EXPLORE_FIND_CHANCE])
    assert 0 < fresh["stamina"] < constants[R.EXPLORE_ATTEMPT_STAMINA]

    await _walk_over(session, gate, finds=4)
    explored = await explore.outlook(session, constants, body)
    assert explored is not None
    assert explored["explored"] == 4
    assert explored["minutes"]["max"] > fresh["minutes"]["max"]
    assert explored["chance"] < fresh["chance"]
    assert explored["stamina"] > fresh["stamina"]


# --- search goals (D-152) ----------------------------------------------------


async def test_plot_sought_in_city_and_is_civic(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Civic land is not taken -- the authority hands it out (D-089)."""
    city, core, body = await _townsman(session, catalog)
    for _ in range(12):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(session, constants, body, goal=explore.LOT)
        await _return(session, body)

    plots = [
        node
        for node in (await session.execute(select(Node))).scalars().all()
        if node.properties.get("участок")
    ]
    assert plots, "двенадцать заходов в городе не дали ни одного участка"
    for plot in plots:
        assert plot.layer is Layer.CITY, "участок стоит в городе, а не в поле"
        assert plot.owner_city_id == city.id, "земля в кольцах — городская"
        assert plot.owner_identity_id is None, "раздаёт её власть, а не находка"


async def test_plot_not_sought_outside_walls(
    session: AsyncSession, constants: Constants
) -> None:
    """There is no city built-up area beyond the walls: nothing to seek there.

    The refusal comes **before** leaving: the player must not spend three
    hours and stamina on a goal that is impossible in advance.
    """
    _, _, body = await _scout(session)
    before = float(body.stamina)
    with pytest.raises(explore.ExploreError):
        await explore.survey(session, constants, body, goal=explore.LOT)
    assert float(body.stamina) == before, "отказ не стоит выносливости"


async def test_named_species_is_exactly_what_is_found(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One seeks not "something" but what is needed."""
    _, _, body = await _scout(session)
    for _ in range(20):
        body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
        await session.flush()
        await explore.survey(
            session, constants, body, goal=explore.VEIN, resource="Медная руда"
        )
        await _return(session, body)

    veins = (await session.execute(select(Vein))).scalars().all()
    assert veins, "двадцать заходов за медью не дали ни одной жилы"
    assert {vein.resource for vein in veins} == {"Медная руда"}


async def test_rare_found_worse_than_common(
    constants: Constants, catalog: Catalog
) -> None:
    """Otherwise everyone would seek only the most expensive, and exploration would become a
    faucet."""
    iron_ = explore._aim(constants, catalog, explore.VEIN, "Железная руда")
    tin = explore._aim(constants, catalog, explore.VEIN, "Оловянная руда")
    blindly = explore._aim(constants, catalog, explore.VEIN, None)
    assert blindly == 1.0
    assert iron_ > tin, "редкая порода обязана искаться хуже частой"
    assert 0 < tin <= 1


async def test_nonexistent_species_not_sought(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, body = await _scout(session)
    with pytest.raises(explore.ExploreError):
        await explore.survey(
            session, constants, body, goal=explore.VEIN, resource="Мифрил"
        )
