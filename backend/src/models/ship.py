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

from sqlalchemy import ForeignKey, Index, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Ship(Base):
    __tablename__ = "ship"
    __table_args__ = (
        Index("ix_ship_owner", "owner_identity_id"),
        Index("ix_ship_docked", "docked_node_id"),
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

    created_at: Mapped[datetime] = created_column()
