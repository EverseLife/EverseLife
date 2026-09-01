# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The node's order book (D-003, D-047, D-127).

The order book is local and only local: a single price would collapse
geography and kill the hauler along with arbitrage (pillar P3). The engine does
not value goods -- it matches opposing orders, and the price is what somebody
agreed to pay (D-002).

## What happens where

| Action | Where | Why |
|---|---|---|
| Load goods into the terminal | in person | matter moves only physically |
| List, cancel a sell order | remote | the goods are delivered, from then on an exchange asset |
| Buy | in person | otherwise a player buys everything everywhere without standing up |
| Take bought goods | in person | the same rule of matter |
| View any city's books | remote | everyone knows the prices (D-047) |

Buying means placing a limit buy order, and therefore requires presence: remote
buying would turn the books into a fiction of stuck reserves. The unfilled
remainder rests in the book, money is frozen under it, on fill the goods land
in the terminal -- to be taken on foot.

## Where each formula came from

**A tier, not a number.** Goods trade as positions like "iron ore, good": tiers
come from `quality.tiers` (D-058). A continuous scale would make the book
unreadable and kill liquidity.

**What may stand in the book** (D-241). A thing the world knows how to hand
over, at one of those tiers, under the world's own name for it. Not a name
nobody has heard of, not a relic of the Forerunners -- nobody makes or carries
those (D-232) -- and not a liquid, which exists inside a vessel and nowhere
else (D-230). The rule lives here and not in the client's picker: a buy order
freezes money until it fills or expires, and whoever sends the order need not
be the game's own screen (D-224).

**Priority.** Best price; at equal price, whoever came first. A deal goes at
the price of **the one resting in the book**: they named the terms first, the
newcomer accepted. There are no market orders at all -- only limit ones,
simpler and fairer (30-economy/02, open questions).

**Money.** The buyer freezes `price * volume` on placing the order. Filled
cheaper -- the difference is returned at once: exactly as much is frozen as
may be needed, and not a coin more.

**Tax and commission.** `tax_trade` is paid by the **seller** as a share of
proceeds at fill time (D-127): the buyer sees the price in the book, and that
is the price. Terminal commission is `market.default_fee` until the city sets
its own. Both go to the treasury of the city that owns the node; **no city --
no withholdings**: money cannot vanish into nowhere (I2).

**Term.** An order lives `market.order_lifetime` Terran days and is cancelled
by a journal job, not by a check on read: expiry must happen even if nobody
looks into the book.

**Reservation with a deposit** is the only exception to "buy only standing
here" (D-047). A merchant reserves a lot from afar, pays
`market.reservation_deposit` and must collect within
`market.reservation_period` days; if not, the deposit stays with the seller and
the goods return to the book. Dead reserves do not arise because a reservation
has a price and a term.

**No more than the limit is taken in hand** (D-146): what stops you taking
bought goods is not the terminal's greed but mass. Everything beyond -- only by
vehicle.

## What is not here yet

* **Price ceiling, sales norm, duties** (D-122, D-123) -- city code-laws,
  arrive with cities on E3;
* **Orphaned terminal** (D-100) -- requires building maintenance, i.e.
  buildings and a treasury.

## Where each part of it lives

The file grew past what one file should hold, and it was five subjects all
along -- so it is five now, and this one is the door:

* `_base` -- the words the whole counter speaks: the refusals, the goods keys
  (D-209), the tiers and floors (D-058, D-239), the money arithmetic;
* `counter` -- the terminal and the cells behind it: where the goods lie,
  `load` and `take`, the stack moving everything else pays with;
* `match` -- crossing orders: placing, matching, settlement through escrow;
* `deal` -- the entrances: `sell`, `buy`, the reservation, `cancel` and the
  journal's own expiries;
* `window` -- reading the book: the glued price rows and the last deals.

The door publishes what the world outside the package actually asks for.
What one section says to another and nobody else -- `_place`, `_match`, the
escrow arithmetic, the stack mover -- stays behind it: an order that skipped
`_tradable` or a hold would be a purse locked for nothing, and "anybody may
move stacks between cells" is the reason `_move` was private all along.
"""

from src.engine.market._base import (  # noqa: F401
    CARRIER_SEP,
    TERMINAL,
    BadOrder,
    Book,
    Fill,
    Level,
    MarketError,
    NoGoods,
    NoMoney,
    NoRoom,
    NoTerminal,
    NotHere,
    NotYours,
    TankFull,
    Untradable,
    goods_key,
    split_key,
    tier_of,
    tier_span,
)
from src.engine.market.counter import (  # noqa: F401
    load,
    stall,
    take,
    terminal,
)
from src.engine.market.deal import (  # noqa: F401
    buy,
    cancel,
    expire,
    lapse,
    redeem,
    reserve,
    sell,
)
from src.engine.market.window import (  # noqa: F401
    book,
    last_prices,
    positions,
)
