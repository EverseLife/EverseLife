# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The world underfoot: land and food, water and air, cold and warmth, ore and
# ruins, sleep, wear and death (D-251, wave III).
#
# NAME($id) turns the stable id of a thing, a station or a class into a word of
# this language: `iron_ore` travels the wire, the reader sees “Iron ore”. Before
# this wave a few refusals printed the id straight into the sentence — there are
# none of those here.
#
# Two incompatible Fluent habits, and the file looks the way it does because of
# them:
#   — a line break inside the TEXT of a value survives into the refusal, so the
#     text is written on one line, however long it turns out;
#   — the variants of a select ({ $x -> ... }) each have to stand on a line of
#     their own, and those breaks never reach the text.

# --- land and plots (engine/farm.py) -----------------------------------------

farm-plot-not-sown = the plot is not sown
farm-too-small = no sense marking out less than { $min } m²
farm-body-off-node = the body is outside a node
farm-storey-not-ground = this is a storey, not ground: a plot is cut in the yard — go down
farm-node-not-yours = the land is not yours: city ground is bought out, another's is rented by contract
farm-no-land = node { $node } has { $free } m² free, { $area } asked for
# The plot's state is a value of an enum (`PlotState`), and it becomes a word
# here: before this the player read «не под паром: plowing». `idle` never
# reaches this line — the refusal rises exactly when the plot is not fallow —
# but it has a branch of its own all the same: the test demands one from every
# member of the enum, and the catch-all default is left to the impossible
# value, and to it alone.
farm-not-fallow = plot “{ $plot }” is not fallow: { $state ->
        [plowing] ploughing is under way
        [plowed] already ploughed
        [sown] sown
        [idle] fallow
       *[other] { $state }
    }
farm-job-no-plot = job { $job }: no plot
farm-not-plowed = plot “{ $plot }” is not ploughed
farm-wrong-seeds = “{ NAME($goods) }” are not seeds of the crop “{ CULTURE($culture) }”
farm-seeds-not-in-hands = the seeds are not in hand: one sows one's own
farm-not-enough-seeds = sowing needs { $need } “{ NAME($seeds) }”, there are { $have }
farm-nothing-grows = nothing grows on plot “{ $plot }”
farm-cared-today = already tended today: care is daily, not hourly
farm-no-water = { $need } water needed: there is no river here, water is carried by hand
farm-nothing-to-harvest = nothing to harvest on plot “{ $plot }”
farm-not-ripe = the crop needs longer: { $left }, cycle { $cycle } days
farm-halves-too-small = both halves must be no smaller than farm.plot_min_area
farm-merge-other-node = neighbouring plots are merged, not land from different nodes
farm-no-open-ground = “{ $node }”: { NAME($weather) } — nothing grows in open ground here. Food comes in by ship
farm-dead-works = a dead body does not work
farm-plot-not-yours = another's plot: renting and hiring go through a contract
farm-recut-sown = only the unsown can be recut

# --- food (engine/food.py) ---------------------------------------------------

food-dead-eats = the dead do not eat
food-asleep = the body is asleep: wake up first
food-not-in-hands = the food is not in hand: one eats one's own, and out of the hand
food-not-food = “{ NAME($goods) }” is not food
food-spoiled = “{ NAME($goods) }” has spoiled

# --- breeding (engine/breed.py) ----------------------------------------------

breed-no-drift-in-formula = the drift coefficient cannot be read out of formula “{ $formula }”
breed-dead-sows = a dead body does not sow
breed-no-nursery = the node has no building of class “{ NAME($station) }”
# CULTURE() is a domain of its own: `beans` among goods is grain, not a crop.
breed-different-cultures = { CULTURE($one) } and { CULTURE($other) } are different crops: varieties of one are crossed
breed-one-batch = two batches of seed are needed: a variety is not crossed with itself
breed-not-enough-seeds = a nursery needs { $need } seeds of each variety
breed-nursery-done = this nursery has already been taken apart
breed-nursery-not-ready = the nursery will ripen: { $left }
breed-parent-gone = the parent variety is gone
breed-not-stable = the variety is not stable yet: a name goes to what gives the same result time after time
breed-not-the-author = a variety is named by the one who bred it
breed-empty-name = the name is empty
breed-library-in-person = the Library does not work remotely: knowledge is fetched in person
breed-body-without-identity = a body without an identity
breed-not-variety-seeds = “{ NAME($goods) }” are not seeds of a variety
breed-variety-gone = the variety of these seeds is gone
breed-body-off-node = the body is outside a node

# --- gathering (engine/forage.py) --------------------------------------------

