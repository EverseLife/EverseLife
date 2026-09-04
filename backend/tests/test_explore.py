# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The run itself, and what a run costs (D-152, D-156).

A run spends stamina and takes the scout out of the world; a find lands on
the map as an edge and the scout stays at it; and the price of leaving is a
property of the place -- it grows with every find, hits its ceiling, never
kills the chance, and is told before the walk. What the run brings back and
from where lives in `test_explore_finds.py`.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from explore_kit import _return, _scout
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import explore, world
from src.models.job import JobState
from src.models.world import Edge, Layer, Node
from src.units import MINUTES_PER_HOUR, SECONDS_PER_HOUR

#: Seconds in a minute -- as many as minutes in an hour.
SECONDS_PER_MINUTE = MINUTES_PER_HOUR


async def _walk_over(session: AsyncSession, node: Node, *, finds: int) -> None:
    """Set the node's find count: this many times people already left from here not in vain."""
    node.properties = {**(node.properties or {}), explore.FOUND_HERE: finds}
    await session.flush()


async def test_run_costs_stamina(session: AsyncSession, constants: Constants) -> None:
    """Paid by time in the field: a short run is cheap but not free (D-156)."""
    _, _, body = await _scout(session)
    before = float(body.stamina)
    await explore.survey(session, constants, body)
    written_off = before - float(body.stamina)
    assert written_off > 0, "разведка — работа, а не прогулка"
    assert written_off < constants[R.EXPLORE_ATTEMPT_STAMINA], (
        "минутный заход не может стоить как заход полной длины"
    )


async def test_lack_of_strength_refuses_the_run(
    session: AsyncSession, constants: Constants
) -> None:
    """Without the legs for it nobody leaves at all (D-147).

    A run in trodden surroundings takes hours and costs accordingly; a body
    with one unit stays where it stands -- and stays with its unit, because a
    refusal costs nothing.
    """
    _, gate, body = await _scout(session)
    await _walk_over(session, gate, finds=10)
    body.stamina = Decimal("1")
    await session.flush()

    with pytest.raises(explore.NoStrength):
        await explore.survey(session, constants, body)
    assert float(body.stamina) == 1, "отказ не стоит выносливости"
    assert await explore.pending(session, body) is None, "и никого не отправил"


async def test_strength_asked_by_the_longest_run(
    session: AsyncSession, constants: Constants
) -> None:
    """The threshold is the very number the forecast shows (D-156).

    The length of a run is rolled at departure, so a lower threshold would let
    a second press re-throw the dice. Whoever has the shown price leaves --
    every time, and the run costs no more than it.
    """
    _, gate, body = await _scout(session)
    await _walk_over(session, gate, finds=10)
    shown = (await explore.outlook(session, constants, body))["stamina"]

    #: A hair under the shown price: refused, however the dice might have fallen.
    body.stamina = Decimal(str(shown * 0.99))
    await session.flush()
    with pytest.raises(explore.NoStrength):
        await explore.survey(session, constants, body)

    body.stamina = Decimal(str(shown))
    await session.flush()
    await explore.survey(session, constants, body)
    assert float(body.stamina) >= 0, "заход не стоит больше показанного потолка"


def test_a_run_never_costs_more_than_the_price_shown(constants: Constants) -> None:
    """The roll stays inside the ceiling the door asks for (D-293).

    The two are counted apart -- the ceiling by `span`, the roll by
    `minutes_of` -- and the whole safety of the refusal rests on the second
    never overshooting the first. Pinned over many throws and over a node at
    every stage of depletion, because arithmetic that agrees by accident stops
    agreeing the day one half is changed.
    """
    from src.engine.explore import odds

    node = Node(key="terra.dice", name="Кости", area_m2=100, layer=Layer.PLANET)
    dice = random.Random(20260904)
    for finds in range(0, 30):
        node.properties = {explore.FOUND_HERE: finds}
        ceiling = odds.span(constants, node)[1]
        for _ in range(50):
            assert odds.minutes_of(constants, node, dice) <= ceiling


async def test_scout_unavailable_like_sleeper(session: AsyncSession, constants: Constants) -> None:
    """Exploration is a body state: while the run goes, in-person is closed."""
    from src.engine import travel

    _, _, body = await _scout(session)
    await explore.survey(session, constants, body)
    with pytest.raises(travel.InField):
        await travel.require_here(session, body)


async def test_scout_does_not_walk_away(session: AsyncSession, constants: Constants) -> None:
    """The body is in the field -- it has nowhere to walk from: it is not in the node (D-152).

    Setting out is checked by the same door as every in-person action: keeping
    a separate list of conditions for it means forgetting a line in it one day
    -- exactly how the scout went wandering across the map.
    """
    from src.engine import travel

    planet, gate, body = await _scout(session)
    adjacent = await world.create_node(
        session,
        f"terra.next.{uuid.uuid4().hex[:8]}",
        "Соседний",
        area_m2=100,
        layer=Layer.PLANET,
        parent=planet,
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
            (
                await session.execute(
                    select(Edge).where((Edge.node_a_id == find.id) | (Edge.node_b_id == find.id))
                )
            )
            .scalars()
            .all()
        )
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
            (
                await session.execute(
                    select(Edge).where((Edge.node_a_id == find.id) | (Edge.node_b_id == find.id))
                )
            )
            .scalars()
            .first()
        )
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


# --- the run's price grows with place depletion (D-156) ----------------------


async def test_first_run_takes_minutes(session: AsyncSession, constants: Constants) -> None:
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


async def test_each_find_raises_next_run_price(session: AsyncSession, constants: Constants) -> None:
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


async def test_duration_hits_ceiling(session: AsyncSession, constants: Constants) -> None:
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
    assert explore.chance(constants, gate) == pytest.approx(constants[R.EXPLORE_FIND_FLOOR])


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
