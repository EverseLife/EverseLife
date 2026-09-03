# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Words of the place windows: plot, building, storey, berth, floor, storages,
# convoy, gathering — and the row of node objects they open (D-251, wave IV).
#
# Holdings and deeds live here too: the same property, only the bill for it
# arrives in a side tab rather than on the spot.
#
# A value is one line (a break would land in the text); select variants sit
# each on its own line, and those breaks do not land in the text.

## Common to the place windows: one caption — one line, wherever it is shown.

ui-place-cancel = Cancel
ui-place-empty = empty
ui-place-area = { $area } m²
ui-place-slots = equipment slots { $used } of { $slots }
ui-place-materials-have = { $have } of { $need }
ui-place-short = Short of: { $what }

## Land signs: the heading of the gathering window. Keys are node properties, words are ours.

ui-place-sign-woods = Woods
ui-place-sign-stones = Stones
ui-place-sign-meadow = Meadow

## Plot: whose it is, what it is called, who it lets in and what it costs.

ui-place-plot-title = Plot
ui-place-plot-mine = your plot
ui-place-plot-owner = owner { $owner }
ui-place-plot-city = land of { $city }
ui-place-plot-nobody = nobody's
ui-place-plot-gated = { " " }· closed to entry
ui-place-plot-cut-off = { " " }· cut off for non-payment
ui-place-plot-tax = Land tax: { $tax } ₭ a day on the whole plot area — built up or not. The farther from the bioprinter, the lower the rate.
ui-place-plot-upkeep-owner = You pay for the power here: the bill goes by area once a period.
ui-place-plot-upkeep-city = The node is kept by the city{ $named ->
        [true] { " " }{ $city }
       *[false] {""}
    }: energy leaves the city pool, and no bill in money is issued.
ui-place-plot-upkeep-nobody = There is no meter here: the node has no owner, and no one to bill.
ui-place-plot-upkeep-none = There is no city grid here: no power bill is ever issued, things run off a battery.
ui-place-plot-cede = Cede to the city
ui-place-plot-cede-note = The meter moves to the treasury: a city node burns energy from the pool, and no one pays money for it. The equipment stays where it is, but the authorities dispose of it, not you.
ui-place-plot-cede-yes = Yes, cede to the city
ui-place-plot-cede-rule = The land deed is cancelled, the plot becomes the city's. It can only come back by buyout at the list price — like any other.
ui-place-plot-wild = Land outside a city is nobody's and stays that way: the deed is issued by a city, and there is none here. Anyone may work and build here — what is put up belongs to whoever put it up.
ui-place-plot-buy = Buy out for { $price } ₭
ui-place-plot-buy-note = The price comes from the distance to the bioprinter: money to the treasury, a land deed to you.
ui-place-plot-name-hint = what to call this place
ui-place-plot-rename = Rename
ui-place-plot-rename-note = Everyone on the map sees the name; the location key does not change.
ui-place-plot-emblem-label = node emblem on the map
ui-place-plot-emblem-nail = Nail up the emblem
ui-place-plot-emblem-clear = Take down
ui-place-plot-emblem-clear-hint = the node goes back to the emblem of its land
ui-place-plot-about-label = description of the place
ui-place-plot-about-hint = what this place is — everyone who enters will see it
ui-place-plot-about-save = Save the description

## Strips: the land bears them, and the work on them is in the garden.

ui-place-climate-day = Daytime now, { $now } deg. The day runs { $low }-{ $high } deg, light { $light } of 3, rainfall { $rain } of 100.
ui-place-climate-night = Night now, { $now } deg. The day runs { $low }-{ $high } deg, daytime light { $top } of 3, rainfall { $rain } of 100.
ui-place-marking-title = Strips
ui-place-marking-climate = It is { $climate } here: nothing grows in open ground, and heating the node does not change that. Food comes here by ship.
ui-place-marking-name = strip name
ui-place-marking-area = area, m²
ui-place-marking-mark = Mark out
ui-place-marking-marked = Strips marked out: { $count }. The work on them is in the “Garden” window.
ui-place-marking-none = A marked strip opens the “Garden” window: ploughing, sowing, tending and harvest.

## Entrance: close it, open it, and two lists.

