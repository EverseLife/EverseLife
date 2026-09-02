# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Building, wearing out and taking apart (D-089, D-231).

Construction spends materials and places the building on a term; storeys
cost more than the same area laid flat and the type names the materials;
demolition takes time, returns a share and waits for the yard to empty; a
house wears by its type, is repaired with what it is built of, and at
nothing collapses with what it sheltered. The land itself lives in
`test_estate.py`, its tax in `test_estate_tax.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from estate_kit import _buyer
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import estate, goods, world
from src.models.inventory import Item
from src.models.job import JobState
from src.models.world import Layer
from src.units import SCALE_MAX

# --- building (D-106, D-125) -------------------------------------------------


async def test_construction_spends_materials_and_places_building_on_term(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    pocket = await world.body_container(session, body)
    norms = estate.composition(constants, estate.kinds(constants)[0])
    area = 20.0
    for name, per_metre_ in norms.items():
        await world.grant_item(
            session,
            pocket,
            name,
            amount=float(per_metre_) * area + 1,
            quality=60,
            origin="тест",
        )

    job = await estate.construct(session, constants, body, plot, area)
    assert await estate.built_area(session, plot) == 0, "здание не мгновенно"

    #: The term is the assembly labour: `build.labor_per_m2` hours per metre.
    minutes = area * constants[R.BUILD_LABOR_PER_M2] * 60
    assert (job.run_at - datetime.now(UTC)).total_seconds() / 60 == pytest.approx(minutes, rel=0.05)

    await estate.finish_build(session, job)
    assert await estate.built_area(session, plot) == pytest.approx(area)


async def test_construction_does_not_start_without_materials(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    from src.engine import craft

    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)
    with pytest.raises(craft.NotEnough):
        await estate.construct(session, constants, body, plot, 20)


def test_storeys_cost_more_than_the_same_area_laid_flat(constants: Constants) -> None:
    """Height is paid for: each next floor costs `floor_growth_by_type` (D-125)."""
    plain = estate.kinds(constants)[0]
    flat = estate.estimate(constants, footprint=40, floors=1, kind=plain)
    tall = estate.estimate(constants, footprint=20, floors=2, kind=plain)

    assert sum(tall.values()) > sum(flat.values()), (
        "двадцать метров в два этажа дороже сорока в один: за высоту платят"
    )
    #: And a two-storey house takes half the ground -- that is what it is for.
    assert estate.build_minutes(constants, footprint=20, floors=2, kind=plain) > 0


def test_type_names_the_materials_not_a_multiplier(constants: Constants) -> None:
    """A type is its own composition, not more of one shared recipe (D-218).

    That is the whole difference from the tier ladder it replaced: an all-metal
    house does not spend fourfold timber, it spends iron and glass, and a city
    built of it demands other trades than a city of log huts.
    """
    ladder = estate.kinds(constants)
    plainest, dearest = ladder[0], ladder[-1]
    hut = estate.estimate(constants, footprint=20, floors=1, kind=plainest)
    palace = estate.estimate(constants, footprint=20, floors=1, kind=dearest)

    assert set(hut) != set(palace), "разные типы строятся из разного сырья"
    assert sum(palace.values()) > sum(hut.values()), "дорогой тип и стоит дороже"


def test_dear_types_decay_slower(constants: Constants) -> None:
    """What expensive materials buy is a rarer repair, not a stronger wall (D-218)."""
    ladder = estate.kinds(constants)
    assert estate.decay_per_day(constants, ladder[0]) > estate.decay_per_day(constants, ladder[-1])
    #: And the cheap type pays for that with a steeper floor: height is where a
    #: log house becomes ruinous.
    assert estate.floor_growth(constants, ladder[0]) > estate.floor_growth(constants, ladder[-1])


def test_no_type_has_a_ceiling_of_height(constants: Constants) -> None:
    """A twenty-storey log house is allowed -- and priced out of existence (D-218)."""
    plain = estate.kinds(constants)[0]
    tower = estate.estimate(constants, footprint=10, floors=20, kind=plain)
    hut = estate.estimate(constants, footprint=10, floors=1, kind=plain)
    assert sum(tower.values()) > sum(hut.values()) * 1000, (
        "запрета на высоту нет — отказывает смета, и она обязана быть разорительной"
    )


async def test_unknown_type_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A misnamed type is a refusal, not a silent fallback to the cheap one."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)
    with pytest.raises(estate.UnknownKind):
        await estate.construct(session, constants, body, plot, 20, kind="соломенный")


async def test_house_smaller_than_the_minimum_is_a_lean_to(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Below `build.area_min` there is no building to speak of (D-218)."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    below = constants[R.BUILD_AREA_MIN] - 1
    with pytest.raises(estate.TooSmall):
        await estate.construct(session, constants, body, plot, below)


async def test_storeys_give_area_without_eating_the_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A two-storey house takes ten metres of ground and gives twenty of floor."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    pocket = await world.body_container(session, body)
    needed = estate.estimate(constants, footprint=10, floors=2, kind=estate.kinds(constants)[0])
    for name, quantity in needed.items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )

    job = await estate.construct(session, constants, body, plot, 10, floors=2)
    await estate.finish_build(session, job)

    assert await estate.built_area(session, plot) == pytest.approx(20)
    assert await estate.built_area(session, plot, ground=True) == pytest.approx(10)


async def test_building_no_larger_than_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=50, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)
    with pytest.raises(estate.NoRoom):
        await estate.construct(session, constants, body, plot, 60)


async def test_started_sites_hold_their_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A queue of orders must not walk past the plot (D-218).

    Counting only finished houses, each order is lawful on its own -- and five
    of them put five hundred metres of house on a hundred-metre plot. Ground
    already spoken for is ground taken.
    """
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    pocket = await world.body_container(session, body)
    plain = estate.kinds(constants)[0]
    for name, quantity in estate.estimate(constants, footprint=80, floors=1, kind=plain).items():
        await world.grant_item(
            session, pocket, name, amount=quantity * 3, quality=60, origin="тест"
        )

    await estate.construct(session, constants, body, plot, 80)
    assert await estate.planned_footprint(session, plot) == pytest.approx(80)
    #: Nothing stands yet, and still there is no second house: the first
    #: site holds the plot -- one house per plot (D-279), and the ground it
    #: spoke for is counted taken (D-218) for whatever else asks.
    assert await estate.built_area(session, plot, ground=True) == 0
    with pytest.raises(estate.EstateError):
        await estate.construct(session, constants, body, plot, 30)


async def test_no_building_on_foreign_land(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    owner, owner_body = await _buyer(session, plot, funds=0)
    await own_plot(plot, owner)

    _, foreign_body = await _buyer(session, plot, funds=0)
    with pytest.raises(estate.EstateError):
        await estate.construct(session, constants, foreign_body, plot, 10)


# --- demolition (D-205) ------------------------------------------------------


async def _house(
    session: AsyncSession,
    constants: Constants,
    own_plot,
    *,
    area: float = 20.0,
    floors: int = 1,
    plot_area: float = 100,
):
    """A plot of one's own with a finished house on it."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=plot_area, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    pocket = await world.body_container(session, body)
    for name, quantity in estate.estimate(
        constants, footprint=area, floors=floors, kind=estate.kinds(constants)[0]
    ).items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    job = await estate.construct(session, constants, body, plot, area, floors=floors)
    await estate.finish_build(session, job)
    #: The worker closes a finished job, and here there is no worker: a job left
    #: pending would read as a construction still going on, and demolition waits
    #: for those.
    job.state = JobState.DONE
    await session.flush()
    return plot, identity, body


async def test_demolition_takes_time_and_returns_a_share_of_materials(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """The house goes when the work is done, and part of the material comes back."""
    plot, identity, body = await _house(session, constants, own_plot)
    houses = await estate.buildings_of(session, plot)
    back = estate.salvage(constants, houses)
    spent = estate.estimate(constants, footprint=20.0, floors=1, kind=estate.kinds(constants)[0])

    share = constants[R.BUILD_DEMOLISH_SALVAGE]
    for name, quantity in back.items():
        #: The share of the bill, cut down to whole pieces where the material is
        #: counted (D-212): a house does not give back two thirds of a board.
        assert quantity == goods.whole(name, spent[name] * share), (
            "возвращается доля сметы, а не смета"
        )
        assert quantity < spent[name], "возврат меньше вложенного"

    job = await estate.demolish(session, constants, body, plot)
    assert await estate.built_area(session, plot) > 0, "снос не мгновенен"
    minutes = estate.demolish_minutes(constants, houses)
    assert (job.run_at - datetime.now(UTC)).total_seconds() / 60 == pytest.approx(minutes, rel=0.05)
    assert minutes < estate.build_minutes(
        constants, footprint=20.0, floors=1, kind=estate.kinds(constants)[0]
    ), "разбор быстрее сборки"

    #: The owner is standing here, so the salvage goes into their hands.
    await estate.finish_demolish(session, job)
    assert await estate.built_area(session, plot) == 0, "участок пуст"

    pocket = await world.body_container(session, body)
    from src.models.inventory import Item
    from src.units import amount_float

    at_hand = {
        thing.type_key: amount_float(thing.amount)
        for thing in (await session.execute(select(Item).where(Item.container_id == pocket.id)))
        .scalars()
        .all()
    }
    for name, quantity in back.items():
        assert at_hand.get(name, 0) == pytest.approx(quantity, rel=0.01)


async def test_demolition_waits_for_the_yard_to_empty(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Machines and cargo leave before the work, not after it (D-205).

    Losing possessions to a button is what this order exists to prevent: after
    the demolition a machine has nowhere to stand and the cargo has no room.
    """
    from src.engine import station, storage

    plot, identity, body = await _house(session, constants, own_plot, area=40)
    yard = await world.node_container(session, plot)
    bench = await world.grant_item(session, yard, "workbench", quality=60, origin="тест")

    reasons = await estate.demolish_blockers(session, constants, plot)
    #: A message, not a sentence: the window and the refusal read the same
    #: list, each in the language of whoever is looking (D-251 wave IV).
    assert [one.key for one in reasons] == ["estate-blocker-equipment"]
    with pytest.raises(estate.NoRoom):
        await estate.demolish(session, constants, body, plot)

    #: Taken into the hands -- and the way is clear.
    await station.take(session, catalog, body, bench)
    assert await estate.demolish_blockers(session, constants, plot) == []
    assert await estate.demolish(session, constants, body, plot) is not None

    #: Cargo that fits under a roof but not in the bare yard blocks it the same
    #: way. Two storeys on a small plot are exactly that gap: forty metres of
    #: floor over twenty metres of ground (D-125) -- and since D-247 those forty
    #: are two rooms of twenty, so the load is counted across the whole house.
    from src.engine import gear

    tight, _, owner = await _house(session, constants, own_plot, area=20, floors=2, plot_area=20)
    per_m2 = constants[R.BUILD_FLOOR_PER_M2]
    roofed = await estate.built_area(session, tight)
    #: Halfway between what the yard holds and what the house holds.
    kilos = (float(tight.area_m2) + roofed) / 2 * per_m2
    quantity = kilos / gear.mass_of(catalog, "pipe", 1)

    upstairs = await estate.storeys_of(session, tight)
    assert len(upstairs) == 1, "второй этаж — отдельный узел (D-247)"
    pocket = await world.body_container(session, owner)
    #: Half on each floor: neither room alone is over its own capacity, and the
    #: plot below still cannot hold the two heaps together.
    for where in (tight, upstairs[0]):
        owner.node_id = where.id
        await session.flush()
        goods = await world.grant_item(
            session, pocket, "pipe", amount=quantity / 2, quality=55, origin="тест"
        )
        await storage.drop(session, constants, catalog, owner, goods, quantity / 2)
    owner.node_id = tight.id
    await session.flush()
    blocking = await estate.demolish_blockers(session, constants, tight)
    assert "estate-blocker-overloaded" in [one.key for one in blocking], blocking


async def test_demolition_is_not_ordered_twice(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """The house is one, and the salvage comes back once (I2: matter does not multiply).

    Each order carries its own salvage in the payload, so two orders on one house
    would pay for it twice -- and the second one is refused by name.
    """
    plot, identity, body = await _house(session, constants, own_plot)
    first = await estate.demolish(session, constants, body, plot)

    assert await estate.demolishing(session, plot)
    with pytest.raises(estate.NoRoom):
        await estate.demolish(session, constants, body, plot)

    #: And a job that fires over an already emptied plot gives nothing at all.
    await estate.finish_demolish(session, first)
    pocket = await world.body_container(session, body)
    from src.models.inventory import Item
    from src.units import amount_float

    async def at_hand() -> dict[str, float]:
        return {
            thing.type_key: amount_float(thing.amount)
            for thing in (await session.execute(select(Item).where(Item.container_id == pocket.id)))
            .scalars()
            .all()
        }

    once = await at_hand()
    await estate.finish_demolish(session, first)
    assert await at_hand() == once, "повторное задание материалов не удваивает"


async def test_foreign_civic_plot_is_not_demolished(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Somebody else's house on civic land is taken apart by a court order (D-095)."""
    plot, identity, body = await _house(session, constants, own_plot)

    _, stranger = await _buyer(session, plot, funds=0)
    with pytest.raises(estate.NotOwner):
        await estate.demolish(session, constants, stranger, plot)


async def test_beyond_the_walls_whoever_came_builds_and_takes_apart(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Land outside a city is nobody's, and work on it is open to everyone (D-198, D-205).

    A homestead far from any city is the whole point of that freedom: one builds
    without buying a plot and without taxes -- and the same freedom takes the
    house down. There is no title beyond the walls to make one of them the owner.
    """
    stamp = uuid.uuid4().hex[:6]
    wild = await world.create_node(
        session, f"terra.wild.{stamp}", "Пустошь", area_m2=200, layer=Layer.PLANET
    )
    assert wild.owner_identity_id is None and wild.owner_city_id is None

    settler, settler_body = await _buyer(session, wild, funds=0)
    pocket = await world.body_container(session, settler_body)
    for name, quantity in estate.estimate(
        constants, footprint=20.0, floors=1, kind=estate.kinds(constants)[0]
    ).items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    raising = await estate.construct(session, constants, settler_body, wild, 20.0)
    await estate.finish_build(session, raising)
    raising.state = JobState.DONE
    await session.flush()
    assert await estate.built_area(session, wild) == pytest.approx(20)

    #: Whoever came may take it down -- the settler themselves, or a passer-by.
    _, passerby = await _buyer(session, wild, funds=0)
    job = await estate.demolish(session, constants, passerby, wild)
    await estate.finish_demolish(session, job)
    assert await estate.built_area(session, wild) == 0


async def test_nothing_to_demolish_on_an_empty_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)
    with pytest.raises(estate.NoBuilding):
        await estate.demolish(session, constants, body, plot)


# --- decay, repair and collapse (D-218) --------------------------------------


async def test_house_wears_out_by_its_type(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A day of the world costs the house `build.decay_by_type` of condition."""
    plot, identity, body = await _house(session, constants, own_plot)
    house = (await estate.buildings_of(session, plot))[0]
    assert float(house.condition) == pytest.approx(SCALE_MAX)

    worn, fallen = await estate.decay(session, constants)
    assert worn >= 1 and fallen == 0
    await session.refresh(house)
    assert float(house.condition) == pytest.approx(
        SCALE_MAX - estate.decay_per_day(constants, house.kind)
    )


async def test_repair_costs_what_the_house_is_built_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Mended with the same materials, in the share of condition missing (D-145)."""
    plot, identity, body = await _house(session, constants, own_plot)
    house = (await estate.buildings_of(session, plot))[0]

    whole = await estate.buildings_of(session, plot)
    assert estate.repair_bill(constants, whole) == {}, "целый дом не чинят"
    with pytest.raises(estate.Ruined):
        await estate.repair(session, constants, body, plot)

    house.condition = Decimal("50")
    await session.flush()
    houses = await estate.buildings_of(session, plot)
    needed = estate.repair_bill(constants, houses)
    built_of = estate.composition(constants, house.kind)
    assert set(needed) <= set(built_of), "чинят тем же, чем построено"
    assert needed, "изношенный дом требует материалов"

    #: Cheaper than raising it anew: the walls are standing.
    fresh = estate.bill(
        constants,
        footprint=float(house.footprint_m2),
        floors=house.floors,
        kind=house.kind,
    )
    assert sum(needed.values()) < sum(fresh.values())

    pocket = await world.body_container(session, body)
    for name, quantity in needed.items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    job = await estate.repair(session, constants, body, plot)
    assert float(house.condition) == pytest.approx(50), "состояние — в конце работ"
    await estate.finish_repair(session, job)
    await session.refresh(house)
    assert float(house.condition) == pytest.approx(SCALE_MAX)


async def test_repair_is_an_occupation_and_stops_when_the_mason_leaves(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Mending is done by hand and on the spot (D-211).

    Ordered and forgotten, a repair used to finish by itself while its owner
    was a planet away: it was a journal job and nothing else -- not in the
    activities, not blocking a second pair of hands, not needing anybody
    present. Now it is an occupation, and leaving the node stops it with the
    time left in it.

    What is **not** lost is the materials: they went into the walls at the
    order. Coming back and ordering again resumes the remainder and charges
    nothing -- otherwise a step outside would be a fine.
    """
    from src.engine import occupation

    plot, identity, body = await _house(session, constants, own_plot)
    house = (await estate.buildings_of(session, plot))[0]
    house.condition = Decimal("50")
    await session.flush()

    pocket = await world.body_container(session, body)
    needed = estate.repair_bill(constants, await estate.buildings_of(session, plot))
    for name, quantity in needed.items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    job = await estate.repair(session, constants, body, plot)

    #: It is in the activities, and these hands are busy.
    doing = await occupation.current(session, body)
    assert doing is not None and doing.kind == occupation.MEND
    with pytest.raises(occupation.Busy):
        await occupation.require_free(session, body)

    #: The mason walks away: the work stops with the time left in it.
    full = (job.run_at - datetime.now(UTC)).total_seconds() / 60
    left = await estate.pause(session, body, now=datetime.now(UTC))
    assert left is not None and left == pytest.approx(full, rel=0.05)
    assert await occupation.current(session, body) is None, "ушёл, а ремонт всё идёт"
    await session.refresh(house)
    assert float(house.condition) == pytest.approx(50), "дом починился без мастера"

    #: And back: the remainder resumes, and the pocket is not charged again.
    had = {thing.type_key: thing.amount for thing in await world.contents(session, pocket)}
    again = await estate.repair(session, constants, body, plot)
    now = {thing.type_key: thing.amount for thing in await world.contents(session, pocket)}
    assert now == had, "за возвращение взяли материалы второй раз"
    resumed = (again.run_at - datetime.now(UTC)).total_seconds() / 60
    assert resumed == pytest.approx(left, rel=0.05), "ремонт начался с начала, а не с остатка"

    await estate.finish_repair(session, again)
    await session.refresh(house)
    assert float(house.condition) == pytest.approx(SCALE_MAX)


async def test_house_at_nothing_collapses_with_what_it_sheltered(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Full strength until zero, then gone -- and the yard with it (D-218)."""
    plot, identity, body = await _house(session, constants, own_plot)
    house = (await estate.buildings_of(session, plot))[0]

    yard = await world.node_container(session, plot)
    await world.grant_item(session, yard, "wood", amount=5, quality=60, origin="тест")

    #: One step short of nothing the house is still whole: no places lost, no
    #: area lost. That is what makes repair a decision rather than a levy.
    house.condition = Decimal(str(estate.decay_per_day(constants, house.kind)))
    await session.flush()
    standing = await estate.built_area(session, plot)
    assert standing > 0

    worn, fallen = await estate.decay(session, constants)
    assert fallen == 1
    assert await estate.built_area(session, plot) == 0
    left = (await session.execute(select(Item).where(Item.container_id == yard.id))).scalars().all()
    assert left == [], "двор уходит вместе с крышей, которой над ним больше нет"
