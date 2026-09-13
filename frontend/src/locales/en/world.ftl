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

ui-map-inside = Inside
ui-map-outside = Outside

## Where a node stands: the player reads a place, not an enum.

ui-map-where-space = in space
ui-map-where-planet = on the planet
ui-map-where-location = inside a place

## A road's term and its price to the body: units read at a glance, not by comparison.

ui-map-unit-minutes = min
ui-map-unit-hours = h
ui-map-term-hours = { $term } h
ui-map-term-days = { $term } d

## The column beside the map: everything about the node picked.

ui-map-ongoing = On the way
ui-map-ongoing-rule = While you walk, you are nowhere: mining, crafting, loading and buying are closed, while the account and the orders work. You can turn back at any moment — you return to where you set out from, and what was spent does not come back.
ui-map-ongoing-leg = now — the leg to “{ $to }”
ui-map-ongoing-direct = a direct crossing
ui-map-ongoing-left = { $count } more nodes ahead
ui-map-transit-label = crossing
ui-map-turn-back = Turn back
ui-map-scouting = Scouting
ui-map-scouting-rule = The scout walks to the point you named and, when the term is up, stands on it: a run is a walk one way. While it lasts the body is not here. It can be called off at any moment — you return where you set out from, and what was spent does not come back.
ui-map-scouting-far = { $metres } m to the point
ui-map-scouting-away = the point is on another planet
ui-map-scouting-label = scouting
ui-map-scouting-stop = Call off the scouting
ui-map-here = You are here
ui-map-node-unnamed = Unnamed node
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

## The node menu under the right button.

ui-map-menu-here = You are here.
ui-map-menu-walking = While you walk, there is no going anywhere.

## Captions on the nodes themselves.

ui-map-node-alpha = outside the alpha
ui-map-node-spaceport = spaceport

## Roads from the node: what is laid, what has sagged and what it costs.

ui-map-surface-wild = trackless
ui-map-surface-trail = trail
ui-map-surface-road = road
ui-map-surface-paved = paved way
ui-map-road-working = work under way
ui-map-road-need = { $needs } roadbed needed, { $hand } in hand
ui-map-road-lay = Lay for { $needs }
ui-map-road-pave = Pave for { $needs }
ui-map-road-mend-need = patching: { $needs } roadbed
ui-map-road-mend = Patch for { $needs }
ui-map-road-at-hand = roadbed in hand { $hand }
ui-map-road-rule = The surface rises a step for roadbed and time: trackless ground → trail → road → paved way. A trail is worn in by feet and grows over without walking; without upkeep a road grows over too. No convoy goes over trackless ground or a trail.

## The sky: winding time on and the layer of space.

ui-map-sky-stop = Stop
ui-map-sky-wind = Wind on
ui-map-sky-slider = how many days ahead the sky is shown for
ui-map-sky-now-note = now
ui-map-sky-ahead = +{ $days } d
ui-map-sky-now = Now
# Winding the planet's year (D-334): the season, the snow and the ice a year ahead.
ui-map-year-wind = Wind the year
ui-map-year-slider = how many days ahead the planet is shown for
ui-map-year-pace = winding pace
ui-map-year-pace-sixteenth = ×¹⁄₁₆
ui-map-year-pace-eighth = ×⅛
ui-map-year-pace-quarter = ×¼
ui-map-year-pace-one = ×1
ui-map-year-pace-four = ×4
ui-map-year-pace-sixteen = ×16
ui-map-year-rule = The planet leans to its orbit: over a year the sun walks in latitude, and the snow and the ice come and go with it. Winding shows the year ahead — the light, the shadows and the snow; the planet itself does not change for it.
ui-map-sky-rule = The planets go round the star each on its own term, and the distance between them changes by itself. The eye does not catch it: an orbit passes fractions of a degree in an hour — so the run of time is shown by winding on, not by waiting.

## The strip above the map: the height of the view and the camera tie.

ui-map-cam-tied = camera follows you
ui-map-cam-free = camera is free
ui-map-zoom = zoom in or out

## The map's layers (D-331): what the ground is coloured by and what lies over it.

