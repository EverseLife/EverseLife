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
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.ledger import (
    AccountKind,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    PostingReason,
)
from src.runtime import STATEMENT_DEPTH, TRANSFER_MEMO_LIMIT
from src.units import money_str


class FinanceError(Refusal):
    pass


class NoSuchPayee(FinanceError):
    """No such identity. Money is not sent into the void."""


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
    session: AsyncSession, identity_id: uuid.UUID, *, limit: int = STATEMENT_DEPTH
) -> list[dict]:
    """The account statement: what, when, with whom and on what ground.

    Read remotely and shown to nobody but the owner: how much is in whose
    pocket is not public knowledge, unlike prices (D-047).
    """
    account = await ledger.find_account(session, AccountKind.IDENTITY, identity_id)
    if account is None:
        #: Nothing was ever posted: an empty statement, not a new account.
        return []
    rows = (
        await session.execute(
            select(LedgerEntry, LedgerTransaction)
            .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
            .where(LedgerEntry.account_id == account.id)
            .order_by(LedgerEntry.id.desc())
            .limit(limit)
        )
    ).all()

    out: list[dict] = []
    for entry, operation in rows:
        name, side = await _counterparty(session, operation, entry.account_id)
        out.append(
            {
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
    return out


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
    there is to say; everything else is an institution, and «резерв банка» is
    a word of a language rather than a fact of the ledger -- so the kind
    travels as the enum it is and `ledger-side-<kind>` says it at the edge.
    """
    for entry in operation.entries:
        if entry.account_id == mine:
            continue
        account = await session.get(LedgerAccount, entry.account_id)
        if account is None:  # pragma: no cover -- an entry into nowhere is a bug
            continue
        if account.kind is AccountKind.IDENTITY and account.owner_id is not None:
            who = await session.get(Identity, account.owner_id)
            if who is not None:
                return who.name, None
        if account.kind is AccountKind.CITY_TREASURY:
            city = await town.by_node(session, account.owner_id)
            return (city.name if city else None), account.kind.value
        return None, account.kind.value
    return None, None
