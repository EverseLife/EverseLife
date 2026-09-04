# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Personal finance: transfers and the account statement (D-190).

The account is electronic and lives in the Network (D-044): money is paid from
anywhere, unlike matter. What was missing was the plainest operation of all --
handing money to a person rather than to a market: pay for work, chip in for a
road, return a debt.

## Rules

* **no commission and no tax.** Sales tax is taken by the market, where the
  deal is (D-047). Taxing a transfer would tax a gift and a repaid debt, and
  the engine cannot tell those apart -- nor should it;
* **the ground is written in words** and goes into the journal: a court needs
  "what for", and only the payer can say it;
* **no take-backs.** Sent to the wrong person -- negotiate or sue (D-004): an
  undo button would make payment optional.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import city as town
from src.engine import events, ledger
from src.engine.errors import Refusal
from src.models.event import Event, EventKind
from src.models.identity import Identity
from src.models.ledger import (
    AccountKind,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    PostingReason,
)
from src.models.market import Order, Trade
from src.models.world import Node
from src.runtime import STATEMENT_PAGE, TRANSFER_MEMO_LIMIT
from src.units import amount_float, money_str


class FinanceError(Refusal):
    pass


class NoSuchPayee(FinanceError):
    """No such identity. Money is not sent into the void."""


class NoSuchPosting(FinanceError):
    """No such row on this account. A statement is shown to nobody but its
    owner (D-190), and that holds for a row asked for by its number: somebody
    else's is not there, rather than there and forbidden."""


async def transfer(
    session: AsyncSession,
    payer: Identity,
    payee_name: str,
    amount: int,
    *,
    memo: str = "",
) -> int:
    """Send money to another identity. Remote, instant, without a fee (D-190)."""
    if amount <= 0:
        raise FinanceError(key="finance-zero-transfer")
    ground = memo.strip()
    if len(ground) > TRANSFER_MEMO_LIMIT:
        raise FinanceError(key="finance-memo-too-long", limit=TRANSFER_MEMO_LIMIT)

    payee = (
        await session.execute(select(Identity).where(Identity.name == payee_name.strip()))
    ).scalar_one_or_none()
    if payee is None:
        raise NoSuchPayee(key="finance-no-such-payee", payee=payee_name)
    if payee.id == payer.id:
        raise FinanceError(key="finance-self-transfer")

    source = await ledger.account_for(session, AccountKind.IDENTITY, payer.id)
    target = await ledger.account_for(session, AccountKind.IDENTITY, payee.id)
    if await ledger.balance(session, source.id) < amount:
        raise FinanceError(key="finance-not-enough-money")

    event = await events.record(
        session,
        EventKind.MONEY_TRANSFERRED,
        actor_identity_id=payer.id,
        to=payee.name,
        amount=amount,
        ground=ground,
    )
    await ledger.transfer(
        session,
        PostingReason.TRANSFER,
        debit=source.id,
        credit=target.id,
        amount=amount,
        event_id=event.id,
        #: `ground` is what the payer typed, and the statement shows it. The
        #: field names are ASCII because they are field names (D-251): the
        #: value is a person's own words and stays whatever they wrote.
        memo={"to": payee.name, "from": payer.name, "ground": ground},
    )
    return amount