ui-map-layers = layers
ui-map-layers-now = layers: { $layer }
ui-map-layers-ground = ground colouring
ui-map-layers-over = on top
ui-map-layers-over-terrain = shown on the “{ ui-map-layer-terrain }” layer
ui-map-layer-terrain = terrain
ui-map-layer-relief = relief
ui-map-layer-biomes = biomes
ui-map-layer-temperature = temperature
ui-map-layer-rain = rainfall
ui-map-layer-moisture = soil moisture
ui-map-layer-weather = weather
ui-map-layer-provinces = provinces
ui-map-layer-city-lands = city lands
ui-map-layer-contours = contours
ui-map-layer-clouds = clouds
ui-map-layer-figures = vegetation
ui-map-degrees = { $c }°
ui-map-legend-rain-less = less rain
ui-map-legend-rain-more = more rain
ui-map-legend-moisture-fast = dries fast
ui-map-legend-moisture-slow = dries slowly
ui-map-legend-weather-dry = dry
ui-map-legend-weather-heavy = downpour

## The map field itself.

ui-map-loading = the map is loading…
ui-map-empty = There is nothing here yet.
ui-map-node-drawn = from a map drawn on day { $day }: what is here now, the map does not know
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
ui-ship-air-covered = no crew aboard, nothing is spent
ui-ship-air-outside = there is air outside, the system sleeps

## The feed (D-288): lines from a machine to a vessel.

ui-ship-feed = Feed lines
ui-ship-feed-hint = A port with nothing ticked draws from no vessel and fills none. Tick the vessels; the order of ticking is the order the port draws from them or fills them.
ui-ship-feed-reset = clear the line
ui-ship-feed-no-vessels = No suitable installed vessel aboard: put up a fuel tank, a canister or an oxygen tank in a compartment.
ui-ship-feed-empty = empty
ui-ship-feed-up = up
# The ship's scheme (D-288, D-340): where the lines are edited, from the bridge.
ui-ship-scheme = Ship schematic
ui-ship-scheme-rule = lines from machines to vessels
ui-ship-scheme-hint = Lanes are the compartments in the order they were laid: machines on the left, vessels on the right. A line runs from a port's dot to a vessel — drag it, or press the port and then the vessel. Pressing a line opens its port; pressing a vessel with no port chosen opens its name for editing.
ui-ship-scheme-read-only = The ship's owner draws the lines and names the vessels at a console; the crew sees the scheme as it stands.
ui-ship-scheme-none = No machine with ports aboard — none that draws or gives a liquid by lines: they appear here once they stand in a compartment.
ui-ship-scheme-way-in = inlet
ui-ship-scheme-way-out = outlet
ui-ship-scheme-way-vent = vent
ui-ship-scheme-way-in-note = Draws from the vessels in line order: when the first runs dry it draws from the next.
ui-ship-scheme-way-out-note = Pours into the vessels in line order; when all of them are full the machine stops.
ui-ship-scheme-way-vent-note = Pours into the vessels in line order; what does not fit goes overboard, and the machine keeps running.
ui-ship-scheme-no-line = { $way ->
        [in] no line: the port draws nothing
        [vent] no line: all of it goes overboard
       *[other] no line: the machine has nowhere to pour, and it stops
    }
ui-ship-scheme-port = the “{ $goods }” port: drag it to a vessel
ui-ship-scheme-line-pick = open this line's port
ui-ship-scheme-down = down
ui-ship-scheme-unline = remove
ui-ship-scheme-done = done
ui-ship-scheme-name-label = Vessel name
ui-ship-scheme-name-clear = remove the name
ui-ship-scheme-stall-power = stopped: the hull's batteries are flat
ui-ship-scheme-stall-dry = stopped: the “{ $goods }” line is dry
ui-ship-scheme-stall-full = stopped: the vessels on the “{ $goods }” line are full

## A line about the hull, one for each: where it is and what it breathes.