ui-place-door-title = Entrance
ui-place-door-rule = Whoever enters disposes of what lies on the ground: a door and a chest are protection, not a rule of “do not take”.
ui-place-door-strike = remove from the list
ui-place-door-open = Open the entrance
ui-place-door-shut = Close the entrance
ui-place-door-is-shut = Closed: the owner and the allow list get in.
ui-place-door-is-open = Open: everyone gets in except the deny list.
ui-place-door-through = { " " }Passing through is always possible — and leaving too: the entrance cannot be closed while a guest is here.
ui-place-door-who = name
ui-place-door-allow-hint = who to let into a closed location
ui-place-door-allow = To the allow list
ui-place-door-allowed = We let in:
ui-place-door-allowed-shut = No one yet: only you get in.
ui-place-door-allowed-open = It will come in handy once you close the entrance.
ui-place-door-bar-hint = who not to let in at all
ui-place-door-bar = To the deny list
ui-place-door-barred = We keep out:
ui-place-door-barred-none = The deny list beats the allow list: whoever is named here will not get in.

## Founding a city: the entry bar is buildings, not coin.

ui-place-foundation-title = Founding a city
ui-place-foundation-name = city name
ui-place-foundation-found = Found a city
ui-place-foundation-ready = The land passes to the city, the founder gets full authority.
ui-place-foundation-threshold = The entry bar is buildings, not coin.

## Building: construction, repair, demolition.

ui-place-house-title = Building
ui-place-house-default = House
ui-place-house-summary = { $area } m² over { $floors } fl. on { $ground } m² of land · ground-floor slots { $used } of { $slots }
ui-place-house-condition = condition
ui-place-house-decay = decay ·
ui-place-house-decay-rate = −{ $decay }%/d
ui-place-house-decay-hint = decay runs every day; mending is in this same window
ui-place-house-storeys = Storeys above the ground floor: { $count }. A stair leads to each, and each has its own floor and its own equipment slots — they sit next to it on the map.
ui-place-house-none = No house — only a yard. Work stations and furniture go into a house: build one first.
ui-place-house-site = building site: { $area } m² over { $floors } fl.
ui-place-house-site-kind = { " " }({ $kind })
ui-place-house-site-note = the materials are already in the wall
ui-place-house-site-label = building site
ui-place-house-kind-hint = building type
ui-place-house-kind-option = { $kind } · storey ×{ $growth } · decay { $decay }%/d
ui-place-house-footprint = footprint, m²
ui-place-house-floors = storeys
ui-place-house-plan = { $area } m² × { $floors } fl. = { $living } m² of living space, and every storey above the ground floor becomes a separate place with a stair. { $free } m² of yard free, nothing under { $least } m² is built. Height is not capped — the estimate pays for it.
ui-place-house-counting = The estimate counts itself while you choose.
ui-place-house-term = Work takes { $hours } h; { $kind }.

## House repair: mended with what it was built of.

ui-place-repair-estimate = Estimate the repair
ui-place-repair-whole = The house is sound: there is nothing to mend.
ui-place-repair-condition = Condition { $condition }%. At zero the house collapses along with whatever stands in the yard.
ui-place-repair-do = Repair
ui-place-repair-going = A repair is already under way.
ui-place-repair-term = Work takes { $hours } h; mended with what it was built of.

## Demolition: the yard empties before the demolition, not after.

ui-place-demolition-estimate = Estimate the demolition
ui-place-demolition-going = Nothing to demolish while building is under way: wait for it to finish.
ui-place-demolition-rule = Demolition is work: some of the materials come back, the rest breaks in the taking apart.
ui-place-demolition-back = { $amount } comes back
ui-place-demolition-do = Demolish { $area } m²
ui-place-demolition-hint = the work takes time, the materials arrive at the end
ui-place-demolition-blocked-hint = the yard empties before the demolition, not after
ui-place-demolition-blocking = First: { $what }
ui-place-demolition-term = Work takes { $hours } h. The plot will be left bare.

## Stations and furniture: what stands in the building and what can be put there.

ui-place-equipment-stations = Work stations
ui-place-equipment-stations-rule = One person works at a work station: while a batch runs, it is not given to a second.
ui-place-equipment-furniture = Furniture
ui-place-equipment-furniture-rule = Furniture makes the place liveable: a bed for faster sleep, a chest for storage. No one works at it.
ui-place-equipment-quality = { " " }· quality { $quality }
ui-place-equipment-condition = { " " }· cond. { $condition }
ui-place-equipment-charge = charge { $charge } · charged in “holdings”
ui-place-equipment-busy-mine = in use by you
ui-place-equipment-busy = in use
ui-place-equipment-free = free
ui-place-equipment-take = Take
ui-place-equipment-take-hint = take into your hands
ui-place-equipment-drop-station = drag a station here to put it in the building
ui-place-equipment-drop-furniture = drag furniture here to furnish the building
ui-place-equipment-place = Put down:
ui-place-equipment-place-hint = put down in the building
ui-place-equipment-no-room = no free slots in the building
ui-place-equipment-no-room-hint = no room in the building: build more or carry the surplus out
ui-place-equipment-slots = { " " }Each takes { $area } m² of the building: slots { $used } of { $slots }.

