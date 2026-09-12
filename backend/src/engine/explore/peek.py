# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""What a scout would find at a point, before setting out (D-321 addendum, owner 2026-09-12).

The relief is public and the find is a reading of it (D-321 item 2): the
biome, the face of the ground, the water, the climate are facts of the field
at the cell, and a player who could not see them before the walk was
guessing at what the map already knew. What the dice decide -- a vein, a
scheme of nodes, the marks of the place -- is told as the **chance** it is
rolled with, never as the roll: the cell's dice are seeded and a peek that
rolled them would be the find without the walk.

The aim is judged first, by the same rule the run is (`aim.check`): a point
one may not aim at has nothing to tell, and the refusal names why, so the
player learns it before paying for the walk.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import biome, facet, terrain, transport, travel
from src.engine.explore import aim as aiming
from src.engine.explore._base import AlreadyJoined, Harnessed, NotFromHere, NotLand
from src.engine.world import land as world
from src.models.identity import Body
from src.models.world import ABOARD, Node
from src.units import PERCENT


async def peek(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    target: globe.Geo,
) -> dict:
    """The field's readings at the cell the aim names, and the chances of what is rolled there."""
    origin = await session.get(Node, body.node_id)
    if origin is None or (origin.properties or {}).get(ABOARD):
        raise NotFromHere(key="explore-not-from-here")
    #: The run's own refusals that do not wait for the walk (`run.survey`):
    #: a cart cannot go, and a cell already joined by a way is not surveyed
    #: again. Said here, or the panel would offer a walk the run refuses.
    if await transport.harnessed(session, body) is not None:
        raise Harnessed(key="explore-harnessed")
    aim = await aiming.check(session, constants, catalog, origin, target, body=body)
    if aim.existing is not None and await travel.edge_between(session, origin, aim.existing):
        raise AlreadyJoined(
            key="explore-already-joined", node=aiming.word_of(constants, aim.existing)
        )
    planet, point = aim.planet, aim.point
    #: The target's own biome, classified at the cell as the run classifies
    #: it (`run._found_node`) -- not `aim.biome`, which is the biome of the
    #: node the scout stands in, kept there for the band of reach. Across a
    #: biome's edge the two differ, and that is where a scout aims.
    here = biome.classify(constants, planet, *point)
    if here is None:  # pragma: no cover -- `aim.check` refused water already
        raise NotLand(key="explore-not-land")
    field = terrain.field_of(constants, planet)
    face = facet.at(constants, catalog, planet, *point, here=here)
    marks = terrain.marks_at(constants, planet, *point)
    temperature, precipitation = terrain.climate_at(constants, planet, *point)
    #: The vein's chance exactly as the run rolls it (`run._found_node`): the
    #: vault's share, times the face's and the province's multipliers.
    vein = (
        float(constants[R.GROUND_VEIN_SHARE])
        / PERCENT
        * facet.vein_k(constants, here, face)
        * field.province_vein_k_at(*point)
    )
    scheme = float(constants[R.COMPLEX_CHANCE].get(planet.value, {}).get(here, 0)) / PERCENT
    #: No distance in the answer: the client measures it from the standing
    #: node to the cross it drew (D-225).
    return {
        #: The node already standing in the cell, when somebody found it first:
        #: then the walk ends at a known place and the rest is its own.
        "found": aim.existing.key if aim.existing is not None else None,
        "biome": here,
        "facet": face.id if face is not None else None,
        "province": field.province_at(*point),
        "water": marks[world.WATER],
        #: Where the map draws no water the run still rolls a stream the
        #: relief is too coarse to draw (`site.river_share`, D-321 item 2;
        #: `ground.properties`): a chance, per cent, told like the others.
        "stream_chance": (
            round(float(constants[R.SITE_RIVER_SHARE]))
            if marks[world.WATER] == world.NO_WATER
            else 0
        ),
        "mountain": bool(marks[terrain.MOUNTAIN]),
        "temperature_c": temperature,
        "rain": precipitation,
        "swing_c": round(facet.swing_c(constants, here, face)),
        #: The marks of the place are rolled by the face's shares
        #: (`ground.properties`): chances, per cent.
        "marks": {key: round(share) for key, share in facet.marks(constants, here, face).items()},
        "vein_chance": round(min(vein, 1.0) * PERCENT),
        "complex_chance": round(min(scheme, 1.0) * PERCENT),
    }
