# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Exploration (D-321): the landscape says where and how far, not the dice.

## The map as a thing (D-319 item 6): memory forgets, a map keeps
map-sheet-dead-draws = a dead body draws nothing
map-sheet-not-a-sheet = this is not a map sheet: one draws on a blank sheet
map-sheet-drawn = the sheet is drawn on already: a map keeps its places for as long as it lasts
map-sheet-empty = there are no places in memory: nothing to draw
map-sheet-no-strength = drawing takes { NUMBER($need, maximumFractionDigits: 0) } stamina, and there is { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: memory is free, the work is not

explore-not-from-here = one scouts from a planet's ground: not from aboard and not from a room
explore-too-near = too close: the point is { $metres } m away, and from here one scouts no nearer than { $near } m
explore-too-far = too far: the point is { $metres } m away, and from here one scouts no farther than { $far } m
explore-not-land = there is water or the edge of the world there: a node has nothing to stand on
explore-into-water = the straight way there lies across water: from the shore one does not scout into the sea, a river is crossed at a ford
explore-no-room = it is crowded there: the ground is already taken — { $node }
explore-crosses-way = the way there would cross one already laid: one scouts between ways, not across them
explore-already-out = scouting is already under way
explore-harnessed = one does not scout in harness: a cart does not cross wild ground — unharness it
explore-shut = { $node } stands there already, and its door is shut to you
explore-not-out = no scouting is under way: there is nothing to turn back from
explore-already-joined = there is a way there already: { $node }
explore-scout-gone = the scout is not where the run began: the run is lost
explore-run-dangling = job { $job }: the scout or the node it left from is gone
