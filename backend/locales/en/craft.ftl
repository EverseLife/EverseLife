# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The workshop: the batch, minting, stations, the library, gear
# (D-092, D-133, D-016, D-106, D-053, D-146).
#
# NAME($id) turns the stable key of a thing, a class, an operation or a node
# property into a word of this language (D-251): `iron_ore` goes over the
# wire, the reader sees "Iron ore".
#
# Two incompatible habits of Fluent, which is why the file looks like this:
#   -- a line break in the TEXT of a value survives into the refusal, so the
#      text is written on one line, however long it turns out;
#   -- the variants of a select ({ $x -> ... }) must each stand on their own
#      line, and those breaks do not reach the text.

# --- craft: the batch and its conditions (D-092, D-133) ----------------------

craft-dead-works = a dead body does not work
craft-dead-cooks = a dead body does not cook
craft-dead-reads = a dead body does not read
craft-dead-wipes = a dead body wipes nothing
craft-body-off-node = the body is outside the node
craft-body-without-identity = a body without an identity

craft-zero-batch = a batch of zero units
craft-batch-too-big = batch larger than craft.batch_max: { $units }
craft-counted-whole = “{ NAME($goods) }” is counted in pieces: a batch of whole units
craft-not-learned = the recipe “{ NAME($recipe) }” is not copied into the identity
craft-not-enough = not enough “{ NAME($goods) }”: { $short } more needed
craft-item-not-in-hands = the item is not in hand: you repair and dismantle your own, not another's

craft-no-place = not here: { NAME($place) }
craft-place-not-yours = { NAME($place) } stands on another's land: only the owner may fell it

craft-no-station = the node has no working “{ NAME($station) }” station
craft-station-busy = { $whose ->
        [own] “{ NAME($station) }” is busy with your own work: wait for the batch to end
       *[other] “{ NAME($station) }” is busy: one person works at a station. Your own goes on your own land
    }
craft-cut-off = “{ $node }” is cut off for non-payment: stations do not run until the debt is closed

craft-no-tool = a tool is needed: { NAME($tool) }
craft-tool-not-in-hands = this tool is not in hand: tool means a thing out of your bag, while a station in the node is taken by itself

# --- carriers of knowledge (D-209, D-215) ------------------------------------

craft-write-needs-recipe = a carrier takes one particular recipe: name which
craft-write-not-learned = the recipe “{ NAME($recipe) }” is not in the identity: only your own can be written down
craft-not-a-carrier = “{ NAME($goods) }” is not a carrier: a recipe is not written onto it
craft-carrier-not-in-hands = the carrier is not in hand
craft-carrier-blank = nothing is written on this carrier: there is nothing to read
craft-wipe-not-a-carrier = only a carrier of knowledge can be wiped
craft-no-blank = “{ NAME($carrier) }” has no blank: the class “{ NAME($cls) }” is empty
craft-blank-dead = { $live ->
        [true] a blank worn to zero: nothing goes onto it any more (and the live ones do not add up)
       *[false] a blank worn to zero: nothing goes onto it any more
    }

# --- the pot (D-119, D-128, 16-cooking) --------------------------------------

craft-not-a-dish = “{ NAME($goods) }” is not a dish: it is made by the batch, not in a pot
craft-is-a-dish = “{ NAME($goods) }” is a dish: it is boiled in a pot, by the `cook` command
craft-unknown-roles = no such roles: { $roles }
craft-not-ingredient = “{ NAME($goods) }” is not a foodstuff: what goes into the pot is edible
craft-empty-pot = the pot is empty: fill at least one role

# --- the way of making --------------------------------------------------------

craft-unknown-way = { $known ->
        [true] “{ NAME($goods) }” is not made the “{ $way }” way; ways: { $ways }
       *[false] “{ NAME($goods) }” is not made the “{ $way }” way
    }
craft-unmakeable = “{ NAME($goods) }” is made neither by a recipe nor by an operation
craft-is-a-coin = “{ NAME($goods) }” is a coin: it is minted, and the metal is counted by its fineness (the `coin.mint` command)
craft-operation-extracts = the operation “{ NAME($operation) }” spends nothing: that is extraction, not craft
craft-coin-melts-elsewhere = a coin is melted down by the `coin.melt` command: the metal comes back by the coin's fineness, not by the recipe's rate

