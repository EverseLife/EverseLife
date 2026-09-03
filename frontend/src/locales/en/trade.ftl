# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The words of the windows where things are traded for money and things for
# things: the marketplace, the work station, the inventory, the bank, finance,
# the coin station, the bioprinter, the hearth, the library and the password
# field (D-251, wave IV).
#
# The rules are the same as in `ui.ftl`: a value is one line, however long it
# runs (a wrap would land in the text); the variants of a select go one to a
# line, and those wraps do not land in the text.

## The marketplace

ui-market-title = Marketplace
ui-market-search = find goods
ui-market-search-label = goods search
ui-market-found = { $found } of { $all }
ui-market-hint = traded here and held by you; search — anything can be found
ui-market-none-found = nothing found
ui-market-none-traded = nothing has been traded here yet
ui-market-quality = quality
ui-market-stock = in the terminal { $shelf } · in hand { $hand }
# Goods under one's own order lie where they were, and can be neither sold
# again nor taken back: the line says so before the refusal does.
ui-market-stock-pledged = in the terminal { $shelf }, { $free } free · in hand { $hand }
ui-market-bids = buying
ui-market-price = price ₭
ui-market-asks = selling
ui-market-rung = price { $price } per unit
ui-market-book-empty = the order book is empty on this position: the first one names the price
ui-market-last = last trade: { $price } ₭
ui-market-volume = how much
ui-market-volume-unit = how much, { $unit }
ui-market-price-each = price per unit, ₭
ui-market-total = total
ui-market-tax = the seller pays the tax
ui-market-buy = Buy
ui-market-buy-hint = stand in the order book at your own price; whatever is cheaper is bought at once
ui-market-sell = Sell
ui-market-sell-hint = stand in the order book at your own price; whatever is dearer is sold at once
ui-market-sell-none = nothing to sell: the goods have to lie in the terminal
ui-market-sell-pledged = all that lies here is already under your own orders: cancel one or bring more
ui-market-buy-best = Buy at market
ui-market-buy-best-at = Buy at market · { $price } ₭
ui-market-buy-best-hint = buy at the sellers' best price
ui-market-sell-best = Sell at market
ui-market-sell-best-at = Sell at market · { $price } ₭
ui-market-sell-best-hint = sell at the buyers' best price; the goods have to lie in the terminal
ui-market-rest = What is left of the bid stands as an order and waits. Buying is done standing here; your own orders are beside you in the terminal, and all of them at once in the “finance” tab.
ui-market-reserve-title = Reserve
ui-market-reserve-rule = A reservation is made from afar and redeemed on foot; miss the term and the deposit stays with the seller.
ui-market-reserve = Reserve
ui-market-reserve-hint = put down a deposit and take it before the term
ui-market-reservation = reservation: { $goods } · { $amount } at { $price } ₭
ui-market-redeem = Redeem
ui-market-terminal = Terminal
ui-market-terminal-rule = What lies in the terminal is what sells; what you buy is taken from here too. A click on a row picks the position. Drag a row here from the inventory to put it out, and back into the inventory to take it.
ui-market-terminal-drop = drag a thing here from the inventory to put it out
ui-market-terminal-empty = nothing of yours in the terminal
ui-market-orders = Orders in this node
ui-market-orders-rule = The orders standing in this node: the goods under them lie in the terminal. The whole list is in the “finance” tab.
ui-market-orders-none = no orders of your own in this node
ui-market-row = position { $goods }, { $tier }
ui-market-take = Take
# A liquid trades out of the terminal's tank (D-255). It cannot be dragged
# onto the counter: it does not lie in the hands -- it is inside a canister,
# and the canister is what would travel.
ui-market-pour = Pour into the tank
ui-market-pour-hint = pour out of your own vessel into the terminal's tank; that is what it sells from
ui-market-pour-none = nothing to pour: a liquid goes out of one's own vessel
ui-market-poured-in-part = { $poured } poured, { $left } stayed in the vessel
ui-market-poured-part = { $poured } poured by the room in your vessels, { $left } stayed in the tank
ui-market-row-pledged = { $held } under an order

## The work station

