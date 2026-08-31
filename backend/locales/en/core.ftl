# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Refusals that belong to no one subsystem: land and name (D-198, D-011),
# the device fee (D-110), the wholeness of a countable thing (D-212), the
# place of a node on the map (D-237).
#
# A message value goes on one line -- a line break in the Fluent source ends
# up inside the text of the refusal.

## Land and name

land-outside-city = land outside a city is not claimed: the deed of ownership is issued by the city, and there is no city here. Anyone may build and work here
land-already-owned = the plot already belongs to someone
land-name-taken = the name “{ $name }” is taken: a name cannot be changed

## The device fee (D-110): the client pays it for every session

pow-already-spent = this task is already spent: every session has to be paid for
pow-wrong-answer = the estimate does not match
pow-too-many-starts = { $starts ->
        [one] { $starts } start
       *[other] { $starts } starts
    } in { $minutes } min — that is too often

## Countable things are counted in pieces (D-212)

goods-not-whole = “{ NAME($goods) }” is counted in pieces: { NUMBER($amount) } is less than one, they are taken and put down whole

## The place of a node on the map (D-237)

place-is-fixed = “{ $node }” stands on the world map: a node's place is set once and does not move. Only ship compartments can be shifted
