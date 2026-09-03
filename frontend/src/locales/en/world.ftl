# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The windows' words about the world: the map, the ship, the face, the rig,
# the garden, the station, the nursery, gathering (D-251, wave IV).
#
# They live here for the same reason `ui.ftl` does: this is the voice of the
# interface, it changes together with the client version that shows it, and it
# goes into the build, not over the wire.
#
# A value is one line (a break would reach the text); the variants of a select
# each stand on a line of their own, and those breaks never reach the text. A
# variant key is an identifier, so "is there a name" arrives as a `true`/`false`
# flag, not as a string.
#
# Numbers arrive as strings already: `12`, `1.5`, `<0.1`. Fluent would format a
# number by the language of its own accord — "1,5" instead of "1.5" — and the
# column of numbers would part ways with the one assembled next to it in code.

## The map: layers, nodes, roads, the sky.

ui-map-layer-space = space
ui-map-layer-planet = planet
ui-map-layer-city = city
ui-map-layer-location = location

## Where a node stands: the player reads a place, not an enum.

ui-map-where-space = in space
ui-map-where-planet = on the planet
ui-map-where-location = inside a place

## A road's term and its price to the body: units read at a glance, not by comparison.

ui-map-unit-minutes = min
ui-map-unit-hours = h
ui-map-term-hours = { $term } h
ui-map-term-days = { $term } d

## Scouting from the map: what is looked for here and what it costs.

ui-map-goal-lot = a lot
ui-map-goal-room = Precursor rooms
ui-map-goal-site = a new place
ui-map-goal-vein = a vein
ui-map-goal-forest = forest
ui-map-survey-label = scouting
ui-map-search-away = you are out scouting · back
ui-map-search-return = Come back now
ui-map-search-return-rule = You can turn back at any moment: there will be no find, and the strength spent does not come back.
ui-map-search-room = Break open the next room
ui-map-search-room-rule = A Precursor city stood here before you: scouting does not create places, it opens the next door. A room comes with its contents at once, and the deeper from the spaceport, the richer it is. The city is finite: the more is broken open, the more often a run comes back with nothing, and then there is nothing left to open at all.
ui-map-search-lot = Go looking for a lot
ui-map-search-lot-rule = A lot once found stands as city land: it is bought out from the city. The scout goes out alone, is out of reach until the return, as in sleep, and stays at the find.
ui-map-search-site = Go looking for a new place
ui-map-search-vein = Go looking for a vein
ui-map-search-any-ore = any rock
ui-map-search-far = search farther
ui-map-search-near = search nearby
ui-map-search-reach-rule = A nearby search finds land akin to this place: warmth, rains and terrain drift from it. A far one is the lottery, with a chance at anything.
ui-map-search-forest = Go looking for forest
ui-map-search-forest-odds = chance { $chance }%: forest takes longer to find than the rest
ui-map-search-forest-hint = wood is cut where there is forest
ui-map-search-elsewhere = from here one also looks for
ui-map-search-elsewhere-layer = { $goals } — on the “{ $layer }” layer
ui-map-search-rule = The scout goes out alone and is out of reach until the return, as in sleep. Run out of strength — he sleeps it off in the field and goes on. Found it — there he stays; the farther the find from the city, the longer the road to it. Forest turns up by itself as well, but ordered it takes longer. The button stands on the layer the find will fall on: a lot and a room in the built city, a place, a vein and forest on the planet's surface.
ui-map-forecast = a run from here: { $term } · chance { $chance }% · up to { $price } stamina
ui-map-forecast-rare = { $goods } is rare: the chance is already { $times }× lower
ui-map-forecast-explored = the neighbourhood is walked out: finds from here { $count }
ui-map-forecast-crowding = crowded{ $near ->
        [true] { " " }around { $anchor }
       *[false] {""}
    }: the chance is already { $times }× lower

## The column beside the map: everything about the node picked.

