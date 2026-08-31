# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""explore: the place a search finds -- its ground, its signs and its vein.

Split out of `engine/explore.py` along its sections. What the run does is
one subject; **what comes of it** is another, and it is this one: a node laid
next to the one the scout left from, with a place's merits rolled under a
common budget (D-126) and, on a vein find, a species dealt by how fast it is
mined (D-151).
"""

from __future__ import annotations

import random
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import luck, travel, world
from src.engine.explore._base import (
    FOREST,
    LOT,
    MEADOW,
    MINING_OPERATION,
    PLOT,
    SITE,
    STONES,
    WILD,
    WOODS,
    ExploreError,
)
from src.models.world import Layer, Node, Planet
from src.units import PERCENT


async def lay(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    origin: Node,
    *,
    goal: str,
    vein: bool,
    who: uuid.UUID | None = None,
) -> Node:
    """Create the found node next to the one we left from.

    A city plot lands **in the city** and belongs to it: civic land is not
    taken, the authority hands it out (D-089). Everything else hangs on the
    planet and stays unowned -- the finder gets the right of first night, not
    ownership (D-152).
    """

    #: The node key must be stable and unique forever: the map is eternal,
    #: there are no wipes (D-007), and "wild plot 3" will sooner or later
    #: collide. Named after the planet it is actually on: keys are read by
    #: people -- in the admin, in a migration, in a log line -- and a field of
    #: Pyroxis called `terra.wild.*` is a lie told to whoever reads it next.
    key = f"{origin.planet.value}.wild.{uuid.uuid4().hex}"

    if goal == LOT:
        city = await town.of_node(session, origin)
        if city is None:
            raise ExploreError(key="explore-lot-outside-city")
        delegate = await session.get(Node, city.node_id)
        ring = constants[R.LAND_AREA_RING1]
        plot = await world.create_node(
            session,
            key,
            "Свободный участок",
            area_m2=dice.uniform(ring.min, ring.max),
            layer=Layer.CITY,
            parent=delegate,
            planet=origin.planet,
            #: On the built-up map the plot lies where it was found: beside the
            #: very node the scout set out from (D-237).
            anchor=origin,
            properties=await civic_properties(session, constants, dice, who=who),
        )
        plot.owner_city_id = city.id
        await session.flush()
        return plot

    root = await planet_root(session, origin)
    area = constants[R.EXPLORE_NODE_AREA]
    names = {SITE: "Место под город", FOREST: "Роща"}
    return await world.create_node(
        session,
        key,
        names.get(goal, "Дикий участок"),
        area_m2=dice.uniform(area.min, area.max),
        layer=Layer.PLANET,
        parent=root,
        planet=origin.planet,
        #: And it stands next to it on the map as well: sought from inside a
        #: city, the find lies beside that city, because on the planet's map
        #: the whole city is one point (D-206, D-237).
        anchor=origin,
        #: Distance grows by a step from the node we left from (D-180): the
        #: frontier recedes by itself as it is pushed.
        properties=await properties(
            session, constants, dice, vein=vein, woods=goal == FOREST, who=who
        )
        | {travel.REACH: travel.reach_of(origin) + 1},
    )


async def civic_properties(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    *,
    who: uuid.UUID | None = None,
) -> dict:
    """Place properties of a city plot (D-246).

    A plot inside the rings is **land**, and land has soil. It used to arrive
    with the mark alone -- no fertility, no water, no rain -- and a property
    that is absent reads as nought: every plot inside every city was barren
    rock, and the strips window never appeared on one. The city stands on the
    same ground as the field beyond its wall, so the roll is the same roll.

    Only `дикий` is dropped: this ground is the city's, and the authority hands
    it out (D-089).
    """
    rolled = await properties(session, constants, dice, vein=False, who=who)
    return {name: value for name, value in rolled.items() if name != WILD} | {PLOT: True}


async def properties(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    *,
    vein: bool,
    woods: bool = False,
    who: uuid.UUID | None = None,
) -> dict:
    """Place properties under a common merit budget (D-126).

    There is no perfect place: a river eats part of the budget, and the more
    water, the less is left for fertility.

    Woods grow by themselves on `explore.forest_share` of finds (D-191), and
    always where the woods are what the scout went looking for: the world gets
    forested without anybody asking, and timber becomes geography.
    """

    budget = constants[R.SITE_QUALITY_BUDGET]
    #: Each of the place's signs is a chance with a memory (D-213): a scout
    #: who never once found a river is the same complaint as one who never
    #: found anything.
    river = await luck.hit(session, who, luck.SITE_RIVER, constants[R.SITE_RIVER_SHARE], dice=dice)
    for_water = dice.uniform(0, budget) if river else 0.0
    for_land = max(0.0, budget - for_water)

    temperature = constants[R.SITE_TEMP_RANGE]
    rainfall = constants[R.SITE_RAIN_RANGE]
    return {
        "water": "river" if river else "none",
        #: On a vein find arable land is beside the point: rock bears no bread.
        "fertility": 0 if vein else round(PERCENT * for_land / budget),
        "temperature": round(dice.uniform(temperature.min, temperature.max)),
        "precipitation": round(dice.uniform(rainfall.min, rainfall.max)),
        WOODS: woods
        or await luck.hit(
            session, who, luck.SITE_WOODS, constants[R.EXPLORE_FOREST_SHARE], dice=dice
        ),
        #: Stones and meadow fall out on their own, like woods (D-196): one
        #: goes for stone and for flax in different directions.
        STONES: await luck.hit(
            session, who, luck.SITE_STONES, constants[R.EXPLORE_STONES_SHARE], dice=dice
        ),
        MEADOW: await luck.hit(
            session, who, luck.SITE_MEADOW, constants[R.EXPLORE_MEADOW_SHARE], dice=dice
        ),
        WILD: True,
    }


async def species_of(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    dice: random.Random,
    *,
    planet: Planet = Planet.TERRA,
    who: uuid.UUID | None = None,
) -> str:
    """Which species. The list is from the vault, the weight from the mining pace (D-151).

    The rare is mined slower, so it also turns up rarer. No second rarity table:
    it would diverge from the first.

    A planet bends those weights and does not replace them (D-232): Aurora is
    generous with coal and poor in iron, and that one line is the whole economy
    of the place -- fuel underfoot, metal brought in by ship.
    """
    paces = dict(constants[R.HARVEST_RATES])
    bend: dict[str, float] = constants[R.HARVEST_PLANET_WEIGHTS].get(planet.value, {})
    for name, weight in bend.items():
        if name in paces:
            paces[name] = paces[name] * weight
    operation = next(
        (op for op in catalog.recipes.operations if (op.id or op.name) == MINING_OPERATION),
        None,
    )
    if_missing = "stone"
    if operation is None:  # pragma: no cover -- the mining operation exists by construction
        return if_missing
    species = [name for name in operation.gives if float(paces.get(name, 0)) > 0]
    if not species:  # pragma: no cover
        return if_missing
    #: Dealt from a deck by the same weights (D-213): the rare stays rare, but
    #: "six iron veins and never a copper one" is no longer a thing.

    return await luck.draw(
        session,
        who,
        #: A deck per planet (D-213, D-232): the species are the same names
        #: everywhere and only the weights differ, so one shared deck would go
        #: on dealing Terra's iron on Aurora -- exactly where "coal here, iron
        #: brought in" is the first thing a player should feel.
        f"{luck.EXPLORE_SPECIES}:{planet.value}",
        {name: float(paces[name]) for name in species},
        dice=dice,
    )


async def planet_root(session: AsyncSession, node: Node) -> Node | None:
    """The planet the node stands on: walk up the display hierarchy."""
    current = node
    while current.parent_id is not None:
        parent = await session.get(Node, current.parent_id)
        if parent is None:  # pragma: no cover
            return None
        if parent.layer is Layer.SPACE:
            return parent
        current = parent
    return None