# --- invention (D-092) -------------------------------------------------------

craft-empty-composition = the composition is empty: put in at least something
craft-too-many-ingredients = one composition takes no more than { NUMBER($max, maximumFractionDigits: 0) } kinds of thing
craft-known-operation = this is “{ NAME($operation) }” — an operation without a recipe, it is in the list already
craft-already-known = you know “{ NAME($recipe) }” already: pick it from the list
craft-invent-failed = The composition did not come together: part of what you laid out burned. There are no hints — think and try

# --- the library as a source of knowledge (D-053, D-068, D-148) --------------

craft-no-library = A library does not work at a distance: knowledge is fetched in person
craft-library-lacks = this library has no “{ NAME($recipe) }”: it has not been brought here yet
craft-no-strength = copying it out takes { NUMBER($need, maximumFractionDigits: 0) } stamina, and there is { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: knowledge is free, the work is not

# --- internal faults: a job without a batch and the other way round -----------

craft-job-without-batch = job { $job }: there is no batch
craft-batch-dangling = batch { $batch } points nowhere
craft-target-gone = work { $batch }: the thing is gone

# --- the coin: minting and melting down (D-016, D-086) -----------------------

coin-dead-mints = a dead body does not mint
coin-dead-works = a dead body does not work
coin-not-a-coin = “{ NAME($goods) }” is not a coin
coin-not-minted = “{ NAME($goods) }” is not a coin: it is made by the batch, not by minting
coin-not-melted = “{ NAME($goods) }” is not a coin: that is reprocessing, not melting down
coin-no-composition = “{ NAME($goods) }” has no composition set: there is nothing to mint from
coin-no-input = “{ NAME($goods) }” has no input: there is nothing to mint from
coin-whole-only = coins are counted in whole pieces
coin-not-in-hands = the coin is not in hand: you melt your own
coin-not-enough = not that many coins: { $have } in the stack

# --- stations and furniture (D-106, D-150, D-181, D-232) ---------------------

station-dead-places = a dead body puts nothing down
station-dead-takes = a dead body carries nothing away
station-body-off-node = the body is outside the node
station-not-in-hands = this thing is not in hand
station-not-in-node = this thing is not in this node
station-not-placeable = “{ NAME($goods) }” is neither a station nor furniture: what goes into a building is equipment
station-not-a-station = “{ NAME($goods) }” is neither a station nor furniture
station-relic = “{ NAME($goods) }” is a relic of the Forerunners: it is neither taken down nor dismantled
station-node-not-yours = the node is not yours: equipment goes up on your own land. An empty city plot is bought out, a wild one is taken
station-take-not-yours = the node is not yours: another's equipment is not carried away
station-busy = someone is working at the station: wait for the batch to end
station-not-empty = there are things in “{ NAME($chest) }”: empty it first, carry it away after
station-no-building = the plot has no building: first you build, then you furnish
station-no-room = { $slots ->
        [one] the building has { $slots } space of { $per } m², and it is taken: build more or carry the extra out
       *[other] the building has { $slots } spaces of { $per } m² each, and all are taken: build more or carry the extra out
    }

# --- the library as a store of recipes (D-053, D-068) ------------------------

library-dead-brings = a dead body brings nothing
library-not-here = There is no library here: a recipe is brought to one on foot
library-not-in-hands = this thing is not in hand
library-not-a-carrier = what goes into a library is a written carrier — the “Recipe” item
library-already-there = this library has “{ NAME($recipe) }” already: the carrier stays with you

# --- load and gear (D-146, D-129) --------------------------------------------

gear-dead-dresses = a dead body does not dress
gear-not-in-hands = the thing is not in hand: you put on your own
gear-no-slot = “{ NAME($goods) }” is not worn: it has no slot
gear-unknown-slot = there is no “{ $slot }” slot in the world
gear-overloaded = too much to carry: { NUMBER($carries, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg in hand out of { NUMBER($limit, maximumFractionDigits: 0) }, and this is { NUMBER($extra, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg more. Anything over that goes by transport