ui-ship-sign = { $name } · { $nodes } nodes · thrust { $thrust } on { $mass } kg of mass
ui-ship-ratio-line = thrust to weight { $ratio } against the { $min } needed
ui-ship-stuck = does not lift off
ui-ship-crew = crew { $crew } · fuel { $fuel }
ui-ship-no-life-support = no life support system
ui-ship-in-orbit = in planetary orbit · { $planet }
ui-ship-berthed = at the “{ $port }” shipyard, berth { $berth }
ui-ship-on-voyage = on a passage to “{ $name }”
ui-ship-adrift = adrift
ui-ship-deaf = It cannot be commanded. There is no “Ship control console” aboard.
ui-ship-console-borrowed = The console is somebody else's: orders are given from your own. Put one in your building: “{ $console }”.
ui-ship-no-bridge = Casting off and a passage are ordered from the control console: stand in the compartment it is in. Without a console aboard the ship flies nowhere.

## The climb: the hull's only move on the ground.

ui-ship-no-orbit = No climbing from here: this planet has no orbital node.
ui-ship-no-thrust = no thrust at all: fit an engine
ui-ship-leg-cost = { $hours } h · { $fuel } fuel
ui-ship-ascend = Climb to planetary orbit
ui-ship-ascend-hint = the climb takes time by the planet's gravity and the hull's thrust; it can be turned around
ui-ship-thrust-short = not enough thrust to lift off: shed mass or add an engine
ui-ship-ratio-short = Not enough thrust to weight: the ship does not lift off.
ui-ship-dry-climb = The tanks hold { $fuel }, and the climb needs { $need }: the ship does not leave the pad.
ui-ship-dry-ascent = The tanks hold { $fuel }, and the climb with the descent back needs { $need }: the ship climbs, but stays in orbit until fuel reaches it.
ui-ship-reserve = On top of the climb, the descent back takes another { $kept } units of fuel.
ui-ship-course-later = A course to another planet is set from orbit already: first the climb, then the crossing, then the choice of spaceport above the planet.
ui-ship-airless-none = There is no oxygen on the life support's line, and not one installed vessel holds any. With no air outside the hull the crew has nothing to breathe. Put an oxygen tank up in a compartment and draw a line to it — the “{ ui-ship-feed }” section.
ui-ship-airless-stowed = There is no oxygen on the life support's line. Aboard, off the line: { $off } u. With no air outside the hull the crew has nothing to breathe. Draw the line to that vessel — the “{ ui-ship-feed }” section.

## The descent: the mooring is chosen above the planet.

ui-ship-nowhere-to-land = There is nowhere to land here: not one spaceport with a lit beacon on this planet. A course to another planet is set on the map.
ui-ship-land-title = Land on the planet
ui-ship-pad-choice = pad to land at
ui-ship-pad-wild = unnamed node
ui-ship-pad-room = { $room } m² free
ui-ship-pad-full = no room: { $room } m² free, the hull needs { $need }
ui-ship-pads-label = the planet under the ship: pads to land at
ui-ship-pads-hint = turn the planet and pick a pad: a mark's size is its free ground, and a hollow mark has no room for the hull
ui-ship-land = Land
ui-ship-land-hint = the descent goes by the planet's gravity and the hull's thrust — a little cheaper than the climb
ui-ship-land-short = not enough thrust even to land: shed mass

## The passage: where it goes, how much is left and whether it can turn.

ui-ship-flight = { $back ->
        [true] turn back
       *[false] passage
    } to “{ $name }”
ui-ship-flight-label = passage
ui-ship-flight-autopilot = The autopilot flies the hull on its own: at every step it lays the passage afresh from where the hull is, and the tanks pay as it goes. The course cannot be changed.
ui-ship-may-cancel = The course may be cancelled, or the hull put into orbit round the star.
ui-ship-cancel-course = Cancel course
ui-ship-star-orbit = Enter astrocentric orbit
ui-ship-star-orbit-hint = The engines burn off the difference between the hull's speed and the speed of the circle round the star at the hull's own radius; the tanks pay as they burn. From then on the hull hangs on the circle like a planet.
ui-ship-flight-star = entering orbit round the star
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
ui-mine-rule = Timber costs beams and rope, a fast pace gives more output and more sag. There is no sequence to learn by heart: the optimum moves along with the price of timber. A body lives through its first cave-in, not its second.
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
ui-mine-collapsed = A cave-in. Everything mined this session is lost.
ui-mine-collapsed-lost = mined: { $lost }
ui-mine-rubble = The roof is already down: the rock goes to the waste heap until the rubble is cleared.
ui-mine-rubble-out = The rubble is cleared. The face starts over.
ui-mine-last-cave-in = The next cave-in is this body's last: it stays under the rock, and everything it carries stays lying here whole.

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
ui-rig-down = The rig lies rather than stands: taken down or knocked over, a machine drills nothing. The hopper can still be hauled out — to have it work, set it on its vein.