## Floor and ground: what lies here, and where to put it.

ui-place-floor-title = On the floor
ui-place-ground-title = On the ground
ui-place-floor-taken = { $used } of { $area } m² taken
ui-place-floor-cargo = { " " }· cargo { $mass } kg
ui-place-floor-gear = { " " }· equipment { $count }
ui-place-floor-drop = drag an item here to put it on the floor
ui-place-ground-drop = drag an item here to put it on the ground
ui-place-floor-mass = · { $mass } kg
ui-place-floor-pick = Pick up
ui-place-floor-install = Install
ui-place-floor-install-hint = lift it off the floor and put it up in the building: takes a place
ui-place-floor-pick-hint = pick up into your hands — as much as you can carry; the row can be dragged down too
ui-place-floor-passing = You are here in passing: another's closed location does not give you its floor.
ui-place-floor-rule = What lies about takes up area; in a chest it does not. A collapsing house buries whatever lies under its roof.
ui-place-ground-rule = What lies about takes up yard area — what is left of the plot around the house. If the house falls, this survives.
ui-place-floor-guest = Another's place, but what lies about is taken by anyone who was let in here.

## Storages: chest and tank. Liquid lives only in a vessel.

ui-place-chest-rule = A house keeps what your hands cannot carry away; a full chest is not carried off.
ui-place-chest-taken = { $mass } of { $capacity } kg taken
ui-place-chest-foreign = Another's storage: what is inside is none of your business.
ui-place-chest-drop = drag an item here to put it into the storage
ui-place-chest-take = Take
ui-place-chest-take-hint = take into your hands — as much as you can carry; the row can be dragged down too
ui-place-tank-rule = A vessel takes liquid only, and liquid lives only in a vessel: into a tank from a canister, and out of a tank into a canister.
ui-place-tank-filled = { $mass } of { $capacity } kg poured in
ui-place-tank-foreign = Another's tank: what is inside is none of your business.
ui-place-tank-pour-out = Out
ui-place-tank-pour-out-hint = pour out into a canister — as much as fits and as much as you can carry
ui-place-tank-pour-in = Pour in from “{ $goods }”
ui-place-tank-need-canister = A canister in hand is needed: liquid is not carried in bare palms.

## Storey: a room of your own upstairs, and the house is downstairs.

ui-place-storey-title = Storey
ui-place-storey-which = { " " }· { $floor } of { $floors }
ui-place-storey-name = storey name
ui-place-storey-rename = Rename the storey
ui-place-storey-rule = The house stands on the plot below: type, condition, repair and demolition are down there. If it collapses, the storey falls with it, and everything that stood and lay on it.

## Berth: a room on board. There is no land under it.

ui-place-berth-title = Berth
ui-place-berth-name = berth name
ui-place-berth-rename = Rename the berth

## Naming a place: one button for a storey and for a berth.

ui-place-rename-save = Name it

## Convoy: cargo rides in the hold, not in your hands.

ui-place-convoy-title = Convoy
ui-place-convoy-harnessed = harnessed:
ui-place-convoy-hold = hold
ui-place-convoy-hold-amount = { $mass } of { $capacity } kg
ui-place-convoy-speed = · speed ×{ $speed } · cond. { $condition }
ui-place-convoy-drop = drag an item here to load it into the hold
ui-place-convoy-unload = Unload
ui-place-convoy-unload-hint = unload into your hands — as much as fits; the row can be dragged down too
ui-place-convoy-empty = the hold is empty
ui-place-convoy-unharness = Unharness
ui-place-convoy-unharness-rule = The convoy stays here with its cargo; it does not go off-road.
ui-place-convoy-harness = Harness up:
ui-place-convoy-cart = { $capacity } kg · speed ×{ $speed }
ui-place-convoy-no-capacity = the vault named no carrying capacity
ui-place-convoy-rule = Cargo rides in the hold, not in your hands.

## Gathering by the land sign: felling, breaking, mowing.

