# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""The server speaks first: events from the journal go to the players they
concern (D-226, 08-session-protocol).

The journal is the source. Every change of the world is a row in `event`,
written in the same transaction as its consequences -- by the API process and
by the worker alike. A trigger on the table sends `NOTIFY event, <id>` with
the commit; this module listens in the API process, reads the row, and tells
whoever may see it.

**Notify is the alarm, the table is the truth.** A lost notification is not a
lost event: the hub keeps the last id it delivered and, when woken, reads
everything after it in order. That is also how a reconnecting client catches
up: `hello` with `since` replays the rows it missed through the same tellers.

What is not an event of the journal but must still reach the screen -- a line
of room talk, which the journal does not keep (D-070) -- goes through
`events.announce()`: a notification on the `touch` channel naming whom it
concerns and what parts of their state it changes, and nothing more.

A message to the client has the shape

    {"event": "knowledge.learned", "seq": 184213, "at": "...",
     "touches": ["knowledge"], ...what the teller adds}

`touches` is the promise every event keeps even without a teller: the client
knows what to read again. Tellers add what the recipient could have seen by
asking -- never the journal's innards.
"""

#: Imported for its side effect: every teller registers itself with the
#: Hub's registry as the module loads.
from src.api.push import tellers  # noqa: F401
from src.api.push._base import (  # noqa: F401
    ADDRESSEE_KEYS,
    ADDRESSEE_SUFFIX,
    CITY_VISIBLE_KINDS,
    CITY_VISIBLE_PREFIXES,
    GAP_HORIZON,
    NAMED_KINDS,
    NAMED_PREFIXES,
    NODE_TOUCHES,
    NODE_VISIBLE_KINDS,
    NODE_VISIBLE_PREFIXES,
    OUTBOX_LIMIT,
    PUMP_BATCH,
    REPLAY_HORIZON,
    REPLAY_LIMIT,
    SWEEP_PERIOD,
    TOUCHES,
    TOUCHES_BY_KIND,
    log,
    touches_of,
)
from src.api.push.pump import (  # noqa: F401
    Hub,
    Sink,
    Tally,
    Teller,
    _city_of,
    _follow,
    _parties,
    hub,
    teller,
)
