# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The world's summary and clocks.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import runtime
from src.api.commands.common import _body, _identity
from src.api.registry import command
from src.constants import current
from src.constants import registry as R
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
from src.models.world import Node
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
                #: The line is named, not written (D-251): the client draws
                #: `attention-case` in the language of whoever is reading.
                "say": "attention-case",
                "args": {"claim": case.claim},
                "since": case.opened_at.isoformat(),
                "until": (case.opened_at + window).isoformat(),
            }
        )

    #: A vote you may cast and have not. Yours alone: what other cities decide
    #: is not your business, and a feed of it would be noise. The same reading
    #: the Net tab does -- one walk from a person to their city's ballot box.
    native, polls = await vote.mine(db, identity.id)
    for poll in vote.unanswered(polls):
        #: A law is put to the vote by its D-251 id; everything else is named
        #: by its kind, and the kind is an enum -- a word of it belongs in the
        #: locale, not here, or the player reads «голосование: council».
        law = poll.get("law")
        attention.append(
            {
                "kind": "vote",
                "say": "attention-vote-law" if law else "attention-vote-kind",
                "args": {"law": law} if law else {"kind": poll["kind"]},
                "where": None if native is None else native.name,
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
                    "say": "attention-debt",
                    #: A flag rather than two keys: whether the node is cut off
                    #: is one clause of one sentence, and a variant key in
                    #: Fluent is an identifier, never a boolean.
                    "args": {
                        "node": holding["name"],
                        "cut": "true" if holding.get("cut_off") else "false",
                    },
                    #: No `where`: the sentence already names the node, and the
                    #: client draws `where` beside it -- so the line read «долг
                    #: за быт: Двор · Двор». Carried over from the old `what`
                    #: and dropped with it (D-225).
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
                "say": "attention-reservation",
                #: `type_key` is a D-251 id and travels as one: the message
                #: turns it into a word with `NAME()`. It used to be printed
                #: raw, so the line read «забрать бронь: iron_ore».
                "args": {"goods": row.type_key},
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

    #: And what the **place** lived through while nobody of yours was looking.
    #: An eruption has no actor (D-197): it is the planet's doing, it burns what
    #: was left lying and it redraws the ways out, and a player who comes back
    #: to a changed map must be told why it changed rather than work it out.
    body = await _body(db, identity.id)
    if body is not None:
        #: Merged and **sorted again**, newest first: two lists each ordered by
        #: themselves are not one ordered list, and the client draws the digest
        #: in the order it is given. Cut back to the same limit afterwards, so
        #: the two lists together are no longer than one -- by time, so a place
        #: that lived through a very loud day can push out the player's older
        #: doings. That is the right way round: the digest is about what one
        #: comes back to.
        happened = [
            *happened,
            *(
                (
                    await db.execute(
                        select(Event)
                        .where(
                            Event.node_id == body.node_id,
                            Event.at > since,
                            Event.kind.in_(TOLD_OF_THE_PLACE),
                        )
                        .order_by(Event.at.desc())
                        .limit(runtime.SUMMARY_LIMIT)
                    )
                )
                .scalars()
                .all()
            ),
        ]
        happened = sorted(happened, key=lambda row: row.at, reverse=True)[: runtime.SUMMARY_LIMIT]

    #: «пришли» with no place said nothing: the destination sits on the event
    #: row as a column, not in its payload, and the client cannot turn a node
    #: id into a word -- nodes are not in the renames (D-251), their names come
    #: from the server wherever they show. Attached to the wire copy only: the
    #: journal row keeps its shape, the read writes nothing.
    where_to = {
        row.node_id
        for row in happened
        if row.kind == EventKind.TRAVEL_ARRIVED.value and row.node_id is not None
    }
    called: dict[Any, str] = {}
    if where_to:
        called = dict(
            (await db.execute(select(Node.id, Node.name).where(Node.id.in_(where_to)))).all()
        )

    def _said(row: Event) -> dict:
        if row.kind == EventKind.TRAVEL_ARRIVED.value and row.node_id in called:
            return {**(row.payload or {}), "node": called[row.node_id]}
        return row.payload

    return {
        "at": now_.isoformat(),
        "attention": attention,
        "happened": [
            {"at": row.at.isoformat(), "kind": row.kind, "payload": _said(row)} for row in happened
        ],
    }


#: What the place lived through, with nobody to call its actor. Asked about the
#: node the body stands in, and only about ends of things there: the ground
#: moved, and the ground is about to move.
TOLD_OF_THE_PLACE = frozenset({EventKind.PLATES_ERUPTED.value, EventKind.PLATES_WARNED.value})

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
        EventKind.EMISSION_PRINTED.value,
        EventKind.VOTE_CLOSED.value,
        EventKind.CASE_JUDGED.value,
        EventKind.SANCTION_APPLIED.value,
        EventKind.DEBT_WITHHELD.value,
        EventKind.UTILITY_CUT_OFF.value,
        EventKind.TRANSPORT_BROKE.value,
        EventKind.ROAD_LAID.value,
        EventKind.DEED_SOLD.value,
        EventKind.LAND_RECLAIMED.value,
        EventKind.CITY_GRANT_PAID.value,
        EventKind.ESTATE_SITE_READY.value,
        EventKind.SHIP_ADRIFT.value,
        EventKind.SHIP_LOST.value,
    }
)
