# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The words of the shared modules: the clock, the axes the inventory is sorted
# along, the fill of a vessel, the choice of quality, the emblems of a plot
# (D-251, wave IV).
#
# These words belong to no panel: they are spoken by `clock`, `arrange`,
# `liquids`, `Tier` and `marks` — and shown by half a dozen windows at once.
# Hence a file of their own: the panel that reads them does not own them.
#
# The rules are `ui.ftl`'s: a value is one line, however long it grows (a wrap
# would land in the text); the variants of a select go one per line, and those
# wraps do not land in the text.

## The clock: local time of the planet, and how far off a moment is.

ui-clock-stamp = day { $day } · { $hands }
ui-clock-never = —
ui-clock-soon = any moment
ui-clock-just-now = just now
ui-clock-ahead = in { $span }
ui-clock-ago = { $span } ago

## A span in words. The unit abbreviations take no plural.

ui-clock-seconds = { $n } s
ui-clock-minutes = { $n } min
ui-clock-hours = { $n } h
ui-clock-hours-minutes = { $n } h { $rest } min
ui-clock-days = { $n } d

## The axes a player lays the inventory out along.

ui-arrange-group-none = no groups
ui-arrange-group-goods = by item
ui-arrange-group-tier = by quality
ui-arrange-group-kind = by kind
ui-arrange-group-maker = by maker's mark

ui-arrange-sort-name = by name
ui-arrange-sort-quality = by quality
ui-arrange-sort-amount = by amount
ui-arrange-sort-mass = by mass
ui-arrange-sort-condition = by condition
ui-arrange-sort-spoils = by shelf life

## The group heading: what the stack turned out to be, and what it has none of.

ui-arrange-kind-carriers = carriers
ui-arrange-kind-coins = coins
ui-arrange-kind-raw = raw stock
ui-arrange-kind-food = food
ui-arrange-kind-station = work stations
ui-arrange-kind-furniture = furniture
ui-arrange-kind-tool = tools
ui-arrange-kind-gear = gear
ui-arrange-kind-vehicle = transport
ui-arrange-kind-material = materials
ui-arrange-kind-consumable = consumables
ui-arrange-kind-other = other
ui-arrange-no-tier = no quality
ui-arrange-no-maker = no maker's mark

## The fill of a vessel (D-230).

ui-liquid-empty = empty
ui-liquid-fill = { $what } · { $mass } of { $capacity } kg

## Choosing quality: what to put to use.

ui-tier-none = quality: none in hand
ui-tier-none-title = there is no “{ $goods }” in hand
ui-tier-any = quality: any (worst first)
ui-tier-title = which quality of “{ $goods }” to put to use
# The row of one tier in the list: how much of it is in hand, and across what quality span.
ui-tier-stock = { $tier } · { $amount } · qual. { $span }

## The emblems an owner nails to a plot (D-238).

ui-emblem-house = house
ui-emblem-field = field
ui-emblem-woods = woods
ui-emblem-meadow = meadow
ui-emblem-stones = stones
ui-emblem-workshop = workshop
ui-emblem-market = market
ui-emblem-warehouse = warehouse
ui-emblem-food = food
ui-emblem-water = water
ui-emblem-markup = markings
