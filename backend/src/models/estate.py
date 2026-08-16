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

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Building(Base):
    """A building on a plot: a footprint, storeys over it and a durability tier.

    Height is what makes a plot elastic (D-125, D-145): the ground under a
    house is `footprint_m2` however many storeys stand on it, and the usable
    area -- the one machines and cargo are measured against -- is the sum of
    the floors. Each next floor costs `build.floor_cost_growth` times more than
    the one below, and the ceiling of height comes from the tier
    (`build.floors_by_strength`): a timber house does not grow to eight storeys.
    """

    __tablename__ = "building"
    __table_args__ = (
        Index("ix_building_node", "node_id"),
        CheckConstraint("area_m2 > 0", name="area_positive"),
        CheckConstraint("footprint_m2 > 0", name="footprint_positive"),
        CheckConstraint("floors >= 1", name="floors_positive"),
        CheckConstraint("strength >= 1", name="strength_positive"),
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
    #: Durability tier (D-145): sets the material multiplier and the height cap.
    strength: Mapped[int] = mapped_column(nullable=False, server_default="1")

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
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id"), nullable=False, unique=True
    )
    owner_identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.id"), nullable=False
    )

    #: The issue price: the purchase price in minor units, zero for taken wild land.
    paid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: Listed for sale: the price and, if the contract is addressed, the buyer.
    #: Empty -- the deed is not for sale. Sale is remote: the document lives in the Net.

    sale_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sale_to_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    issued_at: Mapped[datetime] = created_column()