async def statement(
    session: AsyncSession,
    identity_id: uuid.UUID,
    *,
    before: int | None = None,
    limit: int = STATEMENT_PAGE,
) -> tuple[list[dict], bool]:
    """One page of the account statement, newest first, and whether an older
    page exists: what, when, with whom and on what ground.

    Read remotely and shown to nobody but the owner: how much is in whose
    pocket is not public knowledge, unlike prices (D-047).

    `before` is the id of the last row of the page already read, and the next
    page is what stands under it. An id rather than an offset: the journal
    grows at the top, and a page counted from the top shifts by one under the
    reader with every posting -- a row read twice, a row never seen. Entry
    ids only grow, and `ix_ledger_entry_account` walks them.
    """
    account = await ledger.find_account(session, AccountKind.IDENTITY, identity_id)
    if account is None:
        #: Nothing was ever posted: an empty statement, not a new account.
        return [], False
    query = (
        select(LedgerEntry, LedgerTransaction)
        .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
        .where(LedgerEntry.account_id == account.id)
    )
    if before is not None:
        query = query.where(LedgerEntry.id < before)
    #: One row past the page: whether there is more is a fact of the page
    #: rather than a count of the whole journal, which only grows.
    rows = (await session.execute(query.order_by(LedgerEntry.id.desc()).limit(limit + 1))).all()

    out: list[dict] = []
    for entry, operation in rows[:limit]:
        name, side = await _counterparty(session, operation, entry.account_id)
        out.append(
            {
                #: The row's own number: the page is turned by it and the row
                #: is opened by it (`posting`). Nothing else on the wire tells
                #: two postings of the same moment apart (D-225).
                "id": entry.id,
                "at": operation.at.isoformat(),
                "reason": operation.reason.value,
                #: The sign is the direction: minus means it left the account.
                "amount": entry.amount,
                "money": money_str(abs(entry.amount)),
                "incoming": entry.amount > 0,
                "memo": _memo(operation.memo),
                #: The other side's own name, when it has one: a person, or the
                #: city whose treasury it is. Player data, not a sentence.
                "with": name,
                #: What kind of side it is, when it is not a person: `genesis`,
                #: `bank_reserve`. A word for it belongs in the locale (D-251),
                #: and until this wave two of them had none at all -- the works
                #: fund reached the statement as the literal `works_fund`.
                "side": side,
            }
        )
    return out, len(rows) > limit


async def posting(session: AsyncSession, identity_id: uuid.UUID, entry_id: int) -> dict:
    """One row of the statement, opened: every side of the operation, and
    what stood behind it.

    The row itself is not repeated -- the client has it (D-225). What it
    cannot see from the row is sent: the other legs of the same operation
    (a sale pays the seller **and** the treasury), the deal a `trade` row
    settled, and the order an `escrow_hold` row froze money under -- with its
    fills, because the buyer's own statement never shows the deal: their
    money left for the escrow first, and the settlement is the escrow's.

    The parties of a settled deal are named to each other here, and only
    here (D-292): the book stays anonymous, the statement does not -- a
    court reads the history of money by names, not by escrows.
    """
    account = await ledger.find_account(session, AccountKind.IDENTITY, identity_id)
    entry = None if account is None else await session.get(LedgerEntry, entry_id)
    if entry is None or account is None or entry.account_id != account.id:
        raise NoSuchPosting(key="finance-no-such-posting")
    operation = await session.get(LedgerTransaction, entry.transaction_id)
    if operation is None:  # pragma: no cover -- an entry without its operation is a bug
        raise NoSuchPosting(key="finance-no-such-posting")

    sides = []
    for leg in sorted(operation.entries, key=lambda leg: leg.id):
        name, side = await _who(session, leg.account_id)
        #: The reader's own leg comes with the reader's name like any other,
        #: and the client knows that name from the session (D-225).
        sides.append(
            {
                "with": name,
                "side": side,
                "money": money_str(abs(leg.amount)),
                "incoming": leg.amount > 0,
            }
        )
    return {
        "sides": sides,
        "deal": await _deal(session, operation),
        "order": await _held(session, operation),
    }


async def _deal(session: AsyncSession, operation: LedgerTransaction) -> dict | None:
    """The deal a `trade` row settled, from the event that grounds it.

    The posting itself knows only tax, fee and price: what changed hands,
    how much of it and who bought is the event's (`TRADE_EXECUTED`), and the
    settlement is written with its id for exactly this. A `trade` posted
    without an event -- a plot bought from the city, a deed sold hand to
    hand (`estate`) -- opens with its sides alone.

    What the buyer paid is not repeated: it is the escrow's leg among the
    sides. Nor is the seller named: a `trade` row stands on no account but
    the seller's, so the reader is the seller (D-225).
    """
    if operation.reason is not PostingReason.TRADE or operation.event_id is None:
        return None
    event = (
        (
            await session.execute(
                select(Event).where(
                    Event.id == operation.event_id,
                    #: The journal is partitioned by month of `at`, and an id
                    #: alone would be looked for in every month. The event
                    #: and the settlement are written in one transaction, and
                    #: both stamp `now()` -- Postgres's transaction time, the
                    #: same value for both -- so the moment is exact, not a
                    #: window.
                    Event.at == operation.at,
                )
            )
        )
        .scalars()
        .first()
    )
    if event is None or event.kind != EventKind.TRADE_EXECUTED.value:
        return None
    told = event.payload
    buyer = (
        None
        if event.actor_identity_id is None
        else await session.get(Identity, event.actor_identity_id)
    )
    market = None if event.node_id is None else await session.get(Node, event.node_id)
    return {
        "goods": told.get("type_key"),
        "tier": told.get("tier"),
        "amount": told.get("amount"),
        "price": money_str(int(told.get("price") or 0)),
        "tax": money_str(int(told.get("tax") or 0)),
        "fee": money_str(int(told.get("fee") or 0)),
        "buyer": None if buyer is None else buyer.name,
        "market": None if market is None else market.name,
        #: Redeemed in person rather than matched in the book (D-047).
        "reserved": "reservation_id" in told,
    }


