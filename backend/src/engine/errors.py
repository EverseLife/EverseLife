# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The one kind of error the game rules produce: a refusal.

Every engine module used to declare its own `XError(Exception)` -- forty of
them -- and the socket loop listed them by hand; three were missing and went
to the player as "the server failed" (review 2026-08-23). A refusal is not a
server error: it is the world saying no, in words written for the player, and
the socket answers it with `{"refused": ...}`. Everything that is not a
`Refusal` is a bug and is logged as one.

Engine modules keep their own subclasses for callers that want to tell a
`NoGoods` from a `NotYours`; they all descend from here.
"""

from __future__ import annotations


class Refusal(Exception):
    """The rules said no. The message is for the player, in their language."""
