# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The city and everything settled inside it: plots, treasury, offices, charter,
# citizenship, votes, court, location doors, the Net and talk
# (D-089, D-155, D-160, D-163, D-166, D-204, D-222).
#
# Two incompatible habits of Fluent, and the file looks like this because of
# them:
#   — a line break in the TEXT of a value survives into the refusal, so text is
#     written on one line, however long it turns out;
#   — the variants of a select ({ $x -> ... }) must each stand on their own
#     line, and those breaks never reach the text.
#
# Names of nodes, cities and people are words already, they travel as a plain
# { $arg }. NAME($id) is only for the vault's stable keys.

# --- city plots (D-089) -------------------------------------------------------

city-land-not-civic = this is not a city plot
city-land-taken = the plot is already someone's
city-land-dead = a dead body disposes of no plots
city-land-cede-on-foot = a plot is handed over on foot: walk up to it
city-land-not-yours = the plot is not yours: what you give the city is your own
city-land-not-city-land = this is not city land: there is no one here to hand it to
city-land-city-missing = the plot is attached to a city that does not exist
city-land-deed-on-sale = the deed to the plot is up for sale: withdraw it from the market, or the buyer pays for what is not yours
city-land-debt = the node carries a debt of { $debt } ₭: close the account first, the city takes on no one else's debts

# --- treasury (D-155) ---------------------------------------------------------

city-treasury-zero = spending zero is not spending
city-treasury-short = the treasury holds { $have } ₭, and { $need } ₭ is needed

# --- founding a city (D-023, D-098, D-159) ------------------------------------

city-found-dead = a dead body founds no cities
city-body-off-node = the body is off the node
city-found-planet-only = a city is laid down on a node of the planet: none is founded inside someone else's building
city-found-foreign-land = this plot is someone else's: no city is laid down on it
city-found-already-city = a city stands here already
city-found-already-civic = this is city land already
city-found-not-ready = the city is short of: { $missing }. The threshold is buildings, not coin

# The roles no city goes without (D-023, D-098, D-159). Keys, not words: the
# refusal names them and so does the founding window, and both need the
# reader's language.
city-role-bioprinter = bioprinter
city-role-administration = administration
city-role-market = market
city-role-power = power source
city-found-no-name = a city must have a name
city-found-name-too-long = the city's name is longer than { $limit } characters: the card, the chronicle and the official channel all have to carry it
city-found-name-taken = a city named “{ $name }” already stands: a city's name becomes the name of its channel in the Net, and no two channels there share one
city-founder-exists = “{ $city }” already has a founder

# --- power and offices (D-155, D-164, D-231) ----------------------------------

city-no-power = no “{ $power }” right in “{ $city }”: power is an office, not an intention
city-hall-dead = a city is governed by a living body only
city-hall-not-territory = this is not the territory of “{ $city }”: power is exercised at home
city-hall-absent = there is no administration here: the city takes its decisions in it
city-hall-cut-off = the administration is cut off for non-payment: without it the city is blind and mute
city-hall-frozen = “{ $node }” has frozen through: the administration is shut until the node is warmed
city-powers-not-own = you cannot hand over what you do not hold yourself: { $extra }
city-office-no-powers = an office without powers is not an office
city-office-other-city = the office is not of this city
city-founder-not-dismissed = the founder is removed by the charter, not by order: see `ruler_recall` and `charter.silence_days`

# --- laws and charter (D-163) -------------------------------------------------

city-no-such-law = no such code law: { $law }
city-no-such-question = no such charter question: { $question }
city-no-such-option = no such option: { $option }
city-option-requires = option “{ $option }” requires an answer to “{ $requires }”
city-charter-sealed = the charter of this city does not change: it decided so itself
city-about-too-long = the city's word is longer than { $limit } characters: a card is read in ten seconds

# --- citizenship (D-160, D-184) -----------------------------------------------

city-already-citizen-here = you are a citizen of this city already
city-citizenship-is-one = citizenship is one per person: leave the former city first
city-by-invitation-only = this city admits by invitation only: wait for the call of its power
city-already-citizen = { $who } is a citizen already
city-no-application = there is no application from this person
city-already-in-a-city = { $who } belongs to a city already
city-not-a-citizen-anywhere = you belong to no city
city-bound-by-printing = citizenship came as a condition of printing and holds until { $until } UTC. You took that term when you chose the city's door
city-not-a-citizen-here = { $who } is not a citizen of this city

# --- votes and council (D-163, D-164) -----------------------------------------

vote-is-an-election = this is an election: here the vote goes to a person, not “for” or “against”
vote-is-a-poll = this is not an election: here the vote is “for” or “against”
vote-closed = the vote is closed: a late voice changes no outcome
vote-election-closed = the election is closed
vote-no-voice-in-poll = no voice: this vote is decided by { $voters ->
        [council] the council members
       *[citizens] the citizens
    }
vote-no-voice-in-election = no voice: this election is decided by { $voters ->
        [council] the council members
       *[citizens] the citizens
    }
vote-nominee-needs-voice = whoever stands has a voice in this election: { $voters ->
        [council] the council members
       *[citizens] the citizens
    }
