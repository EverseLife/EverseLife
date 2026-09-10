# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the ground lets one see from where one stands (landscape plan wave 9, §10).

The eye is a radius and stays one: `map.sight_km` says how far off a place
can be made out at all (D-319 п. 6, cut to a hundred metres by D-323 so that
a city opens on foot, node by node). What this module adds is that **the
ground may take from that radius and never adds to it** (owner, 2026-09-09):
a node inside the radius is seen unless the land between stands in the way.

So nothing changes on the plain -- the horizon of an eye two metres up is six
hundred metres and the radius is one -- and in broken country a neighbour
behind a rise is simply not there until one walks to it. Measured over Terra
at the sight radius, eight ways out of every point of a four-degree grid:
alpine hides 5.7 % of its neighbours, foothills 2.7 %, the coast 1.5 %, the
floodplain 0.9 %, and the open country 0.6 % all told -- ten times less than
the mountains. (Measured again on the equal-area grid, D-328: the numbers
moved by tenths, which is the point -- a different grid, the same world.)

The curve of the planet is carried even at these distances, because one walk
answers the question at any range: the ground falls away from a straight line
by `d² / 2R`, which is half a metre at ten kilometres and five centimetres at
a hundred. Writing it once is cheaper than keeping two rules and choosing.

The geometry is pure and the field is read apart from it (`profile`), so the
judging can be tested against a slope drawn by hand rather than against
whatever Terra happens to have (plan §9.9: what is testable is the pure
function).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from src import field as fields
from src import globe
from src.constants import Constants
from src.constants import registry as R
from src.engine import terrain
from src.models.world import Planet
from src.units import GRAZE, METRES_PER_KM


def drop_m(radius: float, span: float) -> float:
    """How far the ground falls away from a straight line over this distance.

    The sagitta of the arc, near enough: `d² / 2R` differs from the exact
    drop by parts in a million at every distance an eye reaches.
    """
    return span * span / (radius + radius)


def horizon_m(radius: float, eye_m: float) -> float:
    """How far an eye this high above the sea sees on a sphere this big.

    The tangent from the eye to the sphere: `sqrt(h(2R + h))`. On Terra's
    radius of 99.5 km it gives the ladder the plan reckoned with -- 630 m
    from a standing man, 3.2 km from a fifty-metre hill, 14 km from a
    thousand-metre summit -- and it is the reason the world is unknown.
    """
    high = max(0, eye_m)
    return math.sqrt(high * (radius + radius + high))


def blocked(
    radius: float,
    span: float,
    eye_m: float,
    target_m: float,
    between: Sequence[tuple[float, float]],
) -> bool:
    """Whether the ground between hides the target from the eye.

    Heights are metres above the sea, distances metres along the ground:
    `eye_m` is the eye itself (the standpoint's ground plus a man's height),
    `target_m` the point looked at, `between` the ground on the way as
    (metres along, metres above the sea).

    An angle rather than a height, because the eye is not level: what is
    compared is how far above the line of sight each point stands, divided
    by how far off it is -- and each is dropped by the curve first. A point
    that rises higher than the target's own angle is between the two.

    `GRAZE` is why a hillside does not hide its own foot. On a plain slope
    every point of the way lies exactly on the line of sight, and which side
    of it the arithmetic puts them on is decided by the last bits of a
    float. Ground has to stand **over** the line by a hair to count as being
    in the way; without that hair a slope hid what stood on it, and a
    measure over Terra came out at a fifth of the alpine and a twenty-fifth
    of the steppe -- where nothing is in the way at all.
    """
    if span <= 0:
        return False
    aim = (target_m - eye_m - drop_m(radius, span)) / span
    for along, height in between:
        if along <= 0 or along >= span:
            continue
        if (height - eye_m - drop_m(radius, along)) / along > aim + GRAZE:
            return True
    return False