## The garden: plots, symptoms and work on foot.

ui-farm-land = Land
ui-farm-title = Garden
ui-farm-owned = The land of { $owner }. You do not run another's holding: hiring is access plus a share through a contract.
ui-farm-civic = City land: to keep a holding here, the land has to be bought out in the “Plot” window.
ui-farm-unmarked = The land is not marked out. A hundred metres is as many plots as you cut out of it.
ui-farm-symptom-thirst = the leaves are limp
ui-farm-symptom-soaked = the lower leaves are yellowing
ui-farm-symptom-pale = pale leaf
ui-farm-symptom-burn = leaf edges scorched
ui-farm-symptom-fat = running to leaf
ui-farm-symptom-weedy = weeds
ui-farm-symptom-crowded = crowded
# The pests' signs (D-299): what the eye sees. Which bottle answers is in the agronomy.
ui-farm-symptom-spots = spots on the leaf
ui-farm-symptom-web = webbing
ui-farm-symptom-bitten = bitten leaves
ui-farm-symptom-rot = rot in the axil
ui-farm-state-idle = fallow
ui-farm-state-plowing = being ploughed
ui-farm-state-plowed = ploughed
ui-farm-state-sown = growing
ui-farm-moisture = moisture
ui-farm-moisture-reading = moisture { $value }
ui-farm-carried = water is carried by hand
ui-farm-fed-stage = fed in this stage already
ui-farm-target = the moisture to water up to
ui-farm-water-to = Water: { $target }
ui-farm-feed = Feed: { $goods }
ui-farm-weed = Weed
ui-farm-thin = Thin
ui-farm-thin-why = once and only early: what is pulled is not put back
ui-farm-thinned = thinned
ui-farm-guarded = guarded: { $guard }
ui-farm-treat = Treat: { $goods }
ui-farm-treat-why = holds off its own trouble while the preparation lasts; one already under way is only halted, never healed
ui-farm-stage-sprout = sprouting
ui-farm-stage-leaf = in leaf
ui-farm-stage-bloom = in bloom
ui-farm-stage-fill = filling
ui-farm-stage-ripe = ripe
ui-farm-health-strong = standing strong
ui-farm-health-weak = weakening
ui-farm-health-sick = ailing
ui-farm-health-dying = dying
ui-farm-ripe = ripe — time to harvest
ui-farm-area = { $area } m²
ui-farm-fertility = fertility
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
ui-farm-harvest-select = Harvest with selection
ui-farm-harvest-select-hint = pick the best plants for seed: the fund keeps its vigour
ui-farm-harvest = Harvest
ui-farm-harvest-hint = harvest without looking: the seed fund will lose vigour
ui-farm-new-plot = A new plot is marked out in the “Land” window: marking out is a matter of land, not of farming.
ui-farm-rule = A plot lives by three scales — moisture, health, growth — and only the moisture is shown, as a curve: it leaves as a share of what is there, half as fast by a river. Watering to a target and feeding in a stage are separate actions on foot; drought and overwatering kill, the wrong fertilizer burns. How to tend is a text in the Library. A monoculture wears the land out, rotation and fallow heal it — the boundary remembers what grew on it.
ui-farm-seeds-rule = You sow with seed: a batch has its own variety and its own vigour, and the harvest is counted by them. Part of the harvest stays as seed of your own — with selection the fund holds, without selection it degenerates, and a hybrid splits on top of that.

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
ui-ship-chart-cheap = cheap { $term } · { $fuel }
ui-ship-chart-fast = fast { $term } · { $fuel }
# The bridge display's own words (D-240): the scale in the corner and the names
# of the three lines ahead. Short on purpose -- they stand inside the drawing.
ui-ship-chart-scale = ×{ NUMBER($zoom, minimumFractionDigits: 1, maximumFractionDigits: 1) }
ui-ship-chart-inertia = inertia
ui-ship-chart-course = course
ui-ship-chart-choice = choice
# The ring round the hull and the slider beside it. "Sight" is the world's own
# word for the radius: `ship-target-unseen` says "seen within N map units".
ui-ship-chart-sight = sight
ui-ship-chart-zoom = zoom
# The sky flown, not tabled (D-289): the drift, its verdict, and the Δv the
# console reads the plan against.
ui-ship-fate-stable = Inertia: a stable circle. It can hang like this for ever; refuelled, it can be given a course.
ui-ship-fate-crash = Inertia: a collision · { $body }. Unless refuelled in time, the ship is lost.
ui-ship-fate-escape = Inertia: out of the system. Unless refuelled in time, the ship is lost.
ui-ship-fate-label = drift
ui-ship-lost-status = lost
ui-ship-lost-note = The ship is lost with its crew: it takes no orders any more.
# Two hulls meeting (D-289, wave 3): the rendezvous, the hold, the consent to dock.
ui-ship-course-to-ship = rendezvous · { $name }
ui-ship-target-gone = The target is out of sight.
ui-ship-held = alongside · { $name }
ui-ship-docked-ship = docked · { $name }
ui-ship-dock = Dock
ui-ship-dock-agree = Agree to dock
ui-ship-dock-asked = consent given · the other commander has not answered yet
ui-ship-dock-wanted = the other commander asks to dock
ui-ship-dock-hint = Docking hull to hull takes both commanders' consent; the gangway opens the way for the crew and their canisters.
ui-ship-undock = Undock
ui-ship-star = the star
ui-ship-dv-line = Δv aboard { $have }
ui-ship-short-cross = The tanks hold { $fuel }, and the crossing needs { $need }: the fuel runs out under way, and the ship goes adrift.
ui-ship-short-land = The tanks hold { $fuel }, and the crossing with its landing needs { $need }: the ship reaches orbit and stays there.
ui-ship-course-dv = Δv to go { $need } · aboard { $have }
ui-ship-course-short = Less Δv aboard than the crossing needs: the tanks run dry under way, and the ship goes adrift.
ui-ship-course-failed = The sky did not answer: { $why }