vote-ruler-not-elected = the city's charter did not give power to elections: the ruler is settled otherwise
vote-no-recall = the city's charter allows no recall of the ruler
vote-no-ruler-to-recall = there is no one to recall: the city has no ruler
vote-not-an-election = this is not an election: there is nothing to stand for
vote-nominate-while-open = you stand while the election runs
vote-not-nominated = { $who } did not stand
vote-no-council = the charter of this city keeps no council
vote-council-full = the council has { $seats } seats, and all are taken: free a seat first
vote-council-not-appointed = the seats of this council are not appointed: the charter gave them to elections
vote-council-needs-voice = the council seats citizens who meet the charter's qualification
vote-council-not-elected = the charter of this city does not elect the council

# --- court (D-117, D-166) -----------------------------------------------------

justice-empty-claim = a claim without substance is not a claim
justice-self-claim = no one files against himself
justice-too-late = more than { $days } days have passed since the event: the term has run out
justice-cannot-pay-fee = the court fee is { $fee } ₭, and the account holds less
justice-case-judged = the case has been judged already
justice-case-nowhere = the case points nowhere
justice-not-a-judge = judging belongs to whoever the city gave the justice right
justice-no-such-sanction = no such sanction: { $sanction }
justice-unenforceable = the engine does not enforce “{ $sanction }” yet: a sentence without enforcement is worse than no sentence at all
justice-defendant-gone = the defendant is gone
justice-not-a-prison = “{ $node }” is not a penal colony of this city
justice-many-prisons = the city has several penal colonies: the court names the one to send to

# --- land and building (D-089, D-192, D-198, D-220, D-247) --------------------

estate-unknown-kind = “{ KIND($kind) }” is not a building type; you build from: { KINDS($kinds) }
estate-build-dead = a dead body does not build
estate-build-on-foot = you build on foot: walk up to the plot
estate-build-not-on-storey = this is a storey, not a plot: a house is built on the ground — go down to the yard
estate-build-house-stands = a house already stands on the plot, or a site is laid: a second is not laid beside it
estate-build-not-yours = the plot is not yours: you build at home
estate-build-no-floors = a house without floors is a pit
estate-build-not-on-pyroxis = nothing is built on Pyroxis: quakes bring structures down faster than they go up. Housing here is aboard a ship
estate-build-too-small = a footprint under { NUMBER($smallest, maximumFractionDigits: 0) } m² is a shed, not a building: { NUMBER($area, maximumFractionDigits: 0) } asked
estate-build-no-room = the plot is { NUMBER($plot, maximumFractionDigits: 0) } m², free { NUMBER($free, maximumFractionDigits: 0) }{ $started ->
        [true] , under construction { NUMBER($going, maximumFractionDigits: 0) }
       *[false] {""}
    }: another { NUMBER($area, maximumFractionDigits: 0) } does not fit
estate-build-already-queued = the build is queued already
estate-build-job-nowhere = build { $job } points nowhere
# The construction site (D-266): its phases and their refusals.
estate-site-nowhere = site { $site } points nowhere
estate-site-not-here = the site is on another plot: walk to it
estate-site-not-gathering = the site is no longer gathering: the build is under way or done
estate-site-not-needed = "{ NAME($goods) }" is not on the bill
estate-site-material-full = { NAME($goods) }: brought in full
estate-site-nothing-to-add = nothing to add: less than one piece
estate-site-not-yours = not your site: the owner starts and finishes
estate-site-short = not everything is in: short by { NUMBER($short, maximumFractionDigits: 1) } -- { NAME($goods) }
estate-site-no-strength = not enough strength: the build takes { NUMBER($need, maximumFractionDigits: 1) } stamina, you have { NUMBER($have, maximumFractionDigits: 1) }
estate-site-already-started = the build has already started
estate-site-not-ready = the house is not ready yet: the build is under way

estate-deed-not-yours = the deed is not yours: what you sell is your own
estate-deed-not-on-sale = the deed is not up for sale
estate-deed-own = your own deed is not for buying
estate-deed-addressed = the contract is addressed: the deed is promised to another
estate-deed-site-open = there is a construction site on the plot: the land is not sold under it until the house is up
estate-deed-too-dear = the deed costs { $price } ₭, and the account holds { $have } ₭

estate-demolish-dead = a dead body does not demolish
estate-demolish-on-foot = you demolish on foot: walk up to the plot
estate-demolish-not-yours = the plot is not yours: what you demolish is your own, and someone else's city building is taken down by court decision, not by a button
estate-demolish-nothing = nothing to demolish: there is no building on the plot
estate-demolish-blocked = { $why }

