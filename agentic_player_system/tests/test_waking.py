# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""When an agent's turn is worth taking: what holds the body, and for how long.

`aps/waking.py` with a hand-written `look`: no model, no game, no clock but the
one the test names. The roster at the end reads the engine's own occupations
back and refuses to let a new kind fall to the default unnoticed (D-310)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aps import waking

OCCUPATION_SOURCE = (
    Path(__file__).resolve().parents[2] / "backend" / "src" / "engine" / "occupation.py"
)


def _engine_kinds() -> set[str]:
    """Every occupation id the engine declares, read out of `occupation.KINDS`.

    Parsed rather than imported: the agent system is a separate service with
    its own dependencies and does not have the backend on its path -- it is a
    player, and sees the game only through the socket.
    """
    tree = ast.parse(OCCUPATION_SOURCE.read_text(encoding="utf-8"))
    named = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name) and isinstance(node.value.value, str)
    }
    listed = [
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "KINDS"
    ]
    assert len(listed) == 1, "occupation.KINDS is not a single annotated assignment any more"
    tuple_node = listed[0]
    assert isinstance(tuple_node, ast.Tuple), "occupation.KINDS is not a tuple any more"
    return {named[item.id] for item in tuple_node.elts if isinstance(item, ast.Name)}


def test_busy_until_is_the_latest_occupation_that_holds_the_turn() -> None:
    soon = datetime.now(UTC) + timedelta(minutes=5)
    later = datetime.now(UTC) + timedelta(minutes=9)
    past = datetime.now(UTC) - timedelta(minutes=1)
    seen = {
        "look": {
            "travel": {"arrives_at": soon.isoformat()},
            "doings": [
                {"kind": "field", "until": later.isoformat()},
                #: Sleep holds the turn by the rule and never by the clock: a
                #: sleeper is woken by a decision, so the wire carries no term.
                {"kind": "sleep", "until": None},
            ],
        }
    }
    assert waking.busy_until(seen) == later
    assert waking.busy_until({"look": {"travel": None, "doings": []}}) is None
    assert (
        waking.busy_until({"look": {"doings": [{"kind": "road", "until": past.isoformat()}]}})
        is None
    )


def test_a_work_that_runs_on_its_own_does_not_hold_the_turn() -> None:
    """D-310: a build, a demolition and a paving take the hands, not the legs.

    Their terms are the longest in the game -- twenty-three days for the house
    D-310 takes as its own example -- and waiting one out would stop the agent
    from walking, trading and talking for all of it.
    """
    weeks = datetime.now(UTC) + timedelta(days=23)
    minutes = datetime.now(UTC) + timedelta(minutes=5)
    #: The engine's own ids, beside the older works that already ran wherever
    #: the body was. `paving` is not `road` on purpose: that word is the body
    #: walking one, and the engine says so where it names the id. The three of
    #: D-310 arrive with the engine half of it; until then they are exactly the
    #: unknown kinds this must answer for.
    for kind in ("build", "demolish", "paving", "plot", "care", "keel"):
        seen = {"look": {"doings": [{"kind": kind, "until": weeks.isoformat()}]}}
        assert waking.busy_until(seen) is None, kind
    #: And such a work does not stretch a wait that is real: the road ends in
    #: five minutes, so the agent thinks again in five, not in three weeks.
    both = {
        "look": {
            "travel": {"arrives_at": minutes.isoformat()},
            "doings": [
                {"kind": "road", "until": minutes.isoformat()},
                {"kind": "build", "until": weeks.isoformat()},
            ],
        }
    }
    assert waking.busy_until(both) == minutes


def test_a_work_done_on_the_spot_holds_the_turn_within_the_horizon() -> None:
    """Leaving costs it: a batch and a repair freeze, a search is lost outright.

    The agent is not helpless during one -- trading and talking ask only
    `require_here` -- but walking is the ordinary next thought and walking is
    what costs the work. `forage` is the sharp case: the row is deleted the
    first time the body is seen elsewhere, with the find and the spent stamina.
    """
    soon = datetime.now(UTC) + timedelta(minutes=20)
    for kind in ("forage", "craft", "mend"):
        seen = {"look": {"doings": [{"kind": kind, "until": soon.isoformat()}]}}
        assert waking.busy_until(seen) == soon, kind


