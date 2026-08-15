"""Account, identity, body -- three different things, and they must not be confused.

| Entity | Lost on death |
|---|---|
| Account -- payment, device, telemetry | -- |
| Identity -- name, reputation, knowledge, account, citizenship | **No** |
| Body -- stamina, inventory, location, wounds | **Yes, entirely** |

Hence all behaviour on death: recipes and agrotech survive death, while
tools, seeds and the coin in the purse do not (D-011, D-012, D-033, 09-death).

One account -- one identity (D-011). The name cannot be changed, so reputation
is inalienable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Numeric, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class Account(Base):
    """Payment and device. Deliberately has no game properties.

    Identified by email and password (D-187). The password is stored as an
    Argon2id hash -- the same algorithm as the device fee, so as not to drag
    in a second library. Email is empty only for accounts created before
    D-187: the seed assigns it in catch-up, and after that no empty ones remain.
    """

    __tablename__ = "account"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Email is stored lower-cased: `Tern@` and `tern@` are one person, and
    #: uniqueness must see that.
    email: Mapped[str | None] = mapped_column(unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_column()
    disabled_at: Mapped[datetime | None] = mapped_column(nullable=True)


class LoginToken(Base):
    """Session token: the client holds it instead of the password (D-187).

    Socket reconnection and page refresh are identified by the token, and the
    password is entered once. The database holds the token's **hash**: a leaked
    table does not let anyone in. Logging out of the account panel revokes the token.
    """

    __tablename__ = "login_token"
    __table_args__ = (Index("ix_login_token_account", "account_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(unique=True, nullable=False)
    created_at: Mapped[datetime] = created_column()
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Line(StrEnum):
    """Character line (D-015, D-104). One is playable in the alpha."""

    HUMAN = "human"
    NYMPH = "nymph"


class Identity(Base):
    """The identity. Indestructible: stored in the Net on all bioprinters at once."""

    __tablename__ = "identity"

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id"), unique=True, nullable=False
    )
    #: The name cannot be changed -- reputation rests on that (40-society/05).
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    #: Surname, age and description are self-description, irrelevant to the
    #: engine and changed by the player in the account panel (D-187). Age is a
    #: number, not a date: a printed person's body is always new.
    surname: Mapped[str] = mapped_column(nullable=False, default="", server_default="")
    age: Mapped[int | None] = mapped_column(nullable=True)
    about: Mapped[str] = mapped_column(nullable=False, default="", server_default="")
    line: Mapped[Line] = enum_column(
        Line, "identity_line", nullable=False, default=Line.HUMAN, server_default="human"
    )
    citizenship_city_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = created_column()


class KnowledgeKind(StrEnum):
    """The four kinds of knowledge behave alike: they live in the identity, are
    copied to a carrier, are traded (05-domain-model)."""

    RECIPE = "recipe"
    PROPORTION = "proportion"
    AGROTECH = "agrotech"
    COMBINATION = "combination"
    PROGRAM = "program"


class Knowledge(Base):
    """Knowledge is taken away neither by death, nor by court, nor by city (invariant I8)."""

    __tablename__ = "knowledge"
    __table_args__ = (
        UniqueConstraint("identity_id", "kind", "key", name="uq_knowledge_identity_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    kind: Mapped[KnowledgeKind] = enum_column(KnowledgeKind, "knowledge_kind", nullable=False)
    #: A recipe name from `build/recipes.json` or the name of a saved setting.
    key: Mapped[str] = mapped_column(nullable=False)
    #: The discoverer -- the name is bound to the recipe forever (D-064).
    discovered: Mapped[bool] = mapped_column(nullable=False, default=False)
    acquired_at: Mapped[datetime] = created_column()


class BodyState(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"


class Body(Base):
    """The shell. Finite, and that too is an invariant (I3)."""

    __tablename__ = "body"
    __table_args__ = (
        Index("ix_body_node_state", "node_id", "state"),
        Index("ix_body_identity_state", "identity_id", "state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    state: Mapped[BodyState] = enum_column(
        BodyState, "body_state", nullable=False, default=BodyState.ALIVE
    )

    #: Stamina. There is no daily ceiling: the constraint is economic --
    #: working longer means eating more (D-091).
    stamina: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    #: Hibernation: the body sleeps and recovers offline (D-091). Empty -- awake.
    sleeping_since: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Fell asleep with a bed: hibernation at home goes faster. The flag is
    #: fixed at falling asleep -- a bed brought to a sleeper does not improve their sleep.
    sleeping_home: Mapped[bool] = mapped_column(nullable=False, default=False)

    #: Satiety: until this moment the stamina spend is reduced (D-119). Hot
    #: food adds no reserve -- it slows the spend, and that is not a buff but a meal.
    satiated_until: Mapped[datetime | None] = mapped_column(nullable=True)

    #: When the body took its node: by print or by arrival. Before that moment
    #: it heard nothing here (D-043) -- a chat horizon, not a biography.
    node_since: Mapped[datetime] = created_column()

    printed_at: Mapped[datetime] = created_column()
    died_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Wound(Base):
    """A wound: slows and cuts stamina, healed by time and bandaging (D-096)."""

    __tablename__ = "wound"
    __table_args__ = (Index("ix_wound_body", "body_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("body.id"), nullable=False)
    #: What it was inflicted by -- for the journal and the court: "roof collapse".
    cause: Mapped[str] = mapped_column(nullable=False)
    treated: Mapped[bool] = mapped_column(nullable=False, default=False)
    heals_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = created_column()
