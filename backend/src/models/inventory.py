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

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class ContainerKind(StrEnum):
    """Where the item lies. Matter moves only physically (D-047)."""

    #: A body's inventory. Perishes with the body.
    BODY = "body"
    #: Everything that stands and lies in the node: machines, furniture, chests,
    #: cargo. **One** store, and the node's two surfaces (D-244) are a mark on
    #: the thing rather than a second one of these -- see `Item.outdoors`.
    #:
    #: That was tried the other way and taken back. Some sixty places ask this
    #: container "what is in this node" and mean everything: the fire of an
    #: eruption looking for what to burn, a rig looking for its coal, a brazier
    #: for its fuel, a chest for its lid. A second store answered half of each
    #: question, and the half it left out fell quietly out of the world.
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
        CheckConstraint(
            "quality IS NULL OR (quality >= 0 AND quality <= 100)", name="quality_in_scale"
        ),
        CheckConstraint("condition >= 0 AND condition <= 100", name="condition_in_scale"),
        CheckConstraint(
            "wear_remainder >= 0 AND wear_remainder < 0.01",
            name="wear_remainder_under_a_hundredth",
        ),
        CheckConstraint(
            "fineness IS NULL OR (fineness > 0 AND fineness <= 1000)", name="fineness_in_permille"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("container.id"), nullable=False)
    #: Which of a node's two surfaces this lies on: the floor of the house, or
    #: the open ground beside it (D-244). Read **only** for a thing lying loose
    #: in a node's own container -- in a pocket, a chest or a tank the question
    #: does not arise and the answer is meaningless.
    #:
    #: And read through `estate.outdoors`, never raw: on a node with no building
    #: there is no floor to be on, so everything there is outdoors whatever the
    #: column says. That is what lets the rest of the engine go on putting things
    #: into a node without knowing this column exists -- loot from a death, cargo
    #: spilt by a broken cart, materials back from a demolition.
    outdoors: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    #: Name from `build/recipes.json` -- a recipe or raw material.
    type_key: Mapped[str] = mapped_column(nullable=False)

    #: Internal units (`units.AMOUNT_SCALE`). A product is always one piece.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: How well made. For raw material determined by the vein and work at the face.
    quality: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: How worn now.
    condition: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=100)
    #: Wear the condition column could not show. It keeps hundredths, and a
    #: doing may cost less than one -- a rig settled every half minute, a swing
    #: of a pick on a fine tool. Dropped, such wear never happened at all and a
    #: machine tapped often enough was immortal (D-129). Kept here it is spent
    #: on the next write-off. Always `0 <= wear_remainder < 0.01`: the column
    #: is checked against it, so a bug that broke the bound fails loudly rather
    #: than quietly making things last for ever. The width alone would not do:
    #: `Numeric(9, 9)` holds anything under one.
    wear_remainder: Mapped[float] = mapped_column(
        Numeric(9, 9), nullable=False, default=0, server_default="0"
    )
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

    #: Standing in the building it was put up in (D-278): a machine or a piece
    #: of furniture placed by `station.place` -- counted in the slots, worked
    #: at, shown among the machines. False, it lies as cargo wherever it is:
    #: in the hands, on the floor, in a hold. Only the placeable kinds read it.
    installed: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

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