async def _held(session: AsyncSession, operation: LedgerTransaction) -> dict | None:
    """The order an `escrow_hold` or `escrow_release` row belongs to, with
    what it has bought so far.

    Only a buy holds money, so the side is not sent; what is left of the
    order is not sent either, being the order less its fills (D-225). The
    fills are the deals settled against this order: for the buyer this is
    the only place the statement can say what was bought and from whom.
    """
    if operation.reason not in (PostingReason.ESCROW_HOLD, PostingReason.ESCROW_RELEASE):
        return None
    said = (operation.memo or {}).get("order")
    if not said:
        #: A reservation's deposit is written with the goods alone (D-047).
        return None
    try:
        order = await session.get(Order, uuid.UUID(str(said)))
    except ValueError:  # pragma: no cover -- a memo is written by the engine
        return None
    if order is None:
        return None
    market = await session.get(Node, order.node_id)
    #: The seller of each fill in the same query: the opposing order names
    #: the identity, and one round trip per fill would be the N+1 the review
    #: of 2026-08-23 swept out of the views.
    rows = (
        await session.execute(
            select(Trade, Identity.name)
            .join(Order, Trade.sell_order_id == Order.id)
            .join(Identity, Identity.id == Order.identity_id)
            .where(Trade.buy_order_id == order.id)
            .order_by(Trade.at, Trade.id)
        )
    ).all()
    return {
        "goods": order.type_key,
        "tier": order.tier,
        "amount": amount_float(order.amount_total),
        "price": money_str(order.price),
        "market": None if market is None else market.name,
        "fills": [
            {
                "with": seller,
                "amount": amount_float(trade.amount),
                "price": money_str(trade.price),
                "at": trade.at.isoformat(),
            }
            for trade, seller in rows
        ],
    }


#: What the ground of a posting used to be called. A memo written before
#: D-251 keys it in Russian, and it cannot be migrated: the ledger is
#: append-only (`db.ddl` refuses UPDATE on it), which is the whole point of a
#: ledger. So the reader takes either spelling and the client sees one.
GROUND_WAS = "основание"
GROUND = "ground"


def _memo(memo: dict | None) -> dict:
    """The posting's memo with its ground under the name the client reads."""
    said = dict(memo or {})
    if GROUND not in said and GROUND_WAS in said:
        said[GROUND] = said.pop(GROUND_WAS)
    return said


async def _counterparty(
    session: AsyncSession, operation: LedgerTransaction, mine: uuid.UUID
) -> tuple[str | None, str | None]:
    """Who is on the other side: their name, and what kind of side they are.

    Two values rather than one sentence. A person is named and that is all
    there is to say; everything else is an institution, and the reserve is a
    word of a language rather than a fact of the ledger -- so the kind
    travels as the enum it is and `ledger-side-<kind>` says it at the edge.
    """
    for entry in operation.entries:
        if entry.account_id == mine:
            continue
        return await _who(session, entry.account_id)
    return None, None


async def _who(session: AsyncSession, account_id: uuid.UUID) -> tuple[str | None, str | None]:
    """An account as a side of a posting: a name, or a kind with a name in it."""
    account = await session.get(LedgerAccount, account_id)
    if account is None:  # pragma: no cover -- an entry into nowhere is a bug
        return None, None
    if account.kind is AccountKind.IDENTITY and account.owner_id is not None:
        who = await session.get(Identity, account.owner_id)
        if who is not None:
            return who.name, None
    if account.kind is AccountKind.CITY_TREASURY:
        city = await town.by_node(session, account.owner_id)
        return (city.name if city else None), account.kind.value
    return None, account.kind.value
