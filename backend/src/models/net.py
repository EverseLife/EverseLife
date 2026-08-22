# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The Net: correspondence and channels (D-044, D-069, D-222).

Unlike the room (`models/chat.py`) this **is** history: a letter is kept and
read back later, and that is the whole difference between a conversation and
correspondence. What lies here:

* a **thread** -- correspondence between identities. Today a thread has two
  parties and `pair_key` keeps the pair unique; the parties are a table of
  their own so that a group thread (`chat.net_group_limit`) is one more row,
  not a second schema;
* a **message** in a thread, with the moment it was sent and the moment it
  **arrives** -- the delay is measured once, on sending, from the writer's body
  to the reader's (D-222);
* a **channel** -- one author's feed that others subscribe to. A city's
  official channel is the one with `city_id`; the rest belong to a player;
* a **post** in a channel. Delivered per reader, at reading time: the author's
  node is written down with the post for exactly that.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class NetThread(Base):
    """Correspondence. Exists from the moment somebody decided to write."""

    __tablename__ = "net_thread"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The two identities in order, joined with a colon: one thread per pair.
    #: Empty for a group thread, when there are any.
    pair_key: Mapped[str | None] = mapped_column(unique=True, nullable=True)
    created_at: Mapped[datetime] = created_column()
    #: When the last letter was sent: the list of threads is sorted by it.
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NetParty(Base):
    """Who is in the thread, and up to when they have read it."""

    __tablename__ = "net_party"
    __table_args__ = (
        UniqueConstraint("thread_id", "identity_id", name="uq_net_party"),
        Index("ix_net_party_identity", "identity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("net_thread.id", ondelete="CASCADE"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: Everything **delivered** up to this moment has been read.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NetMessage(Base):
    """One letter. Sent at once, arrives by the road (D-222)."""

    __tablename__ = "net_message"
    __table_args__ = (Index("ix_net_message_thread_delivered", "thread_id", "delivered_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("net_thread.id", ondelete="CASCADE"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    text: Mapped[str] = mapped_column(nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When the reader sees it. Equal to `sent_at` when the two stand together.
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NetChannel(Base):
    """One author's feed. Official when it is a city's (D-222)."""

    __tablename__ = "net_channel"
    __table_args__ = (Index("ix_net_channel_owner", "owner_identity_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(nullable=False)
    about: Mapped[str] = mapped_column(nullable=False, default="", server_default="")
    #: A player's channel: the owner writes, nobody else.
    owner_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    #: A city's official channel: whoever holds the `channel` power writes.
    #: One per city.
    city_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True, nullable=True)
    created_at: Mapped[datetime] = created_column()
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NetSubscription(Base):
    """A reader of a channel, and up to when they have read it.

    Citizens of a city are **not** listed for their city's channel: that
    subscription is implied by citizenship and cannot be dropped. A row for it
    appears only to hold `read_at`.
    """

    __tablename__ = "net_subscription"
    __table_args__ = (
        UniqueConstraint("channel_id", "identity_id", name="uq_net_subscription"),
        Index("ix_net_subscription_identity", "identity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("net_channel.id", ondelete="CASCADE"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: A row kept only for `read_at` of an implied subscription, not chosen by the reader.
    chosen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NetPost(Base):
    """One post in a channel. Arrives to every reader by their own road (D-222)."""

    __tablename__ = "net_post"
    __table_args__ = (Index("ix_net_post_channel_at", "channel_id", "at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("net_channel.id", ondelete="CASCADE"), nullable=False
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: Where the author stood when posting: the delay to each reader starts here.
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    text: Mapped[str] = mapped_column(nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
