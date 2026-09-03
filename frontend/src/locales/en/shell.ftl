# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The words of the shell: the sidebar rail, the top bar with its gauges, the
# phone's zones and the small shared details — the deadline, the hint, the
# rule, the drag (D-251, wave IV).
#
# This is the frame around the panels: it is seen on every screen, and it
# changes together with the version of the client that draws it. So the words
# live in the build and do not arrive over the wire — as with `ui.ftl`.
#
# A value is one line (a wrap would land in the text); the variants of a select
# go one per line, and those wraps do not land in the text. A variant key is an
# identifier, which is why “is there a name”, “is there a bed” and “is it hot”
# arrive as a `true`/`false` flag and not as a string.
#
# Numbers arrive as strings already: `12`, `1.5`, `92`. Fluent would dress a
# number by the language — “1,5” instead of “1.5”, “1 200” instead of “1200” —
# and the column of numbers would drift away from the one assembled beside it
# in code. The only thing that stays a number is `$count` in a plural select:
# its rules look at the number.

## The sidebar rail: the name of a tab, and one line saying what is behind it (D-238).

ui-side-rail = sidebar sections
ui-side-fold = collapse the sidebar
ui-side-unfold = expand the sidebar
ui-side-tab-me = account
ui-side-tab-me-of = character details, password, email, view, sign out
ui-side-tab-goods = inventory
ui-side-tab-goods-of = what is in hand and what is worn
ui-side-tab-work = activity
ui-side-tab-work-of = what is under way, how to end it, and what can be done by hand
ui-side-tab-money = finance
ui-side-tab-money-of = account, statement, loan, your own orders
ui-side-tab-knows = knowledge
ui-side-tab-knows-of = the recipes you know
ui-side-tab-estate = estate
ui-side-tab-estate-of = the grid, utility bills, papers
ui-side-tab-net = network
ui-side-tab-net-of = correspondence and channels
ui-side-tab-state = city
ui-side-tab-state-of = economy and population
ui-side-tab-alpha = alpha
ui-side-tab-alpha-of = debugging: print a thing, hurry a term, pour in energy

# The rail button: the glyph says nothing, so the tab's name and its tally are read aloud.
ui-side-tab-counted = { $tab } · { $n }
ui-side-tab-title = { $tab } — { $about }
ui-side-no-account = the account is unavailable: reload the screen

## Doings: what the body has under way, and how it is ended (D-211).

ui-side-doings = Doings
ui-side-doings-rule = The body does one doing at a time: it sleeps, searches, plows, scouts, walks or works at a station. Everything under way is seen here, and it is ended here — there is no need to look for the window a doing was started from. The road walks itself, including while you are offline. A batch runs only while you stand at the station: leave or lie down to sleep and it halts, come back and it goes on. One person has one job running; the rest wait their turn in the order they were started.
ui-side-doings-none = nothing is under way
ui-side-travel = on the road: { $to }
ui-side-travel-next = to the next node
# The occupation is named by the server in the reader's language: only the colon between is ours.
ui-side-doing = { $title }: { $what }

## How an occupation is ended, and what that is paid for with.

ui-side-end-sleep = Wake
ui-side-end-sleep-why = stamina is credited on waking
ui-side-end-forage = Finish
ui-side-end-forage-why = the strength spent does not come back
ui-side-end-field = Return
ui-side-end-field-why = the run is cut short, there will be no find
ui-side-end-mine = Leave the face
ui-side-end-mine-why = what was mined goes into your hands
ui-side-end-plot = Pause
ui-side-end-plot-why = what is done stays; take it up or drop it from the strip

## Sleep: how long it has gone on. In two pieces, because a live counter stands between them.

ui-side-sleeping-for = asleep for
ui-side-sleeping-credited = · credited on waking
ui-side-slept-under-minute = less than a minute
ui-side-slept-minutes = { $n } min
ui-side-slept-hours = { $n } h

## A rest: lie down where you stand.

ui-side-sleep-title = lie down where you stand: stamina is credited on waking
ui-side-sleep = { $bed ->
        [true] Lie down in bed
       *[false] Lie down to sleep
    }
