# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The job journal -- the basis of the world tick.

The world lives without players: batches, caravans, harvest growth, daily
maintenance, meters, spoilage. All of these are deferred events, and each must
run **exactly once**, even if the process was restarted mid-tick
(01-tech-notes, pattern 1).

Hence the construction: not "a cron poking a function" but a job table with
state, a `FOR UPDATE SKIP LOCKED` selection and job completion **in one
transaction with its effects**. The `dedup_key` is unique -- queueing the same
job again does not create a second one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, enum_column, uuid_pk


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobKind(StrEnum):
    """Kinds of deferred work. The handler is registered in `engine.tick`."""

    WORLD_TICK = "world.tick"
    DAILY_TICK = "world.daily"
    #: One step of a tick as a job of its own (wave 4): the tick schedules
    #: them and returns; a long step no longer holds arrivals and finishes
    #: behind it, and a failed step retries alone.
    TICK_STEP = "world.step"
    CRAFT_BATCH = "craft.batch"
    TRAVEL_LEG = "travel.leg"
    MARKET_ORDER_EXPIRY = "market.order_expiry"
    MARKET_RESERVATION_EXPIRY = "market.reservation_expiry"
    FARM_PLOW = "farm.plow"
    #: Household meter: once every `energy.meter_period` hours (D-135, D-149).
    UTILITY_METER = "utility.meter"
    #: Exploration run: the find arrives on schedule, like every work (D-152).
    EXPLORE_SURVEY = "explore.survey"
    #: Body print: minutes in a city, `death.print_time_capital` at the Forerunners (D-028).
    BODY_PRINT = "body.print"
    #: Building construction: materials written off at once, the building rises on schedule (D-131).
    BUILD_FINISH = "build.finish"
    #: Taking one's own house apart: the building goes when the work is done, and
    #: part of the materials comes back with it (D-205).
    BUILD_DEMOLISH = "build.demolish"
    #: Mending a house: the materials go into the wall at once, the condition
    #: comes back on schedule -- construction's own order, in miniature (D-218).
    BUILD_REPAIR = "build.repair"
    #: Laying surface on an edge: the surface is written off at once, the road
    #: is laid on schedule (D-158).
    ROAD_WORK = "road.work"
    #: Laying a ship node: the foundation is written off at once, the node
    #: appears on schedule (D-202).
    SHIP_KEEL = "ship.keel"
    #: A ship's passage: undocked here, docks there when the term is up (D-201).
    SHIP_FLIGHT = "ship.flight"
    #: Leaving citizenship: the declaration is filed, the term is up (D-160).
    CITIZENSHIP_EXIT = "city.citizenship_exit"
    #: Vote tally: the term is up, the result applies itself (D-161).
    VOTE_CLOSE = "city.vote_close"
    #: Term of office is up: the office is vacated by itself (D-163).
    RULER_TERM = "city.ruler_term"
    #: A timed sanction ended: lifted by itself (D-166).
    SANCTION_LIFT = "justice.sanction_lift"
    #: Key-rate review: once every `bank.rate_review_period` days (D-167).
    RATE_REVIEW = "bank.rate_review"
    #: Return of interest income to cities: once every `bank.seigniorage_period`.
    SEIGNIORAGE = "bank.seigniorage"
    #: The world chronicle out to Discord: once every `HERALD_PERIOD` (`src/herald/`).
    #: The world does not depend on it -- the job only reads the event journal.
    HERALD_POST = "herald.post"
    #: A letter or a post reaching its reader by the road (D-222): nothing is
    #: written, the reader is told it has arrived (D-226).
    NET_DELIVER = "net.deliver"


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (
        #: The worker's working selection: only pending ones, by firing time.
        Index(
            "ix_job_due",
            "run_at",
            postgresql_where=text("state = 'pending'"),
        ),
        Index("ix_job_state_kind", "state", "kind"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(nullable=False)
    state: Mapped[JobState] = enum_column(
        JobState, "job_state", nullable=False, default=JobState.PENDING, index=False
    )

    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    #: Idempotency. The job "write off maintenance for day 12 from house X" is
    #: queued any number of times and exists in one copy.
    dedup_key: Mapped[str | None] = mapped_column(unique=True, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(nullable=True)

    #: Who took the job and when -- for examining stuck workers.
    locked_by: Mapped[str | None] = mapped_column(nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = created_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: The event that spawned the job -- a chain of causes for investigation.
    cause_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    body_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job {self.kind} {self.state} run_at={self.run_at}>"
