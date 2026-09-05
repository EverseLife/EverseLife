# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a place is made of: the roll of a site's properties and of a vein's species.

Until D-319 this was exploration's half. A scout's find rolled its ground here
-- fertility against water under one merit budget (D-126), woods, stones and
meadow as signs of the place (D-191, D-196), the vein's species by the mining
pace bent by the planet (D-151, D-232). Exploration is gone: the whole surface
of a planet is laid at the world's birth and walked, not found. The roll is the
generation's now -- the seed asks it for every node it lays, and the catch-up
seed for a plot laid before soil existed (D-246).

What is deliberately **not** here any more is the near search of D-262: a find
no longer drifts from the node a scout left from, because nobody leaves from
anywhere. The terrain field of the globe map (plan §3) replaces these dice with
a reading of the field at the point; the shape of the answer -- the keys a node
carries -- stays as it is, and that is why the roll lives in a module of its
own rather than in the seed.
"""

from __future__ import annotations

import random
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import luck, world
from src.models.world import PLOT, Planet
from src.units import PERCENT

#: The vault operation whose outputs are the species a vein may carry (D-151).
MINING_OPERATION = "mining"

#: Signs of a place (D-191, D-196): woods bear timber, stones and meadow are
#: marks with no mechanic of their own yet. `wild` says the land is nobody's.
WOODS = "woods"
STONES = "stones"
MEADOW = "meadow"
WILD = "wild"


def mineable(catalog: Catalog) -> tuple[str, ...]:
    """Which species a vein may carry: the outputs of the mining operation.

    The engine keeps no species list: add a fifth in the vault and it appears
    in the ground without a code change (D-151).
    """
    operation = next(
        (op for op in catalog.recipes.operations if (op.id or op.name) == MINING_OPERATION),
        None,
    )
    return tuple(operation.gives) if operation is not None else ()


async def properties(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    *,
    vein: bool,
    woods: bool = False,
    who: uuid.UUID | None = None,
    at: tuple[Planet, tuple[float, float]] | None = None,
    shares: dict[str, float] | None = None,
) -> dict:
    """Place properties under a common merit budget (D-126).

    There is no perfect place: a river eats part of the budget, and the more
    water, the less is left for fertility. Woods grow on `explore.forest_share`
    of places (D-191), and always where asked for; stones and meadow fall out
    on their own the same way (D-196).

    Given a point on a planet (`at`), the signs and the climate are the
    relief's there (D-319, `terrain`): the river is where the map draws one,
    the woods where the field puts them, the temperature the latitude's and
    the height's. Without a point -- a test's node, a room of the inside --
    the dice roll as they always did; each sign is a chance with a memory
    (D-213) when a `who` is given, and the world's own generation rolls plain.
    """
    from src.engine import terrain  # noqa: PLC0415 -- lazy: terrain reads this module's marks

    budget = constants[R.SITE_QUALITY_BUDGET]
    if at is not None:
        planet, point = at
        marks = terrain.marks_at(constants, planet, *point)
        temperature, precipitation = terrain.climate_at(constants, planet, *point)
        #: The map's river is a fact and never lies (`terrain.river_reach_km`);
        #: the streams beside it the relief is too coarse to draw -- its cells
        #: are degrees wide, a settled edge is kilometres -- and they fall out
        #: by `site.river_share` as they always did (D-126). Without the second
        #: half no laid node ever had water at all.
        river = marks[world.WATER] == world.RIVER or await luck.hit(
            session, who, luck.SITE_RIVER, constants[R.SITE_RIVER_SHARE], dice=dice
        )
        #: The budget is spent the same way: water costs fertility.
        for_water = dice.uniform(0, budget) if river else 0.0

        #: The biome's shares, when the caller knows the biome (D-321): a
        #: forest is woods nine times in ten whatever the noise says. Without
        #: them the field's own texture decides, as for a seeded node.
        def sign(key: str) -> bool:
            if shares is None:
                return bool(marks[key])
            return dice.random() < float(shares.get(key, 0)) / PERCENT

        return {
            world.WATER: world.RIVER if river else marks[world.WATER],
            terrain.MOUNTAIN: marks[terrain.MOUNTAIN],
            "fertility": 0 if vein else round(PERCENT * max(0.0, budget - for_water) / budget),
            "temperature": temperature,
            "precipitation": precipitation,
            WOODS: woods or sign(WOODS),
            STONES: sign(STONES),
            MEADOW: sign(MEADOW),
            WILD: True,
        }

    async def mark_of(key: str, share: float) -> bool:
        return await luck.hit(session, who, key, share, dice=dice)

    river = await mark_of(luck.SITE_RIVER, constants[R.SITE_RIVER_SHARE])
    for_water = dice.uniform(0, budget) if river else 0.0
    for_land = max(0.0, budget - for_water)
    temperature = constants[R.SITE_TEMP_RANGE]
    rainfall = constants[R.SITE_RAIN_RANGE]
    return {
        world.WATER: world.RIVER if river else world.NO_WATER,
        #: On a vein arable land is beside the point: rock bears no bread.
        "fertility": 0 if vein else round(PERCENT * for_land / budget),
        "temperature": round(dice.uniform(temperature.min, temperature.max)),
        "precipitation": round(dice.uniform(rainfall.min, rainfall.max)),
        WOODS: woods or await mark_of(luck.SITE_WOODS, constants[R.EXPLORE_FOREST_SHARE]),
        STONES: await mark_of(luck.SITE_STONES, constants[R.EXPLORE_STONES_SHARE]),
        MEADOW: await mark_of(luck.SITE_MEADOW, constants[R.EXPLORE_MEADOW_SHARE]),
        WILD: True,
    }


async def civic_properties(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    *,
    who: uuid.UUID | None = None,
    at: tuple[Planet, tuple[float, float]] | None = None,
) -> dict:
    """Place properties of a city plot (D-246).

    A plot inside the rings is **land**, and land has soil: the city stands on
    the same ground as the field beyond its wall, so the roll is the same roll
    -- and the same reading of the relief, where the city stands on it. Only
    `wild` is dropped -- this ground is the city's, and the authority hands it
    out (D-089).
    """
    rolled = await properties(session, constants, dice, vein=False, who=who, at=at)
    return {name: value for name, value in rolled.items() if name != WILD} | {PLOT: True}


async def species_of(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    dice: random.Random,
    *,
    planet: Planet = Planet.TERRA,
    who: uuid.UUID | None = None,
) -> str:
    """Which species a vein carries. The list is the vault's, the weight the mining pace (D-151).

    A planet bends those weights and does not replace them (D-232): Aurora is
    generous with coal and poor in iron, and that one line is the whole economy
    of flying there.
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
    #: "six iron veins and never a copper one" is no longer a thing. A deck per
    #: planet (D-232): the names are the same everywhere and only the weights
    #: differ, so one shared deck would go on dealing Terra's iron on Aurora.
    return await luck.draw(
        session,
        who,
        f"{luck.GROUND_SPECIES}:{planet.value}",
        {name: float(paces[name]) for name in species},
        dice=dice,
    )
