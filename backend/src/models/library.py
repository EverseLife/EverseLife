# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a library holds (D-068, D-209).

A library contains what was put into it, not the whole catalog: the capital's
gets the base set at genesis, one built by a city starts empty and fills as
people bring carriers. An entry never leaves -- what is given to a library is
given for good -- and the contributor's name stays with it (03-crafting).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class LibraryEntry(Base):
    __tablename__ = "library_entry"
    __table_args__ = (
        UniqueConstraint("node_id", "recipe", name="uq_library_entry_node_recipe"),
        Index("ix_library_entry_node", "node_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The node the library stands in. Entries belong to the place, as the
    #: shelves do: carry the machine away and the knowledge stays on the wall.
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: A recipe name from `build/recipes.json`.
    recipe: Mapped[str] = mapped_column(nullable=False)
    #: Who brought it. Empty for the base set laid down at genesis.
    contributor_identity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    contributed_at: Mapped[datetime] = created_column()