forage-nothing-here = nothing lies on this land: { $node } is bare ground, and a walk turns up what a place grows or is made of
forage-nowhere-to-pour = nothing to take the { NAME($goods) } in: a liquid lives only in a vessel
forage-dead-gathers = a dead body gathers nothing
forage-already-searching = a search is already under way: wait for the find or finish it
forage-body-off-node = the body stands nowhere
forage-not-your-land = another's land: what lies on it belongs to the owner
forage-too-little-land = empty land: { NUMBER($free, minimumFractionDigits: 0, maximumFractionDigits: 0) } m², and there is room to gather from { NUMBER($min, minimumFractionDigits: 0, maximumFractionDigits: 0) } up
forage-no-strength = no strength for a search: { NUMBER($need, minimumFractionDigits: 2, maximumFractionDigits: 2) } needed, { NUMBER($have, minimumFractionDigits: 2, maximumFractionDigits: 2) } in hand
forage-not-searching = no search is under way: start one first
forage-still-searching = the search is not over: { $left } until the find shows
forage-nothing-to-stop = no gathering is under way: nothing to finish

# --- liquids and vessels (engine/liquid.py) ----------------------------------

liquid-dead-pours = a dead body pours nothing
liquid-same-vessel = no sense pouring into the same vessel
liquid-not-a-vessel = “{ NAME($vessel) }” is not a vessel for liquids
liquid-body-off-node = the body is outside a node
liquid-source-empty = { $named ->
        [true] “{ NAME($vessel) }” holds no “{ NAME($goods) }”
       *[false] “{ NAME($vessel) }” is empty
    }
liquid-nothing-to-pour = there is nothing to pour
liquid-no-room = “{ NAME($vessel) }” has { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg free: it does not fit
liquid-vessel-not-here = “{ NAME($vessel) }” is neither in hand nor here
liquid-vessel-not-yours = “{ NAME($vessel) }” is not yours: vessels in a node are the owner's to dispose of

# --- air (engine/oxygen.py) --------------------------------------------------

oxygen-no-suit = nothing to breathe in “{ $node }”: without a “{ NAME($suit) }” no tank will help, however many lie in the bag
oxygen-tanks-empty = nothing to breathe in “{ $node }”: the tanks are empty, refill aboard
oxygen-not-enough = the way to “{ $node }” needs { NUMBER($need, minimumFractionDigits: 1, maximumFractionDigits: 1) } oxygen, and the tanks hold { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: the crossing would end in suffocation

# --- cold (engine/frost.py) --------------------------------------------------

frost-node-frozen = “{ $node }” is frozen through: “{ NAME($station) }” does not work here. Warmth comes from “{ NAME($plant) }”, “{ NAME($heater) }” or “{ NAME($brazier) }” with fuel
frost-dead-warms = a dead body does not warm itself
frost-asleep = the body is asleep: wake up first
frost-not-a-warmer = “{ NAME($goods) }” gives no warmth: “{ NAME($warmer) }” is what does
frost-warmer-from-hands = a warmer is taken out of the hand
frost-no-cold-here = nobody freezes here: no reason to warm up, and a warmer is single-use
frost-reserve-full = the heat reserve is full as it is ({ NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) } h out of { NUMBER($ceiling, minimumFractionDigits: 1, maximumFractionDigits: 1) }): a warmer is saved for the cold

# --- energy (engine/energy.py) -----------------------------------------------

energy-dead-loads = a dead body loads nothing
energy-body-off-node = the body is outside a node
energy-no-station = there is no station here that needs fuel
# $fuel is a comma-separated list of ids: NAMES() takes it apart.
energy-wrong-fuel = “{ NAME($goods) }” does not burn in “{ NAME($station) }”: { NAMES($fuel) } will do
energy-fuel-from-hands = fuel is loaded out of the hand
energy-nothing-to-load = there is nothing to load
energy-no-grid = the batch “{ NAME($goods) }” needs energy, and there is no city grid here: outside the city things run on a battery
energy-pool-short = the batch “{ NAME($goods) }” needs { NUMBER($need, minimumFractionDigits: 0, maximumFractionDigits: 0) } energy, and the pool holds { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 0) }: a city without fuel stands still

# --- batteries (engine/battery.py) -------------------------------------------

battery-dead-charges = a dead body charges nothing
battery-not-a-battery = “{ NAME($goods) }” is not a battery: energy does not lie in a bag
battery-body-off-node = the body is outside a node
battery-not-here = the battery is neither in hand nor standing here
battery-no-grid = there is no city grid here: outside the city things run on a battery, and it is charged in the city
battery-nothing-to-give = the pool holds { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 0) } energy, and the battery has room for { NUMBER($place, minimumFractionDigits: 0, maximumFractionDigits: 0) }

# --- the face (engine/mining.py) ---------------------------------------------

mining-vein-not-here = a vein is reached on foot
mining-dead-works = a dead body does not work
mining-vein-depleted = vein { $vein } is worked out
mining-penal-face = the penal face is worked by convicts only
mining-no-strength = a swing needs { NUMBER($need, minimumFractionDigits: 2, maximumFractionDigits: 2) } stamina, and there is { NUMBER($have, minimumFractionDigits: 2, maximumFractionDigits: 2) }: sleep or a meal first
mining-session-open = the body already has a session open: one does not swing in two faces at once
mining-no-timber = there is no mine timber
mining-session-without-body = a session without a body
mining-session-closed = session { $session } is closed: { $state ->
        [left] the face was left
        [collapsed] the roof came down
       *[active] the face is still worked
    }
