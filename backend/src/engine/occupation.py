# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""One body, one occupation (D-211).

Being busy used to mean three things and no more: the body is on the road, the
body is in the field, the body is asleep -- everything `travel.require_here`
knows about. Nothing else asked anybody, and so one pair of hands carried three
advances at once: a search running on the empty land, a plot under the plough
and a sleeper gaining stamina, all on the same hour. D-209 added a fourth on
purpose -- a batch went on while the master slept, "the body is on the spot".

An occupation is what takes the body's time: the road, the field, sleep, the
search, work on a plot, a batch at a machine, a working face. Starting a second
one while the first runs is refused, and the refusal names what the body is at
and until when -- so that the player has a decision to make ("finish the
search, then lie down") rather than a mystery.

## Why this lives apart from `travel.require_here`

Presence and occupation are two different questions, and nearly every action
asks the first one: putting a thing down, speaking, trading, taking the find
out of the hands. Only the beginning of a new occupation asks the second. Were
the check inside `require_here`, a search under way would forbid handing a
neighbour a rope -- and foraging itself would forbid picking up its own find.

## What sleep does to a batch

Sleep is the one occupation a batch does not refuse: lying down is **stepping
away from the bench**. The batch freezes with the time left in it, the machine
is freed for others, and waking resumes it -- exactly what leaving the node
already does (D-209, `craft.freeze` / `craft.wake`). So sleep asks with
`besides={CRAFT}`, and everything else -- a search, a plough, a face -- is
refused while a batch is moving, because a batch moves while the master stands
here doing nothing else.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import craft, explore, forage, mining, travel
from src.engine.errors import Refusal
from src.models.farm import Plot
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.units import MINUTES_PER_HOUR, SECONDS_PER_MINUTE


class Busy(Refusal):
    """The body is already at something: one body does one thing (D-211)."""


#: The kinds of occupation. The id is ASCII on purpose: the client switches on
#: it -- which line to draw, which button ends it -- and a word of the interface
#: must be free to change without breaking that.
ROAD = "road"
FIELD = "field"
SLEEP = "sleep"
FORAGE = "forage"
PLOT = "plot"
MINE = "mine"
CRAFT = "craft"
MEND = "mend"


def left_in_words(until: datetime, now: datetime | None = None) -> str:
    """How long is left, for a person: "меньше минуты", "ещё 12 мин", "ещё 2 ч 5 мин".

    A deadline is told as a duration rather than an hour. The player decides
    between waiting and going elsewhere, and that is a question of "how long",
    not of "at which moment" -- the more so as the world's own clock counts a
    day of its own length (D-029), and a stamp in it needs a conversion nobody
    does in their head.
    """
    seconds = (until - (now or datetime.now(UTC))).total_seconds()
    if seconds < SECONDS_PER_MINUTE:
        return "меньше минуты"
    minutes = int(seconds // SECONDS_PER_MINUTE)
    if minutes < MINUTES_PER_HOUR:
        return f"ещё {minutes} мин"
    hours, rest = divmod(minutes, int(MINUTES_PER_HOUR))
    return f"ещё {hours} ч" if rest == 0 else f"ещё {hours} ч {rest} мин"


@dataclass(frozen=True)
class Doing:
    """What the body is at, as the player is told about it."""

    kind: str
    #: The occupation's name in one word: the line's title in the client.
    title: str
    #: What is going on, and where it can be ended -- the refusal's own words.
    what: str
    #: When it is over by itself. Empty -- it ends by a decision, not a clock.
    until: datetime | None = None

    def refusal(self) -> str:
        term = "" if self.until is None else f" ({left_in_words(self.until)})"
        return f"тело занято: {self.what}{term}"


async def _sleeping(session: AsyncSession, body: Body) -> Doing | None:
    if body.sleeping_since is None:
        return None
    return Doing(SLEEP, "сон", "тело спит — сначала проснуться")


async def _travelling(session: AsyncSession, body: Body) -> Doing | None:

    going = await travel.current(session, body)
    if going is None:
        return None
    return Doing(ROAD, "путь", "тело в пути", going.arrives_at)


async def _exploring(session: AsyncSession, body: Body) -> Doing | None:

    run = await explore.pending(session, body)
    if run is None:
        return None
    return Doing(FIELD, "разведка", "тело в разведке — вернуть его можно на карте", run.run_at)


async def _foraging(session: AsyncSession, body: Body) -> Doing | None:

    row = await forage.current(session, body)
    if row is None:
        return None
    if forage.revealed(row):
        return Doing(
            FORAGE,
            "собирательство",
            f"на земле лежит находка ({row.found}) — решите с ней или закончите поиск",
        )
    return Doing(FORAGE, "собирательство", "идёт поиск", row.ready_at)


async def _ploughing(session: AsyncSession, body: Body) -> Doing | None:
    """The plough: a journal job of this body's, still pending.

    The plot is not asked about its state -- a plot ploughs for whoever started
    it, and the journal is the only place that link is written down.
    """
    stmt = select(Job).where(
        Job.body_id == body.id,
        Job.kind == JobKind.FARM_PLOW.value,
        Job.state.in_((JobState.PENDING, JobState.RUNNING)),
    )
    job = (await session.execute(stmt)).scalars().first()
    if job is None:
        return None
    #: The plot's name, so that the line says which strip is under the plough
    #: -- a farmer with four of them has nothing to go by otherwise.

    plot = await session.get(Plot, uuid.UUID(job.payload["plot"]))
    named = "" if plot is None else f" «{plot.name}»"
    return Doing(PLOT, "вспашка", f"идёт вспашка{named}", job.run_at)


async def _mending(session: AsyncSession, body: Body) -> Doing | None:
    """A repair of this body's that is still running.

    Mending is done by hand and on the spot: the job is this body's, and it
    stops when the body leaves the node (`estate.pause`). So while it is in the
    journal, these hands are busy.
    """
    stmt = select(Job).where(
        Job.body_id == body.id,
        Job.kind == JobKind.BUILD_REPAIR.value,
        Job.state.in_((JobState.PENDING, JobState.RUNNING)),
    )
    job = (await session.execute(stmt)).scalars().first()
    if job is None:
        return None
    return Doing(MEND, "ремонт", "идёт ремонт дома", job.run_at)


async def _crafting(session: AsyncSession, body: Body) -> Doing | None:
    """A batch of this body's that is actually moving.

    A queued batch does not count: it is paid for and waiting, not being
    worked on, and one body may have any number of those (D-209).
    """

    batch = await craft.running(session, body)
    if batch is None:
        return None
    return Doing(CRAFT, "партия", f"идёт работа «{batch.output}»", batch.ready_at)


async def _mining(session: AsyncSession, body: Body) -> Doing | None:

    face = await mining.active(session, body)
    if face is None:
        return None
    return Doing(MINE, "забой", "вы в забое — сначала выйти из него")


#: Order matters: the refusal names the first match, and the road comes before
#: everything because a body away from here cannot do anything at all.
_LOOKUP: tuple[tuple[str, Callable[[AsyncSession, Body], Awaitable[Doing | None]]], ...] = (
    (ROAD, _travelling),
    (FIELD, _exploring),
    (SLEEP, _sleeping),
    (MINE, _mining),
    (FORAGE, _foraging),
    (PLOT, _ploughing),
    (MEND, _mending),
    (CRAFT, _crafting),
)


async def current(
    session: AsyncSession, body: Body, *, besides: frozenset[str] = frozenset()
) -> Doing | None:
    """What this body is at, or nothing when it is free.

    `besides` names the kinds not to count -- an occupation asking about
    itself: a second batch is a queue entry rather than a second work (D-209),
    and foraging's own commands live inside the search.
    """
    for kind, look in _LOOKUP:
        if kind in besides:
            continue
        doing = await look(session, body)
        if doing is not None:
            return doing
    return None


async def all_of(session: AsyncSession, body: Body) -> list[Doing]:
    """Everything the body is at, in the order the lookup names them.

    One occupation at a time is the rule (D-211), but the list is not always of
    one: a batch waits out a sleep beside it, and a plough goes on while its
    farmer walks. The client draws this list as "дела" -- one place where
    everything running is seen and stopped, instead of hunting for the window
    each thing was started in.
    """
    found: list[Doing] = []
    for _, look in _LOOKUP:
        doing = await look(session, body)
        if doing is not None:
            found.append(doing)
    return found


async def require_free(
    session: AsyncSession, body: Body, *, besides: frozenset[str] = frozenset()
) -> None:
    """Refuse the start of a new occupation while another one runs (D-211)."""
    doing = await current(session, body, besides=besides)
    if doing is not None:
        raise Busy(doing.refusal())
