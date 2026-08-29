# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The node -- the atom of the world, and the vein inside it.

A node is not "a place where a building stands" but the building itself: a
location, a graph leaf, a point one arrives at (D-089, 10-world/07-map-topology).
A node holds exactly one building **or** vein **or** nothing -- that is an
integrity invariant, not a wish (05-domain-model, I4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class Planet(StrEnum):
    TERRA = "terra"
    AQUATICA = "aquatica"
    PYROXIS = "pyroxis"
    AURORA = "aurora"


class Layer(StrEnum):
    """The map layer the node is shown on (D-045, D-097).

    The world is one graph of locations; layers are a display abstraction, not
    the world's structure. Upper-layer nodes (a planet in space, a city on a
    planet) are group delegates: they have children, but one does not walk on
    them -- one walks on the leaves.
    """

    #: Planets and ships: what is seen from space.
    SPACE = "space"
    #: Cities and large solitary locations of the planet.
    PLANET = "planet"
    #: City built-up area: rings around the bioprinter (D-089).
    CITY = "city"
    #: Sub-nodes of a location: floors of a house, rooms of a complex.
    LOCATION = "location"


class Node(Base):
    __tablename__ = "node"
    __table_args__ = (
        Index("ix_node_parent", "parent_id"),
        #: `world.epoch()` is `min(created_at)`, asked by every look.
        Index("ix_node_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: A stable key for references from data and tests: `terra.capital`.
    key: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    planet: Mapped[Planet] = enum_column(Planet, "planet", nullable=False)

    #: Which layer the node is shown on. One walks on leaves; a node with
    #: children is the group's delegate on its layer.
    layer: Mapped[Layer] = enum_column(Layer, "node_layer", nullable=False, default=Layer.CITY)
    #: The group the node belongs to: location -> city -> planet. A display
    #: hierarchy over the graph, not a second graph.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("node.id"), nullable=True)

    #: Plot area, m2. Rolled when the node appears (D-125).
    area_m2: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    #: Place properties: temperature, rainfall, water, fertility, wind, forest.
    #: Rolled at generation, the sum of merits is bounded (D-126). Kept as a
    #: map because the set of properties will still change.
    properties: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    owner_city_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: The plot's owner: land is taken in person, in a wild node (06-farming).
    #: The title is sold by exchange like things (D-116) -- when exchange arrives.
    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: The location is shut for entry (D-199, D-204): only the owner and the
    #: white list come in. Land outside a city has no owner and nothing to shut
    #: (D-198). Shutting stops **entry**, not passage: a route goes straight
    #: through a shut location, and departures are never stopped -- shutting the
    #: gate on a guest inside would be a way to take a body away, and death
    #: without a way out is forbidden.
    gated: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    #: How far this place is from its city's bioprinter, in nodes (D-220), and
    #: which printer that was. Land is dearer to buy and to hold near the
    #: centre, so both the price and the day's tax ask for this number -- the
    #: tax for **every** held plot at once, once a day.
    #:
    #: Written down rather than walked for: measuring it means reading the whole
    #: edge table and walking the graph, and a city of six hundred nodes made
    #: that eleven milliseconds per question. Kept honest by two things, and
    #: only two are needed:
    #:
    #: * `center_node_id` says whose centre was measured to. A plot sold to
    #:   another city, a city founded, a printer carried away -- the centre no
    #:   longer matches and the number is measured again;
    #: * `center_steps` is emptied wherever the graph itself changes, which is
    #:   where an edge appears or goes (`travel.connect`, `ship.ascend`).
    center_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    center_steps: Mapped[int | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = created_column()


class NodePass(Base):
    """A name in one of the location's two lists (D-204).

    The white list gets into a shut location, the black one gets in nowhere --
    neither into a shut location nor into an open one. The contradiction between
    them is resolved by one line, and it is shorter than the rule of a single
    roster whose meaning flipped with the gate: **black beats white**.

    One row per person per location: `allowed` says which list the name is in,
    so moving somebody from one list to the other is a change of the flag rather
    than a pair of rows that could both exist.
    """

    __tablename__ = "node_pass"
    __table_args__ = (
        Index("ix_node_pass_node", "node_id"),
        UniqueConstraint("node_id", "identity_id", name="uq_node_pass"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: Which list the name is in: the white one (entry to a shut location) or
    #: the black one (no entry at all). The default is the white list -- the old
    #: single roster of a shut yard meant exactly that (D-199).
    allowed: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    listed_at: Mapped[datetime] = created_column()


class Surface(StrEnum):
    """The edge's surface decides both time and the very possibility to drive through (D-107)."""

    #: Offroad: two to three times longer, no vehicle passes at all.
    TRAIL = "trail"
    #: Road -- the time reference.
    ROAD = "road"
    #: Paved highway: faster, holds heavy vehicles.
    PAVED = "paved"


class Edge(Base):
    """A graph edge: a route with properties, not just a link (10-world/07).

    The edge is **undirected**: the road is the same both ways. Stored as one
    row, and lookup goes by both ends -- otherwise sooner or later an edge
    leading only one way appears.
    """

    __tablename__ = "edge"
    __table_args__ = (
        Index("ix_edge_a", "node_a_id"),
        Index("ix_edge_b", "node_b_id"),
        UniqueConstraint("node_a_id", "node_b_id", name="uq_edge_pair"),
        CheckConstraint("node_a_id <> node_b_id", name="edge_not_a_loop"),
        CheckConstraint("base_seconds > 0", name="edge_takes_time"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_a_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    node_b_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    #: How long to walk along the road. Seconds: a step inside the city and a
    #: transit between nodes live in one quantity, not two different units.
    base_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    surface: Mapped[Surface] = enum_column(
        Surface, "edge_surface", nullable=False, default=Surface.ROAD
    )
    #: Surface condition, 0..100 (D-158). Falls by `road.decay_rate` per day;
    #: at zero the surface drops a tier and the condition starts anew. For
    #: offroad it has no meaning: nothing there to overgrow.
    condition: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=100)
    created_at: Mapped[datetime] = created_column()


class Vein(Base):
    """A vein: species, stock, richness. Veins are finite -- that is irrevocable (pillar P2)."""

    __tablename__ = "vein"
    __table_args__ = (
        Index("ix_vein_node", "node_id"),
        CheckConstraint("remaining >= 0", name="remaining_non_negative"),
        CheckConstraint("richness >= 0 AND richness <= 100", name="richness_in_scale"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    #: The species -- a raw-material name from `build/recipes.json` ("Ore", "Coal", "Stone").
    resource: Mapped[str] = mapped_column(nullable=False)

    #: Richness 0..100. Sets yield, raw-material quality and roof stability.
    #: Falls as worked out by `vein.richness_decay` per `vein.depletion_step`.
    richness: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: The remaining stock in internal units (`units.AMOUNT_SCALE`).
    #: Reached zero -- the vein disappears, and the mining town with it.
    remaining: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: How much has been extracted in total -- depletion tiers are counted from it.
    extracted: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: HIDDEN roof stability of the working (D-188). It belongs to the vein, not
    #: to a session: rock does not knit back together while the miner is away,
    #: and leaving the pit used to reset the risk to zero. Empty means untouched
    #: -- the first session starts it from richness. Never leaves the API.
    roof: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)

    created_at: Mapped[datetime] = created_column()
    depleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
