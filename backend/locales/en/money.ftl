# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Money: the marketplace, the bank, transfers, work orders, customs, utilities
# (D-047, D-190, D-248, D-251).
#
# NAME($id) turns the stable key of a thing or a station into a word of this
# language: `iron_ore` travels over the wire, the reader sees “Iron ore”.
#
# Sums arrive already assembled: money_str() writes them by D-190, and that
# rule must not be rewritten in FTL -- a finished string is substituted in.
#
# Two incompatible habits of Fluent, which is why the file looks like this:
#   -- a line break in the TEXT of a value survives into the refusal, so text
#      is written on one line, however long it turns out;
#   -- the variants of a select ({ $x -> ... }) each have to stand on their own
#      line, and those breaks do not reach the text.

## Marketplace

market-no-terminal = node { $node } has no marketplace terminal
market-nothing-free = no free “{ NAME($goods) }” in the terminal: all of it is under orders
market-not-enough-free = the terminal has { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } free “{ NAME($goods) }” of tier “{ TIER($tier) }”, and { NUMBER($quantity, minimumFractionDigits: 1, maximumFractionDigits: 1) } is wanted
market-dead-trades = a dead body does not trade
market-body-without-identity = a body without an identity
market-not-enough-money = the account does not hold that much: the order costs { $money } ₭
market-price-not-positive = the price must be positive
market-volume-not-positive = the volume must be positive
market-body-off-node = the body is outside the node
market-order-off-node = the order is outside the node
market-node-city-missing = node { $node } belongs to a city that does not exist
market-no-such-tier = no such quality tier: “{ TIER($tier) }”
market-floor-not-in-tier = quality { $floor } is the “{ TIER($floor_tier) }” tier, but the order names “{ TIER($tier) }”
market-floor-off-scale = quality is a number from { $frm } to { $to }, not { $floor }
market-no-such-goods = the world knows no such goods: “{ NAME($goods) }”
market-goods-relic = “{ NAME($goods) }” is a Forerunner relic: nobody makes or carries those
market-goods-liquid = “{ NAME($goods) }” is a liquid: it exists in a vessel and cannot lie on a counter
market-no-such-recipe = the world knows no such recipe: “{ NAME($recipe) }”

market-reserve-not-a-sale = goods are reserved, not a buy order
market-order-not-active = this order is already { $state ->
        [filled] filled
        [cancelled] cancelled
        [expired] expired
       *[active] active
    }
market-reserve-own = no point reserving your own goods: they are yours already
market-reserve-zero = a reservation of nothing
market-reserve-too-much = the order has { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } free, and the reservation asks for { NUMBER($quantity, minimumFractionDigits: 1, maximumFractionDigits: 1) }
market-reservation-not-yours = someone else's reservation
market-reservation-not-held = the reservation is already { $state ->
        [redeemed] redeemed
        [lapsed] lapsed
       *[held] held
    }
market-reservation-elsewhere = the reservation is not here: goods are collected in person
market-reservation-expired = the reservation has run out: the deposit stays with the seller
market-goods-vanished-reservation = the goods left the terminal between the reservation and the redemption
market-goods-vanished-trade = the goods left the terminal between the check and the trade

market-order-not-yours = someone else's order
market-order-already = the order is already { $state ->
        [filled] filled
        [cancelled] cancelled
        [expired] expired
       *[active] active
    }
market-job-no-reservation = job { $job }: no reservation
market-job-no-order = job { $job }: no order

## Bank

bank-loan-not-positive = the loan must be positive
bank-over-limit = that much is not lent: { $available } ₭ available out of a { $limit } ₭ limit ({ $reason })
bank-loan-closed = this loan is already closed
bank-nothing-to-pay-with = nothing to pay with
bank-council-not-yet = the algorithm decides the rate: fewer than { NUMBER($cities, maximumFractionDigits: 2) } cities have an administration, or a lock is in force
bank-out-of-corridor = the algorithm recommends { NUMBER($recommendation, minimumFractionDigits: 2, maximumFractionDigits: 2) }%, and { NUMBER($corridor, maximumFractionDigits: 2) } pp of deviation is allowed — { NUMBER($rate, minimumFractionDigits: 2, maximumFractionDigits: 2) }% is asked for
bank-complain-about-self = no one complains about themselves, not even in the lore

## The bank explains itself: the rate, the limit, your rate (D-030, D-173, D-193)
#
# The rate and the limit are counted by an open formula, and the formula is
# explained in words: otherwise there is nothing to argue with monetary policy
# about. Every clause is a message of its own, and how they are strung into one
# line is the language's business (i18n.join).
#
# The “+” before a number is put there by the text itself: NUMBER() cannot show
# it (fluent.runtime has no signDisplay), and “+0.50” says which way the lever
# moved. The engine passes a flag, the language writes the sign.

bank-why-rate-base = base { NUMBER($rate, maximumFractionDigits: 2) }
bank-why-rate-inflation = inflation { $inflation_up ->
        [true] +
       *[false] {""}
    }{ NUMBER($inflation, minimumFractionDigits: 1, maximumFractionDigits: 1) } against a goal of { NUMBER($goal, maximumFractionDigits: 2) } → { $bonus_up ->
        [true] +
       *[false] {""}
    }{ NUMBER($bonus, minimumFractionDigits: 2, maximumFractionDigits: 2) }
bank-why-rate-inflation-unknown = inflation is not measured: no reaction to it
bank-why-rate-emission = issue { NUMBER($share, maximumFractionDigits: 0) }% against a goal of { NUMBER($goal, maximumFractionDigits: 2) } → { $bonus_up ->
        [true] +
       *[false] {""}
    }{ NUMBER($bonus, minimumFractionDigits: 2, maximumFractionDigits: 2) }
