# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Space's own keys: the ship, the sky, and the worlds one flies to (D-201, D-271, D-230-D-233).

A section of `registry`, cut off by roadmap stage when the registry passed the
eight hundred lines the quality bar allows (2026-09-13): the ship as a
subgraph and the mechanics of its sky (E5), the eruptions of Pyroxis, the
Forerunners' ruins and reactor on Aurora, and the two survival scales of the
airless and frozen worlds -- oxygen and heat (E6).
Star-imported into the registry, which is the only module that imports it.
"""

from __future__ import annotations

from src.constants.spec import Book, Num, Span, Table

# --- Ship as a subgraph (D-201, D-202) --------------------------------------
#: A node aboard: its own mass and its usable area. Every node added is both a
#: place to put things and extra mass -- that is the whole design of a ship.
SHIP_NODE_MASS = Num("ship.node_mass")
SHIP_NODE_AREA = Num("ship.node_area")
SHIP_FOUNDATION_HOURS = Num("ship.foundation_hours")
#: Thrust and class **by the engine's name**, the way capacity is by the
#: vehicle's (`transport.capacity`): the engine keeps no list of what engines
#: exist. Add a second-class one in the vault and it flies without a release.
SHIP_THRUST = Table("ship.thrust")
SHIP_ENGINE_CLASS = Table("ship.engine_class")
#: Thrust-to-mass: below the first the ship does not undock at all, at the
#: second the passage takes exactly the table time.
SHIP_MIN_THRUST_RATIO = Num("ship.min_thrust_ratio")
SHIP_REFERENCE_RATIO = Num("ship.reference_ratio")
#: The speed ceiling: a passage never goes faster than this share of the table.
SHIP_ROUTE_MIN_SHARE = Num("ship.route_min_share")
#: The gangway: docking and undocking are not instant, and the edge to the port
#: costs exactly this to walk.
SHIP_BERTH_SECONDS = Num("ship.berth_seconds")
#: A step between two rooms aboard, and the same for every pair (D-240): a ship
#: is a room one walks through, not ground one crosses -- there is no distance
#: between compartments to measure.
SHIP_STEP_SECONDS = Num("ship.step_seconds")
SHIP_FUEL_PER_TON_DAY = Num("ship.fuel_per_ton_day")
#: Reference units one unit of a fuel kind is worth (D-252): the spend is
#: computed in rocket-fuel units, the tanks pay by density -- kerosene burns
#: fewer units for the same passage. Absent from the table -- worth one.
SHIP_FUEL_ENERGY = Table("ship.fuel_energy")
#: No number of people per life support system (D-288): the draw is the
#: ceiling, as mass is the hold's -- `ship.life_support_crew` left with it.
#: The orbital step (D-245): what it costs to leave a planet and to come back
#: down to it. Multiplied by the planet's own gravity and stretched by
#: thrust-to-mass, like every other passage. Descent is the shorter of the two:
#: down, the gravity one climbed against helps.
SHIP_ASCENT_HOURS = Num("ship.ascent_hours")
SHIP_DESCENT_HOURS = Num("ship.descent_hours")
#: What a world *is*, and the only two numbers the vault gives it (D-320):
#: how much matter it holds and how far that matter reaches, both as shares
#: of Terra's. Everything a planet does to a ship follows from the pair --
#: the pull at its surface is `mass / radius^2` and prices the climb off it
#: and the fall onto it (D-245), the pull in its sky is `ORBIT_PLANET_MU`
#: times the mass (D-289), and how dense the world is -- what it is made of
#: -- is `mass / radius^3`. Storing the surface pull as well was the older
#: shape, and it could not stay true: `g = M / R^2` ties the three, and the
#: vault set all three by hand (OQ-138).
PLANET_MASS = Table("planet.mass")
#: A share of the Earth's radius, not a length, and the **sky** body alone
#: since D-324: on the map it becomes ground through `ORBIT_BODY_RADIUS`, and
#: that is the only place it has a size in units. The land one walks is
#: `PLANET_LAND_AREA_SHARE` and has nothing to do with this number.
PLANET_RADIUS = Table("planet.radius")
#: Fuel for a crossing between worlds, per ton of hull and per unit of delta-v
#: (D-271): a passage pays for speed, not for hours -- the legs to and from
#: the ground still pay per day (`SHIP_FUEL_PER_TON_DAY`).
SHIP_FUEL_PER_TON_SPEED = Num("ship.fuel_per_ton_speed")
#: The fuel multiplier by the ship's class (D-235). Class is power and
#: efficiency, never a licence for a route: a first-class engine reaches
#: Pyroxis too, it just takes longer and burns more.
SHIP_ENGINE_EFFICIENCY = Table("ship.engine_efficiency")

# --- Celestial mechanics (D-271) -----------------------------------------------
#: A passage between worlds is a Lambert arc round the star, and the vault
#: tunes what the hull makes of it, not the sky itself: the star's pull is
#: read off the orbits (Kepler III), so that two sources cannot disagree.
#: Acceleration per unit of thrust-to-mass, map units per day squared: the
#: fast end of the slider, since the engines must give the arc's delta-v
#: within `ORBIT_BURN_SHARE` of the flight.
ORBIT_THRUST_SCALE = Num("orbit.thrust_scale")
ORBIT_BURN_SHARE = Num("orbit.burn_share")
#: Below this perihelion an arc is not offered: one does not cut through the star.
ORBIT_CORONA_RADIUS = Num("orbit.corona_radius")
#: The slow end of the slider: no arc longer than this, however cheap.
ORBIT_LONGEST_DAYS = Num("orbit.longest_days")
#: How many map units a Terra radius is (D-320): the scale that turns the
#: share in `PLANET_RADIUS` into the ground a hull can strike. The bodies are
#: drawn far larger than life on purpose -- the tick steps a minute at a time,
#: and a hull on approach crosses a real planet end to end in about two fifths
#: of one step, so a true-to-scale world could not be hit at all.
ORBIT_BODY_RADIUS = Num("orbit.body_radius")
#: The sky simulated (D-289): a planet's pull all the way, per unit of
#: `PLANET_MASS`; the parking circle a moored hull runs on; the steps of
#: the tick's integrator and of the planner's; the window a hull is put on
#: the circle in; the edge of the system, the horizon of the forecast and
#: how often a coasting hull's stamp is moved along.
#: The system's own layout (D-271), in the vault since 2026-09-08: how long
#: each world's year is and where it stood at the epoch. The **radii are not
#: here** -- the periods are the tuned numbers and a radius follows from one
#: by Kepler's third law against `ORBIT_TERRA_RADIUS`, so a radius that broke
#: the law cannot be written at all. The star's pull is read off the orbits,
#: and a broken law would price one passage differently by where it began.
ORBIT_PERIOD_DAYS = Table("orbit.period_days")
ORBIT_PHASE = Table("orbit.phase")
ORBIT_TERRA_RADIUS = Num("orbit.terra_radius")
ORBIT_PLANET_MU = Num("orbit.planet_mu")
#: The parking circle, in radii of the body it is round (D-324): one number
#: for the whole system put Pyroxis' circle inside Pyroxis once the worlds
#: became honest sizes.
ORBIT_PARK_RADII = Num("orbit.park_radii")
ORBIT_STEP_MINUTES = Num("orbit.step_minutes")
ORBIT_PLAN_STEP_MINUTES = Num("orbit.plan_step_minutes")
#: The ejection window: the helm holds the departure burn until the parking
#: circle has turned the hull within this of the excess its arc leaves with
#: (D-316).
ORBIT_EJECT_WINDOW = Num("orbit.eject_window")
#: The window a hull is put on the circle in, in radii of the body it is
#: arriving at (D-324): flat units would have put the window inside a
#: world drawn larger than it, where no hull can ever be.
ORBIT_CAPTURE_RADII = Num("orbit.capture_radii")
ORBIT_CAPTURE_SPEED = Num("orbit.capture_speed")
ORBIT_SYSTEM_RADIUS = Num("orbit.system_radius")
ORBIT_FORECAST_DAYS = Num("orbit.forecast_days")
ORBIT_RESTAMP_HOURS = Num("orbit.restamp_hours")
#: Where the helm stops chasing the arc and matches the circle whatever
#: its speed, in parking radii; and the shortest leg it lays when the
#: planned hour has passed without a capture, days.
ORBIT_APPROACH_RADII = Num("orbit.approach_radii")
ORBIT_LATE_LEG_DAYS = Num("orbit.late_leg_days")
#: How close a foreign hull is seen from the chart, map units; how close and
#: how slow two hulls must be for the hold, and so for a docking (D-289).
ORBIT_SIGHT_RADIUS = Num("orbit.sight_radius")
ORBIT_DOCK_RADIUS = Num("orbit.dock_radius")
ORBIT_DOCK_SPEED = Num("orbit.dock_speed")
#: The slider's grid: the shortest arc offered, hours, and the step between
#: samples as a share; the map's calendar is coarser and looks this many
#: days ahead.
ORBIT_SLIDER_FROM_HOURS = Num("orbit.slider_from_hours")
ORBIT_SLIDER_STEP = Num("orbit.slider_step")
ORBIT_CALENDAR_STEP = Num("orbit.calendar_step")
ORBIT_CALENDAR_DAYS = Num("orbit.calendar_days")

# --- Eruptions of Pyroxis (D-197, D-233) ------------------------------------
#: The planet's rhythm, not an event of the server: how often the ground moves,
#: and how long the free signal comes before it does (P6: the window to walk out).
PYROXIS_ERUPTION_PERIOD = Span("pyroxis.eruption_period")
PYROXIS_ERUPTION_WARNING = Num("pyroxis.eruption_warning")
#: How much one eruption rebuilds: how many nodes it shakes, what share of a
#: shaken node's ways it redraws, and what share of its veins moves next door.
PYROXIS_NODES_SHIFTED = Span("pyroxis.nodes_shifted")
PYROXIS_EDGE_REDRAW_SHARE = Num("pyroxis.edge_redraw_share")
PYROXIS_VEIN_RELOCATE_SHARE = Num("pyroxis.vein_relocate_share")

# --- The Forerunners' ruins (D-232) -----------------------------------------
#: A city is finite: it holds this many rooms, every one opened makes the next
#: search worse, and when the stock is out there is nothing left to open. A
#: worked-out city is a worked-out vein, not a locked door: the map keeps it.
RUINS_CITY_ROOMS = Num("ruins.city_rooms")
#: What rooms a city holds, by what the city **was**, and what lies in a room,
#: by what the room is. Two books, and both are content: a new kind of room is
#: a line in the vault.
#: The area of a room, a hall or a pier of the Forerunners (D-232); apart from
#: the found node's area, which D-321 shrank for the plain's reach.
RUINS_ROOM_AREA = Span("ruins.room_area")
RUINS_ROOM_TYPES = Book("ruins.room_types")
RUINS_ROOM_FINDS = Book("ruins.room_finds")
#: How much lies in a room, and how much richer each step deeper makes it (D-061).
RUINS_ROOM_HAUL = Span("ruins.room_haul")
RUINS_DEPTH_BONUS = Num("ruins.depth_bonus")
#: How long ago the reactor of a **found** city was started, in lifetimes of a
#: reactor: it died long before anybody came, and its beacon is dark.
RUINS_NEW_CITY_AGE = Num("ruins.new_city_age")

# --- The Forerunners' reactor (D-232) ---------------------------------------
#: Decay heat into the city pool, without fuel and without people. The output
#: falls from the moment the seed lays Aurora's surface and reaches zero in a
#: year of real time -- not a switch but a fading, visible in advance.
REACTOR_OUTPUT = Num("reactor.output")
REACTOR_LIFETIME = Num("reactor.lifetime")

# --- Oxygen (D-233, D-234) ---------------------------------------------------
#: Oxygen (D-233, D-234): the second scale of survival, and only where there is
#: no air -- in flight and on Pyroxis. Terra and Aurora never see it. The
#: reserve is not on the body: it is the oxygen in the cylinders it carries,
#: and a cylinder gives nothing without a suit to breathe it through.
OXYGEN_CREW_DRAW = Num("oxygen.crew_draw")
OXYGEN_BODY_DRAW = Num("oxygen.body_draw")
OXYGEN_CYLINDER_STORE = Num("oxygen.cylinder_store")
#: The balance target the rest of the group is derived from. Read by the
#: simulation, never by the engine: three months is a promise about numbers,
#: not a rule the world enforces.
OXYGEN_AUTONOMY_TARGET = Num("oxygen.autonomy_target")
#: What a sown hydroponic plot breathes out an hour per square metre while
#: its culture grows (D-288, D-340): into the vessels on the oxygen line of
#: the hydroponic units standing in its compartment.
OXYGEN_HYDROPONICS_RATE = Num("oxygen.hydroponics_rate")

# --- Frost and heat (D-231) --------------------------------------------------
#: The body's heat reserve, hours, by climate -- the frost's and the heat's
#: are their own since D-338: Aurora's snow lengthens its off-road, and
#: Pyroxis has no snow. Melts hour by hour in the climate, and in a warm node
#: fills from empty to its ceiling in `frost.warm_hours`, whatever the ceiling.
FROST_RESERVE_MAX = Table("frost.reserve_max", keys=("frost", "heat"))
FROST_WARM_HOURS = Num("frost.warm_hours")
#: How much worn gear multiplies the reserve, by thing class -- keyed the way
#: `inventory.exo_bonus` is: the engine keeps no list of warm clothes.
FROST_SUIT_K = Table("frost.suit_k")
#: Hours one warmer adds. The thing one walks into the cold with.
FROST_WARMER_HOURS = Num("frost.warmer_hours")
#: The frozen body: how much more it spends on any work, and how much stamina
#: it burns on nothing but time -- the latter by climate since D-338, so that
#: the frost's larger reserve does not make a bare night in its cold survivable.
FROST_FROZEN_DRAIN_K = Num("frost.frozen_drain_k")
FROST_FROZEN_STAMINA = Table("frost.frozen_stamina", keys=("frost", "heat"))
#: What heat costs the city pool per hour, and what the brazier burns instead
#: of a pool: heat is a round-the-clock drain, and that is the price of living
#: on a frozen planet.
FROST_PLANT_DRAW = Num("frost.plant_draw")
FROST_HEATER_DRAW = Num("frost.heater_draw")
FROST_BRAZIER_FUEL_DRAW = Num("frost.brazier_fuel_draw")