ui-place-gather-qty = how much to gather
ui-place-gather-needs = { $needs } needed; what is done goes to “doings”
ui-place-gather-barehanded = bare-handed, and slower for it; what is done goes to “doings”
ui-place-gather-missing = needed: { $needs }
ui-place-gather-rule = A batch takes time, and what is done is collected in “doings”. Deadwood and other things lying about are in “Foraging”.

## Forerunner reactor: it heats the city and in its time goes out.

ui-place-reactor-title = Forerunner legacy
ui-place-reactor-rule = The reactor heats the city and feeds the spaceport beacon with no fuel and no people — but its output falls and in its time reaches zero. After that the city is held up by those who live in it: their own generation, their own power plant. When the planet's last working spaceport goes dark, there is nowhere left to land, and the planet is lost.
ui-place-reactor-when = goes out
ui-place-reactor-out = out
ui-place-reactor-days = { $days } d
ui-place-reactor-already = already out
ui-place-reactor-in = in { $days } d
ui-place-reactor-warning = The reactor is running out: without generation of its own the city will go cold, and the spaceport will go dark with it.

## The row of node objects: the tile's name, what it does and what its window is for.

ui-stand-nothing = Nothing stands here — only roads.
ui-stand-busy-mine = in use by you
ui-stand-busy = in use
ui-stand-free = free
ui-stand-quality = qual. { $quality }
ui-stand-condition = cond. { $condition }
ui-stand-mine = Mine face
ui-stand-mine-going = a session is under way
ui-stand-mine-vein = vein: { $goods }
ui-stand-mine-about = The mine face window: go down into the vein and cut, rock by rock.
ui-stand-batch = batch · { $goods }
ui-stand-bench-about = The “{ $machine }” work station window: batches by recipe, repairs and attempts without a recipe.
ui-stand-trade-kitchen = { " " }Food is cooked here.
ui-stand-trade-factory = { " " }Machines work by themselves here.
ui-stand-trade-nursery = { " " }Animals are bred here.
ui-stand-trade-fuel-plant = { " " }Ship fuel is distilled here.
ui-stand-trade-mint = { " " }The city's coin is minted here.
ui-stand-gather-about = Gathering by the land sign: work by hand right on the spot.
ui-stand-rig = Rig
ui-stand-rig-in-hands = in hand: set it on a vein
ui-stand-rig-about = The rig window: set it on a vein and drill deep.
ui-stand-console-about = The bridge window: this ship's flight map, lift to orbit, course and landing.
ui-stand-console-aground = works only aboard a ship
ui-stand-ground-console-about = The ground console window: your ships wherever they are — flight map, lift, course, landing and turnaround.
ui-stand-ship = Ship
ui-stand-ship-about = The ship window: thrust against mass, oxygen, name and blueprint — the berth layout.
ui-stand-yard-about = The shipyard window: lay down a hull and watch the mooring.
ui-stand-farm = Garden
ui-stand-farm-strips = { $count ->
        [one] one strip
       *[other] strips: { $count }
    }
ui-stand-farm-about = The garden window: ploughing, sowing, daily tending and harvesting the strips.
ui-stand-forage = Foraging
ui-stand-forage-found = found: { $goods } ×{ $units }
ui-stand-forage-searching = a search is under way
ui-stand-forage-area = { $area } m² of bare land
ui-stand-forage-about = The foraging window: searching bare land for anything useful.
ui-stand-library = Library
ui-stand-library-about = The library window: take recipes and give your own.
ui-stand-hall = Administration
ui-stand-hall-about = The administration window: citizenship, power, courts and city law.
ui-stand-market-mine = your goods: { $count }
ui-stand-market-about = The market window: the order book, buying, selling and your own goods in the terminal.
ui-stand-convoy = Convoy
ui-stand-convoy-hold = hold { $mass } of { $capacity } kg
ui-stand-convoy-standing = standing: { $goods }
ui-stand-convoy-about = The convoy window: harness up and carry more in the hold than your hands will take.
ui-stand-floor = floor { $used } / { $area } m²
ui-stand-berth = Berth
ui-stand-berth-about = The berth window: stations and furniture on board, the berth floor with things on it, and the berth's name.
ui-stand-storey = Storey
ui-stand-storey-about = The storey window: stations and furniture on this storey, its floor with things on it, and the storey's name.
ui-stand-house = Building
ui-stand-house-size = { $area } m² over { $floors } fl.
ui-stand-house-floor = { " " }· floor { $used } / { $area } m²
ui-stand-house-condition = { " " }· condition { $condition }%
ui-stand-house-building = under construction
ui-stand-house-absent = not built
ui-stand-house-about = The building window: construction, repair, demolition and the layout of stations and furniture — and what lies on the building's floor and in its storages.
ui-stand-reactor = Forerunner reactor
ui-stand-reactor-about = The Forerunner reactor window: how much energy the city has left.
ui-stand-plot = Land
ui-stand-plot-cut-off = cut off for non-payment
ui-stand-plot-gated = entry closed
ui-stand-plot-price = for sale at { $price } ₭
ui-stand-plot-wild = nobody's land
ui-stand-plot-ground = lying { $used } / { $area } m²
ui-stand-plot-owner = owner { $owner }
ui-stand-plot-city = city { $city }
ui-stand-plot-about = The land window: running the location — the node's name, emblem and description, access, buyout and founding a city — and what lies on the ground.
ui-stand-plot-bare-about = The land window: what lies here on the ground — put down and pick up.