ui-map-ongoing = On the way
ui-map-ongoing-rule = While you walk, you are nowhere: mining, crafting, loading and buying are closed, while the account and the orders work. You can turn back at any moment — you return to where you set out from, and what was spent does not come back.
ui-map-ongoing-leg = now — the leg to “{ $to }”
ui-map-ongoing-direct = a direct crossing
ui-map-ongoing-left = { $count } more nodes ahead
ui-map-transit-label = crossing
ui-map-turn-back = Turn back
ui-map-here = You are here
ui-map-enter = Enter
ui-map-node-rule = You can walk to any node on the map: the route builds itself by time with coverage in mind, every leg is a job of its own, and arriving leads on into the next. There is no going as the crow flies: no edge, no way. The map shows two steps around you — a far node opens once you come nearer to it.
ui-map-node-ship-flight = ship · on a passage
ui-map-node-ship-port = ship · at the spaceport
ui-map-node-expandable = something to expand
ui-map-node-far = another planet: nothing to look at from here
ui-map-flight-label = passage
ui-map-road = road
ui-map-road-price = costs the body
ui-map-planet-deferred = The planet is outside the alpha: it is not in the world yet, and there is no getting to it.
ui-map-planet-other = Another planet. There is no way there on foot: only by ship from a spaceport.
ui-map-planet-ship = The planet your ship is at.
ui-map-planet-own = Your planet: you stand on its surface.
ui-map-ship-flying = The ship is on a passage: no gangway until it moors.
ui-map-ship-gangway = You come aboard on foot, up the gangway from the spaceport.
ui-map-node-offworld = This is another planet: no way there on foot, only by ship from a spaceport.
ui-map-node-far-walk = Not a neighbour: the route will build itself, along passable edges.
ui-map-go = Go
ui-map-expand = Expand
ui-map-surveying = The scout is in the field: the body is out of reach, as in sleep.

## The node menu under the right button.

ui-map-menu-here = You are here.
ui-map-menu-walking = While you walk, there is no going anywhere.

## Captions on the nodes themselves.

ui-map-node-alpha = outside the alpha
ui-map-node-gate = gate
ui-map-node-spaceport = spaceport

## Roads from the node: what is laid, what has sagged and what it costs.

ui-map-surface-trail = trackless
ui-map-surface-road = road
ui-map-surface-paved = paved way
ui-map-road-working = work under way
ui-map-road-need = { $needs } roadbed needed, { $hand } in hand
ui-map-road-lay = Lay for { $needs }
ui-map-road-pave = Pave for { $needs }
ui-map-road-mend-need = patching: { $needs } roadbed
ui-map-road-mend = Patch for { $needs }
ui-map-road-at-hand = roadbed in hand { $hand }
ui-map-road-rule = The surface rises a step for roadbed and time: trackless ground → road → paved way. Without upkeep a road grows over again, and no convoy goes over trackless ground at all.

## The sky: winding time on and the layer of space.

ui-map-sky-stop = Stop
ui-map-sky-wind = Wind on
ui-map-sky-slider = how many days ahead the sky is shown for
ui-map-sky-now-note = now
ui-map-sky-ahead = +{ $days } d
ui-map-sky-now = Now
ui-map-sky-rule = The planets go round the star each on its own term, and the distance between them changes by itself. The eye does not catch it: an orbit passes fractions of a degree in an hour — so the run of time is shown by winding on, not by waiting.

## The strip above the map: the height of the view and the camera tie.

ui-map-cam-tied = camera follows you
ui-map-cam-free = camera is free
ui-map-zoom-in = zoom in
ui-map-zoom-out = zoom out
ui-map-switcher-rule = Two steps of the graph are visible around you — where you can walk and what is seen from there; the rest opens by walking. Nodes stand where they stand: a node's place is the same for every player and the same tomorrow, so they are not dragged with the mouse. Camera follows you: you are in the middle of the map, it rides after you; the wheel, the loupe buttons and a two-finger pinch only zoom in and out. Camera is free: the map is panned with the mouse or a finger and stays where it was left — it will not ride after you as you walk. Layers: space, planet, city — the same graph from different heights.

## The map field itself.

ui-map-loading = the map is loading…
ui-map-layer-empty = There is nothing on this layer yet.
ui-map-world = world map

## The ship: the hull's card, the bridge's orders, the plan.

