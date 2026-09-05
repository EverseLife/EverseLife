# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The floor of exploration: the lattice, the refusals, the shape of an aim (D-321).

A planet is covered by a **lattice** of `map.lattice_m`: the point a scout aims
at is pressed to the nearest cell, and the cell is the node's key. That is what
makes a find the same for everybody (D-237) without a single row laid in
advance -- two cities exploring towards each other find one node, not two.

The lattice is laid in metres of arc: rows of latitude `map.lattice_m` apart,
and along each row columns as wide as the row's own metres allow, so a cell
near the pole is not a sliver. Two cells therefore never share a key, and a
point has exactly one cell.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from src import globe
from src.constants import Constants
from src.constants import registry as R
from src.engine.errors import Refusal
from src.models.world import Node, Planet

#: The cell of a point: row and column of the lattice.
Cell = tuple[int, int]


class ExploreError(Refusal):
    pass


class NotFromHere(ExploreError):
    """One explores from a node of a planet's surface: not from aboard, not from a room."""


class TooNear(ExploreError):
    """Closer than the biome lets one aim."""


class TooFar(ExploreError):
    """Farther than the biome lets one aim."""


class NotLand(ExploreError):
    """The aim is in the water, or past the last latitude."""


class IntoWater(ExploreError):
    """The straight way to the aim crosses water: from the shore one does not aim at the sea."""


class NoRoom(ExploreError):
    """The aim lies on ground another node already takes."""


class CrossesWay(ExploreError):
    """The new way would cross an existing one."""


class AlreadyOut(ExploreError):
    """The body is already on a run."""


class AlreadyJoined(ExploreError):
    """The aimed cell is a node the origin already has a way to: nothing to find."""


class ScoutGone(ExploreError):
    """The scout died or walked away before the run was over."""


def lattice_deg(constants: Constants, planet: Planet) -> float:
    """The lattice step as degrees of latitude on this planet."""
    radius = globe.radius_m(constants, planet)
    return math.degrees(float(constants[R.MAP_LATTICE_M]) / radius)


def _row_lon_step(step: float, row: int) -> float:
    return step * globe.lon_stretch(row * step)


def cell_of(constants: Constants, planet: Planet, point: globe.Geo) -> Cell:
    """The cell a point falls into."""
    step = lattice_deg(constants, planet)
    row = round(point[0] / step)
    col = round(point[1] / _row_lon_step(step, row))
    return row, col


def point_of(constants: Constants, planet: Planet, cell: Cell) -> globe.Geo:
    """Where a cell stands: its centre, the place a node found there has."""
    step = lattice_deg(constants, planet)
    row, col = cell
    lat = max(-globe.LAST_LAT, min(globe.LAST_LAT, row * step))
    lon = globe.wrap_lon(col * _row_lon_step(step, row))
    return lat, lon


def key_of(planet: Planet, cell: Cell) -> str:
    """The node key of a cell: one for the world, whoever finds it."""
    return f"{planet.value}.cell.{cell[0]}.{cell[1]}"


@dataclass(frozen=True, slots=True)
class Aim:
    """A lawful aim: where the scout goes, what it costs, and what is already there."""

    origin_id: uuid.UUID
    planet: Planet
    cell: Cell
    point: globe.Geo
    metres: float
    biome: str
    #: The node already standing in the cell, when somebody found it first.
    existing: Node | None