## Holdings: the grid, batteries, bills and land deeds.

ui-holdings-stale = The server did not answer: what is below is an old reading. Press “refresh”.
ui-holdings-grid = City grid
ui-holdings-grid-rule = The treasury keeps up city buildings: the energy they burn is the city's expense, not the visitor's.
ui-holdings-grid-asking = Polling the grid…
ui-holdings-grid-pool = { $city }: { $stored } in the pool · tariff { $tariff } ₭ per 100
ui-holdings-grid-none = There is no city grid here: outside a city things run off a battery, and the battery is charged in a city.
ui-holdings-batteries = Batteries
ui-holdings-batteries-none = There is no battery: energy is either in the city pool or in a battery.
ui-holdings-in-hands = in hand
ui-holdings-here = standing here
ui-holdings-charge = Charge
ui-holdings-charge-hint = fill to the top at the tariff
ui-holdings-charge-asking = the grid is still being polled
ui-holdings-charge-no-grid = there is no grid here
ui-holdings-title = Holdings and bills
ui-holdings-area = { $area } m²
ui-holdings-cut-off = · cut off
ui-holdings-per-period = { $cost } ₭ / period
ui-holdings-no-grid = no grid
ui-holdings-debt = debt { $amount } ₭
ui-holdings-pay = Pay
ui-holdings-home = Go home
ui-holdings-home-hint = go to this node; the route builds itself
ui-holdings-home-here = you are here
ui-holdings-no-way = There is no direct way there.
ui-holdings-bill-rule = The bill is counted by area — light, heat, ventilation. Unpaid, the node is cut off, and its work stations stand idle until the debt is closed. The engine may not take a node away over a debt: that is a court's decision.
ui-holdings-debt-total = { " " }Debts now stand at { $amount } ₭.
ui-holdings-deeds = Deeds
ui-holdings-deeds-rule = A deed is an electronic document: it lives in the Net, outlives the body and is sold from here, even from the road. Title to the plot passes with it.
ui-holdings-deeds-none = You have no deeds. A deed comes with a plot: buy out or take up land and the holding is put on paper.
ui-holdings-deed-area = · { $area } m²
ui-holdings-deed-sale = for sale at { $price } ₭
ui-holdings-deed-sale-to = { " " }· for { $who }
ui-holdings-deed-not-sold = not for sale
ui-holdings-price = price, ₭
ui-holdings-price-hint = contract price, ₭
ui-holdings-to-whom = to whom (empty — anyone)
ui-holdings-sell = Sell
ui-holdings-unsell = Withdraw from sale
ui-holdings-market = Deeds for sale
ui-holdings-deed-market-area = · { $area } m² · from { $owner }
ui-holdings-buy = Buy

## The construction site (D-266): materials by contribution, the build by time and body.

ui-place-site-lay = Lay out a site · { $area } m² on { $floors } fl.
ui-place-site-lay-hint = the footprint is taken at once; materials are carried to the site by parts
ui-place-site-title = site: { $area } m² on { $floors } fl.
ui-place-site-gathering = gathering materials
ui-place-site-brought = brought { $brought } of { $needed }
ui-place-site-need = { $need } on the bill
ui-place-site-in-hands = in hand { $have }
ui-place-site-add = Bring
ui-place-site-add-hint = bring from the hands: as much as the bill still takes
ui-place-site-start = Start the build · { $stamina } stamina
ui-place-site-start-hint = the bill is full: the build takes time and the owner's stamina
ui-place-site-waiting = waits for the whole bill to be brought
ui-place-site-building = under construction
ui-place-site-ready = the house is ready: finish it
ui-place-site-finish = Finish the build
ui-place-site-owner-only = the site's owner starts and finishes