ui-workshop-by-hand = By hand
ui-workshop-rule = A batch runs only while you stand here: leave and it halts, come back and it goes on. One person has one work running, the rest wait their turn in “doings”. One person works at a work station.
ui-workshop-cut-off = The node is cut off for non-payment: work stations do not run until the debt is closed. The bill is in the sidebar, in the “estate” tab.
ui-workshop-station-quality = quality { $quality }
ui-workshop-station-condition = condition { $condition }
ui-workshop-station-busy-mine = busy with you
ui-workshop-station-busy-other = busy with another
ui-workshop-station-free = free
ui-workshop-station-take = Take
ui-workshop-station-take-hint = take the station into your hands
ui-workshop-input = { $goods } · in hand { $amount }
ui-workshop-write = write down a recipe:
ui-workshop-write-nothing = you know nothing yet but the carrier itself
ui-workshop-quality = quality
ui-workshop-ceiling-hint = the machine's ceiling: { $ceiling }
ui-workshop-seconds = s
ui-workshop-minutes = min
ui-workshop-waste = waste
ui-workshop-ceiling = ceiling
ui-workshop-consumes = will be spent:
ui-workshop-energy = electricity { $energy } · { $price } ₭
ui-workshop-energy-cells = electricity { $energy } · from the batteries beside it
ui-workshop-forecast = The forecast counts itself while you choose.
ui-workshop-queue = Queue
ui-workshop-start = Start the batch
ui-workshop-running = “{ $goods }” is running now: a new batch queues behind it
ui-workshop-repair-title = Repair or take apart
ui-workshop-thing-condition = { $goods } · condition { $condition }
ui-workshop-repair = Repair
ui-workshop-recycle = Take apart
ui-workshop-invent-title = Without a recipe
ui-workshop-invent-rule = Lay out the composition of one unit — up to { $cap } kinds of things from your hands — and how many units you make. Match what is made here and the recipe is yours and the batch has started. Miss and a random part of what you laid out burns: the price of the attempt. There are no “warmer — colder” hints.
ui-workshop-invent-empty = Your hands are empty: there is nothing to lay out.
ui-workshop-invent-per-unit = how much per unit
ui-workshop-invent-drop = remove
ui-workshop-invent-add = + thing
ui-workshop-invent-units = units
ui-workshop-invent-try = Try
ui-workshop-invent-done = It came together: “{ $learned }” is in your knowledge now.
ui-workshop-invent-done-batch = It came together: “{ $learned }” is in your knowledge now — and the first batch has started.
ui-workshop-invent-burned = Burned: { $burned }.

## The inventory

ui-inventory-carry = in hand { $load } of { $capacity } kg
ui-inventory-carry-rule = Looking is done from anywhere, eating out of your hands and on the road too, but the rest is touched on foot only. Handing over is hand to hand: both people stand in one place, and the others see it — a line about it appears in the talk. Full hands take no parcel: the carrying limit is another's too.
ui-inventory-slot-empty = empty
ui-inventory-unequip = take off
ui-inventory-group = group
ui-inventory-sort = sort
ui-inventory-desc = descending — click for ascending
ui-inventory-asc = ascending — click for descending
ui-inventory-drop-hint = drag a thing here to take it into your hands
ui-inventory-empty = There is nothing in your hands.
ui-inventory-menu = what can be done with “{ $goods }”
ui-inventory-amount = how much
ui-inventory-equip = Put on
ui-inventory-eat = Eat
ui-inventory-warm = Warm up
ui-inventory-warm-hint = break the warmer: hours of warmth at once, nothing keeps past the ceiling
ui-inventory-copy = Copy into knowledge
ui-inventory-copy-hint = copy the recipe into your knowledge: costs stamina, the carrier stays whole
ui-inventory-copy-known = this recipe is in the identity already
ui-inventory-wipe = Wipe
ui-inventory-wipe-hint = wipe the writing: a blank is left
ui-inventory-install = Install
ui-inventory-install-hint = put it up in the building: takes a place and becomes a working machine; put on the floor it is cargo
ui-inventory-put = Put…
ui-inventory-hand = Hand over…
ui-inventory-where = Where to put · { $amount }
ui-inventory-floor = On the floor
ui-inventory-ground = On the ground
ui-inventory-passing = You are passing through: the door is shut, and nothing is put on the floor.
ui-inventory-in-hands = { $goods } in hand
ui-inventory-pour = Pour into { $target }
ui-inventory-pour-hint = pour over all that is inside, as much as fits
ui-inventory-into = Into { $chest }
ui-inventory-contribute = To the library
ui-inventory-contribute-hint = give to the library for good: your name stays with the recipe
ui-inventory-contribute-there = this recipe lies here already
ui-inventory-hold = Into the hold
ui-inventory-terminal = Into the terminal
ui-inventory-terminal-hint = put out into the terminal: what lies in it is what sells
ui-inventory-cancel = Cancel
ui-inventory-whom = Whom to hand it to · { $amount }
ui-inventory-nobody = There is nobody else here: handing over is hand to hand.
ui-inventory-on-terminal = In the terminal
ui-inventory-average = { $quality } on average
ui-inventory-mass = { $mass } kg
# Two arguments for one number: `$count` picks the form of the word (only a
# number can do that), `$shown` is the very digits the panel has already
# chosen. `{ $count }` itself in the text would be formatted by Fluent to the
# rules of the language, and “1000 positions” would come out as “1,000
# positions” — unlike the other numbers of the same window.
ui-inventory-positions = { $count ->
    [one] { $shown } position
   *[other] { $shown } positions
  }
