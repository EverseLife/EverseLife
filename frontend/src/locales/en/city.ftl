# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The words of the city windows and of the way into the world: administration,
# work orders, treasury, population, doors, registration, login, the first word
# (D-251, wave IV).
#
# The rules are the same as `ui.ftl`'s: a value is one line, however long it
# gets (a break would land in the text); the variants of a select each go on a
# line of their own.
#
# Numbers arrive here as strings wherever the screen printed them as they were:
# `{ $n }` with a real number would be formatted by Fluent to the rules of the
# language — “1,234” instead of “1234” — and the line would change in silence.

## Login (D-187)

ui-login-alpha = alpha
ui-login-title = Log in
ui-login-email = email
ui-login-password = password
ui-login-submit = Log in
ui-login-no-account = No account yet?
ui-login-register = Register

## The word on waking (D-182)

ui-intro-title = You opened your eyes
ui-intro-forerunners = Real people — the Forerunners — built a machine that prints people, and vanished. Their cities lie under the ice, their ruins under the capital; not one living witness is left.
ui-intro-machine = The machine still works. The body you are standing in was assembled by it a minute ago: a processor holds your thinking, and the Net keeps your name, your knowledge and your account. That is why death here takes your things, but not you.
ui-intro-legacy = The inheritance came whole and empty: the blueprints exist, the ore is in the ground, there are no roads. There is no one left to carry on the Forerunners' work — no one but you and others printed like you. Everything that appears in this world will be made by people.
ui-intro-start = Where to begin
ui-intro-step-look = Look around the node and gather the simple things: stone, brushwood, fibre. An action is a thing, and that is the first thing worth checking with your hands.
ui-intro-step-recipe = Take your first recipe at the Library of the capital. You were given no knowledge at all: a craft is knowledge, and you have to come for it.
ui-intro-step-sell = Sell what you made at a terminal. Prices here are set by people, not by the world: your first earnings are your first meeting with them.
ui-intro-go = Begin
ui-intro-again = To open this again — the “?” in the header.

## Registration in four steps (D-187)

ui-register-step-account = account
ui-register-step-line = line
ui-register-step-character = character
ui-register-step-city = city
ui-register-steps-label = registration steps
ui-register-bad-email = the email looks wrong
ui-register-short-password = the password is shorter than { $min } characters
ui-register-password-mismatch = the passwords do not match
ui-register-no-name = no name given
ui-register-long-name = the name is longer than { $limit } characters
ui-register-long-surname = the surname is longer than { $limit } characters
ui-register-age-range = age from { $min } to { $max }
ui-register-long-about = the description is longer than { $limit } characters
ui-register-account = Account
ui-register-email = email
ui-register-password = password
ui-register-password-hint = no shorter than { $min } characters
ui-register-again = once more
ui-register-again-hint = repeat the password
ui-register-to-login = ← to login
ui-register-next = Next →
ui-register-line = Line
ui-register-line-note = Who printed you. One line is playable in the alpha; the second is shown as a promise, not as a stub.
ui-register-line-players = playing
ui-register-line-world = world
ui-register-pick = Choose
ui-register-soon = Still in the works
ui-register-back = ← back
ui-register-character = Character
ui-register-name = name
ui-register-name-hint = what you will be called
ui-register-name-note = The name is unique and never changes: your reputation rests on it. Everything else can be fixed later in the account tab.
ui-register-surname = surname
ui-register-age = age
ui-register-about = description
ui-register-about-hint = looks, character, where you are from — as you like

## Where to be printed the first time (D-013, D-182, D-184)

