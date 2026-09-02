# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The push vocabulary and floor: what every event kind touches, which kinds
a node or a city may see, the replay and sweep tunables, and the small
pure helpers. Asks nobody above itself.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


log = logging.getLogger(__name__)

#: How far back `hello` with `since` reaches. Older than this the client reads
#: its state whole -- replaying a week of a busy player would cost more than
#: the reads it saves.
REPLAY_HORIZON = timedelta(days=1)

#: Cap on a single replay. Beyond it the client is told to reread.
REPLAY_LIMIT = 500

#: The fallback sweep: without a notification the hub still looks at the
#: journal this often, so a missed `NOTIFY` costs seconds, not a session.
SWEEP_PERIOD = 5.0

#: Messages a client may leave unread before it is cut off.
OUTBOX_LIMIT = 256

#: Journal rows read per pass of the pump: a tick that wrote thousands is
#: delivered in slices, not held in memory whole.
PUMP_BATCH = 500

#: How long a gap in the id run may stay open before the pump rules it a
#: rolled-back id and steps the watermark over it. Longer than any job's
#: transaction may live (`JOB_STATEMENT_TIMEOUT_MS`), with room to spare.
GAP_HORIZON = timedelta(minutes=20)

#: Which parts of the player's state an event kind changes, by prefix of the
#: kind (`knowledge.learned` -> `knowledge`). The client rereads those parts.
#: A kind without a prefix here changes nothing the client caches -- it is
#: still delivered, with empty `touches`.
TOUCHES: dict[str, tuple[str, ...]] = {
    "body": ("body",),
    "meal": ("body",),
    "knowledge": ("knowledge",),
    "item": ("inventory",),
    "gear": ("inventory", "body"),
    "mining": ("mining", "inventory"),
    "travel": ("body", "node"),
    "road": ("node",),
    #: A site laid, fed, started or ripe changes the plot and, for the
    #: bringer, the hands (D-266).
    "estate": ("node", "inventory"),
    "ship": ("node", "ships"),
    "transport": ("node", "inventory"),
    "craft": ("doings", "inventory", "orders"),
    "carrier": ("inventory",),
    "library": ("shelf",),
    "land": ("node", "deeds"),
    "deed": ("node", "deeds"),
    "building": ("node",),
    #: Farm work moves goods too: sowing spends seeds, care spends water,
    #: fertilizing spends compost, the harvest fills the pocket (D-264 review
    #: -- the second tab used to keep a stale inventory through all of them).
    "farm": ("farm", "inventory"),
    "energy": ("node",),
    #: The planet redrew the map around you (D-197): the ways out, what lies
    #: here and what the veins are have all just changed.
    "plates": ("node",),
    "utility": ("node", "money"),
    "station": ("node",),
    #: The factory floor changed under its owner (D-253): a programme, a wire
    #: or a payout -- the node view is where the panel reads it back.
    "automat": ("node",),
    "storage": ("node", "inventory"),
    "explore": ("doings", "node"),
    "forage": ("doings", "inventory"),
    "customs": ("body", "money"),
    "city": ("city",),
    "justice": ("justice",),
    "bank": ("money", "bank"),
    "identity": ("profile",),
    "market": ("orders", "market"),
    "money": ("money",),
    #: The works fund paid or the board changed (D-248): the wallet and the
    #: public bank numbers both move.
    "works": ("money", "bank"),
    #: The alpha's debug widget (D-229). Without a line here the widget would
    #: work only because the client rereads the world after any action of its
    #: own: a second tab of the same player would see nothing. The thing
    #: printed rides in the inventory, the pulled-up term in the doings.
    "alpha": ("inventory", "doings"),
}

#: Kinds with their own list, when the prefix rule is too coarse.
TOUCHES_BY_KIND: dict[str, tuple[str, ...]] = {
    "craft.invented": ("doings", "inventory", "knowledge", "orders"),
    #: The widget's energy goes into the city pool, not the hands (D-229).
    "alpha.energized": ("node",),
    "body.printed": ("body", "node", "inventory"),
    "body.died": ("body", "node", "inventory"),
}

#: What everybody standing in the node sees happen there, by prefix or kind.
#: Personal affairs -- a meal, a purchase, a lesson -- stay personal; what
#: changes the place or who is in it is public to the place.
NODE_VISIBLE_PREFIXES = frozenset(
    {
        "road",
        #: The signal before an eruption and the eruption itself (D-197). Free
        #: and to everybody standing in the node: it is the window to walk out
        #: of, and the whole licence for the burning and the deaths that follow.
        "plates",
        "ship",
        "building",
        #: A construction site on the plot is the plot's business (D-266).
        "estate",
        "land",
        "station",
        "storage",
        "energy",
        "market",
        "farm",
        "transport",
    }
)

NODE_VISIBLE_KINDS = frozenset(
    {
        "body.printed",
        "body.died",
        "travel.started",
        "travel.arrived",
        "travel.cancelled",
        "item.dropped",
        "item.picked",
        "mining.collapsed",
        "explore.found",
        "utility.cut_off",
        "city.founded",
        "deed.offered",
        "deed.sold",
    }
)

#: What a bystander in the node rereads. The node itself, and the book for
#: trade: their own pocket did not change.
NODE_TOUCHES: dict[str, tuple[str, ...]] = {"market": ("market",), "ship": ("node", "ships")}

#: Payload keys that name a second party by identity id. Those get the event
#: as their own: the office appointed, the defendant, the seller. Any key
#: ending in `_identity_id` counts; the journal names parties by id, and the
#: teller turns ids into names (review 2026-08-23, wave 2).
ADDRESSEE_KEYS = ("to_identity_id", "seller")

ADDRESSEE_SUFFIX = "_identity_id"

#: What every citizen of the city hears, wherever they stand: the city's
#: affairs are theirs (D-160). The event names the city by `city_id`.
CITY_VISIBLE_PREFIXES = frozenset({"city", "justice"})

CITY_VISIBLE_KINDS = frozenset({"bank.rate_decided"})

#: Bystanders learn **who** only where the deed is in plain sight: somebody
#: arrived, fell, dropped a thing, was appointed. Trade, tax, fuel and
#: farming stay nameless to the room (D-047: the book trades goods, not
#: reputation). A teller may still add `who` for its kind.
NAMED_PREFIXES = frozenset({"travel", "body", "city", "justice"})

NAMED_KINDS = frozenset({"item.dropped", "item.picked", "mining.collapsed", "explore.found"})


def touches_of(kind: str) -> tuple[str, ...]:
    return TOUCHES_BY_KIND.get(kind) or TOUCHES.get(kind.split(".", 1)[0], ())


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return uuid.UUID(value)
    return None