ui-side-sleep-note = { $bed ->
        [true] there is a bed here: sleep is faster
       *[false] no bed: sleep is slower
    }

## Batches: what the station is busy with, why it waits and how much is left (D-209).

ui-side-batch-make = { $output }: { $recipe }
ui-side-batch-repair = repair: { $goods }
ui-side-batch-melt = melting down: { $goods }
ui-side-batch-quality = quality { $n }
ui-side-batch-queued = queued
ui-side-batch-away = halted: go back to “{ $node }”
ui-side-batch-no-station = waiting for a free station
ui-side-batch-left-soon = less than a minute of work
ui-side-batch-left = { $n } min of work left
ui-side-batch-aside = { $why } · { $left }
ui-side-batch-resume = Resume
ui-side-batch-resume-title = the station is free — resume

## Reservations: the only purchase made at a distance, and it comes with a clock (D-047).

ui-side-reservations = Reservations
ui-side-reservations-rule = Collected in person: come to the node and redeem. Once the time is out, the deposit stays with the seller and the goods go back on the market.
ui-side-reservation = { $goods }, { $tier }
ui-side-reservation-aside = { $amount } at { $price } ₭ · { $node } · deposit { $deposit } ₭

## Your own orders: managed from here, while the goods lie in the terminal.

ui-side-orders = Orders
ui-side-orders-rule = Every order of yours, wherever it stands; the ones standing in the node you are in are managed from its terminal as well. The goods lie in the terminal.
ui-side-orders-none = no orders of your own
# `buy` and `sell` are the wire's own words: the variant is named after them.
# Only the verb differs, so the select sits inside the line: the tail is one,
# and an edit to the price or the tier cannot drift between the variants.
ui-side-order = { $side ->
        [buy] buying
       *[sell] selling
    } { $goods }, { $tier } · { $left } at { $price } ₭
ui-side-order-cancel = Withdraw

## Knowledge: the recipes a person loses neither to death nor to court.

ui-side-recipes = Recipes
ui-side-recipes-rule = Knowledge lives in the person and is lost neither to death nor to court (И8). It is taken at the Library, read off a “Recipe” carrier, or discovered on your own — at a station, without a recipe. Your own discovery is marked ✦.
ui-side-recipes-none = nothing yet: recipes are taken at the Library, read off a carrier, or discovered on your own
ui-side-recipe-discovered = discovered by you: your own experiment
ui-side-recipe-pioneer = first discovered by: { $name }
ui-side-recipe-details = Details of the “{ $recipe }” recipe
ui-side-recipe-station = station: { $station }
ui-side-recipe-by-hand = made by hand, no station needed
ui-side-recipe-inputs = takes per unit: { $inputs }
ui-side-recipe-source-learned = learned ready-made: taken at the Library or read off a carrier

## Agronomy: the text remembered in the Library (D-293).

ui-side-care = Agronomy
ui-side-care-rule = The agronomy remembered: the moisture band, the feeding by stage, the hardiness. Read in the Library; the knowledge lives in the person and is not lost.
ui-side-care-none = nothing yet: the basic crops' agronomy is read and remembered in the Library — free, for good

## The top bar: where the body is and what is with it, above everything else (D-238).

ui-top-cloud = in the cloud
ui-top-travel = on the road: { $to }
ui-top-surveying = out scouting
ui-top-asleep = asleep
ui-top-away-asleep = { $where } · asleep

## The ground is about to move: seen from any tab (P6).

ui-top-shaking = the ground moves in
ui-top-eruption = eruption
ui-top-shaking-title = eruption: what lies on the ground burns, the roads are redrawn, and a road that tears under a walker kills them along with their bag. Buildings are safe: the world does not erase what was built

## The body's gauges: stamina and satiety.

ui-top-stamina = stamina: spent by work, returned by sleep
ui-top-satiety = { $fed ->
        [true] fed
       *[false] —
    }
ui-top-satiety-title = { $fed ->
        [true] fed: stamina drains slower
       *[false] has not eaten: the usual drain
    }

## The top bar's utility row.