ui-ship-title = Ship
ui-ship-yard = Space shipyard
ui-ship-console = Ship control console
ui-ship-ground-console = Ground control console
ui-ship-console-aground = The console stands on the ground and says nothing: it works only in a node of a ship — on a foundation laid at a spaceport out of a “ship node foundation”. For orders from the ground there is another thing — the “Ground control console”.
ui-ship-rule = A ship is not a thing but a group of map nodes with one way out. Mooring and casting off are one edge appearing and disappearing, and flight is its absence: from aboard there is simply nowhere to step off. Speed follows from thrust against mass, so there is no carrying capacity as a number — an overloaded ship stays in port. The road runs on three legs: the climb to planetary orbit, the crossing from orbit to orbit, the descent to the chosen spaceport. The course is set on the bridge chart: it shows the hours and the fuel of this hull in particular.

## The hull's card: engines, mass, speed, air.

ui-ship-engines = engines
ui-ship-engines-none = not a single one: the ship does not fly
ui-ship-engine-row = ×{ $count } · thrust { $thrust } each · class { $class }
ui-ship-mass = mass
ui-ship-mass-parts = hull { $hull } kg · stations { $machines } kg · cargo { $cargo } kg
ui-ship-speed = speed
ui-ship-ratio = { $ratio } thrust per kg of mass
ui-ship-class = class { $class }
ui-ship-below-threshold = below the lift-off threshold
ui-ship-air = oxygen
ui-ship-air-line = { $units } on the life support's line
ui-ship-air-burn = { $spend } an hour · lasts { $term }
ui-ship-air-covered = nobody aboard is breathing
ui-ship-air-outside = there is air outside, the system sleeps

## The feed (D-288): lines from a machine to a vessel.

ui-ship-feed = Feed
ui-ship-feed-hint = A port with nothing ticked drinks from any installed vessel aboard. Tick vessels to narrow the line; the order of ticking is the order of use.
ui-ship-feed-none = No machine with lines aboard: the engine and the life support appear here once they stand in a compartment.
ui-ship-feed-any = any installed vessel aboard
ui-ship-feed-reset = any again
ui-ship-feed-no-vessels = No suitable installed vessel aboard: put a tank, a canister or a cylinder up in a compartment.
ui-ship-feed-empty = empty
ui-ship-feed-up = up

## A line about the hull, one for each: where it is and what it breathes.

ui-ship-sign = { $name } · { $nodes } nodes · thrust { $thrust } on { $mass } kg of mass
ui-ship-ratio-line = thrust to weight { $ratio } against the { $min } needed
ui-ship-stuck = does not lift off
ui-ship-crew = crew { $crew } · fuel on the lines { $fuel }
ui-ship-no-life-support = no life support system
ui-ship-in-orbit = in planetary orbit around { $planet }
ui-ship-berthed = at the “{ $port }” shipyard, berth { $berth }
ui-ship-on-voyage = on a passage to “{ $name }”
ui-ship-adrift = off the mooring
ui-ship-deaf = It cannot be commanded. There is no “Ship control console” aboard.
ui-ship-no-bridge = Casting off and a passage are ordered from the control console: stand in the compartment it is in. Without a console aboard the ship flies nowhere.

## The climb: the hull's only move on the ground.

ui-ship-no-orbit = No climbing from here: this planet has no orbital node.
ui-ship-no-thrust = no thrust at all: fit an engine
ui-ship-leg-cost = { $hours } h · { $fuel } fuel
ui-ship-ascend = Climb to planetary orbit
ui-ship-ascend-hint = the climb takes time by the planet's gravity and the hull's thrust; it can be turned around
ui-ship-thrust-short = not enough thrust to lift off: shed mass or add an engine
ui-ship-ratio-short = Not enough thrust to weight: the ship does not lift off.
ui-ship-dry-ascent = The tanks hold { $fuel }, and { $needs } is needed: the climb and the descent back. Nobody refuels in orbit — no edges reach the ship, and there is no stepping off it.
ui-ship-reserve = Over the burn of the climb, { $kept } is kept for the descent back: there is nowhere to climb to without the fuel to come down.
ui-ship-course-later = A course to another planet is set from orbit already: first the climb, then the crossing, then the choice of spaceport above the planet.