ui-inventory-fineness = fineness { $fineness }
ui-inventory-maker = mark { $maker }
ui-inventory-variety = variety
ui-inventory-vigor = { $variety } · vigour { $vigor }
ui-inventory-charge = charge { $charge }
ui-inventory-condition = cond. { $condition }
ui-inventory-spoiled = spoiled
ui-inventory-spoils = spoils in { $hours } h
ui-inventory-keeps = keeps { $days } d

## The bank

ui-bank-title = Bank
ui-bank-rule = There is no collateral: the limit is earned by work — turnover and the interest paid on loans carried. Your own city lends to you at its own margin, and while its line is not spent the rate is lower: past it the money comes straight from the capital, with a premium for the risk.
ui-bank-rate = key rate
ui-bank-circulating = in circulation
ui-bank-reserve = in reserve
ui-bank-fund = in the works fund
ui-bank-debts = Your debts
ui-bank-outstanding = left to repay
ui-bank-loan = of { $principal } ₭ at { $rate }% · taken { $taken }
ui-bank-repay = Repay
ui-bank-borrow-title = Borrow
ui-bank-limit = your limit
ui-bank-your-rate = your rate will be
ui-bank-amount = how much to borrow, ₭
ui-bank-borrow = Take the loan
ui-bank-works = Work orders
ui-bank-order-road-mend = road upkeep
ui-bank-order-building-repair = building repair
ui-bank-order-building-build = construction
ui-bank-order-fuel-delivery = fuel delivery
ui-bank-fuel-left = { $goods }: { $left } left
ui-bank-building = { $kind }, { $footprint } m², floors { $floors }
ui-bank-council = Council of cities
ui-bank-council-locked = the rate is back with the algorithm for another { $left }: inflation is past the alarm line
ui-bank-council-waiting = the algorithm counts the rate: { $cities } cities of { $needed } have an administration, past that the Council of cities decides
ui-bank-council-rate = the city's rate, % — a corridor of ±{ $corridor } around { $advised }%
ui-bank-council-vote = The city's vote on the rate
ui-bank-council-advises = the algorithm advises { $advised }%
ui-bank-council-corridor = a corridor of ±{ $corridor }: the Council argues with the algorithm, it does not replace it
ui-bank-council-voter = the vote is cast by the holder of the “laws” power

## Finance

ui-finance-account = Account
ui-finance-account-rule = The account outlives the body: the money is in the Net, not in a pocket.
ui-finance-transfer-title = Transfer
ui-finance-transfer-rule = A transfer goes with no fee and no tax — and it cannot be called back. The ground is seen by the payee and by the court: it is all that will be left of the deal if it comes to a dispute.
ui-finance-to = to whom
ui-finance-to-hint = an identity's name
ui-finance-amount = how much, ₭
ui-finance-memo = what for
ui-finance-memo-hint = seen by the payee and by the court
ui-finance-transfer = Transfer
ui-finance-statement = Statement
ui-finance-none = no postings yet
# The pages turn by the last row read, newest first.
ui-finance-newer = newer
ui-finance-older = older
# The eye on a row: what it opens into is asked over, so the row waits a
# moment, and a row the server no longer shows comes back with nothing.
ui-finance-peek = Details: { $ground }
ui-finance-peek-wait = reading…
ui-finance-peek-none = no details
# The reader's own leg of the operation, where a name would stand.
ui-finance-side-me = you
ui-finance-ground = Ground: { $ground }
# A sale, as the seller's row opens: the tier and the quantity are details
# after the separator, the names stay after a label (D-258).
ui-finance-deal-goods = Goods: { $goods }, { $tier } · { $amount }
ui-finance-deal-price = Price: { $price } ₭ · total { $cost } ₭
ui-finance-deal-buyer = Buyer: { $name }
# The node the terminal stands in: a place's name, not a market's.
ui-finance-node = Node: { $node }
ui-finance-deal-charges = Tax: { $tax } ₭ · market fee: { $fee } ₭
ui-finance-deal-reserved = redeemed reservation
# A deposit, as the buyer's row opens: the order the money was frozen under,
# and the deals settled against it -- the buyer's statement has no other
# row that says what was bought.
ui-finance-order = Buy order: { $goods }, { $tier } · { $amount } at { $price } ₭
ui-finance-order-filled = Filled: { $filled } of { $amount }
ui-finance-fill = { $name } · { $amount } at { $price } ₭ · { $when }

## The coin station

