# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The client's own words: headings, buttons, labels — what the window says and
# not what the world says (D-251, wave IV).
#
# Why they are here and not in `backend/locales/`, where the refusals come from:
#   — this is the voice of the interface, and it changes together with the
#     build of the client that shows it;
#   — they go into the bundle, not over the wire: the player is waiting for the
#     first screen, and dragging another hundred kilobytes along for it is
#     pointless.
# The `ui-` prefix keeps them clear of the engine's keys: there is nowhere for
# the two to collide.
#
# A value is one line (a break would land in the text); the variants of a
# select go one per line, and those breaks do not land in the text.

## The summary of a return (D-226)

ui-summary-label = What happened
ui-summary-title = While you were away
ui-summary-rule = The summary is counted from the moment you last closed it. Everything with a term is shown with what is left of it in real hours: a missed term in this world cannot be undone, so it is spoken of beforehand and not after.
ui-summary-attention = Needs attention
ui-summary-attention-none = Nothing is waiting: no term is pressing.
ui-summary-attention-rest = and { $count } more: they are visible where they live — in the city, in the estate, in money.
ui-summary-happened = Happened
ui-summary-happened-none = Nothing has happened since last time.
ui-summary-talk = Talk
ui-summary-talk-rule = The talk lives while you are in the room: it has no history, and there is no going back to what was said. This is not letters — this is speech.
ui-summary-close = Got it

## What marks a line of “needs attention”: one word per kind of matter.

ui-need-case = court
ui-need-vote = vote
ui-need-debt = debt
ui-need-reservation = reservation