# What stands in the way of a demolition (D-197). Keys, not phrases: this list
# is read by the refusal and by the window that greys the button out — both in
# the language of whoever is looking.
estate-blocker-equipment = there is equipment in the building ({ $count }): work stations and furniture are taken out before the demolition — after it they have nowhere to stand
estate-blocker-overloaded = { NUMBER($floor, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg on the floor and { NUMBER($yard, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg in the yard, and the plot holds { NUMBER($holds, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg: cart the excess away or pack it into a chest
estate-blocker-building = a build is going on here: wait for its end first
estate-blocker-demolishing = a demolition is going on already: it is not ordered twice
estate-demolish-already-queued = the demolition is queued already
estate-demolish-job-nowhere = demolition { $job } points nowhere

estate-land-no-price = the city has set no land price: code law `land_price` is empty
estate-land-buy-dead = a dead body does not buy
estate-land-buy-on-foot = a plot is bought on foot: walk up to it
estate-land-taken = the plot is already someone's
estate-land-not-civic = this is not city land: outside a city it is neither sold nor claimed, yet anyone may work and build there
estate-land-not-vacant = the node is not empty: the price list sells neither buildings nor the city's veins
estate-land-city-missing = the node is attached to a city that does not exist
estate-land-permit = “{ $city }” does not sell land to everyone: code law build_permit is “{ $permit }”. Join the citizens
estate-land-too-dear = the plot costs { $price } ₭, and the account holds { $have } ₭

estate-about-dead = a dead body describes nothing
estate-about-on-foot = the plot has to be reached: a description is written on the spot
estate-about-not-yours = the plot is not yours: the owner gives the description, and on city land the power with the right to plots
estate-about-too-long = the description is longer than { $limit } characters

estate-emblem-dead = a dead body nails up no emblems
estate-emblem-on-foot = the plot has to be reached: an emblem is nailed up on the spot
estate-emblem-not-yours = the plot is not yours: the owner sets the emblem, and on city land the power with the right to plots
estate-emblem-unknown = there is no such emblem: you choose from the offered ones

estate-rename-dead = a dead body renames nothing
estate-rename-on-foot = the plot has to be reached: a sign is nailed up on the spot
estate-rename-not-yours = the plot is not yours: the owner gives the name, and on city land the power with the right to plots
estate-rename-no-name = a plot must have a name
estate-rename-too-long = the name is longer than { $limit } characters

estate-repair-dead = a dead body does not repair
estate-repair-on-foot = you repair by hand: walk up to the plot
estate-repair-not-yours = the plot is not yours: you repair at home
estate-repair-under-way = a repair is going on already: it is not ordered twice
estate-repair-nothing = nothing to repair: there is no building on the plot
estate-repair-intact = the house is sound: there is nothing in it to repair
estate-repair-already-queued = the repair is queued already
estate-repair-job-nowhere = repair { $job } points nowhere

# --- the door of a location (D-198, D-204, D-247) -----------------------------

access-door-downstairs = the door belongs to the place, not to a storey inside it: the way in is closed downstairs, on the plot
access-no-holder = { $land ->
        [city] this is city land: entry to it is settled by citizenship and toll, not by the door of a location
       *[wild] this land has no holder: outside a city no doors are set
    }
access-not-yours = the location is not yours: the door is the holder's to command
access-self-in-list = you are not kept on your own lists: the holder always enters
access-barred = “{ $node }” is someone else's location, and the holder does not let you in. You may pass through it, but not stop; a dispute over entry is settled by a claim

# --- the Net (D-222) ----------------------------------------------------------

net-no-body = without a body you only read the Net: there is nothing to write with
net-empty = { $what ->
        [letter] the letter is empty
        [name] the name is empty
       *[post] the post is empty
    }
net-too-long = { $what ->
        [letter] the letter
        [name] the name
       *[post] the post
    } is longer than { $limit } characters
net-letter-to-self = a letter to yourself is a diary, not the Net
net-not-your-thread = this is not your correspondence
net-about-too-long = the description is longer than { $limit } characters
net-channel-exists = channel “{ $channel }” exists already
net-no-such-channel = no such channel
net-own-channel-kept = you do not unsubscribe from your own channel
net-city-channel-kept = the channel of your own city is always read: this is citizenship, not a subscription
net-cannot-post = { $channel ->
        [own] this channel is written in by its author
       *[city] the city channel is written in with the “channel” right
    }

# --- talk (D-043) -------------------------------------------------------------

chat-dead-are-silent = the dead do not talk
chat-nothing-to-say = there is nothing to say
chat-too-long = the line is longer than { $limit } characters
chat-group-not-here = this circle is not here

# The one line the server itself says into a room: a thing passed from hand to
# hand is seen by everybody standing here. The line is a single record for the
# whole room, so it is written in the world's default language, like the
# chronicle.
chat-hands-over = hands over { $named ->
        [true] to { $who }
       *[false] —
    }: { NAME($goods) }{ $counted ->
        [true] { " " }×{ $amount }
       *[false] {""}
    }

## Emission by signatures (D-270)

emission-not-capital = only the capital prints money, and “{ $city }” is not it
emission-not-positive = the sum to print must be greater than zero
emission-proposal-open = a proposal is still collecting signatures: { $money } ₭; a second does not stand beside it
emission-no-proposal = there is no such emission proposal
emission-proposal-closed = the proposal is closed already: the money was printed or the term ran out
emission-proposal-expired = the proposal's term ran out; no more signatures are collected under it
emission-already-signed = your signature already stands under this proposal
cmd-no-such-proposal = there is no such proposal
