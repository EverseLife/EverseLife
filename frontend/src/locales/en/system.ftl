# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The words said not by a panel but by the client itself: the link to the
# server, the vocabulary of the planets and the alpha debug window (D-251,
# wave IV).
#
# What lives here is whatever has no window of its own. A refusal from the
# transport — “the server is not answering” — surfaces in the refusal strip of
# any panel: `api` says it, and the one where the button was pressed shows it.
# The word for the built-up layer belongs to the planet and not to the map,
# though it is the map, the overview and the node menu that read it. The alpha
# widget will go whole, along one seam, and its words go with it.
#
# What is not here and will not be — the names of things, qualities, buildings
# and planets: the vault's rename table holds them, and they are taken by the
# functions `NAME`, `KIND`, `PLANET`. A second list of the same names would
# drift from the first in silence.
#
# The rules are the same as in `ui.ftl`: a value is one line (a break would
# land in the text); the variants of a select go one per line.

## The link breaks: what `api` puts into a refusal when the server is not heard.
#
# The prefix is `ui-wire-`, not `ui-net-`: in this world the “Net” is the
# social network (`Net.tsx`, `talk.ftl`), and one word for the wire and for it
# would muddle both.

ui-wire-no-answer = the server is not answering
ui-wire-session-closed = the session is closed
ui-wire-no-session = there is no session
ui-wire-timed-out = the server did not answer

## Broad rights in the city (D-155). A narrow right is called by the name of its own law.

ui-power-laws = all laws
ui-power-charter = charter
ui-power-treasury = treasury
ui-power-offices = offices
ui-power-land = plots
ui-power-dashboard = city dashboard
ui-power-justice = court
ui-power-citizens = citizens
ui-power-channel = city channel

## What the built-up layer is called on each planet (D-230).
#
# The name of the planet itself does not move in here: the rename table knows
# it, and `PLANET($planet)` takes it from there — in any language and without
# a second list.

ui-planet-name = { PLANET($planet) }

ui-city-word-terra = city
ui-city-word-terra-in = in the city
ui-city-word-aquatica = commune
ui-city-word-aquatica-in = in the commune
ui-city-word-pyroxis = camp
ui-city-word-pyroxis-in = in the camp
ui-city-word-aurora = abandoned city
ui-city-word-aurora-in = in the abandoned city

## The alpha debug window (D-229): printing things and cutting a term short.

ui-alpha-name = Alpha
ui-alpha-open-title = the alpha debug window: printing things and finishing terms early
ui-alpha-fold = fold
ui-alpha-what = what to print
# The example in an empty field is a name out of the catalog, not written here again.
ui-alpha-what-hint = { NAME("iron_ore") }
ui-alpha-amount = how much
ui-alpha-quality = quality
ui-alpha-no-quality = no quality
ui-alpha-print = Print
ui-alpha-finish = Finish now
ui-alpha-printed = printed: { $goods } · { $amount }
ui-alpha-hurry-nothing = nothing to hurry: nothing is under way
ui-alpha-hurried = term pulled in: { $kinds }
ui-alpha-note-print = It prints into the hands and into the ledger: the thing carries the reason “alpha”, and everything the world did not earn can be found by it.
ui-alpha-note-hurry = “Finish now” moves the term of what you have already started — a survey, a leg, work, building, plowing, a flight and the printing of a body: the ordinary handler finishes them, the same one as after an honest wait.

## The kinds of term the alpha can pull in.

ui-alpha-job-explore-survey = survey
ui-alpha-job-travel-leg = leg
ui-alpha-job-craft-batch = batch
ui-alpha-job-ship-keel = keel laying
ui-alpha-job-ship-flight = flight
ui-alpha-job-build-finish = building
ui-alpha-job-build-demolish = demolition
ui-alpha-job-build-repair = repair
ui-alpha-job-farm-plow = plowing
ui-alpha-job-body-print = body printing

## The city hall: what is left over from the walk through the panels.

ui-admin-lot-area = { $area } m²
