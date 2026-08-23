# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city as an institution: charter, code-laws, offices, meter (D-036, D-130, D-154).

Before this a "city" existed only as a delegate node on the map: it had a
treasury but nobody entitled to dispose of it. Here the missing part appears,
and exactly three things:

* **city** -- the charter (answers to `laws.json` questions) and code-laws (values);
* **office** -- "an identity may do this in this city";
* **meter** -- how much a node owes for household and up to what moment it is billed.

There is and will be no branching on office titles in the engine: the engine
knows powers, and what the post holding them is called is the city's business
(D-154). Otherwise every new form of government would need a release, and the
whole idea "players write the rules" would die with it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, created_column, uuid_pk


class Power(StrEnum):
    """A broad power of authority (D-154, D-155).

    A full right is a **string**, and it is not exhausted by the broad values:
    `law:<id>` opens exactly one code-law, and `laws` covers them all. The
    engine keeps no list of specific laws: it is exactly the one in the vault's
    `data/laws.yaml`. A "minister of economy" is a set of rights the city gave
    a name to, and there is no branching on office titles in code.
    """

    #: Edit **all** code-laws. Covers any `law:<id>`.
    LAWS = "laws"
    #: Answer charter questions.
    CHARTER = "charter"
    #: Dispose of the treasury: pay from it.
    TREASURY = "treasury"
    #: Appoint and dismiss offices.
    OFFICES = "offices"
    #: Allot civic plots to residents (D-089).
    LAND = "land"
    #: Full snapshot of the economic panel: the public one is visible to all anyway (D-140).
    DASHBOARD = "dashboard"
    #: Court and sanctions. The power is declared, the mechanics arrive with their own system.
    JUSTICE = "justice"
    #: Admit citizens and refuse (D-160). Exile does not go from here but by
    #: `justice`: exile is a sanction, not a personnel decision.
    CITIZENS = "citizens"
    #: Write in the city's official channel in the Net (D-222): tell the
    #: citizens a law changed without gathering them in one room.
    CHANNEL = "channel"


#: Prefix of the right to one law: `law:import_duty`. Separated by a colon
#: because law identifiers come from the vault and contain no dots.
LAW_SCOPE = "law:"


class City(Base):
    """The city. Lives on the delegate node: its territory is that node's children."""

    __tablename__ = "city"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: The city's delegate node on the planet layer (D-045). One city -- one node.
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(nullable=False)
    #: The city's word to newcomers: what it announces about itself on the door
    #: card (D-183). A promise, not a contract -- the engine neither enforces nor parses it.
    about: Mapped[str] = mapped_column(nullable=False, default="", server_default="")
    #: The founder. By default also the ruler -- that is what the charter says (D-130).
    founder_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )

    #: Charter answers: `{question: option}`. Filled with `laws.json` defaults
    #: at founding -- the city arises working, not empty (D-130).
    charter: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    #: Numeric parameters of charter options: `{question: value}`.
    charter_params: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    #: Code-laws: `{law: value as string}`. As a string because a law can be a
    #: number or a word, and there is no branching on law type in code (D-094).
    laws: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    created_at: Mapped[datetime] = created_column()


class Office(Base):
    """Office: an identity and what it is allowed in this city.

    A vacated office is not deleted but marked with a date: who controlled what
    last month is a matter for the court, and the answer must be preserved.
    """

    __tablename__ = "city_office"
    __table_args__ = (
        Index("ix_city_office_city", "city_id"),
        Index("ix_city_office_identity", "identity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)

    #: What the office is called in this city. The engine does not care: it
    #: looks at powers, and "president" or "elder" is the city's business.
    title: Mapped[str] = mapped_column(nullable=False)
    #: Powers as a list of `Power` values.
    powers: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=list)

    appointed_by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_column()
    #: Empty -- the office is in force.
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class CityGrant(Base):
    """Settlement grant paid by a city to an identity (D-153).

    A separate row, not a flag on the identity: the **city** pays the grant,
    and the same person, having moved, may receive it in another city -- but
    not twice in one. The record is that rule.
    """

    __tablename__ = "city_grant"
    __table_args__ = (UniqueConstraint("city_id", "identity_id", name="uq_city_grant_identity"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: How much was paid, in minor units.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = created_column()


class UtilityMeter(Base):
    """The node's meter: household runs by time, the bill comes once a period (D-135, D-149).

    A meter is opened on an **occupied** node -- own or civic. An unowned node
    has no meter: there is nobody to bill, and money has nowhere to vanish (I2).
    """

    __tablename__ = "utility_meter"

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False, unique=True)

    #: Up to what moment household is already billed. Moved by a journal job.
    counted_at: Mapped[datetime] = created_column()
    #: Unpaid, in minor units. Debt neither expires nor grows by interest:
    #: interest is the bank's business (E4), not the utility service's.
    debt: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Disconnected for non-payment: the node's machines do not work until the
    #: debt is settled. The engine may not take the node for debt -- that is a court decision
    #: (D-149).
    cut_off: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: How much energy went to household over the last period -- for showing the holder.
    last_energy: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)

    created_at: Mapped[datetime] = created_column()


class Citizen(Base):
    """Citizenship: the identity belongs to a city (D-160).

    **One per person.** Dual citizenship is forbidden by the world's charter,
    not by agreement -- so the constraint is in the database: a second record
    for the same identity is physically impossible.

    Leaving is free but not instant: the declaration sets `leaving_at`, and the
    record holds until then. The delay exists exactly so that one cannot leave
    the city right before a verdict.
    """

    __tablename__ = "citizen"
    __table_args__ = (
        UniqueConstraint("identity_id", name="uq_citizen_identity"),
        Index("ix_citizen_city", "city_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    #: Since when: the residency census (`vote_qualification`) is counted from it.
    since: Mapped[datetime] = created_column()
    #: When citizenship lapses by the exit declaration. Empty -- not leaving.
    leaving_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: Until when citizenship cannot be given up: a print condition accepted by
    #: choosing the door (D-184). Written at print time and not changed later --
    #: a city that raises the term retroactively does not lengthen somebody's
    #: obligation. Empty -- no obligation, one may leave the same day.
    bound_until: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created_column()


class CitizenshipRequest(Base):
    """A citizenship application or an invitation (D-160).

    One and the same record: the difference is only who started it.
    `application` -- the person asked and waits for the authority; `invite` --
    the authority called and waits for the person. No second table is needed
    for this, and two names in code would diverge.
    """

    __tablename__ = "citizenship_request"
    __table_args__ = (
        UniqueConstraint("identity_id", "city_id", name="uq_request_identity_city"),
        Index("ix_request_city", "city_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    #: `application` -- from the person, `invite` -- from the authority.
    kind: Mapped[str] = mapped_column(nullable=False)
    #: Who invited, if this is an invitation.
    by_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity.id"), nullable=True
    )
    created_at: Mapped[datetime] = created_column()


class CouncilSeat(Base):
    """A seat on the city council (D-164).

    The council is a set of seats, not a rank: a rank would have to be checked
    by office title, and the engine does not know titles and must not (D-154).
    A seat either exists or not.

    A vacated seat is not deleted but marked with a date: who voted on the
    council last month is a matter for the court, and the answer must be preserved.
    """

    __tablename__ = "council_seat"
    __table_args__ = (Index("ix_council_city", "city_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    identity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity.id"), nullable=False)
    #: How the seat was obtained: by election or appointment.
    how: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = created_column()
    #: Empty -- the seat is occupied.
    vacated_at: Mapped[datetime | None] = mapped_column(nullable=True)