def test_a_long_work_on_the_spot_is_looked_at_again_within_the_hour() -> None:
    """A repair of a large house is days and a slow batch as much. Sleeping
    through all of it would be the idling this whole rule exists to stop, so
    the wait stops at the horizon and the agent decides for itself."""
    days = datetime.now(UTC) + timedelta(days=8)
    ceiling = datetime.now(UTC) + waking.MAX_STANDING_WAIT
    for kind in ("forage", "craft", "mend"):
        seen = {"look": {"doings": [{"kind": kind, "until": days.isoformat()}]}}
        moment = waking.busy_until(seen)
        assert moment is not None and moment <= ceiling, kind
        assert moment > datetime.now(UTC) + waking.MAX_STANDING_WAIT - timedelta(minutes=1), kind
    #: The horizon is for the works of the hands alone: on the road the body
    #: cannot act at all, and no amount of looking would change that.
    road = {"look": {"travel": {"arrives_at": days.isoformat()}}}
    assert waking.busy_until(road) == days


#: The occupations the agent deliberately does NOT wait out, kept here rather
#: than in `waking` because nothing reads them at run time: `busy_until` treats
#: an unclassified kind as one of these already. They are written down so that
#: the roster below can be a partition -- a kind the engine grows later has to
#: be put on one side or the other by a person, instead of falling to the
#: default in silence. The three of D-310 are listed ahead of the engine half
#: that defines them; the check only runs the other way.
RUNS_ON_ITS_OWN = frozenset({"plot", "care", "keel", "mine", "build", "demolish", "paving"})


def test_every_occupation_the_engine_defines_is_answered_for() -> None:
    """The set names engine ids by hand, and the engine is free to change them.

    Two ways that hurts, both silent: a kind renamed under `HOLDS_THE_TURN`
    stops being waited out -- the agent walks off a search it still loses --
    and a kind the engine adds falls to the default, which is how a build came
    to freeze an agent for twenty-three days in the first place. So the ids are
    read back from the engine's own source and the two sets must cover it.
    """
    kinds = _engine_kinds()
    #: Sanity on the reader itself before it is trusted as a guard.
    assert {"road", "field", "sleep", "craft"} <= kinds
    unknown = waking.HOLDS_THE_TURN - kinds
    assert not unknown, f"kinds the engine no longer defines: {sorted(unknown)}"
    unclassified = kinds - waking.HOLDS_THE_TURN - RUNS_ON_ITS_OWN
    assert not unclassified, (
        f"occupations the agent has no rule for: {sorted(unclassified)} -- decide whether "
        "leaving costs them anything, then add each to HOLDS_THE_TURN or RUNS_ON_ITS_OWN"
    )
    assert not (waking.HOLDS_THE_TURN & RUNS_ON_ITS_OWN)
    #: The two halves of the waiting set do not overlap either: a kind is
    #: either one the body cannot act during or one it must not walk away from.
    assert not (waking.AWAY & waking.ON_THE_SPOT)
    assert waking.HOLDS_THE_TURN == waking.AWAY | waking.ON_THE_SPOT


def test_busy_until_waits_out_the_printing_of_a_new_body() -> None:
    """No body at all (D-012): nothing in person is possible, so the turn waits.

    The shape is the one the server sends a bodiless identity -- no `doings`
    and no `travel` key at all, since there is nothing to have them.
    """
    ready = datetime.now(UTC) + timedelta(hours=2)
    seen = {
        "look": {
            "body": None,
            "node": None,
            "inventory": [],
            "printers": [],
            "printing": {"ready_at": ready.isoformat()},
        }
    }
    assert waking.busy_until(seen) == ready
