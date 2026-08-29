# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The storage schema.

Import order matters: Alembic sees tables through `Base.metadata`, and it is
filled by importing the modules.
"""

from __future__ import annotations

from src.db import ddl
from src.db.base import Base
from src.models.bank import DefectReport, Loan, LoanState, RateDecision
from src.models.chat import ChatGroup, ChatMember, ChatMessage, Utterance
from src.models.city import City, CityGrant, Office, Power, UtilityMeter
from src.models.config import ConstantChange, ConstantOverride
from src.models.craft import BatchKind, BatchState, CraftBatch
from src.models.energy import EnergyPool
from src.models.estate import Building, Deed
from src.models.event import Event, EventKind
from src.models.farm import Plot, PlotState
from src.models.food import Meal
from src.models.forage import Forage
from src.models.gear import Equipped
from src.models.identity import (
    Account,
    Body,
    BodyState,
    Identity,
    Knowledge,
    KnowledgeKind,
    Line,
    LoginToken,
    Wound,
)
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.justice import Case, CaseState, Sanction
from src.models.ledger import (
    AccountKind,
    Currency,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    PostingReason,
)
from src.models.library import LibraryEntry
from src.models.luck import Luck
from src.models.market import (
    Order,
    OrderSide,
    OrderState,
    Reservation,
    ReservationState,
    Trade,
)
from src.models.metrics import DailyMetric
from src.models.mining import MiningSession, Pace, PowChallenge, SessionState
from src.models.net import (
    NetChannel,
    NetMessage,
    NetParty,
    NetPost,
    NetSubscription,
    NetThread,
)
from src.models.plant import Nursery, Variety
from src.models.rig import Rig
from src.models.ship import Ship
from src.models.travel import Harness, Travel, TravelState
from src.models.vote import Ballot, Vote, VoteKind, VoteState
from src.models.works import WorkOrder, WorkOrderKind, WorkOrderState
from src.models.world import Edge, Node, Planet, Surface, Vein

#: The rules the database itself holds are attached right after the tables are declared.
ddl.attach(Base.metadata)

__all__ = [
    "Account",
    "AccountKind",
    "Base",
    "BatchKind",
    "BatchState",
    "Body",
    "BodyState",
    "Building",
    "ChatGroup",
    "ChatMember",
    "ChatMessage",
    "City",
    "CityGrant",
    "ConstantChange",
    "ConstantOverride",
    "Container",
    "ContainerKind",
    "CraftBatch",
    "Currency",
    "DailyMetric",
    "Deed",
    "Edge",
    "Equipped",
    "EnergyPool",
    "Event",
    "EventKind",
    "Forage",
    "Luck",
    "Identity",
    "Item",
    "Job",
    "JobKind",
    "JobState",
    "Knowledge",
    "KnowledgeKind",
    "LedgerAccount",
    "LibraryEntry",
    "LedgerEntry",
    "LedgerTransaction",
    "Line",
    "LoginToken",
    "Meal",
    "MiningSession",
    "NetChannel",
    "NetMessage",
    "NetParty",
    "NetPost",
    "NetSubscription",
    "NetThread",
    "Node",
    "Nursery",
    "Office",
    "Order",
    "OrderSide",
    "OrderState",
    "Pace",
    "Planet",
    "Plot",
    "PlotState",
    "PostingReason",
    "PowChallenge",
    "Power",
    "Reservation",
    "ReservationState",
    "Rig",
    "SessionState",
    "Ship",
    "Surface",
    "Trade",
    "Harness",
    "Ballot",
    "Loan",
    "LoanState",
    "RateDecision",
    "DefectReport",
    "Case",
    "CaseState",
    "Sanction",
    "Travel",
    "TravelState",
    "Vote",
    "VoteKind",
    "VoteState",
    "UtilityMeter",
    "Utterance",
    "Variety",
    "Vein",
    "WorkOrder",
    "WorkOrderKind",
    "WorkOrderState",
    "Wound",
]
