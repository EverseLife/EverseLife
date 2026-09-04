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
search, work on a plot, a keel at a yard, a batch at a machine, a working face.
Starting a second one while the first runs is refused, and the refusal names
what the body is at and until when -- so that the player has a decision to make
("finish the search, then lie down") rather than a mystery.

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
from src.engine.errors import Refusal, Says, left_to_say
from src.models.farm import Plot
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState


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
KEEL = "keel"
#: A watering or a feeding of a bed (D-296): the hands are busy for the
#: action's minutes, the effect was written when the button was pressed.
CARE = "care"

#: Every kind there is. Written down rather than inferred because each one
#: owes the locale a one-word title under `doing-<kind>` (see `Doing.title`),
#: and a kind added without its word would show the player the key instead.
KINDS: tuple[str, ...] = (ROAD, FIELD, SLEEP, FORAGE, PLOT, MINE, CRAFT, MEND, KEEL, CARE)


@dataclass(frozen=True)
class Doing:
    """What the body is at -- named, not worded.

    The engine does not know which language this will be read in (D-251), so
    it names the occupation and hands over the numbers. The one-word title is
    the kind's own message; what is going on is a message of its own because
    one occupation may be at two different things -- a search is running, or
    its find is lying on the ground waiting for a decision.
    """

    kind: str
    #: The message that says what is going on, with its own arguments.
    says: Says
    #: When it is over by itself. Empty -- it ends by a decision, not a clock.
    until: datetime | None = None

    @property
    def title(self) -> str:
        """The key of the occupation's one-word name: `doing-road`, `doing-mine`."""
        return f"doing-{self.kind}"


class Journal:
    """This body's journal work, read once for a whole lookup and only if asked.

    Three of the occupations below are journal jobs and differ by kind alone.
    Asked one at a time they cost three round-trips of every `look`; asked
    eagerly they cost one even for a body that is merely walking, and walking
    is answered by the very first lookup. So: one query, on the first question,
    and none at all if nobody asks.
    """

    def __init__(self, session: AsyncSession, body: Body) -> None:
        self._session = session
        self._body = body
        self._read: dict[str, Job] | None = None

    async def of(self, kind: JobKind) -> Job | None:
        if self._read is None:
            self._read = await _own_jobs(self._session, self._body)
        return self._read.get(kind.value)