ui-top-summary = summary
ui-top-summary-title = what happened while you were away
ui-top-intro-title = who you are and where to start
ui-top-refresh = refresh
ui-top-source = source
ui-top-source-title = the source code of this version

## On a phone the utility row folds behind one button: the instrument strip has no room for four.

ui-top-more = more: summary, who you are, refresh, source
ui-top-intro = who you are

## The account in the top bar, and the quick transfer under it.

ui-top-money = { $money } ₭
ui-top-money-title = the account — a quick transfer
ui-top-transfer = Quick transfer
ui-top-transfer-to = to whom
ui-top-transfer-to-hint = the name of a person
ui-top-transfer-amount = how much, ₭
ui-top-transfer-memo = what for
ui-top-transfer-memo-hint = seen by the recipient and by the court
ui-top-transfer-send = Transfer
ui-top-transfer-more = statement and loan are in “finance”

## The planet's clock: the world lives by its own time, not by the viewer's zone (D-029).

ui-top-clock = { $hands } · day { $day }
ui-top-clock-title = local time: { $stamp }

## Air: the store in units, the hours out of the rate it drains (D-233).

ui-top-air-no-suit = no spacesuit
ui-top-air-no-suit-title = nothing to breathe: it is the spacesuit that joins a tank to the body, and without it there is no air, however many tanks lie in the bag
ui-top-air-out = nothing to breathe
ui-top-air-out-title = the oxygen has run out: the next tick is death
ui-top-air-units = { $n } u.
ui-top-air-hours = { $n } h ↓
ui-top-air-title = { $aboard ->
        [true] the ship's air: life support drives it out of water and energy, and the crew breathes
       *[false] oxygen from a tank through the spacesuit: outside it drains five times faster
    }

## Warmth, and on a hot planet coolness: the same gauge, another word (D-231).

ui-top-warmth-word = { $heat ->
        [true] coolness
       *[false] warmth
    }
ui-top-warmth-frozen = frozen
ui-top-warmth-frozen-title = { $word }: a frozen body burns stamina on time alone and spends more than usual on work; when it runs out, death
ui-top-warmth-warm-title = { $word }: the node is heated, the store refills
ui-top-warmth-cold-title = { $word }: the node is cold, the store melts away
ui-top-warmth-hours = { $n } h { $warm ->
        [true] ↑
       *[false] ↓
    }

## The stage: the map or the location (D-050).

ui-view-map = map
ui-view-place = location

## The phone's four zones: the same zones, one at a time.

ui-app-zones = sections
ui-app-zone-me = me
ui-app-zone-here = here
ui-app-zone-map = map
ui-app-zone-talk = chat
ui-app-away-title = { $ongoing ->
        [true] On the road
       *[false] Out scouting
    }
ui-app-away-note = { $ongoing ->
        [true] While you walk you are nowhere: whatever needs your presence is closed.
       *[false] The scout is out in the field: the body is out of reach, as in sleep.
    }

## The refusal, the hint, the rule, the name.

ui-refusal-dismiss = dismiss the message
ui-hint = hint
ui-rule = how this works
ui-person-profile = Profile

## The deadline bar: the one thing in the interface allowed to move.

ui-deadline-soon = any moment
ui-deadline-left = { $named ->
        [true] { $label }: { $remains } left
       *[false] { $remains } left
    }

## Dragging a stack: exactly how much to move (D-238).

ui-drag-what = { $what } · { $amount }
ui-drag-how-much = How much
ui-drag-how-much-of = How much: { $what }
ui-drag-all = all
ui-drag-ok = OK

## The amount field, and how an amount reads.

ui-amount-max = { $whole ->
        [true] no more than { $max }, in whole pieces
       *[false] no more than { $max }
    }
ui-amount-pieces = { $amount } pcs

## A busy body: what stands in the way of a second doing (D-211).

ui-busy-what = the body is busy: { $what }
ui-busy-what-until = the body is busy: { $what } · { $when }

## Display density: each mode described by whom it suits.

ui-density-plain = plain
ui-density-plain-about = roomy rows and margins
ui-density-normal = normal
ui-density-normal-about = the middle: this is how the game looks by default
ui-density-dense = dense
ui-density-dense-about = the most data on the screen
