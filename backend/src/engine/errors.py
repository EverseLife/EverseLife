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

## A refusal is a key, not a sentence (D-251 wave III)

Two languages are equal (D-249), so the engine may not know which one is
being read. It names the reason and the numbers; the sentence is assembled by
`src.i18n` at the edge, in the language of whoever asked:

    raise NoGoods(key="goods-not-enough", goods="iron_ore", short=3)

The wire carries all three -- the rendered text for the player, the `code` for
the client and the agents (D-224), and the `args` for whoever wants to draw
the refusal rather than print it.

The old form, `raise NoGoods("нет столько")`, still works and travels as a
bare string without a code: the conversion of some six hundred sites goes
module by module, and a half-converted engine must keep refusing correctly.

## A sentence inside a sentence (wave IV)

Some refusals quote another one: "тело занято: идёт разведка", "для города не
хватает: биопринтер, рынок". The quoted half used to be Russian assembled in
Python and handed over as an argument -- which reads correctly and cannot be
translated, so the wave that moved the words out of the code left them in.

`inner` names those halves the same way `key` names the whole: a parameter,
and under it the messages to put there. The edge renders the inner ones first,
in the reader's language, and hands the result to the outer message.

    raise Busy(key="occupation-busy", inner={"what": [Says("doing-field-what")]})

A list, because the quoted half is often several: what a city still lacks is
as many messages as there are missing buildings, and how they are strung
together is the language's business, not the engine's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.units import MINUTES_PER_HOUR, SECONDS_PER_MINUTE


@dataclass(frozen=True)
class Says:
    """One message to be quoted inside another: its key and its own arguments."""

    key: str
    params: dict[str, Any] = field(default_factory=dict)


class Refusal(Exception):
    """The rules said no. Either a key with arguments, or a legacy sentence."""

    def __init__(
        self,
        text: str = "",
        *,
        key: str | None = None,
        inner: dict[str, list[Says]] | None = None,
        **params: Any,
    ) -> None:
        #: The message key (`goods-not-enough`), or None while this call site
        #: still writes its own Russian.
        self.key = key
        #: What the message interpolates. Ids travel as ids: the sentence turns
        #: them into words, the client and the agents keep the key.
        self.params = params
        #: Parameters whose value is itself a message -- rendered at the edge,
        #: in the reader's language, and only then put into the outer one.
        self.inner = inner or {}
        super().__init__(text)

    def __str__(self) -> str:
        """The legacy sentence, or -- for a converted site -- the key itself.

        A converted refusal carries no text, and `str()` of it used to be an
        empty string: `jobs.py` wrote `"CraftError: "` into `job.last_error`
        and the operator read nothing where a reason had been. The player never
        sees this -- the edge renders the key through `i18n` -- so it stays
        ASCII: a diagnostic line, not a sentence.
        """
        told = super().__str__()
        if told or self.key is None:
            return told
        if not self.params:
            return self.key
        shown = ", ".join(f"{name}={value!r}" for name, value in self.params.items())
        return f"{self.key} ({shown})"


def left_to_say(until: datetime, now: datetime | None = None) -> Says:
    """How long is left, as a message: hours and minutes, not a phrase.

    A deadline is told as a duration rather than an hour. The player decides
    between waiting and going elsewhere, and that is a question of "how long",
    not of "at which moment" -- the more so as the world's own clock counts a
    day of its own length (D-029), and a stamp in it needs a conversion nobody
    does in their head. An ISO string in a refusal was the worst of both.

    Which words those two numbers become -- "меньше минуты", "ещё 12 мин" --
    is the language's business: Russian counts minutes in three forms and
    English in two, and neither belongs in this arithmetic.

    Here rather than in `occupation`, where it started: half the engine needs
    to say a deadline, and `occupation` imports half the engine.
    """
    seconds = (until - (now or datetime.now(UTC))).total_seconds()
    minutes = max(0, int(seconds // SECONDS_PER_MINUTE))
    hours, rest = divmod(minutes, int(MINUTES_PER_HOUR))
    return Says("time-left", {"hours": hours, "minutes": rest})
