# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""Bank: reserve, credit, key rate (D-030, D-087, D-167).

Until now the only source of money was `genesis`, so any issue would have been
pure emission, and "monetary policy" a word.

## The reserve sterilises, it does not hoard

Issuing a loan, the system takes TC from the **reserve** -- already existing
money collected as interest. What is missing it prints through `genesis`.
Repayment and interest return TC **to the reserve**, not into circulation.
Hence the invariant the engine keeps and checks:

    total TC supply = money on accounts + system reserve

Prices depend not on the total supply but on the **circulating** one -- what
is on accounts.

## The key rate is computed by formula, not decided

    rate = bank.base_rate
         + bank.rate_reaction_k     * (inflation - bank.target_inflation)
         + bank.emission_reaction_k * (emission share - bank.emission_share_target)

with floor `bank.rate_floor`, ceiling `bank.rate_cap` and a step of at most
`bank.rate_step_max` per review. The algorithm is public and deterministic: the
same inputs give the same answer, otherwise the bank turns into a hidden NPC
with a will of its own (D-030). **A silent sensor is no reason to move the
lever:** no inflation data -- no reaction to it.

## A loan is a contract

The borrower's rate is fixed at issue and does not change afterwards, whatever
the bank decides later. There is no collateral (D-173): the limit is granted
by **labour** -- sales turnover, repaid loans, a record without overdue payments
and trust -- and it is computed by a public formula, like the rate.

## The bank is two-tier (D-175)

Only the capital prints money. A citizen borrows **from their city** at
"key + city margin" (code-law `bank_margin`, ceiling `bank.city_margin_cap`);
each such loan sits on the city's credit line with the capital --
`bank.debt_to_turnover_cap` of its turnover. Line exhausted or no citizenship
-- a direct loan from the capital at the worse rate: there is always a way out,
but cheap credit is a privilege of citizenship (D-160).

The margin from each interest payment goes to the city treasury, the key part
to the capital's reserve. So the city earns on its borrowers and answers for
them with its line: seigniorage (D-171) is cancelled as unnecessary.

## What is not here

Deposit interest -- that is income without labour, i.e. emission around pillar
P1 (D-087). Processing for reports: a "defective print" report lowers trust
and cuts the limit but does not kill -- only out-of-game support does the
irreversible. And the reserve surplus: what happens to it -- burning or the
works fund -- is decided in `engine/works.py` (D-248), the bank only collects.
"""

from src.engine.bank._base import (  # noqa: F401
    RESERVE,
    BankError,
    NotCouncilTime,
    NothingToRepay,
    OutOfCorridor,
    Restrained,
    TooMuch,
    key_rate,
    reserve,
    reserve_account,
)
from src.engine.bank.council import (  # noqa: F401
    cities_with_hall,
    council_decides,
    council_set_rate,
    locked_until,
)
from src.engine.bank.line import (  # noqa: F401
    city_line,
    city_margin,
    city_outstanding,
    offered_rate,
)
from src.engine.bank.loan import (  # noqa: F401
    accruable,
    accrue,
    borrow,
    collect,
    credit_limit,
    debt_of,
    loans_of,
    overdue,
    overdue_days,
    prison_credit,
    repay,
    restrained,
)
from src.engine.bank.rate import (  # noqa: F401
    PRICE_INDEX,
    _emission_share,
    circulating,
    compute_rate,
    inflation,
    price_index,
    rate_review,
    review_rate,
    schedule_review,
    seigniorage_cancelled,
)
from src.engine.bank.trust import (  # noqa: F401
    personal_turnover,
    repaid_total,
    report_defect,
    trust,
    withdraw_report,
)