## The descent: the mooring is chosen above the planet.

ui-ship-nowhere-to-land = There is nowhere to land here: not one spaceport with a lit beacon on this planet. A course to another planet is set on the map.
ui-ship-land-title = Land on the planet
ui-ship-pad-choice = spaceport to land at
ui-ship-blind = blind landing
ui-ship-blind-hint = there are no spaceports here: the landing node is drawn on the approach, and you land where the rock lets you
ui-ship-land = Land
ui-ship-land-hint = the descent goes by the planet's gravity and the hull's thrust — a little cheaper than the climb
ui-ship-land-short = not enough thrust even to land: shed mass

## The passage: where it goes, how much is left and whether it can turn.

ui-ship-flight = { $back ->
        [true] turn back
       *[false] passage
    } to “{ $name }”
ui-ship-flight-label = passage
ui-ship-flight-fixed = The time was counted at the casting off and is not recounted: a sky that turned under a flying ship would make the passage longer than the one paid for. The course cannot be changed.
ui-ship-may-turn = But it can turn back.
ui-ship-recall = Turn back{ $known ->
        [true] { " " }to “{ $port }”
       *[false] {""}
    }
ui-ship-no-origin = Where the ship set out from is unknown: there is nothing to turn back to, it will go through to the end.

## The course: what the planet picked on the map costs.

ui-ship-pick-planet = The course is set on the map: pick a planet.
ui-ship-no-route = There is no way from here to there: either no route is laid in the world, or not one beacon is lit on that planet — the ship would go and stay in orbit.
ui-ship-thrust-cut = not enough thrust: shed mass
ui-ship-fly = Fly
ui-ship-fly-hint = the crossing runs from orbit to orbit; the spaceport is chosen above the planet
ui-ship-dry-fly = the tanks hold { $fuel }, and { $needs } is needed: the crossing and the landing at the end

## The hull's name: the owner's word, the engine derives nothing from it.

ui-ship-rename = Rename
ui-ship-name-label = ship name
ui-ship-name-set = Name
ui-ship-cancel = Cancel

## The keel: the hours between the foundation written off and the node appearing.

ui-ship-lay-keel = Lay a foundation for a spaceship
ui-ship-keel-label = keel
ui-ship-keel-note = the foundation is written off, the node will appear on its own — no need to stand at the shipyard. But the hands are busy with the keel: until the term you will neither sleep, nor scout, nor stand at a station. Walking is allowed.
ui-ship-name-placeholder = Ship name
ui-ship-foundation-word = ship node foundation
ui-ship-need-foundation = You need a “{ $goods }” in hand — it is made in a space workshop. A ship grows a node at a time: every next node is both a place and extra mass.

## The bridge chart and the plan of the compartments.

ui-ship-chart = passage chart
ui-ship-plan = ship plan
ui-ship-plan-rule = Drag the compartments about the grid: only the plan changes. The crossings stay as they arose at the keel, and each of them is one second. Empty field drags the plan, the wheel zooms in.
ui-ship-plan-askew = Some compartments do not stand on the cells: they were placed before the grid.
ui-ship-plan-home = To the foundation
ui-ship-plan-align = Align to the grid

## The face: three buttons and a lever for the pace.

ui-mine-title = The face
ui-mine-rule = Timber costs beams and rope, a fast pace gives more output and more sag. There is no sequence to learn by heart: the optimum moves along with the price of timber.
ui-mine-vein = Vein: { $goods }, richness { $richness }
ui-mine-no-vein = There is no vein in this node
ui-mine-no-vein-here = no vein here
ui-mine-computing = counting the device's toll…
ui-mine-start = Start a session
ui-mine-pow = One Argon2id evaluation per session: { $memory } MB, { $rounds } passes. Your device does the counting — it is a tax on scale, not on you.
ui-mine-mined = mined
ui-mine-swings = swings
ui-mine-timbers = timbers
ui-mine-swing = Swing
ui-mine-timber = Set timber
ui-mine-leave = Leave
ui-mine-pace = pace: { $fast ->
        [true] fast
       *[false] steady
    }
