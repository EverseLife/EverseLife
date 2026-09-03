# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The road: legs, convoys, roadbed, ships and the body's occupation
# (D-045, D-107, D-147, D-199, D-201, D-202, D-211, D-230, D-235, D-245).
#
# NAME($id) turns a stable vault key into a word of this language (D-251):
# `roadbed` travels the wire, the reader sees "Roadbed". Names of nodes, ships
# and people are words already -- they travel as a plain { $arg }.
#
# Two incompatible habits of Fluent, which is why the file looks like this:
#   -- a line break inside the TEXT of a value survives into the refusal, so
#     the text is written on one line, however long it turns out;
#   -- the variants of a select ({ $x -> ... }) must each stand on their own
#     line, and those breaks never reach the text.

# --- a leg (D-045, D-091, D-152, D-199) ---------------------------------------

travel-asleep = the body is asleep: wake up first
travel-in-transit = the body is on the road: { $left } — matter demands presence
travel-in-field = the body is out scouting: { $left }; to call the run off — “return” on the map
travel-no-route = { $how ->
        [convoy] no road there for a convoy: trackless ground lets no transport through
       *[foot] there is no way at all: the nodes share no edge
    }
travel-dead-goes-nowhere = a dead body goes nowhere
travel-already-going = the body is already on the road
travel-same-node = that is the same node
travel-imprisoned = confinement: leaving the node is forbidden { $term ->
        [date] — { $left }
       *[verdict] until the court rules
    }
travel-in-default = the debt is not being served: you may not leave the node until you settle up. Anyone at all may pay for you
travel-route-node-gone = the route leads to a node that is gone
travel-impassable = “{ NAME($vehicle) }” will not get through here: { $surface ->
        [road] a road
        [paved] a paved way
       *[trail] trackless ground
    } lets no transport through. Unharness or look for a road
