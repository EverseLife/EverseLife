# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Items and what holds them.

Two 0..100 numbers on an item, and they are confused most often (15-quality):

* **quality** -- how well the item is made. Never changes;
* **condition** -- how worn it is now. Starts at 100.

Quality determines how fast condition falls and how effective the item is at
each moment. The mark is mandatory: every product remembers its craftsman, and
that makes reputation tangible (D-058).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class ContainerKind(StrEnum):
    """Where the item lies. Matter moves only physically (D-047)."""

    #: A body's inventory. Perishes with the body.
    BODY = "body"
    #: What stands in the node under the open sky: machines, products at the
    #: machine. A temporary home for machines: with buildings (E3) they move to
    #: `BUILDING`, because the machine sets what a building is (D-106).
    NODE = "node"
    #: A building: warehouse, workshop, yard.
    BUILDING = "building"
    #: The inside of a storage -- a chest or a shelf (D-181). The container's
    #: owner is the furniture item itself: carry the furniture away and the contents go too.
    STORAGE = "storage"
    #: An identity's goods loaded into the node's marketplace terminal.
    #: Loading is physical, disposing is remote (D-047).
    MARKET = "market"
    #: A vehicle's cargo.
    VEHICLE = "vehicle"
    #: What was mined in the current session: not in the inventory yet, lost on a collapse.
    MINING_SESSION = "mining_session"


class Container(Base):
    __tablename__ = "container"
    __table_args__ = (Index("ix_container_owner", "kind", "owner_id", "node_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[ContainerKind] = enum_column(ContainerKind, "container_kind", nullable=False)
    #: Body, building, identity or session -- depending on the kind.
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: Where it lies, if bound to a place. Empty for a body's inventory: the
    #: body carries its own with it. Mandatory for goods in a terminal -- the
    #: marketplace is always local, there is and will be no global market (D-003).
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = created_column()


class Item(Base):
    """A stack of identical raw material, or one product.

    Raw material stacks, products do not (04-items). The sign is taken from
    `build/recipes.json`: the recipe's `kind` determines behaviour (D-090).
    """

    __tablename__ = "item"
    __table_args__ = (
        Index("ix_item_container", "container_id"),
        Index("ix_item_type", "type_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("quality IS NULL OR (quality >= 0 AND quality <= 100)",
                        name="quality_in_scale"),
        CheckConstraint("condition >= 0 AND condition <= 100", name="condition_in_scale"),
        CheckConstraint("fineness IS NULL OR (fineness > 0 AND fineness <= 1000)",
                        name="fineness_in_permille"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("container.id"), nullable=False)

    #: Name from `build/recipes.json` -- a recipe or raw material.
    type_key: Mapped[str] = mapped_column(nullable=False)

    #: Internal units (`units.AMOUNT_SCALE`). A product is always one piece.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: How well made. For raw material determined by the vein and work at the face.
    quality: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: How worn now.
    condition: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=100)
    #: Condition ceiling. Falls with every repair by `quality.repair_ceiling_loss`,
    #: so the item is finite anyway (pillar P2, D-129).
    condition_cap: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=100)

    #: The mark: who, when and where made it (D-058).
    maker_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    made_at: Mapped[datetime | None] = mapped_column(nullable=True)
    made_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    #: For food: when it spoils. Cooked spoils `cook.spoilage_multiplier` times
    #: faster than raw (D-119).
    spoils_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Coin fineness in thousandths (D-016). A coin has no quality at all: its
    #: metal content describes it, and the issuer's mark is `maker_identity_id`.
    #: The issuer may debase the fineness, keeping the difference -- and that shows here.
    fineness: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)

    #: For a battery: how much energy is in it and when that was recorded
    #: (D-071). Energy does not lie in a sack -- it is either in the city pool
    #: or here, and from here it slowly leaks: `energy.battery_selfdischarge` per day.
    charge: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    charged_at: Mapped[datetime | None] = mapped_column(nullable=True)

    #: For seeds: whose cultivar and how much of its strength the batch kept, %
    #: (D-057). Strength falls for a seed fund resown without selection -- and
    #: that is why a breeder is needed even where no new cultivars are bred (D-067).
    variety_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    vigor: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)

    #: For a machine: whose body works at it now (D-150). While a batch runs
    #: the machine is taken and not given to a second: a workshop is as many
    #: places as machines, not a free shop floor for the whole town.
    busy_body_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    #: Until when it is taken. The stamp insures against eternal occupancy if
    #: the batch vanishes past its job.
    busy_until: Mapped[datetime | None] = mapped_column(nullable=True)

    #: Dish kind: the combination decides the kind, not the quality (D-128).
    #: Dietary variety is counted by kind (D-105). Empty for non-food.
    flavor: Mapped[str | None] = mapped_column(nullable=True)
    #: Share of filled roles: a full meal keeps satiety longer (D-128).
    roles_filled: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)

    #: For a knowledge carrier: which recipe is written on it (D-209). A blank
    #: has none; a written one is a different item on the counter, though the
    #: type is the same -- the market keys it together with the recipe.
    recipe_key: Mapped[str | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = created_column()