ui-mine-collapsed = A collapse. Everything mined this session is lost.
ui-mine-closed = The session is closed.

## The rig: capital instead of labour.

ui-rig-title = The rig
ui-rig-rule = The machine does not sleep, but it loses to a human in everything else: the output is lower, the quality is capped by the setting, and it eats a vein out twice as fast. Coal is carted in by people, the hopper is hauled out by people, the wear is repaired by people — capital hires society, it does not free you from it.
ui-rig-hopper = { $resource } · { $hopper } of { $capacity } in the hopper
ui-rig-full = the hopper is full, the machine stands
ui-rig-state = coal for { $hours } h ({ $fuel }) · condition { $condition } · { $left } left in the vein
ui-rig-no-fuel = the fuel has run out, the machine stands
ui-rig-empty = Haul out the hopper
ui-rig-in-hands = The rig is in hand. Set it on a vein — after that it works without you, as long as there is coal and room in the hopper.
ui-rig-place = Set on the vein

## The garden: plots, symptoms and work on foot.

ui-farm-land = Land
ui-farm-title = Garden
ui-farm-owned = The land of { $owner }. You do not run another's holding: hiring is access plus a share through a contract.
ui-farm-civic = City land: to keep a holding here, the land has to be bought out in the “Plot” window.
ui-farm-unmarked = The land is not marked out. A hundred metres is as many plots as you cut out of it.
ui-farm-symptom-thirst = the leaves are limp
ui-farm-symptom-pale = pale leaf
ui-farm-symptom-stunted = stunted growth
ui-farm-symptom-ripe = the ear has filled
ui-farm-state-idle = fallow
ui-farm-state-plowing = being ploughed
ui-farm-state-plowed = ploughed
ui-farm-state-sown = growing
ui-farm-water = water today
ui-farm-litres = { $litres } l
ui-farm-missed = missed
ui-farm-missed-days = { $count } d
ui-farm-ripe = ripe — time to harvest
ui-farm-area = { $area } m²
ui-farm-fertility = fertility
ui-farm-norm = the variety's norm: { $norm }
ui-farm-reading = { $value } of { $norm }
ui-farm-ripens = ripens
ui-farm-fertilize = Fertilize: { $goods }
ui-farm-plow = Plough
ui-farm-plow-pause = Pause
ui-farm-plow-pause-why = what is done stays, take it up again from here
ui-farm-plow-resume = Resume ploughing
ui-farm-plow-reset = Drop the ploughing
ui-farm-plow-reset-why = the strip is fallow again, what was done is lost
ui-farm-plow-paused = paused · { $share }% ploughed
ui-farm-plow-share = { $share }% ploughed
ui-farm-no-seeds = — no seeds —
ui-farm-vigor = vigour { $vigor }
ui-farm-sow = Sow
ui-farm-care = Tend
ui-farm-cared = already tended today
ui-farm-harvest-select = Harvest with selection
ui-farm-harvest-select-hint = pick the best plants for seed: the fund keeps its vigour
ui-farm-harvest = Harvest
ui-farm-harvest-hint = harvest without looking: the seed fund will lose vigour
ui-farm-new-plot = A new plot is marked out in the “Land” window: marking out is a matter of land, not of farming.
ui-farm-rule = Growth runs offline, tending is once a day and only on foot: days missed cut the harvest, but do not zero it. A monoculture wears the land out, rotation and fallow heal it — the boundary remembers what grew on it.
ui-farm-seeds-rule = You sow with seed: a batch has its own variety and its own vigour, and the harvest is counted by them. Part of the harvest stays as seed of your own — with selection the fund holds, without selection it degenerates, and a hybrid splits on top of that.
ui-farm-no-agrotech = You do not know the agronomy of this variety: you see the symptom, not the norm. The basic eight lie in the Library free of charge; the agronomy of a bred variety only its author knows.

## The fuel station: the reserve, the burn and the loading.