def walk(step: float, span: float) -> np.ndarray:
    """How far along the way each reading is taken, strictly between the ends.

    The last step used to land on the target itself when the span was a hair
    over a multiple of the step, and then the ground at the target was
    compared with the target and the answer came out of the rounding.
    """
    many = max(0, int(math.ceil((span - globe.midpoint(0.0, step)) / step)) - 1)
    return np.arange(1, many + 1, dtype=float) * step


def profile(
    constants: Constants,
    planet: Planet,
    frm: globe.Geo,
    to: globe.Geo,
    span: float,
) -> list[tuple[float, float]]:
    """The ground between two points, every `map.sight_step_m` of the way.

    Read straight off the field, which is a file (D-237): the same two points
    give the same profile for ever. The step is the vault's, and it is set
    against the distances a place stands at -- a node's neighbours are metres
    to a hundred metres off (`map.city_step_m`, `map.sight_km`) -- and not
    against the field's own cell, which is read between its own anyway.
    """
    field = terrain.field_of(constants, planet)
    rise = float(constants[R.TERRAIN_RELIEF_M])
    along = walk(float(constants[R.MAP_SIGHT_STEP_M]), span)
    if not along.size:
        return []
    lat, lon = globe.walk_between(frm, to, along / span)
    return list(zip(along.tolist(), (field.reliefs(lat, lon) * rise).tolist(), strict=True))


def _height(field: fields.Field, rise: float, at: globe.Geo) -> float:
    """The ground at a point, metres over the sea, off a field already in
    hand. `terrain.height_m` says the same thing and looks the field up
    every time it is asked -- a hundred times a walk, and the walk is run
    for every place inside the radius on every reading of the map."""
    if field.is_sea(at[0], at[1]):
        return 0.0
    return field.relief(at[0], at[1]) * rise


def hidden(
    constants: Constants,
    planet: Planet,
    frm: globe.Geo,
    to: globe.Geo,
    *,
    radius: float | None = None,
    span: float | None = None,
) -> bool:
    """Whether the ground hides `to` from an eye standing at `frm`.

    `radius` and `span` may be handed in by a caller that has already
    measured them -- the map asks this of every place inside the eye's
    radius, and it found the distance to each of them to get there.

    The eye, the target and the whole way between them are read in **one**
    ask of the field. Point by point they were forty microseconds each on
    the equal-area grid against two on the old one, and this runs for every
    node in sight on every reading of the map: the map went slow to the hand.
    """
    radius = globe.radius_m(constants, planet) if radius is None else radius
    span = globe.distance_m(radius, frm, to) if span is None else span
    if span <= 0:
        return False
    field = terrain.field_of(constants, planet)
    rise = float(constants[R.TERRAIN_RELIEF_M])
    along = walk(float(constants[R.MAP_SIGHT_STEP_M]), span)
    #: The eye's own point and the target lead the walk, so the whole
    #: reading is one ask of the field.
    ends = np.array([0.0, 1.0])
    lat, lon = globe.walk_between(frm, to, np.r_[ends, along / span])
    ground = field.reliefs(lat, lon) * rise
    eye = float(ground[0]) + float(constants[R.MAP_EYE_M])
    between = list(zip(along.tolist(), ground[ends.size :].tolist(), strict=True))
    return blocked(radius, span, eye, float(ground[1]), between)


def reach_m(constants: Constants, planet: Planet, at: globe.Geo) -> float:
    """How far the eye reaches from this point: the vault's radius, and never
    further than the planet's own curve allows from the height one stands at.

    On the plain the radius is the answer and the curve is not near binding;
    the cut is there because the rule is "the ground may take, not add", and
    a rule with an exception for the flat case would be two rules.
    """
    radius = globe.radius_m(constants, planet)
    eye = terrain.height_m(constants, planet, at[0], at[1]) + float(constants[R.MAP_EYE_M])
    reach = float(constants[R.MAP_SIGHT_KM]) * METRES_PER_KM
    return min(reach, horizon_m(radius, eye))
