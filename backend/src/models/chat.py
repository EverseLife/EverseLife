"""Live talk in a location (D-043, D-050).

A conversation in a room, not correspondence: those nearby hear, left -- left
the conversation. **The server keeps no history** (D-070, D-081): what was said
aloud is not stored, and no court request will bring it up, because there is
nothing to bring up.

The table below is not history but a **delivery buffer**: clients poll the
chat, and until the next poll a remark has to lie somewhere. The buffer is
swept by the world tick after `runtime.CHAT_BUFFER` -- an execution quantity,
not a balance one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

#: `text` is imported under an alias: inside `ChatMessage` that name is the remark column.
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy import text as sql
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class Utterance(StrEnum):
    """Three message kinds, and the kind is mandatory (D-050).

    Without "action" roleplay is indistinguishable from remarks; without
    "out-of-game" metagame mixes with the in-game, and in court one cannot tell
    what the character said and what the person did.
    """

    #: The character's direct speech.
    SPEECH = "speech"
    #: A third-person description.
    ACTION = "action"
    #: Talk of players, not characters. Explicitly separated and filtered.
    OOC = "ooc"


class ChatGroup(Base):
    """A circle inside a location: groups are visible, their content is not (D-043)."""

    __tablename__ = "chat_group"
    __table_args__ = (Index("ix_chat_group_node", "node_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: A group can be named or left nameless.
    name: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_column()


class ChatMember(Base):
    """Who is in which circle. A person coming up to a circle is seen."""

    __tablename__ = "chat_member"
    __table_args__ = (
        #: An identity stands in one circle: nobody whispers in two conversations at once.
        UniqueConstraint("identity_id", name="uq_chat_member_identity"),
        Index("ix_chat_member_group", "group_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_group.id", ondelete="CASCADE"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    joined_at: Mapped[datetime] = created_column()


class ChatMessage(Base):
    """One remark in the delivery buffer."""

    __tablename__ = "chat_message"
    __table_args__ = (Index("ix_chat_message_node_at", "node_id", "at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    #: Empty -- the location's common talk, heard by everyone in it.
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_group.id", ondelete="CASCADE"), nullable=True
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    kind: Mapped[Utterance] = enum_column(Utterance, "utterance", nullable=False)
    #: In an undertone: fewer leaks, but one's own are heard worse too (D-043).
    quiet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    text: Mapped[str] = mapped_column(nullable=False)

    #: The remark reached foreign ears: rolled once on sending.
    leaked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: `clock_timestamp()`, not `now()`: in Postgres `now()` is frozen for the
    #: whole transaction, and remarks need wall-clock order -- "heard since
    #: arrival" is cut by it.

    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql("clock_timestamp()"), nullable=False
    )