ui-doors-title = Where to print you
ui-doors-lead = { $name }, you have no body yet — you have a choice of the machine that will assemble it. The first body is printed at once and free of charge everywhere; after that you pay for speed.
ui-doors-search = find a city
ui-doors-search-label = city search
ui-doors-count = { $shown } of { $total } · sorted by people in the city
ui-doors-empty-world = There is not a single bioprinter in the world. This state must not happen: the way into the game is never blocked.
ui-doors-nothing-found = Nothing found — try it another way.
ui-doors-precursor = Forerunner printer
ui-doors-precursor-note = The everlasting machine of the real people: it asks no city's treasury and refuses no one.
ui-doors-city-note = A city bioprinter: it runs on the city's energy and iron.
ui-doors-city = city
ui-doors-outside = outside a city
ui-doors-people = people right now
ui-doors-citizens = citizens
ui-doors-grant = settling grant
ui-doors-nothing = none
ui-doors-first-body = first body
ui-doors-at-once = at once
ui-doors-citizenship = citizenship
ui-doors-not-required = not required
ui-doors-tax = sales tax
ui-doors-print-here = Print here
ui-doors-grant-note = The settling grant is paid by the city out of its own treasury, not by the world out of thin air: a new resident is worth something to a city, and so cities bid for one.
ui-doors-rules-note = The rows of the table are enforced by the engine: required citizenship takes hold at the moment of printing and holds for the whole term, the tax is withheld from every sale. The Forerunner printer sets no conditions — the machine belongs to no one.
ui-doors-word-note = In quotes is the city's own word. That is a promise of living people, and the engine does not answer for it: if it is broken, it is a matter for the court.
ui-doors-back = ← back
ui-doors-term-always = required
ui-doors-term-hours = required · { $hours } h
ui-doors-term-days = required · { $days } d

## The state tab: economy and population (D-124, D-140, D-154)

ui-city-asking = Asking the city…
ui-city-outside = You are outside a city: beyond the walls there are no laws.
ui-city-silent = The city did not answer: nothing is known about it right now.
ui-city-again = Once more
ui-city-recount = Recount
ui-city-treasury-sign = { $city } · treasury { $treasury } ₭
ui-city-money = The world's money
ui-city-money-total = ₭ in circulation
ui-city-money-median = median account
ui-city-money-gini = inequality (Gini)
ui-city-trades = trades in a day
ui-city-prices = Prices over the day
ui-city-laws = The rules we live by
ui-city-laws-rule = Laws are changed in the administration: power is held in person. The tab is shown only to offices: these are the numbers they rule with.
ui-city-law-own = the city's decision
ui-city-law-default = default
ui-city-people-world = persons in the world
ui-city-people-here = bodies in the city
ui-city-people-printed = printed in the window
ui-city-offices = Offices
ui-city-offices-rule = Appointing and removing is done in the administration: power is held in person.
ui-city-offices-none = no offices
ui-city-residents = Residents
ui-city-residents-none = no one yet
ui-city-report-who = name of the defective print
ui-city-report = Report
ui-city-unreport = Withdraw
ui-city-unreport-title = withdraw your report
ui-city-report-note = A report lowers the target's trust and credit limit — no more than that. If you were wrong, withdraw it.

## Work orders and credit to the treasury (D-248)

ui-city-works-repair = building repair
ui-city-works-build = construction
ui-city-works-fuel = fuel delivery
ui-city-works-title = Work orders
ui-city-works-rule = The city names the work and its own price for the non-labour part — materials, fuel; the works fund adds a share of the labour rate on top. The money is set aside when the order is posted: an empty treasury or an empty fund refuses at once. An order is a licence too: while it hangs, anyone may repair and build on the city's plot.
ui-city-works-cancel = Withdraw
ui-city-works-node = plot (node key)
ui-city-works-offer = the city's offer, ₭ — for materials or fuel
ui-city-works-order-repair = Order a repair
ui-city-works-kind = house type
ui-city-works-footprint = footprint, m²
ui-city-works-floors = floors
ui-city-works-order-build = Order construction
ui-city-works-fuel-label = fuel
ui-city-works-amount = how many units
ui-city-works-price = price per unit, ₭
ui-city-works-order-fuel = Order a delivery
ui-city-loan-title = Credit to the treasury
ui-city-loan-rule = The treasury borrows from the central bank for public works: at the key rate, with no margin and no premiums, on the city's common credit line — the same one that carries the citizens' loans.
ui-city-loan-line = line: { $occupied } ₭ taken of { $permitted } ₭
ui-city-loan-row = { $outstanding } ₭ left of { $principal } ₭ at { $rate }% · taken { $taken }
ui-city-loan-repay = Repay from the treasury
ui-city-borrow = Borrow ₭ from the central bank

