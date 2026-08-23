# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Execution parameters -- not game parameters.

The difference is fundamental. How many times to retry a failed job and how
long to sleep on an empty queue are process properties: whoever runs the
server tunes them, looking at load. A balance number is tuned by the game
designer, looking at telemetry, and lives in the vault's `data/constants.yaml` (D-065).

The module is excluded from the magic-number check for the same reason as
`units.py`: these quantities cannot be "balanced".
"""

from __future__ import annotations

from datetime import timedelta

#: How many times we try a job before declaring it broken.
JOB_MAX_ATTEMPTS = 5
#: Pause before the first retry.
JOB_RETRY_BASE = timedelta(seconds=30)
#: How many times the pause grows with each next attempt.
JOB_RETRY_GROWTH = 2
#: How many characters of the error text we keep in the job journal.
JOB_ERROR_LIMIT = 2000

#: Worker pause when the queue is empty, seconds.
WORKER_IDLE_SLEEP = 1.0
#: The longest one statement of a job may run before the database cuts it.
#: Ten minutes: a daily step over every body of a big world fits; a job
#: stuck on a lock does not hold a lane for ever.
JOB_STATEMENT_TIMEOUT_MS = 600_000
#: When the steps of a tick run, in minutes after the tick's moment: the
#: land tax before the debt collection, the snapshots after everything. A
#: technical stagger, not a balance number (D-065 does not apply).
TICK_STAGES: dict[str, int] = {"first": 0, "later": 5, "last": 15}

#: How many price levels of the book are given out. Display depth, not a
#: market property: the book itself is not bounded by anything.
MARKET_BOOK_DEPTH = 20

#: Live chat delivery buffer. Not history -- the server keeps none (D-070): a
#: remark lives exactly long enough for clients to poll it.
CHAT_BUFFER = timedelta(minutes=30)
#: Remark length limit. Channel hygiene, not balance.
CHAT_TEXT_LIMIT = 1000

#: The Net (D-222). How long the map's edges, read once into memory for the
#: delay sums, are trusted before being read again: a road laid or a ship cast
#: off shows up in the delays within this. Execution, not balance -- the
#: delay itself is the vault's.
NET_GRAPH_TTL = timedelta(minutes=5)
#: How many source nodes keep their measured distance maps: a writer's second
#: letter and a post's every reader pay nothing for the walk.
NET_REACH_CACHE = 64
#: Letter and post length limits, and the channel's name: hygiene.
NET_TEXT_LIMIT = 4000
NET_NAME_LIMIT = 40
NET_ABOUT_LIMIT = 300
#: How many letters of a thread and posts of a channel one reading brings.
NET_PAGE = 100
#: How many names a search for a correspondent shows.
NET_SEARCH_LIMIT = 8

#: Plot name length limit (D-178). The same hygiene: a map label must fit in a
#: map label, and there is nothing to balance here.
LAND_NAME_LIMIT = 40


#: How the growth constant of a remembered chance is solved (D-213). None of
#: this is balance: the announced chance lives in the vault, and these decide
#: only how exactly the arithmetic hits it.
#:
#: The tail of the sum below which the rest is dust.
LUCK_EPSILON = 1e-9
#: How far the sum looks before giving up: a thousand tries without a success
#: is well past any chance worth remembering.
LUCK_LONGEST = 1000
#: To how many decimals a share is rounded before solving -- a hundredth of a
#: percent, finer than any chance the vault names. Rounding is what makes the
#: cache work for chances that float: a leak grows with the crowd, a run's odds
#: fall with every find.
LUCK_GRAIN = 4
#: Steps of the bisection: each one halves the interval, so this many is far
#: past the precision of a double.
LUCK_STEPS = 200
#: How many solved constants are kept. Every chance in the game, at a hundredth
#: of a percent, fits many times over.
LUCK_CACHE = 1024

#: Transfer ground length and statement depth (D-190). Display hygiene: a
#: payment line is read at a glance, and a statement is the latest operations
#: rather than the whole journal since day one.
TRANSFER_MEMO_LIMIT = 140
STATEMENT_DEPTH = 50

#: The herald (`herald/`): how often the worker carries the chronicle out and
#: how many events it processes per pass. A process property, not a game one
#: -- the feed affects nothing in the world, and it cannot be "balanced".
HERALD_PERIOD = timedelta(minutes=2)
HERALD_BATCH = 20
#: How long we wait for the webhook's answer. Discord answers fast or not at all.
HERALD_TIMEOUT = 10.0
#: Discord message length limit -- their number, not ours.
DISCORD_CONTENT_LIMIT = 2000
#: How many letters of somebody's text (verdict, name) get into a chronicle
#: line. The feed is a feed, not a copy of the document: the full case is read in the game.
HERALD_TEXT_LIMIT = 200

#: Length limit of the city's word to newcomers (D-183). Door cards are
#: compared by eye: a city that wrote a page would turn the choice into reading.
CITY_ABOUT_LIMIT = 300

#: Account (D-187): login hygiene, not game. A shorter password is not a
#: password; a session token lives a month so that a page refresh does not
#: ask for the password; name, surname and description are bounded to fit the header and card.
PASSWORD_MIN_LENGTH = 8
LOGIN_TOKEN_TTL = timedelta(days=30)
LOGIN_TOKEN_BYTES = 32
CHARACTER_NAME_LIMIT = 24
CHARACTER_SURNAME_LIMIT = 32
CHARACTER_ABOUT_LIMIT = 600
#: Age is self-description, but still a number from the human range.
CHARACTER_AGE_MIN = 16
CHARACTER_AGE_MAX = 120

#: Length of the device-fee challenge and answer, bytes. Size affects neither
#: the fee's cost nor the game: the work is set by `pow.*` from the vault.
POW_NONCE_BYTES = 32
POW_HASH_BYTES = 32
#: Argon2id parallelism. One on purpose: we compute in one WASM thread on the
#: client and one verification thread on the server so that the cost matches.
POW_PARALLELISM = 1
#: How many session starts an account is allowed per window. Server protection
#: against challenge brute-forcing, not a game constraint: the game limits
#: mining by stamina and the price of food (D-091).

POW_STARTS_PER_WINDOW = 20
POW_WINDOW = timedelta(minutes=10)

#: How many past events the return summary carries. A display depth, not a
#: property of the world: the journal keeps everything, and a screen meant to be
#: read in ten seconds cannot.
SUMMARY_LIMIT = 40