async def _sleeping(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:
    if body.sleeping_since is None:
        return None
    return Doing(SLEEP, Says("doing-sleep-what"))


async def _travelling(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:

    going = await travel.current(session, body)
    if going is None:
        return None
    return Doing(ROAD, Says("doing-road-what"), going.arrives_at)


async def _exploring(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:

    run = await explore.pending(session, body)
    if run is None:
        return None
    return Doing(FIELD, Says("doing-field-what"), run.run_at)


async def _foraging(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:

    row = await forage.current(session, body)
    if row is None:
        return None
    if forage.revealed(row):
        return Doing(FORAGE, Says("doing-forage-found", {"goods": row.found}))
    return Doing(FORAGE, Says("doing-forage-searching"), row.ready_at)


#: The occupations the journal knows about: a job of this body's, still
#: pending. All three are asked in one query -- `all_of` runs in every `look`,
#: and three round-trips for one answer is three.
_JOURNAL = (JobKind.FARM_PLOW, JobKind.FARM_CARE, JobKind.BUILD_REPAIR, JobKind.SHIP_KEEL)


async def _own_jobs(session: AsyncSession, body: Body) -> dict[str, Job]:
    """This body's running journal work, by kind."""
    stmt = select(Job).where(
        Job.body_id == body.id,
        Job.kind.in_([kind.value for kind in _JOURNAL]),
        Job.state.in_((JobState.PENDING, JobState.RUNNING)),
    )
    return {job.kind: job for job in (await session.execute(stmt)).scalars().all()}


async def _ploughing(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:
    """The plough: a journal job of this body's, still pending.

    The plot is not asked about its state -- a plot ploughs for whoever started
    it, and the journal is the only place that link is written down.
    """
    job = await jobs.of(JobKind.FARM_PLOW)
    if job is None:
        return None
    #: The plot's name, so that the line says which strip is under the plough
    #: -- a farmer with four of them has nothing to go by otherwise.

    plot = await session.get(Plot, uuid.UUID(job.payload["plot"]))
    return Doing(
        PLOT,
        #: A variant key in Fluent is an identifier, never a string -- so
        #: "has a name" is said as a flag and the name travels beside it.
        Says(
            "doing-plot-what",
            {
                "named": "false" if plot is None else "true",
                "plot": "" if plot is None else plot.name,
            },
        ),
        job.run_at,
    )


async def _caring(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:
    """A watering or a feeding still holding the hands (D-296).

    The effect was written when the button was pressed; the job only keeps
    the hands busy for the action's minutes, so once its hour has passed the
    hands are free whether or not the worker has swept it yet.
    """
    job = await jobs.of(JobKind.FARM_CARE)
    if job is None or job.run_at <= datetime.now(UTC):
        return None
    plot = await session.get(Plot, uuid.UUID(job.payload["plot"]))
    return Doing(
        CARE,
        #: A variant key in Fluent is an identifier, never a string -- so
        #: "has a name" is said as a flag and the name travels beside it.
        Says(
            "doing-care-what",
            {
                "named": "false" if plot is None else "true",
                "plot": "" if plot is None else plot.name,
            },
        ),
        job.run_at,
    )


async def _mending(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:
    """A repair of this body's that is still running.

    Mending is done by hand and on the spot: the job is this body's, and it
    stops when the body leaves the node (`estate.pause`). So while it is in the
    journal, these hands are busy.
    """
    job = await jobs.of(JobKind.BUILD_REPAIR)
    if job is None:
        return None
    return Doing(MEND, Says("doing-mend-what"), job.run_at)


async def _keeling(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:
    """A ship's keel of this body's that is still being laid (D-202).

    The foundation is written off the moment the button is pressed and the node
    arrives eight hours later. Between those two moments the work existed
    nowhere on screen: the item was gone and nothing said why. It is a work of
    these hands like the plough, so it belongs here -- one place where
    everything running is seen.
    """
    job = await jobs.of(JobKind.SHIP_KEEL)
    if job is None:
        return None
    #: The first node is laid under a name, every later one is an
    #: extension of a ship that already has one -- and the line says which,
    #: because a yard may be laying a keel for somebody's second hull.
    return Doing(
        KEEL,
        Says(
            "doing-keel-what",
            {
                "named": "true" if job.payload.get("name") else "false",
                "ship": job.payload.get("name") or "",
            },
        ),
        job.run_at,
    )


async def _crafting(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:
    """A batch of this body's that is actually moving.

    A queued batch does not count: it is paid for and waiting, not being
    worked on, and one body may have any number of those (D-209).
    """

    batch = await craft.running(session, body)
    if batch is None:
        return None
    #: `output` is a D-251 id and travels as one: the message turns it into a
    #: word with `NAME()`, in whichever language is reading.
    return Doing(CRAFT, Says("doing-craft-what", {"goods": batch.output}), batch.ready_at)


async def _mining(session: AsyncSession, body: Body, jobs: Journal) -> Doing | None:

    face = await mining.active(session, body)
    if face is None:
        return None
    return Doing(MINE, Says("doing-mine-what"))


#: Order matters: the refusal names the first match, and the road comes before
#: everything because a body away from here cannot do anything at all.
_LOOKUP: tuple[
    tuple[str, Callable[[AsyncSession, Body, Journal], Awaitable[Doing | None]]], ...
] = (
    (ROAD, _travelling),
    (FIELD, _exploring),
    (SLEEP, _sleeping),
    (MINE, _mining),
    (FORAGE, _foraging),
    (PLOT, _ploughing),
    (CARE, _caring),
    (MEND, _mending),
    (KEEL, _keeling),
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
    jobs = Journal(session, body)
    for kind, look in _LOOKUP:
        if kind in besides:
            continue
        doing = await look(session, body, jobs)
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
    jobs = Journal(session, body)
    for _, look in _LOOKUP:
        doing = await look(session, body, jobs)
        if doing is not None:
            found.append(doing)
    return found


async def require_free(
    session: AsyncSession, body: Body, *, besides: frozenset[str] = frozenset()
) -> None:
    """Refuse the start of a new occupation while another one runs (D-211)."""
    doing = await current(session, body, besides=besides)
    if doing is not None:
        #: Two messages quoted inside a third: what the body is at, and how
        #: long it has left. Both are keys -- the engine still says nothing in
        #: any language of its own (D-251).
        raise Busy(
            key="occupation-busy",
            term="true" if doing.until is not None else "false",
            inner={"what": [doing.says]}
            | ({} if doing.until is None else {"left": [left_to_say(doing.until)]}),
        )