## Administration: offices, rights, laws, charter (D-154, D-155)

ui-admin-title = Administration
ui-admin-no-city = There is no city here: beyond the walls there are no laws.
ui-admin-title-city = Administration · { $city }
ui-admin-tab-power = power
ui-admin-tab-panel = panel
ui-admin-treasury-sign = treasury { $treasury } ₭
ui-admin-upkeep = City nodes on upkeep: { $nodes }. They burn { $energy } energy over { $hours } h — no one pays money for them, but at the tariff of { $tariff } ₭ per 100 that is { $worth } ₭ of unsold energy.
ui-admin-resident = You are a resident here: the laws are visible, and the officeholders rule them.
ui-admin-your-rights = Your rights: { $rights }.
ui-admin-come-in = Decisions are taken in the administration — come to it.
ui-admin-offices = Offices
ui-admin-offices-none = no offices
ui-admin-revoke = Remove
ui-admin-create-office = Create an office
ui-admin-whom = whom to appoint
ui-admin-post-default = Minister of economy
ui-admin-post-title = the city invents the title, the engine looks at the rights
ui-admin-appoint = Appoint
ui-admin-laws = Code-laws
ui-admin-law-own = the city's decision
ui-admin-law-default = default
ui-admin-law-accept = Accept
ui-admin-laws-note = The right to a law is pinpoint: the “minister of economy” rules the duties and does not touch the tax. You can hand over only what you hold yourself.
ui-admin-lots = Vacant plots
ui-admin-which-lot = which plot
ui-admin-to-whom = to whom
ui-admin-allot = Allot
ui-admin-treasury = Treasury
ui-admin-pay = Pay ₭
ui-admin-charter = Charter
ui-admin-charter-rule = The charter decides who approves a law: “the ruler alone” changes it at once, “by a vote of citizens” calls a vote. Elections of the ruler and the council will arrive with mechanics of their own.

## Citizenship: one per person, entry by the charter, exit with a term (D-160, D-184)

ui-admin-admission-open = admits freely
ui-admin-admission-application = by application with approval
ui-admin-admission-invite = by invitation only
ui-admin-citizenship = Citizenship
ui-admin-citizenship-in = you belong to
ui-admin-citizenship-leaving = you are leaving: citizenship falls away { $when }
ui-admin-citizenship-bound = the obligation ends { $when }
ui-admin-citizenship-none = You belong nowhere: a guest pays duties, but not taxes.
ui-admin-your-city = This is your city.
ui-admin-invited = You have been called: accept the invitation.
ui-admin-applied = The application is in — it awaits the power's decision.
ui-admin-join-blocked = citizenship is one per person: leave your former city first
ui-admin-accept-invite = Accept the invitation
ui-admin-join = Join the citizens
ui-admin-admission-line = { $city }: { $order }
ui-admin-leave-bound-title = you took on the term of the obligation when you chose this city's door
ui-admin-leave-title = the statement goes out over the Net
ui-admin-leave = Leave citizenship
ui-admin-leave-bound-note = The obligation of the printing holds until its term is up.
ui-admin-leave-note = Leaving is not instant: citizenship falls away when the term is up.

## A word to the city (D-183)

ui-admin-word = The city's word
ui-admin-word-none = the city is silent: a newcomer sees nothing but numbers
ui-admin-word-hint = what the city calls a newcomer with
ui-admin-word-publish = Announce
ui-admin-word-count = { $used } of { $limit } characters · seen by everyone choosing where to be printed

## The duty: goods, rate and the duty-free allowance (D-123)

ui-admin-customs-open = the border is open: no rates
ui-admin-customs-free = { $free } kg a day duty-free
ui-admin-customs-drop = Drop
ui-admin-customs-goods = goods
ui-admin-customs-rate-title = rate, % of the reference price
ui-admin-customs-free-title = duty-free allowance, kg a day per person
ui-admin-customs-add = Impose

## The rights of an office

ui-admin-scopes-note = The rights of an office — you can hand over only your own:
ui-admin-scopes-lacking = you do not hold this

## The economic panel (D-140)

