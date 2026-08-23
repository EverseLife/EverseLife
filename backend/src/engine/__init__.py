# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The engine: what changes the world.

The layer rule: any state change goes through this package's functions, not
through direct queries from the API. The reason is the journal -- an event and
its consequences must be committed together (01-tech-notes).
"""

from __future__ import annotations

#: Importing a module also registers its job handlers, so the package pulls
#: them all in: a missing handler must be discovered at startup
#: (`require_handlers`), not in a tick in the middle of the night.
from src.engine import (
    bank,
    chat,
    city,
    craft,
    customs,
    death,
    estate,
    events,
    explore,
    farm,
    food,
    jobs,
    justice,
    ledger,
    market,
    mining,
    net,
    panel,
    pow,
    rest,
    road,
    ship,
    station,
    tick,
    transport,
    travel,
    utility,
    vote,
    wear,
    world,
)

__all__ = [
    "bank",
    "chat",
    "city",
    "craft",
    "customs",
    "death",
    "estate",
    "events",
    "explore",
    "farm",
    "food",
    "jobs",
    "justice",
    "ledger",
    "market",
    "mining",
    "net",
    "panel",
    "pow",
    "rest",
    "road",
    "ship",
    "station",
    "tick",
    "transport",
    "travel",
    "utility",
    "vote",
    "wear",
    "world",
]
