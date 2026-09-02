# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Real estate: a building on a plot and a deed of ownership.

A **building** is what is built on a plot before any machine (D-106, D-125):
a machine is placed in a building and takes its area, so a house's area is
not decoration but capacity: `build.slots_per_area` square metres per work place.

A **deed** is an electronic document of plot ownership. It is issued by a city
and only by a city -- on buying a civic plot or on being allotted one; land
outside a city is never privatized and has no deed (D-198). It lives in the
Net, not the pocket: the body's death does not touch it (D-012), and it is
sold by a sale contract -- remotely, like any document (D-116).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class Building(Base):
    """A building on a plot: a footprint, storeys over it, a type and a condition.

    Height is what makes a plot elastic (D-125): the ground under a house is
    `footprint_m2` however many storeys stand on it, and the usable area -- the
    one machines and cargo are measured against -- is the sum of the floors.

    The **type** settles the rest (D-218): what goes into the wall per square
    metre (`build.types`), how much dearer each next floor is
    (`build.floor_growth_by_type`) and how fast the house decays
    (`build.decay_by_type`). Height has no ceiling of its own -- a twenty-storey
    log house is allowed and simply ruinous, and that refuses better than a rule.
    """

    __tablename__ = "building"
    __table_args__ = (
        Index("ix_building_node", "node_id"),
        CheckConstraint("area_m2 > 0", name="area_positive"),
        CheckConstraint("footprint_m2 > 0", name="footprint_positive"),
        CheckConstraint("floors >= 1", name="floors_positive"),
        CheckConstraint("condition >= 0 AND condition <= 100", name="building_condition_in_scale"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    #: Usable area, m2: the footprint times the storeys. Machines, cargo and
    #: upkeep are all counted against it.
    area_m2: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    #: The ground the house stands on, m2. No more than the plot: the yard is
    #: the remainder, and only this is taken from it -- not the usable area.
    #:
    #: Unnamed, it equals the usable area -- that is exactly a one-storey
    #: house, and such were all of them before storeys arrived. Without this
    #: default a building could be created with a footprint of zero, and a
    #: house standing on no ground at all is not a house.
    footprint_m2: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=lambda context: context.get_current_parameters()["area_m2"],
        server_default="0",
    )
    floors: Mapped[int] = mapped_column(nullable=False, server_default="1")

    #: The building type (D-218) -- the vault's own word, not a number: with
    #: the tier ladder gone the class of a house is a name, and the bill,
    #: the price of a floor and the rate of decay are all read by it.
    kind: Mapped[str] = mapped_column(String(64), nullable=False, server_default="wooden")

    #: How worn the house is now, on the same 0-100 scale as a tool's condition.
    #: Until it reaches zero the house stands at full strength -- it loses
    #: neither places nor area; at zero it collapses (D-218). That is what keeps
    #: repair a decision one takes rather than a levy one stops noticing.
    condition: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, default=100, server_default="100"
    )

    built_at: Mapped[datetime] = created_column()


class Deed(Base):
    """A deed of plot ownership.

    The deed holder is the node's owner: `node.owner_identity_id` is the same
    thing, duplicated for the engine's fast checks, and they change together.
    """

    __tablename__ = "deed"
    __table_args__ = (
        Index("ix_deed_owner", "owner_identity_id"),
        CheckConstraint("sale_price IS NULL OR sale_price > 0", name="sale_price_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: One plot -- one deed: a second deed for the same node is a forgery.
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False, unique=True)
    owner_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    #: The issue price: the purchase price in minor units, zero for taken wild land.
    paid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: Listed for sale: the price and, if the contract is addressed, the buyer.
    #: Empty -- the deed is not for sale. Sale is remote: the document lives in the Net.

    sale_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sale_to_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    issued_at: Mapped[datetime] = created_column()


class SiteState(StrEnum):
    """Where a construction site stands (D-266)."""

    #: Laid out; materials arrive by contribution, the ground is spoken for.
    GATHERING = "gathering"
    #: Every material brought and the owner paid with the body: the term runs.
    BUILDING = "building"
    #: The term is out; the house rises when the owner says so.
    READY = "ready"
    #: The house stands; the site is history.
    DONE = "done"


class BuildSite(Base):
    """A construction site on a plot: the bill, what was brought, the phase (D-266).

    A house used to be bought in one motion -- every material written off the
    builder's hands at once -- and nothing bigger than a shed fit in those
    hands. The site takes the materials by parts, from anybody, in as many
    trips as it takes; then the owner starts the build with time and the
    body, and raises the house by hand when the term is out.

    `needed` and `brought` are the bill and its fulfilment, goods key to
    units. Both change only under the row's own lock: two contributions at
    once must not both fill the last gap.
    """

    __tablename__ = "build_site"
    __table_args__ = (
        Index("ix_build_site_node", "node_id"),
        CheckConstraint("footprint_m2 > 0", name="site_footprint_positive"),
        CheckConstraint("floors >= 1", name="site_floors_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    owner_identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    footprint_m2: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    floors: Mapped[int] = mapped_column(nullable=False, server_default="1")
    kind: Mapped[str] = mapped_column(String(64), nullable=False)

    needed: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    brought: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    state: Mapped[SiteState] = enum_column(
        SiteState, "build_site_state", nullable=False, default=SiteState.GATHERING
    )

    laid_at: Mapped[datetime] = created_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