ui-admin-panel-none = the panel is unavailable
ui-admin-panel-blind = The city is blind: the administration is not standing, or is switched off for non-payment. The data is not updating, and decisions are taken blind.
ui-admin-panel-sign = over the last { $hours } h · { $trades } trades · turnover { $volume } ₭
ui-admin-panel-rule = The summary's step is slower than the market on purpose: instant data would give the power a trading advantage over its own merchants. There is nothing personal here about anyone — neither incomes nor routes.
ui-admin-panel-people = People
ui-admin-panel-people-line = in the city { $here } · printed over the period { $printed }
ui-admin-panel-energy = Energy
ui-admin-panel-energy-line = in the pool { $stored } · tariff { $tariff } ₭ per 100 · to work { $work } · to homes { $home }
ui-admin-panel-border = Border
ui-admin-panel-border-line = imported { $imported } · exported { $exported }
ui-admin-panel-kg = { $goods } { $kg } kg
ui-admin-panel-trips = trips: { $in } inward, { $out } outward · duties collected { $duty } ₭
ui-admin-panel-production = Production
ui-admin-panel-production-line = mined { $mined } · harvested { $harvested } · crafted { $crafted }
ui-admin-panel-prices = Prices
ui-admin-panel-no-trades = there were no trades over the period
ui-admin-panel-goods = Goods in the city
ui-admin-panel-treasury = Treasury
ui-admin-panel-balance = balance { $balance } ₭
ui-admin-panel-collected = collected: { $lines }
ui-admin-panel-spent = spent: { $lines }
ui-admin-panel-ledger-line = { $ground } { $amount } ₭
ui-admin-panel-treasury-closed = The treasury by line item goes to those holding the “city panel” right. Balances, turnovers and prices are open to everyone: without them there is nothing to argue with the power about.

## Votes (D-161, D-162)

ui-admin-threshold-simple = simple majority
ui-admin-threshold-two-thirds = two thirds
ui-admin-threshold-unanimous = unanimously
ui-admin-votes = Votes
ui-admin-votes-rule = A vote is cast over the Net — presence is needed to rule, not to take part. The outcome applies itself when the term runs out.
ui-admin-call-election = Call an election
ui-admin-call-council = Council election
ui-admin-call-recall = Recall the ruler
ui-admin-votes-note = The outcome applies itself: the elected takes over the former ruler's set of rights, a recall strips the office and calls an election on the spot.
ui-admin-vote-council = council election
ui-admin-vote-ruler = election of the ruler
ui-admin-vote-no-candidates = · no candidates
ui-admin-vote-recall = recall of the ruler
ui-admin-vote-charter = charter
ui-admin-vote-charter-note = · the threshold is set by the charter itself
ui-admin-vote-by-council = the council decides
ui-admin-vote-turnout = { $yes } voted of { $of }
ui-admin-vote-tally = for { $yes } · against { $no } · of { $of }
ui-admin-vote-quorum = · quorum { $quorum }%
ui-admin-vote-closes = closes { $when }
ui-admin-nominate = Stand
ui-admin-nominate-title = stand for ruler
ui-admin-vote-for = For { $name }
ui-admin-vote-yes = For
ui-admin-vote-no = Against
ui-admin-vote-none = no vote

## The court (D-095, D-117, D-166, D-176)

ui-admin-court = Court
ui-admin-case-open = awaiting the court
ui-admin-case-judged = verdict: { $sanction }
ui-admin-case-dismissed-why = dismissed: { $why }
ui-admin-case-dismissed = dismissed
ui-admin-sanction-unenforced = (not enforced)
ui-admin-fine-title = the amount of the fine or the term of confinement in days
ui-admin-prison-title = which penal colony to send to
ui-admin-prison-pick = — penal colony —
ui-admin-verdict = Verdict
ui-admin-dismiss = Dismiss
ui-admin-sue-whom = against whom
ui-admin-sue-claim = the substance of the complaint
ui-admin-sue = File a complaint
ui-admin-sue-note = A complaint costs a duty to the city treasury.
ui-admin-court-queue = Cases in the queue: { $count }. The one the city gave the right of court to judges them.
