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
event-travel-arrived = arrived
event-farm-harvested = harvest gathered
event-explore-found = scouting: a find
event-explore-empty = scouting: nothing
event-body-died = body died
event-body-printed = body printed
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
event-ship-lost = ship lost
event-road-laid = road laid
event-deed-sold = deed sold
event-land-reclaimed = the city took its location back
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