mining-session-dangling = the session points nowhere
# A liquid vein (D-252): the pick has nothing to grip.
mining-vein-liquid = “{ NAME($goods) }” is not taken by pick: a liquid vein is pumped by the rig
# $names is a list of ids: NAMES() takes it apart.
mining-no-tool = mining needs a tool of class “{ NAME($tool_class) }” ({ NAMES($names) }), and there is none in hand

# --- the rig (engine/rig.py) -------------------------------------------------

rig-dead-works = a dead body does not work
rig-not-a-rig = “{ NAME($goods) }” is not a drilling rig
rig-vein-not-here = the vein is not here: a rig is set up on the spot
rig-not-here = the rig is not here: the hopper is hauled out on foot
rig-not-yours = another's rig: hauling out goes by contract with the owner
# A liquid hopper (D-252): pours only into vessels, the remainder waits in the hopper.
rig-liquid-no-room = nowhere to pour “{ NAME($goods) }”: bring a vessel with room, in hand or in the node

# --- the automats (engine/automat.py) ----------------------------------------

auto-dead-works = a dead body does not work
auto-not-an-automat = “{ NAME($goods) }” is not an automat
auto-not-here = the automat is not here: a programme is loaded on the spot
auto-not-entitled = automats are programmed on one's own ground
auto-recipe-unknown = you do not know the recipe “{ NAME($goods) }”: an automat is not a library, learn it first
auto-not-covered = “{ NAME($station) }” is not the business of the automat “{ NAME($goods) }”: every station has its own
auto-barred-input = “{ NAME($goods) }” cannot be programmed: the pyroxite tier waits for its own station
auto-no-station-builds = “{ NAME($goods) }” is a build: stations are put together by hand, no machine builds them
auto-body-off-node = the body is off any node

# --- Precursor ruins (engine/ruins.py) ---------------------------------------

ruins-no-relic-of-class = the registry has no relic of class “{ NAME($thing_class) }”
ruins-not-ruins = there is nothing to break open here: this is not a Precursor city
ruins-exhausted = “{ $city }” is worked out: there is nothing left to break open
# `planets` is not among the display_name domains, so NAME() here would be an
# empty promise: a planet's name travels as it is, exactly as before the wave.
ruins-planet-without-node = planet “{ PLANET($planet) }” has no node: the world has nothing to extend

# --- sleep (engine/rest.py) --------------------------------------------------

rest-dead-sleeps = a dead body does not sleep — it is dead
rest-not-tired = stamina is full: no reason to lie down
rest-not-sleeping = the body is not asleep

# --- death and printing a body (engine/death.py) -----------------------------

death-body-alive = the body is alive: one identity does not get a second
death-print-running = printing is already under way
death-no-printer = node “{ $node }” has no bioprinter
death-print-queued = printing is already queued
death-no-grid = there is no city grid here: printing needs energy, and there is nowhere to take it from
death-pool-short = the pool holds { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 0) } energy, and printing needs { NUMBER($need, minimumFractionDigits: 0, maximumFractionDigits: 0) }: a city without fuel does not print
death-no-iron = the printer holds { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 0) } iron out of { NUMBER($need, minimumFractionDigits: 0, maximumFractionDigits: 0) }: nothing to assemble a processor from
death-prison-printer = the prison printer prints convicts only: this is not a door into the world
death-cannot-afford = printing costs { $price } ₭, and the account holds { $balance } ₭. The Precursor printer in the capital prints free — but takes twelve hours
death-job-dangling = printing { $job } points nowhere

# --- exploration (engine/explore/) -------------------------------------------

explore-unknown-goal = unknown search goal: { $goal }
explore-dead-scouts = a dead body does not scout
explore-no-such-ore = that rock is not mined in this world: “{ NAME($resource) }”
explore-body-off-node = a search goes out from a node, and the body stands nowhere
explore-not-from-aboard = one does not scout from aboard: there is no land under a ship. Step off in the port and go from there
explore-lot-only-in-city = a lot is looked for in a city: beyond the walls there is no city building
# What can be looked for here: each goal names its own word and the language
# strings them together (`inner`). This used to be a map of five Russian nouns
# welded to this one sentence, which no other language could say.
explore-goal-lot = a lot
explore-goal-site = a new place
explore-goal-vein = a vein
explore-goal-forest = woods
explore-goal-room = Forerunner rooms
explore-wrong-goal-here = that is not looked for from here: here one looks for { $offers ->
        [none] nothing
       *[some] { $words }
    }
explore-city-exhausted = “{ $city }” is worked out: everything that could be broken open already has been
explore-already-out = a run is already out: wait for the return
explore-run-queued = a run is already queued
explore-run-dangling = run { $job } points nowhere
explore-not-out = the body is not out scouting: there is nowhere to come back from
explore-lot-outside-city = a lot is looked for in a city: beyond the walls there is no building