travel-no-strength = the road needs { NUMBER($need, minimumFractionDigits: 1, maximumFractionDigits: 1) } stamina and you have { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: eat or sleep first
travel-not-going = the body is not on the road: there is nowhere to come back from
travel-passage-not-turned = “{ $node }” is somebody else's closed location and you are walking it as a passage: nobody turns back halfway here, a passage is walked to the end
travel-job-no-leg = job { $job }: there is no leg
travel-leg-nowhere = leg { $leg } points nowhere
travel-plan-node-gone = leg { $leg }: the plan leads to a node that is gone
travel-not-an-exit = “{ $node }” is not a way out of the city: only the gates and the spaceport lead beyond the wall, a road is laid from them
travel-edge-in-use = somebody is walking this edge right now: the gangway is not pulled from under a walker. Wait until the road is free

# --- the convoy (D-107, D-157) ------------------------------------------------

transport-unknown-capacity = the vault does not know the capacity of “{ NAME($vehicle) }”: enter it in transport.capacity and transport.speed_k
transport-harness-dead = a dead body harnesses itself to nothing
transport-not-a-vehicle = “{ NAME($vehicle) }” is not transport: one harnesses oneself to a cart
transport-body-off-node = the body is outside a node
transport-not-here = the transport is not in this node: one harnesses to what stands beside
transport-already-harnessed = already harnessed: unharness first
transport-vehicle-taken = somebody is already harnessed to this transport
transport-load-dead = a dead body loads nothing
transport-load-not-harnessed = there is nothing to load into: harness up first
transport-not-in-hands = this thing is not in your hands: one loads one's own, and out of the hands
transport-nothing-to-load = there is nothing to load
transport-overloaded = the hold has { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg free and this is { NUMBER($mass, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg: nobody hauls more than the capacity
transport-unload-dead = a dead body unloads nothing
transport-unload-not-harnessed = there is nothing to unload from: harness up first
transport-not-in-hold = this thing is not in the hold
transport-nothing-to-unload = there is nothing to unload

# --- the roadbed (D-107) ------------------------------------------------------

road-top-surface = a paved way is the top of the ladder: there is nothing higher to lay
road-dead = a dead body lays no roads
road-stand-at-an-end = a road is laid standing at one end of the edge
road-intact = the road is whole: there is nothing to patch
road-trail-not-mended = trackless ground has nothing to patch: lay a road first
road-edge-busy = work is already under way on this edge: wait for the end of it
road-no-goods = you need { NUMBER($need, maximumFractionDigits: 0) } “{ NAME($goods) }” and your hands hold { NUMBER($have, maximumFractionDigits: 0) }: a road is materials, not intent
road-already-queued = the work is already queued
road-job-no-edge = job { $job }: there is no edge

# --- laying down and building a ship (D-202, D-215) ---------------------------

ship-keel-dead = a dead body lays down no ships
ship-no-name = a ship must have a name
ship-body-off-node = the body is outside a node
ship-keel-at-spaceport = the foundation of a ship is laid at a spaceport: there is nowhere else to moor
ship-keel-not-aboard = a new ship is not laid down aboard: the foundation goes on a planet's spaceport, and a hull is extended from inside
ship-extend-dead = a dead body builds no ships
ship-extend-from-aboard = a ship is extended from aboard: stand in a node of the ship. The first node is laid at a spaceport
ship-extend-not-yours = this ship is somebody else's: one builds on one's own
ship-no-foundation = you need “{ NAME($goods) }” and your hands are without it: a ship is materials, not intent. Made by recipe: { NAMES($makes) }
ship-keel-already-queued = the keel is already queued
ship-keel-job-no-node = keel { $job }: there is no node
ship-keel-job-no-ship = keel { $job }: there is no ship
ship-no-group = the ship has no group

# --- the console and the order (D-230, D-242) ---------------------------------

ship-no-spaceport = “{ $port }” has no spaceport: { $why ->
        [land] there is nowhere to land
        [turn-back] there is nowhere to come back to, the ship will reach the goal of its passage
       *[dock] there is nothing to moor to
    }
ship-no-mooring-to-hull = one does not moor to a hull: the goal of a passage is a spaceport
ship-beacon-dark = the beacon of “{ $port }” is dark: the node is frozen through or the shipyard is without power. A spaceport works while its node is warm and there is something to feed the shipyard — generation is brought there on foot and no other way
ship-command-dead = a dead body commands no ship
ship-not-yours = this ship is somebody else's
ship-no-console-here = a ship is commanded from the console: stand in the compartment where the “Ship control console” is
ship-command-from-aboard = a ship is commanded from aboard or from a “Ground control console”: board it or stand at a ground console
ship-console-not-yours = the console is somebody else's: orders are given from your own. Put a “Ground control console” in your building
ship-deaf = “{ $ship }” cannot be commanded: there is no “Ship control console” aboard

# --- the passage (D-201, D-232, D-233, D-235, D-245) --------------------------

ship-in-flight = “{ $ship }” is already under way: until the leg ends it takes no orders
ship-in-passage = the ship is already on a passage{ $known ->
        [true] { " " }to “{ $goal }”
       *[false] {""}
    }: until the leg ends it takes no orders
ship-no-connector-or-port = the ship has no connector or port
ship-not-enough-thrust = thrust is { NUMBER($have, minimumFractionDigits: 2, maximumFractionDigits: 2) } per kilogram against the { NUMBER($need, minimumFractionDigits: 2, maximumFractionDigits: 2) } needed: with that mass the ship goes nowhere. Add engines or take cargo off
ship-no-life-support = there is no life support system aboard: without one the ship goes nowhere
ship-no-engines = the ship has not a single engine
ship-no-fuel = { $why ->
        [climb] there is not enough fuel for the climb
        [cross] there is not enough fuel to leave the parking circle
        [turn-back] there is not enough fuel to turn back: on empty tanks nobody turns around in the void, the passage goes through to the end
       *[land] there is not enough fuel to land
    }: you need { NUMBER($need, minimumFractionDigits: 1, maximumFractionDigits: 1) } “{ NAME($goods) }” counted in rocket-fuel units, and the tanks answer for { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }
ship-passage-already-queued = the passage is already queued
ship-already-in-orbit = “{ $ship }” is already in planetary orbit: there is nowhere higher to climb
ship-planet-has-no-orbit = planet { PLANET($planet) } has no orbital node
ship-cross-from-orbit = “{ $ship }” stands at a spaceport: planets are crossed between from orbit. Climb to planetary orbit first
ship-cross-to-orbit = “{ $node }” is not an orbit: a crossing runs from planetary orbit to planetary orbit, and the spaceport is chosen once above the planet
ship-already-over-planet = “{ $ship }” is already above this planet: from here one lands, one does not cross
ship-nowhere-to-land = there is nowhere to land at { $node }: not one beacon is lit. The ship would go there and stay in orbit
ship-no-such-route = the world has no route { PLANET($planet_from) } — { PLANET($planet_to) }
ship-lost = “{ $ship }” is lost: no order and no turn-back reaches it any more
ship-no-route-adrift = from where it drifts the sky offers no arc · { PLANET($planet_to) }
# Two hulls meeting (D-289, wave 3).
ship-target-self = a ship does not fly to itself
ship-dock-self = a ship does not dock with itself
ship-target-unseen = the target is not in sight: another's hull is seen within { NUMBER($radius) } map units or moored at the same planet
ship-target-not-adrift = only a drifter is met: a hull under an order, moored, or alongside another is no rendezvous
ship-target-unknown = the target's inertia is not reckoned yet: it has only just gone adrift, the sky will show it in a minute
ship-not-held = docking takes station alongside: come within { NUMBER($radius) } map units at a relative speed under { NUMBER($speed) } units of speed
ship-dock-at-port = hull to hull only in space: at a pier it would be a bridge past the inspection
ship-already-docked-ship = the ship is already docked · { $other }
ship-not-docked-ship = the ship is not docked to another hull
ship-no-route-to-ship = the sky offers no arc to the target · { $other }
ship-already-landed = “{ $ship }” already stands on a planet: there is nowhere to land from
ship-land-not-into-orbit = “{ $node }” is an orbit, not a spaceport: from orbit one lands on the planet below it
ship-land-other-planet = “{ $node }” is on another planet: from orbit one lands on what is below, and another planet is reached by a crossing from orbit to orbit
ship-not-in-passage = the ship is going nowhere: there is nothing to turn back
ship-already-turning-back = “{ $ship }” is already coming back: a turn cannot be turned, wait for the arrival
ship-no-home-to-turn-to = where “{ $ship }” set out from is unknown: there is nothing to turn back to, and the passage will have to be seen through
ship-turn-back-already-queued = the turn back is already queued
ship-passage-nowhere = passage { $job } leads nowhere
ship-no-connector = the ship has no connector
ship-no-thrust-at-all = there is no thrust at all

# --- the blueprint of a ship (D-178, D-202) -----------------------------------

ship-arrange-dead = a dead body rearranges no ships
ship-arrange-from-aboard = a ship is rearranged from aboard: board it
ship-name-too-long = the name is longer than { $limit } characters
ship-cell-whole-number = a cell is given as a whole number
ship-cell-not-fractional = a compartment goes into a cell whole: there are no fractional cells
ship-cell-off-the-grid = cell { NUMBER($cell, useGrouping: 0) } is off the blueprint: no further than { NUMBER($reach, useGrouping: 0) } from the origin
ship-nothing-to-arrange = there is nothing to move
ship-no-such-node = this ship has no node “{ $node }”
ship-cell-is-a-pair = a cell is a pair of numbers
ship-cell-is-a-pair-of-two = a cell is a pair of numbers: across and down
ship-cell-taken = two compartments in one cell: “{ $first }” and “{ $second }”

# --- the body's occupation (D-211) --------------------------------------------

occupation-busy = the body is busy: { $what }{ $term ->
        [true] { " " }({ $left })
       *[false] {""}
    }

# The arc between worlds (D-271).
ship-hours-out-of-range = { NUMBER($hours) } h is off the slider: an arc flies from an hour to { NUMBER($limit) } h
ship-no-arc = the sky offers no arc for { NUMBER($hours) } h: every one cuts through the star's corona. Pick another time on the slider
ship-too-fast-for-thrust = in { NUMBER($hours) } h the engines deliver { NUMBER($have) } units of speed and the arc needs { NUMBER($need) }: move the slider towards the cheap end, shed mass or add engines
ship-hours-is-a-number = the flight time is a number of hours