ui-mint-title = Coin station
ui-mint-nothing = Nothing to strike: the coin's recipe is taken in the Library. A coin is a thing, and it is made like any thing, only by its own door.
ui-mint-count = how many coins
ui-mint-fineness = fineness { $fineness } ‰ — one for the whole world
ui-mint-cost = { $metal } of “{ $metalName }” (in hand { $metalHave }) and { $iron } of “{ $ironName }” (in hand { $ironHave }) will be spent. The alloy is a tenth part iron: a coin is always of the 900 fineness.
ui-mint-strike = Strike
ui-mint-not-enough = not enough metal or iron: the batch will not start
ui-mint-purse = Purse
ui-mint-purse-rule = Melting down returns the refined metal less the loss in the fire; the alloy is lost — picking it out costs more than the iron itself.
ui-mint-row-fineness = fineness { $fineness }
ui-mint-row-maker = mark { $maker }
ui-mint-melt = Melt down

## The bioprinter

ui-printer-title = No body
ui-printer-rule = The city sells not life but speed: pay and you are back in minutes, do not pay and it is twelve hours at the Precursors' printer. That is why the price of a resurrection has a ceiling, and nobody can lock an identity away.
ui-printer-note = The identity is whole: the name, the knowledge, the account and the obligations outlived the body. What the body carried is lost — and a third of it stayed lying where it died.
ui-printer-printing = printing is under way · the body will be ready { $when }
ui-printer-none = There is not one bioprinter in the world. This is a situation that must not arise: entry into the game is never blocked.
ui-printer-precursor = Precursors
ui-printer-free = free
ui-printer-at-city-expense = at the city's expense
ui-printer-no-cost = needs no energy and no iron
ui-printer-energy = energy
ui-printer-iron = iron
ui-printer-enough = { $needed } { $what }
ui-printer-short = { $what } { $here } of { $needed }
ui-printer-print = Print
ui-printer-term-minutes = { $minutes } min
ui-printer-term-hours = { $hours } h
ui-printer-soon = any moment now
ui-printer-in-minutes = in { $minutes } min
ui-printer-in-hours = in { $hours } h

## The hearth

ui-kitchen-title = Hearth
ui-kitchen-rule = An empty role cuts the quality harder than a poor foodstuff does. The combination decides the kind of dish — the variety of a diet is counted by kinds. Utensils are needed in the bag: a pot or a cauldron.
ui-kitchen-none = Not one dish in the identity: recipes are taken in the Library.
ui-kitchen-whole = the pot is boiled whole
ui-kitchen-empty = — empty —
ui-kitchen-cook = Boil the pot

## The library

ui-library-title = Library
ui-library-rule = Free and on no conditions, but only by coming; copying out costs stamina. What lies here is what was put here: the capital's is full from the founding, a city's is filled with carriers — from the inventory, “Put… → To the library”. What is put stays for good, the contributor's name with the recipe.
ui-library-search = recipe, station, input or name
ui-library-found = { $found } of { $all }
ui-library-shelf-empty = The shelves are empty: this library has been given nothing yet. Bring a “Recipe” carrier and put it here from the inventory.
ui-library-recipe = recipe
ui-library-station = station
ui-library-inputs = what from
ui-library-contribution = contribution
ui-library-founding = founding
ui-library-pioneer = discovered by
ui-library-known = known
ui-library-take = Take
ui-library-none-found = nothing found
ui-library-page = page { $page } of { $pages }
ui-library-carriers = Carriers in hand
ui-library-already = lies here already
ui-library-give = Put in the library
ui-library-give-hint = give for good: your name stays with the recipe
ui-library-agrotech = Agronomy
ui-library-agrotech-known = the agronomy is in the identity already
ui-library-agrotech-hint = take the crop's norm: free, for good
ui-library-agrotech-note = The agronomy of the basic crops is for everyone: with it a bed shows the norm, not a symptom. What is taken is marked ✓.

## The password field

ui-secret-password = password
ui-secret-hide = hide
ui-secret-hide-label = hide the password
ui-secret-show = show
ui-secret-show-label = show the password

# The quality floor and the price step (D-239, D-241). A buy takes anything no
# worse than the floor named, a sale goes as a lot of its own tier -- one
# sentence for both rules, because the player makes one decision.
ui-market-floor = no worse than
ui-market-floor-rule = A buy takes “{ $floor }” and anything better; a sale goes as a lot of tier “{ $tier }”.
# The tier of one's own buy order with a floor named by hand inside the band:
# without it the buyer cannot recover their own terms (D-239, D-225).
ui-market-order-floor = { $tier } (no worse than { $floor })
ui-market-step = price step
ui-market-step-auto = auto
ui-market-step-auto-title = the server picks the step: the finest one the book fits into