bank-why-council = a decision of the Council of Cities ({ $city }); the algorithm advised { NUMBER($advised, minimumFractionDigits: 2, maximumFractionDigits: 2) }

bank-why-limit-base = base { $money }
bank-why-limit-turnover = turnover { $money } over { NUMBER($days, maximumFractionDigits: 2) } days
bank-why-limit-repaid = { $money } repaid before
bank-why-limit-no-overdue = a record without arrears
bank-why-limit-trust = trust { NUMBER($trust, maximumFractionDigits: 0) }% from reports

bank-why-offer-key = key rate { NUMBER($key, minimumFractionDigits: 2, maximumFractionDigits: 2) }%
bank-why-offer-no-citizenship = key rate { NUMBER($key, minimumFractionDigits: 2, maximumFractionDigits: 2) }% + { NUMBER($premium, minimumFractionDigits: 2, maximumFractionDigits: 2) }% for risk: without citizenship you borrow from the capital directly (D-175)
bank-why-offer-city = key rate { NUMBER($key, minimumFractionDigits: 2, maximumFractionDigits: 2) }% + city margin { NUMBER($margin, minimumFractionDigits: 2, maximumFractionDigits: 2) }% ({ $city }); { $free } ₭ free on the line
bank-why-offer-line-exhausted = key rate { NUMBER($key, minimumFractionDigits: 2, maximumFractionDigits: 2) }% + { NUMBER($premium, minimumFractionDigits: 2, maximumFractionDigits: 2) }% for risk: the line of city { $city } is exhausted — { $permitted } ₭ allowed against turnover, { $free } ₭ free. The line is raised by trades on its land (D-193)

## Transfers

finance-zero-transfer = a transfer of nothing is not a transfer
finance-memo-too-long = the ground is longer than { $limit } characters
finance-no-such-payee = no such identity: “{ $payee }”
finance-self-transfer = a transfer to yourself changes nothing
finance-not-enough-money = the account does not hold that much

## City work orders

works-city-offer-negative = a city's offer is never negative
works-city-no-labor = an order without labour is not an order
works-city-order-exists = this object already carries such an order
works-city-fund-empty = the works fund is empty: { $money } ₭ short of the fund's share. The fund fills up with interest income — wait
works-city-treasury-poor = the treasury is { $money } ₭ short of the city's share

works-city-repair-not-own = a city orders repairs on its own: this plot is not its
works-city-nothing-to-repair = nothing to repair: the houses are whole, or there are none
works-city-build-not-own = a city orders building on its own: this plot is not its
works-city-unknown-building = the type “{ NAME($building) }” is unknown to this world
works-city-no-footprint = a house without a footprint or without floors is not an order

works-city-station-not-in-city = the station is not on the city's land: the city orders no hauling there
works-city-no-station = there is no station here that needs fuel
works-city-not-a-fuel = “{ NAME($goods) }” does not burn in “{ NAME($station) }”
works-city-zero-haul = hauling nothing is not an order

works-city-no-such-order = the city has no such order
works-city-order-closed = the order is closed already: there is nothing to withdraw
works-city-work-under-way = work on the order is already under way: the worker has put materials in — wait for the end of it

works-city-loan-not-positive = the loan must be positive
works-city-line-exhausted = the city's line is exhausted: { $money } ₭ free out of { NUMBER($cap, maximumFractionDigits: 2) }% of turnover
works-city-not-treasury-loan = this is not a treasury loan of this city

## Customs

customs-banned = “{ NAME($goods) }” does not cross the border of city “{ $city }”: { $direction ->
        [import] import
       *[export] export
    } is banned
customs-cannot-pay = the duty is { $duty } ₭ and the account holds { $have } ₭: the goods do not pass. No debt arises from it

## Utilities

utility-node-not-yours = the node is not yours: someone else's bills are paid by contract, not by the engine
utility-nothing-due = there is no debt
utility-no-grid = there is no city grid here
utility-not-enough-money = the debt is { $debt } ₭ and the account holds { $have } ₭

## The statement: who is on the other side of a posting, and on what ground

# The side of a posting that has no person's name. The kind of account arrives
# over the wire as it is (`genesis`, `bank_reserve`) and becomes a word here --
# otherwise the player reads `works_fund` in the statement, as they did before
# this wave.

ledger-side-city_treasury = { $named ->
        [true] treasury: { $name }
       *[false] city treasury
    }
ledger-side-genesis = issue
ledger-side-bank_reserve = bank reserve
ledger-side-works_fund = works fund
ledger-side-escrow = trade escrow
ledger-side-identity = person

# The ground of a posting: the same list as `PostingReason`. Before this wave
# the list lived on the client as a map of its own and drifted away from the
# server -- a new ground was shown to the player by its code.

ledger-ground-genesis = issue
ledger-ground-trade = trade
ledger-ground-tax_trade = sales tax
ledger-ground-market_fee = market fee
ledger-ground-duty = duty
ledger-ground-salary = salary
ledger-ground-tax_land = land tax
ledger-ground-energy_bill = energy
ledger-ground-court_fee = court fee
ledger-ground-fine = fine
ledger-ground-escrow_hold = deposit
ledger-ground-escrow_release = deposit returned
ledger-ground-loan = loan
ledger-ground-loan_repayment = repayment
ledger-ground-seigniorage = seigniorage
ledger-ground-bank_margin = city margin
ledger-ground-transfer = transfer
ledger-ground-works_recycle = returned to the works fund
ledger-ground-works_print = printed into the works fund
ledger-ground-works_payout = work order payout
