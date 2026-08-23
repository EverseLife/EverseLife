# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The world's summary and clocks.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import runtime
from src.api.commands.common import _identity
from src.api.registry import command
from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import (
    utility,
    vote,
)
from src.models.event import Event, EventKind
from src.models.justice import Case, CaseState
from src.models.market import (
    Reservation,
    ReservationState,
)
from src.telemetry import metrics


@command("world.metrics")
async def _world_metrics(state: dict, db: AsyncSession, message: dict) -> dict:
    """World summary: aggregates and invariant checks (60-meta/04).

    Remote read: world figures are not tied to a place. Nothing personal here --
    only aggregates, and that is a privacy decision, not an official secret.
    """
    constants = current()
    return {
        "metrics": await metrics.collect(db, constants),
        "invariants": await metrics.invariants(db, constants),
    }


@command("world.summary")
async def _world_summary(state: dict, db: AsyncSession, message: dict) -> dict:
    """The most important screen of an asynchronous game (04-notifications).

    Somebody comes back after a day away and must understand what happened in
    ten seconds. Until now the only way was to walk eight sidebar tabs and three
    view modes, and the mechanics that depend on being told -- a court case with
    a reaction window, a vote with a quorum, a debt that cuts a node off -- were
    running blind. The vault states the law plainly: any event with irreversible
    consequences must have both a notification **and** a window to react; one
    without the other is pointless. The windows existed; this is the other half.

    Three levels, and the order is the point:

    - **attention** -- where something can still be done, each with the time
      left. Never longer than five lines: if it grows, importance is marked
      wrong, and that is a design fault rather than a display one;
    - **happened** -- what is done and needs no answer. Read from the event
      journal, which records the identity even for what the worker did while
      nobody was watching;
    - **talk** -- a count. There is no chat history to return to (D-043): a
      conversation in a room is not correspondence.

    Remote: this is the Net, and it is read from the road as well.
    """
    constants = current()
    identity = await _identity(state, db)
    now_ = datetime.now(UTC)

    #: How far back "happened" reaches. The client sends when it last looked;
    #: without that we show a day, which is the absence the screen is built for.
    since = now_ - timedelta(days=1)
    if message.get("since"):
        try:
            told = datetime.fromisoformat(str(message["since"]))
            since = told if told.tzinfo else told.replace(tzinfo=UTC)
        except ValueError:
            pass

    attention: list[dict[str, Any]] = []

    #: A case against you is the most urgent thing there is: it ends in a
    #: sanction whether or not you noticed it.
    for case in (
        (
            await db.execute(
                select(Case).where(
                    Case.defendant_identity_id == identity.id,
                    Case.state == CaseState.OPEN,
                )
            )
        )
        .scalars()
        .all()
    ):
        window = timedelta(days=constants[R.JUSTICE_CLAIM_WINDOW])
        attention.append(
            {
                "kind": "case",
                "what": f"против вас иск: {case.claim}",
                "since": case.opened_at.isoformat(),
                "until": (case.opened_at + window).isoformat(),
            }
        )

    #: A vote you may cast and have not. Yours alone: what other cities decide
    #: is not your business, and a feed of it would be noise.
    own_ = await town.citizenship(db, identity.id)
    if own_ is not None:
        native = await town.by_id(db, own_.city_id)
        if native is not None:
            for poll in await vote.view(db, current_catalog(), native, identity.id):
                if poll["may_vote"] and poll["mine"] is None and poll["choice"] is None:
                    subject = poll.get("law") or poll["kind"]
                    attention.append(
                        {
                            "kind": "vote",
                            "what": f"голосование: {subject}",
                            "where": native.name,
                            "until": poll["closes_at"],
                        }
                    )

    #: A debt cuts the node off, and the machines in it stop. Property under
    #: threat, and nobody but the owner can clear it.
    for holding in await utility.holdings(db, constants, identity.id):
        if holding.get("debt", 0) > 0:
            attention.append(
                {
                    "kind": "debt",
                    "what": (
                        f"долг за быт: {holding['name']}"
                        + (" — узел отключён" if holding.get("cut_off") else "")
                    ),
                    "where": holding["name"],
                }
            )

    #: A reservation not redeemed in time leaves the deposit with the seller.
    for row in (
        (
            await db.execute(
                select(Reservation).where(
                    Reservation.buyer_identity_id == identity.id,
                    Reservation.state == ReservationState.HELD,
                )
            )
        )
        .scalars()
        .all()
    ):
        attention.append(
            {
                "kind": "reservation",
                "what": f"забрать бронь: {row.type_key}",
                "since": row.created_at.isoformat(),
                "until": row.expires_at.isoformat(),
            }
        )

    #: Soonest first: what expires today matters more than what expires in a week.
    attention.sort(key=lambda line: line.get("until") or "9999")

    happened = (
        (
            await db.execute(
                select(Event)
                .where(
                    Event.actor_identity_id == identity.id,
                    Event.at > since,
                    Event.kind.in_(TOLD),
                )
                .order_by(Event.at.desc())
                .limit(runtime.SUMMARY_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    return {
        "at": now_.isoformat(),
        "attention": attention,
        "happened": [
            {"at": row.at.isoformat(), "kind": row.kind, "payload": row.payload} for row in happened
        ],
    }


#: What is worth telling about on return. The journal records everything -- the
#: swing of a pick, every ledger posting -- and a feed of that is not a summary
#: but a log. These are the ends of things: what finished, arrived, was found,
#: was decided, was lost.
TOLD = frozenset(
    {
        EventKind.CRAFT_FINISHED.value,
        EventKind.TRAVEL_ARRIVED.value,
        EventKind.PLOT_HARVESTED.value,
        EventKind.EXPLORE_FOUND.value,
        EventKind.EXPLORE_EMPTY.value,
        EventKind.BODY_DIED.value,
        EventKind.BODY_PRINTED.value,
        EventKind.MINING_COLLAPSED.value,
        EventKind.TRADE_EXECUTED.value,
        EventKind.ORDER_EXPIRED.value,
        EventKind.RESERVATION_LAPSED.value,
        EventKind.CITY_LAW_SET.value,
        EventKind.VOTE_CLOSED.value,
        EventKind.CASE_JUDGED.value,
        EventKind.SANCTION_APPLIED.value,
        EventKind.DEBT_WITHHELD.value,
        EventKind.UTILITY_CUT_OFF.value,
        EventKind.TRANSPORT_BROKE.value,
        EventKind.ROAD_LAID.value,
        EventKind.DEED_SOLD.value,
        EventKind.CITY_GRANT_PAID.value,
    }
)
