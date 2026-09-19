# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# What happened while you were away (D-226): the lines of the return digest.
#
# The key is derived from the event kind: `craft.finished` -> `event-craft-finished`
# (a dot is not allowed in a Fluent message name). The list of kinds is set by the
# server -- `api/commands/world.TOLD` and `TOLD_OF_THE_PLACE` -- and the completeness
# test compares against that very list: an event added to the digest without a line
# fails the tests rather than showing the player `plates.erupted`.
#
# The journal records everything -- every swing of the pick, every posting. Here are
# only the ends of things: what finished, arrived, was found, was settled, was lost.

event-craft-finished = batch ready
event-explore-found = scouting reached a place
event-explore-empty = scouting came back empty: the ground was taken meanwhile
event-travel-arrived = arrived
event-farm-harvested = harvest gathered
event-farm-died = plot died
event-farm-ripened = plot ripe
# A pest (D-299): the summary names the sign, not the trouble -- which
# bottle answers it is known to whoever read the agronomy.
event-farm-struck = trouble on the plot
# The field automaton (D-339): a new trouble; the detail is the node, the trouble is its window's.
event-agro-stalled = field automaton needs attention
event-body-died = body died
event-body-printed = body printed
# Nothing to breathe outside (D-343): the countdown to asphyxia has begun --
# the tanks are dry, or nothing connects the body to them.
event-body-airless = body began to suffocate
# A worn thing the next day's wear will finish (D-343). The detail after the
# separator is the thing itself.
event-gear-wearing_out = worn gear will not last another day
event-mining-collapsed = cave-in at the face
event-market-trade = trade
event-market-order_expired = order withdrawn on expiry
event-market-reservation_lapsed = reservation lapsed
event-city-law_set = the city changed a law
event-city-vote_closed = vote closed
event-justice-case_judged = verdict
event-justice-sanction_applied = sanction imposed
event-bank-debt_withheld = withheld toward the debt
event-utility-cut_off = node cut off for non-payment
event-transport-broke = wagon broke apart
# The sky (D-289): the tanks ran dry under way, or the coast ended.
event-ship-adrift = ship went adrift
event-ship-star_orbit = ship entered orbit round the star
event-ship-lost = ship lost
event-ship-sighted = ship sighted
event-ship-held = hull alongside
event-ship-dock_asked = asked to dock
event-ship-docked_ship = docked hull to hull
event-ship-undocked_ship = undocked from the hull
# The air aboard (D-233, D-340): warnings to the crew. The detail after the
# separator is what ran dry or filled up, or the machine itself.
event-ship-airless = the air aboard is running out
event-ship-machine_dry = ship's machine stopped: its inlet line is dry
event-ship-machine_full = ship's machine stopped: the vessels on its outlet are full
event-ship-machine_unpowered = ship's machine stopped: the hull's batteries are flat
event-ship-machine_unlined = ship's machine stopped: a port has no line
event-automat-no_flare = automat stopped: the node has no flare stack for its vent gas
event-road-laid = road laid
event-deed-sold = deed sold
event-land-reclaimed = the city took its location back
# A city grows by paved ways (D-332): the node at the far end of a paved way
# from the city's land passed to the city. The detail after the separator is
# the node's name, or for a find the word of its facet or biome in the
# reader's language.
event-land-annexed = land at the end of the paved way passed to the city
# A city's land is everything within its border (D-356): a node nobody held
# that the border came to cover passed to the city; one the border drew back
# from is nobody's again. The detail after the separator is the node's word off
# the payload.
event-land-covered = land within the city's border became city land
event-land-uncovered = the city's border drew back: the land is nobody's again
event-city-grant_paid = settlement grant paid
event-estate-site_ready = the build is done: the house waits for its owner

# The earthquake and the warning before it (D-197). They come not to whoever caused
# them but to whoever is standing here: these two have no culprit. Until this wave
# they showed as a raw key -- the only two digest events without a line.
event-plates-warned = the ground is shaking: a harder shock is coming
event-plates-erupted = earthquake

# --- what to look at: the lines of the attention list (`world.digest`) --------
#
# The attention list is not events but unfinished business: where something can
# still be done. The server names the line by key and hands over the values, the
# client draws it; so a vote kind and a goods id become words of the language the
# reader reads in, and not whatever happens to lie in the database.

attention-case = a claim against you: { $claim }
attention-vote-law = vote: { LAW($law) }
attention-vote-kind = vote: { $kind ->
        [election] election of a ruler
        [recall] recall of a ruler
        [charter] charter amendment
        [council] election to the council
       *[law] a law
    }
attention-debt = utility debt: { $node }{ $cut ->
        [true] { " " }— node cut off
       *[false] {""}
    }
attention-reservation = collect the reservation: { NAME($goods) }
event-emission-printed = money was printed into the treasury