## Scouting: a point on the ground and sending the body (D-321).

ui-map-scout = scouting
ui-map-join = way
ui-map-time = time
ui-map-join-aim = Way to the node “{ $node }”: { $metres } m
ui-map-join-go = Lay the way
ui-map-survey-aim = Scouting point: { $metres } m from you
ui-map-survey = Scout
ui-map-survey-clear = Clear the point
ui-map-peek-asking = reading the field…
ui-map-peek-found = already found
ui-map-peek-found-there = the way will lead there
ui-map-peek-ground = ground
ui-map-peek-water = water
ui-map-peek-water-river = river
ui-map-peek-water-lake = lake
ui-map-peek-water-none = none
ui-map-peek-stream = stream { $percent }%
ui-map-peek-mountain = mountains
ui-map-peek-climate = climate
ui-map-peek-climate-value = { $c }° ± { $swing }°, rainfall { $rain } of 100
ui-map-peek-moisture = soil moisture
ui-map-peek-marks = marks, chances
ui-map-peek-mark = { $mark } { $percent }%
ui-map-peek-vein = vein chance
ui-map-peek-complex = complex chance
ui-map-peek-percent = { $percent }%
ui-map-probe-height = { $m } m
ui-map-probe-rain = rainfall { $percent } of 100
ui-map-probe-moisture = moisture { $percent }%
ui-map-probe-weather-rain = rain { $percent }%
ui-map-probe-weather-cloudy = overcast
ui-map-probe-weather-clear = clear
