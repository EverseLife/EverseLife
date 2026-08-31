# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# What the command layer says: parsing the request and finding what it is about.
# The rules of the world refuse in their own words -- those live in engine/*.ftl.
#
# A message value is one line: a line break in the Fluent source lands in the
# text of the refusal. The variants of a select { $x -> ... } go one per line,
# and those breaks do not land in the text.

## Who is asking

cmd-no-live-body = no living body
cmd-no-live-body-name-city = no living body: name the city outright
cmd-identity-gone = the identity is gone
cmd-identity-not-found = identity not found
cmd-body-off-node = the body is not at a node
cmd-no-such-identity = no such identity
cmd-no-identity-named = no identity named “{ $name }”
cmd-person-not-here = no such person here

## Account

cmd-old-password-wrong = the old password is wrong
cmd-password-wrong = the password is wrong
cmd-passwords-differ = the passwords do not match
cmd-email-taken = this email is already taken
cmd-account-without-identity = the account has no identity: registration was never finished

## What is not there: asked for something that does not exist, or not here

cmd-no-such-node = no node “{ $node }”
cmd-no-such-node-plain = no such node
cmd-no-such-edge = no such edge
cmd-no-such-thing = no such thing
cmd-no-such-item = no such item
cmd-item-not-yours = you do not have that thing
cmd-no-such-storage = no such storage
cmd-no-such-vessel = no such vessel
cmd-no-such-vein = no such vein
cmd-no-such-rig = no such rig
cmd-no-such-ship = no such ship
cmd-no-such-plot = no such plot
cmd-no-such-nursery = no such nursery
cmd-no-such-variety = no such variety
cmd-no-such-deed = no such deed
cmd-no-such-order = no such listing
cmd-no-such-reservation = no such reservation
cmd-no-such-book-order = no such order
cmd-no-such-loan = no such loan
cmd-no-such-case = no such case
cmd-no-such-office = no such office
cmd-no-such-work-order = no such work order
cmd-no-such-vote = no such vote in this city
cmd-no-city-here = no city here: beyond the walls there are no laws

## Parsing the request

cmd-not-your-job = the task is not yours
cmd-session-not-open = the session is not open
cmd-session-gone = the session is gone
cmd-need-hello = hello first
cmd-unknown-command = no such command: { $cmd }
cmd-since-not-a-number = since must be a number
cmd-composition-shape = the composition is given as “thing: how many” pairs
cmd-area-and-storeys-from-one = area and storeys count from one
cmd-need-layout = a layout is needed: node key to cell
cmd-not-aboard = you are not aboard: name the ship or board it
cmd-step-not-on-ladder = the price step is not on the ladder: { $step }
cmd-nothing-to-resume = nothing to resume: either nothing waits here, or the station is busy, or the work is already running

## Printing the first body (D-187, D-229)

cmd-door-does-not-print = there is no printing at the door “{ $node }”
cmd-world-not-created = the world is not made yet: nowhere to be printed
