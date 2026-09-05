# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Exploration (D-321): the landscape says where and how far, not the dice.

explore-not-from-here = one scouts from a planet's ground: not from aboard and not from a room
explore-too-near = too close: the point is { $metres } m away, and from here one scouts no nearer than { $near } m
explore-too-far = too far: the point is { $metres } m away, and from here one scouts no farther than { $far } m
explore-not-land = there is water or the edge of the world there: a node has nothing to stand on
explore-into-water = the straight way there lies across water: from the shore one does not scout into the sea, a river is crossed at a ford
explore-no-room = it is crowded there: the ground is already taken — { $node }
explore-crosses-way = the way there would cross one already laid: one scouts between ways, not across them
explore-already-out = scouting is already under way
explore-already-joined = there is a way there already: { $node }
explore-scout-gone = the scout is not where the run began: the run is lost
explore-run-dangling = job { $job }: the scout or the node it left from is gone
