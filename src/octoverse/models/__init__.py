"""Схема хранения.

Порядок импорта важен: Alembic видит таблицы через `Base.metadata`, а она
наполняется импортом модулей.
"""

from __future__ import annotations

from octoverse.db import ddl
from octoverse.db.base import Base
from octoverse.models.config import ConstantChange, ConstantOverride
from octoverse.models.event import Event, EventKind
from octoverse.models.identity import (
    Account,
    Body,
    BodyState,
    Identity,
    Knowledge,
    KnowledgeKind,
    Wound,
)
from octoverse.models.inventory import Container, ContainerKind, Item
from octoverse.models.job import Job, JobKind, JobState
from octoverse.models.ledger import (
    AccountKind,
    Currency,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    PostingReason,
)
from octoverse.models.world import Node, Planet, Vein

#: Правила, которые держит сама база, вешаются сразу после объявления таблиц.
ddl.attach(Base.metadata)

__all__ = [
    "Account",
    "AccountKind",
    "Base",
    "Body",
    "BodyState",
    "ConstantChange",
    "ConstantOverride",
    "Container",
    "ContainerKind",
    "Currency",
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
    "Node",
    "Planet",
    "PostingReason",
    "Vein",
    "Wound",
]
