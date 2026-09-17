# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Whether the body is free, and until when: the wait between two turns.

A turn costs a call to the model, and a body that cannot act has nothing to
spend it on. `busy_until` reads the world's own stamps out of a `look` and says
when it is worth thinking again; the runner sleeps until then instead of waking
on its cadence to be refused (D-224). Two sets below are the whole rule -- what
puts the body out of the agent's reach, and what only asks it to stand still --
plus their union. There is deliberately no third set for the works that run by
their own clock and hold no turn at all (D-310): a kind named in neither of the
two is treated as one of those already, and the roster that keeps that default
honest lives in `test_waking.py`, not here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

#: The states in which the body is not available to the agent at all.
#: `travel.require_here` -- the door every in-person action goes through --
#: refuses exactly these two: asleep (D-091) and on the road. Nothing the agent
#: could try would not be refused, so waking the model is tokens for nothing
#: (D-224). The field was the third until D-319 rebuilt exploration: a scout
#: now keeps `node_id` for the whole run and is refused only the road
#: (`travel-scouting`), so the survey moved to the set below, where the body
#: acts but must not walk. `sleep` earns its place by the rule
#: and not by the clock: it carries no term, because a sleeper is woken by a
#: decision, so it never yields a stamp and the agent does wake on its cadence
#: to be refused. That is the engine's shape, not something a wait can mend --
#: the prompt tells the agent to name its own `wait_seconds` when it lies down.
AWAY = frozenset({"road", "sleep"})

#: The works done standing here, which leaving would cost. A batch comes off
#: the bench with its time left and the bench goes to whoever is here, on
#: walking out and on lying down alike (D-209, D-211: `craft.freeze`); a repair
#: stops the same way (D-218: `estate.pause`). A search is worse than either:
#: `forage` deletes the row the first time the body is seen in another node,
#: and the find and the stamina already spent on it go with it (D-210). A
#: survey is the same kind of loss held shut from the other side: the scout
#: keeps standing in the node they set out from, and the road is what is
#: refused them (`travel.depart`, `travel-scouting`), so the run is not walked
#: away from -- it is waited out or turned back from by hand (D-319, D-327).
#:
#: The agent is not helpless during one of these -- everything that asks only
#: `require_here` is open, so it may trade, talk, write and equip, and only a
#: second occupation is refused (`occupation.require_free`). What it must not
#: do is walk, and walking to a market or a field is the ordinary next thought.
#: So the wait is a default that keeps the work whole, not a cage.
ON_THE_SPOT = frozenset({"forage", "survey", "craft", "mend"})

#: Every kind the agent waits out. What is in neither set costs nothing to
#: walk away from -- a plough, a bed's watering, a keel, and since D-310 a
#: house going up, a house coming down and a surface being laid, each running
#: by its own clock wherever the body is. Those three are why this rule exists
#: at all: their terms are the longest in the game, twenty-three days for the
#: house D-310 takes as its own example, and waiting one out is not patience
#: but a citizen standing still for three weeks while the world goes on. The
#: one kind that fits neither sort is `mine`: a working face is stood at rather
#: than left running, but it ends by a decision and never carries a term, so a
#: wait has nothing to hold on to.
#:
#: A kind named in neither set is treated as one that runs on its own. The cost
#: of being wrong is real both ways -- a body-holding kind mistaken for a free
#: one wakes the model every cadence until its term runs out, some three
#: hundred turns a day, and may end in the runner's `_stuck` pause -- but the
#: opposite mistake is the unrecoverable one: weeks of a citizen's life spent
#: in silence, with nothing in the agent's reach to break it. `test_waking.py`
#: reads `occupation.KINDS` back and refuses to let a new kind fall here by
#: accident rather than by decision.
HOLDS_THE_TURN = AWAY | ON_THE_SPOT

#: How long a work done on the spot may hold the turn. The work itself may run
#: far longer -- a repair of a large house is days, and a batch of a slow
#: recipe as much -- and sleeping through all of it would be the very idling
#: D-310 forced this rule to be written: many days of a citizen's life spent
#: waiting on something that only asks it not to walk. Short works, which is
#: nearly all of them, are still slept through whole; a long one is looked at
#: again each hour, and the agent decides for itself whether the wait is still
#: worth it -- it may ask for more with `finish(wait_seconds=...)`, which is
#: capped by `brain.MAX_WAIT` in the same spirit: that one caps what the agent
#: asks for and belongs to the tool that asks, this one caps what the world's
#: own stamp is allowed to buy. `AWAY` needs no such horizon: there
#: the body cannot act at all, and no amount of looking would change that.
MAX_STANDING_WAIT = timedelta(hours=1)


def _latest(stamps: list[str]) -> datetime | None:
    """The last of the world's own stamps, or None when none of them parse."""
    latest: datetime | None = None
    for stamp in stamps:
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if latest is None or moment > latest:
            latest = moment
    return latest


def busy_until(seen: dict[str, Any]) -> datetime | None:
    """When it is worth thinking again, by the world's own clock. None when
    there is no reason to wait -- a work that runs on its own is not one."""
    look = seen.get("look") or seen
    away: list[str] = []
    standing: list[str] = []
    travel = look.get("travel")
    if isinstance(travel, dict) and travel.get("arrives_at"):
        away.append(travel["arrives_at"])
    for doing in look.get("doings") or []:
        if not isinstance(doing, dict) or not doing.get("until"):
            continue
        kind = doing.get("kind")
        if kind in AWAY:
            away.append(doing["until"])
        elif kind in ON_THE_SPOT:
            standing.append(doing["until"])
    #: No body at all: the identity is in the cloud and a new one is being
    #: printed (D-012). Nothing in person is possible -- there is nothing to do
    #: it with -- so this waits like the road, and to its end.
    printing = look.get("printing")
    if isinstance(printing, dict) and printing.get("ready_at"):
        away.append(printing["ready_at"])

    now = datetime.now(UTC)
    latest = _latest(away)
    spot = _latest(standing)
    if spot is not None:
        spot = min(spot, now + MAX_STANDING_WAIT)
        if latest is None or spot > latest:
            latest = spot
    if latest is None or latest <= now:
        return None
    return latest