ui-plant-fuel = fuel
ui-plant-lasts = lasts
ui-plant-burn = burns { $draw } { $fuel } an hour and gives { $output } energy
ui-plant-count = stations { $count }
ui-plant-at-hand = { $amount } in hand
ui-plant-pour = Load { $fuel }
ui-plant-given = What is loaded goes to the city: fuel is not taken back.
ui-plant-none = No { $fuel } in hand. The station runs on deliveries: without fuel the city sits without energy.

## The breeding nursery: crossing and varieties.

ui-nursery-title = Breeding nursery
ui-nursery-first = — first parent —
ui-nursery-second = — second parent —
ui-nursery-variety = variety
ui-nursery-cross = Cross
ui-nursery-rule = Varieties of one crop are crossed. One attempt costs seed, room and a full cycle of growth: breeding is a matter of weeks, not of an evening.
ui-nursery-beds = In the nursery
ui-nursery-sprouts = sprouts { $when }
ui-nursery-gather = Take the sprouts
ui-nursery-sprouted = sprouted: a new hybrid is in your hands
ui-nursery-failed = did not sprout: what came out is too like what already grows
ui-nursery-own = Your varieties
ui-nursery-own-rule = A hybrid gives an excellent harvest once — its seed splits. Generations of selection bring it to a stable variety, and then the author names it for good.
ui-nursery-hybrid = hybrid, generation { $generation }
ui-nursery-row = { $stable ->
        [true] stable
       *[false] splits
    } · yield { $yield } · cycle { $cycle } d
ui-nursery-name = variety name
ui-nursery-name-set = Name

## Gathering: empty land gives up what lies on it.

ui-forage-title = Gathering
ui-forage-rule = Empty land — land with no building footprint on it — gives up what lies on it. What turns up is not chosen: the search runs on time, and at the term the land shows one find. Want it — pick it up, and the search ends there: to go over the land again or to leave is yours to decide. Do not want it — “search on”, and the search goes on by itself. Every search costs strength — whether it found something or was passed over. The more empty land, the faster the find. Leave the place and the search breaks off along with what was not found.
ui-forage-area = empty land { $area } m²
ui-forage-about = a find in about { $term }
ui-forage-cost = { $stamina } strength per search
ui-forage-took = picked up:
ui-forage-done = the search is over: search on or leave
ui-forage-found = found:
ui-forage-find = { $mass } kg · qual. { $quality }
ui-forage-start = Start gathering
ui-forage-start-hint = go over the land: a find will show by the term
ui-forage-barred = no more searching here: the land is another's or built on
ui-forage-again = Search on
ui-forage-pass-hint = leave it lying — and search on
ui-forage-take = Pick up
ui-forage-take-hint = into your hands; the search ends there — whether to search on is yours to decide
ui-forage-stop = Finish
ui-forage-stop-hint = finish: the strength spent does not come back
ui-forage-stop-hint-took = finish: the find is already in hand
ui-forage-stop-hint-found = finish gathering; the find will stay lying there
ui-forage-searching = searching · a find will show in
ui-forage-label = search
ui-forage-finds = found here:

## The factory floor: the node editor of the automats (D-253, wave 5).

ui-factory-title = Factory floor
ui-factory-rule = machines and wires
ui-factory-hint = A wire runs from a machine's right dot to another's left dot: what feeds stands left of what eats. A click on a wire cuts it.
ui-factory-wire-armed = The output is taken -- click the left dot of the machine it feeds. Clicking the output again cancels.
ui-factory-unlink = cut the wire
ui-factory-port-in = input
ui-factory-port-out = output
ui-factory-idle = -- no programme --
ui-factory-backlog = in work { $backlog }

## The course slider: from the fastest arc to the cheapest (D-271).

ui-ship-course-loading = The sky is computing the arcs…
ui-ship-no-arc-fits = The engines cannot fly any arc: shed mass or add engines.
ui-ship-slider = flight time
ui-ship-end-fast = fast: { $term }
ui-ship-end-cheap = cheap: { $term }
ui-ship-arc-cost = { $term } · { $fuel } fuel · Δv { $dv }
ui-ship-arc-via = flyby: { $planet }
ui-ship-chart-cheap = cheap { $term } · { $fuel }
ui-ship-chart-fast = fast { $term } · { $fuel }
ui-ship-course-failed = The sky did not answer: { $why }
