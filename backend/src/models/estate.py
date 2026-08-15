"""Real estate: a building on a plot and a deed of ownership.

A **building** is what is built on a plot before any machine (D-106, D-125):
a machine is placed in a building and takes its area, so a house's area is
not decoration but capacity: `build.slots_per_area` square metres per work place.

A **deed** is an electronic document of plot ownership. Issued on buying civic
land and on taking wild land; lives in the Net, not the pocket: the body's
death does not touch it (D-012), and it is sold by a sale contract --
remotely, like any document (D-116).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Building(Base):
    """A building on a plot. For now one per node and of one durability tier.

    Storeys and durability tiers are declared by the vault
    (`build.strength_levels`, `build.floors_by_strength`) and will arrive with
    their own mechanic; the table is created so that their arrival is adding
    columns, not a rework.
    """

    __tablename__ = "building"
    __table_args__ = (
        Index("ix_building_node", "node_id"),
        CheckConstraint("area_m2 > 0", name="area_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)

    #: Floor area, m2. No more than the plot area: the yard is the remainder.
    area_m2: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

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
