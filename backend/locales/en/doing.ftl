# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# What the body is at (D-211): one word for the row of the to-do list, and a
# sentence about what is going on and where it is ended.
#
# These messages are quoted by another one -- the refusal `occupation-busy` --
# so the sentence is written to read both on its own and after a colon.
#
# A value is one line; the variants of a select go one per line.

## One word: the title of the row in the to-do list. The key is derived from the
## kind of occupation (`doing-<kind>`), so the names here are not arbitrary.

doing-road = road
doing-field = survey
doing-sleep = sleep
doing-forage = foraging
doing-plot = plowing
doing-mine = mining
doing-craft = batch
doing-mend = repair
doing-keel = keel laying

## What is going on and where it is ended

doing-road-what = the body is on the road
doing-field-what = the body is out on a survey — it can be called back on the map
doing-sleep-what = the body is asleep — wake it first
doing-forage-searching = a search is under way
doing-forage-found = a find lies on the ground ({ NAME($goods) }) — settle it or end the search
doing-plot-what = plowing is under way{ $named ->
        [true] { " " }on “{ $plot }”
       *[false] {""}
    }
doing-mine-what = you are at the face — leave it first
doing-craft-what = work on “{ NAME($goods) }” is under way
doing-mend-what = the house is being repaired
doing-keel-what = { $named ->
        [true] the ship “{ $ship }” is being laid down
       *[false] a compartment of the ship is being laid down
    }

## How much is left. Hours and minutes arrive as numbers: how many words that
## makes, and in which form, is the language's business and not that of the
## arithmetic that counted them.

time-left = { $hours ->
        [0] { $minutes ->
                [0] less than a minute
               *[other] { $minutes } min left
            }
       *[other] { $minutes ->
                [0] { $hours } h left
               *[other] { $hours } h { $minutes } min left
            }
    }
