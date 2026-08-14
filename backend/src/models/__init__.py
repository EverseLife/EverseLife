"""Схема хранения.

Порядок импорта важен: Alembic видит таблицы через `Base.metadata`, а она
наполняется импортом модулей.
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
from src.models.gear import Equipped
from src.models.identity import (
    Account,
    Body,
    BodyState,
    Identity,
    Knowledge,
    KnowledgeKind,
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
from src.models.plant import Nursery, Variety
from src.models.rig import Rig
from src.models.travel import Harness, Travel, TravelState
from src.models.vote import Ballot, Vote, VoteKind, VoteState
from src.models.world import Edge, Node, Planet, Surface, Vein

#: Правила, которые держит сама база, вешаются сразу после объявления таблиц.
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
    "Identity",
    "Item",
    "Job",
    "JobKind",
    "JobState",
    "Knowledge",
    "KnowledgeKind",
    "LedgerAccount",
    "LedgerEntry",
    "LedgerTransaction",
    "Meal",
    "MiningSession",
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
    "Wound",
]
