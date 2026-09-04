# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A ship: a group of nodes of the same graph, not a thing in a node (D-201).

There is no new notion of the map here, and that is the whole point. A ship's
rooms are ordinary nodes; membership is the same `parent` hierarchy a city has
over its locations (D-097); docking is one edge between the connector and the
spaceport. This table only holds what the graph itself cannot say: whose the
ship is, which of its nodes faces outwards, and which port it is coupled to.

**Undocked means `docked_node_id` is empty**, and that is the only flight state
there is. The body aboard needs none: there is simply nowhere to step off to.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Ship(Base):
    __tablename__ = "ship"
    __table_args__ = (
        Index("ix_ship_owner", "owner_identity_id"),
        Index("ix_ship_docked", "docked_node_id"),
        Index("ix_ship_held", "held_ship_id"),
        #: Partial: the sweep asks every minute for the few marks there are,
        #: and never for the many nulls (`hold.sweep`).
        Index(
            "ix_ship_docked_ship",
            "docked_ship_id",
            postgresql_where=text("docked_ship_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The name is the owner's, like a plot's (D-178): the engine neither
    #: checks it nor makes anything of it.
    name: Mapped[str] = mapped_column(nullable=False)

    #: The ship belongs to a person, not to land: nodes aboard carry no title
    #: and never will -- there is no ground under them (D-198, D-201). Shares
    #: between builders are a contract (D-116), and the engine counts none.
    owner_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    #: The group's delegate node on the space layer. Nodes aboard are its
    #: children -- the same way a city's locations are children of its node.
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False, unique=True)

    #: The connector: the node laid first and the only one ever holding an edge
    #: outwards. A second one would turn the ship into a bridge between
    #: spaceports and let cargo past the inspection (D-201).
    connector_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id"), nullable=False, unique=True
    )

    #: The spaceport the connector is coupled to. Empty -- in flight: the
    #: subgraph has no edge outwards at all.
    docked_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: Which berth of that port the ship stands at, counted from one. The
    #: gangway is as long as the number: the first ship in is a second's walk
    #: from the yard, the fifth is five. A ship takes the **lowest free** berth,
    #: so casting off does not leave a hole -- the next arrival stands where the
    #: departed one stood. Empty in flight, along with the port.
    berth: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The pier the hull last cast off from (D-242). Kept because "back where
    #: you came from" has to point somewhere: a passage under way knows only
    #: where it is going, and undocking is what erases the other end. Empty for
    #: a hull that has never left a port -- and for one that has, it stays
    #: written after the arrival too: it is a memory, not a state.
    left_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: When the hull's air was last settled (D-233). A stamp, and the reserve is
    #: elsewhere for the same reason a body's is: oxygen is a liquid and lives
    #: in the vessels on the life support's line (D-230, D-288). Nothing is
    #: ticked while the ship sits
    #: at a port of a planet that has air -- the stamp simply moves with the
    #: clock, so a month at a Terran pier is never charged to the tanks the hour
    #: it casts off.
    air_at: Mapped[datetime] = created_column()

    #: The hull in the sky (D-289): where it is and how it moves, and the
    #: moment that was true. Map units and units a day, the sky's own clock.
    #: Empty at a spaceport -- there the graph says where the hull is. Moored
    #: to an orbital node it is the parking circle's state at that moment
    #: (`park_phase` is the angle round the planet), and the circle is
    #: analytic from there; under way or adrift it is the integrator's, moved
    #: by the tick. Never derived from the passage job again: the crossing is
    #: flown, not tabled.
    sky_at: Mapped[datetime | None] = mapped_column(nullable=True)
    sky_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    sky_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    sky_vx: Mapped[float | None] = mapped_column(Float, nullable=True)
    sky_vy: Mapped[float | None] = mapped_column(Float, nullable=True)
    park_phase: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: The order the autopilot flies (D-289): the target, the planned hour,
    #: the line to draw and the phase the helm is in. Empty -- no order: at a
    #: mooring, on a climb or a descent, or adrift. Written by `ship.sim`.
    #: `none_as_null`: a Python None goes in as SQL NULL, not as the JSON
    #: value `null` -- the tick and the hold's sweep filter on `course IS
    #: NOT NULL` in SQL, and a JSON null passed for an order once, taking
    #: every drifter for an ordered hull every minute (review of wave 3).
    course: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    #: The coast ahead as the tick last counted it (D-289): the verdict, its
    #: hour, the line to draw and the moment it was counted from. Written by
    #: the tick and the loss job, read by the console and the map -- a read
    #: never flies ninety days itself. Nothing on a moored hull.
    forecast: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    #: When the hull was lost -- on a body or out of the system (D-289). The
    #: rows stay as history; the map and the tick leave a lost hull alone.
    lost_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Two hulls that met (D-289, wave 3). `held_ship_id` is the hull this one
    #: came to rest beside and now flies as one with: its state is that hull's,
    #: and the tick moves the pair by moving that one. `docked_ship_id` is the
    #: hull it is joined to by an edge connector to connector -- the hold plus
    #: both commanders' consent -- and `dock_ask_ship_id` is the consent this
    #: hull has given and not yet had returned. `sightings` is which foreign
    #: hulls this one has in sight, so the journal says "sighted" once.
    held_ship_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    docked_ship_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dock_ask_ship_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    sightings: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    created_at: Mapped[datetime] = created_column()
