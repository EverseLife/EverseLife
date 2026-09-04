# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The words and the formulas of the "Roof" mechanic (D-143).

Three buttons, one hidden number, two or three forks per session. Dig, set a
support, leave; plus a pace lever. Roof stability is never shown to the player
-- a sign string goes out, and it lies by `mine.sign_noise`.

## Where each formula came from

The vault sets numbers but not the order of steps: formulas are the engine's
business (vault CLAUDE.md). Below is the derivation of each so it can be
checked against D-143 rather than taken on faith.

**Swing length.** `mine.roof_per_swing` is described as: "without a single
support the roof holds about sixteen swings, that is the length of a short
session", and `mining.iron_per_hour` as "units per hour of active mining". So
a full session without support is that very hour, and one swing is its share:

    swing_hours = mine.roof_per_swing / mine.roof_start

**Yield per swing.** An hour of mining gives `mining.iron_per_hour` on a vein
of ordinary richness. Ordinary is `mining.rich_threshold`, the boundary
between rich and poor. Hence yield is proportional to richness relative to
that boundary:

    yield = mining.iron_per_hour * swing_hours * richness / mining.rich_threshold

**Starting stability.** "A rich vein gives less -- richness is paid for with
risk". A scale between two already given quantities: a poor vein starts at
`mine.roof_start`, a rich one at `mine.roof_timber_cap`, above which support
does not raise it anyway:

    roof = mine.roof_start - (mine.roof_start - mine.roof_timber_cap) * richness / 100

**Pace.** "Fast pace -- that many times more yield, roof sag and stamina
spend". One multiplier `mine.pace_k` for all three quantities.

Not one number beyond the vault appeared here, and none must: if a formula
lacks a quantity, it is added to `data/constants.yaml`, not to code (D-065).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import food, wear
from src.engine.errors import Refusal
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item
from src.models.mining import MiningSession, Pace, SessionState
from src.models.world import Vein
from src.units import PERCENT, SCALE_MAX, SCALE_MIN, amount, amount_float


class MiningError(Refusal):
    pass


class NotHere(MiningError):
    """The body is not in the vein's node. Matter requires presence (D-044)."""


class SessionClosed(MiningError):
    pass


class NoTimber(MiningError):
    """No support. It costs timber and rope -- that is the whole point of the choice."""


class NoStrength(MiningError):
    """No strength for a swing. Mining is the body's work, and a body at zero does not work."""


class VeinDepleted(MiningError):
    """The vein is worked out. Veins are finite, and that is irrevocable (pillar P2)."""


class VeinLiquid(MiningError):
    """A liquid vein (D-252). Oil is pumped by the rig; the pick has nothing to grip."""


#: The thing class of mine supports (D-215).
TIMBER = "mine_support"


class NoTool(MiningError):
    """No fitting tool in the hands. The vault has always required one
    (`Добыча requires: [Кирка, Жила]`); the engine simply never checked (D-215)."""


@dataclass(frozen=True, slots=True)
class Sight:
    """Everything the player sees about the session. The roof number is not here and cannot be."""

    sign: str
    mined: float
    swings: int
    timbers: int
    stamina: float
    pace: Pace
    state: SessionState


def swing_hours(constants: Constants) -> float:
    """The share of an hour that one swing takes."""
    return constants[R.MINE_ROOF_PER_SWING] / constants[R.MINE_ROOF_START]


def starting_roof(constants: Constants, richness: float) -> float:
    """Richness is paid for with risk: the fatter the vein, the shorter the session."""
    floor = constants[R.MINE_ROOF_TIMBER_CAP]
    ceiling = constants[R.MINE_ROOF_START]
    return ceiling - (ceiling - floor) * richness / SCALE_MAX


def roof_of(constants: Constants, vein: Vein) -> float:
    """The working's current stability (D-188).

    Stored on the vein and shared by everyone who digs it (D-099): one miner
    shakes the roof -- it is dangerous for the next. An untouched vein has none
    yet, and its first session starts from richness.
    """
    if vein.roof is None:
        return starting_roof(constants, float(vein.richness))
    return float(vein.roof)


async def remember_roof(
    session: AsyncSession, mining: MiningSession, *, roof: float | None = None
) -> None:
    """Write the session's roof back into the vein, so leaving does not reset it."""
    vein = await session.get(Vein, mining.vein_id)
    if vein is None:  # pragma: no cover -- a session without a vein is a bug
        return
    vein.roof = None if roof is None else Decimal(str(roof))
    await session.flush()


def pace_factor(constants: Constants, pace: Pace) -> float:
    return constants[R.MINE_PACE_K] if pace is Pace.FAST else 1.0


def swing_cost(
    constants: Constants, body: Body, pace: Pace, moment: datetime, *, chill: float = 1.0
) -> float:
    """The stamina price of one swing -- the same formula as the write-off.

    Computed before the swing: a body at zero does not hit the vein, it sleeps
    or eats (D-148). Otherwise stamina stops being a constraint at all: the
    floor is at zero, and the ore keeps coming.

    `chill` is what the cold adds (D-231): asked for by the caller, which has a
    session to ask the planet with, and passed in so that the price named to the
    player and the price written off stay one formula.
    """
    return (
        constants[R.BODY_DRAIN_RATE].min
        * swing_hours(constants)
        * pace_factor(constants, pace)
        * food.drain_multiplier(constants, body, moment)
        * chill
    )


def sign_of(constants: Constants, roof: float, noise: random.Random) -> str:
    """The sign as a string, and it lies.

    Without noise the bands are invertible into arithmetic, and the hidden
    number is gone (D-143).
    """
    spread = constants[R.MINE_SIGN_NOISE]
    apparent = roof + noise.uniform(-spread, spread)
    #: A band is given by its lower bound; take the highest of those that fit.
    bands = sorted(constants[R.MINE_SIGN_BANDS].items(), key=lambda pair: pair[1], reverse=True)
    for name, floor in bands:
        if apparent >= floor:
            return name
    return bands[-1][0]


async def crowd_factor(constants: Constants, session: AsyncSession, vein: Vein) -> float:
    """Neighbours on the vein (D-099).

    The engine does not split the yield -- splitting remains a contract. But
    the yield depends on how many people work the vein: a rich one shares
    worse, a poor one better. One line of balance, two opposite social modes.
    """
    others = await session.scalar(
        select(func.count())
        .select_from(MiningSession)
        .where(MiningSession.vein_id == vein.id, MiningSession.state == SessionState.ACTIVE)
    )
    neighbours = max(0, (others or 0) - 1)
    if neighbours == 0:
        return 1.0

    if float(vein.richness) > constants[R.MINING_RICH_THRESHOLD]:
        #: A rich vein is fought over: every extra person hurts everyone.
        penalty = constants[R.MINING_CROWD_RICH_PENALTY] * neighbours / PERCENT
        return max(0.0, 1.0 - penalty)

    #: A poor one feeds a crew, but not an endless one.
    counted = min(neighbours, int(constants[R.MINING_CROWD_BONUS_CAP]) - 1)
    return 1.0 + constants[R.MINING_CROWD_POOR_BONUS] * counted / PERCENT


async def session_container(session: AsyncSession, mining: MiningSession) -> Container:
    """What was mined during the session lies apart: leave -- take it, collapse -- lose it."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.MINING_SESSION, Container.owner_id == mining.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:
        container = Container(kind=ContainerKind.MINING_SESSION, owner_id=mining.id)
        session.add(container)
        await session.flush()
    return container


async def active(session: AsyncSession, body: Body) -> MiningSession | None:
    """The open working of this body, if any: one body swings in one face."""
    stmt = select(MiningSession).where(
        MiningSession.body_id == body.id, MiningSession.state == SessionState.ACTIVE
    )
    return (await session.execute(stmt)).scalars().first()


async def _require_active(
    session: AsyncSession,
    mining: MiningSession,
    *,
    working: bool = True,
    fresh: bool = False,
    hold: bool = False,
) -> tuple[Body, Vein]:
    """The open session, its body and its vein.

    `working=False` allows a **worked-out** vein. Every kind of work at a face
    needs rock left in it, but leaving one does not: the last swing is the one
    that takes the remainder to nought, and it leaves the session open with the
    haul in it. Refusing to leave then would shut the miner in a face they
    could neither work nor walk out of -- and the ore mined by the swing before
    would stay in a container nobody can ever open again.

    `fresh=True` takes the session's row as the database has it now before
    reading it; `hold=True` takes it **and keeps it for the transaction**, and
    implies the reread. Either is what makes this worth calling **again** after
    a wait on a lock (`face.swing`): what was checked before the wait says
    nothing about the world after it -- the ground may have closed the face
    (`plates._close_faces`), and a rig sharing the vein may have taken the
    remainder to nought.

    **Which of the two is decided by what is already held**, because the
    danger is the order and not the lock. The eruption takes the veins of a
    shaken node *before* the sessions at them, precisely because a swing goes
    the other way round: a swing holding this row while it waits for the vein
    closes that circle into a deadlock. So before the vein, a plain read --
    it waits for nobody, and under READ COMMITTED it sees whatever the winner
    of the row committed. After the vein the row is taken, and taken it must
    be: `veins -> sessions` is the eruption's own direction and closes no
    circle, while nothing weaker stops `leave` from a second socket of one
    identity. That one takes this row and nothing else -- no body, no vein --
    so it shares no other lock with a swing, and a plain read only narrows
    the window in which it leaves the swing's ore in a container its own
    state will then refuse to open. Both are pinned by `test_races_face.py`.
    """
    if fresh or hold:
        row = select(MiningSession).where(MiningSession.id == mining.id)
        await session.execute(
            (row.with_for_update() if hold else row).execution_options(populate_existing=True)
        )
    if mining.state is not SessionState.ACTIVE:
        raise SessionClosed(
            key="mining-session-closed", session=str(mining.id), state=mining.state.value
        )
    body = await session.get(Body, mining.body_id)
    vein = await session.get(Vein, mining.vein_id)
    if body is None or vein is None:  # pragma: no cover
        raise MiningError(key="mining-session-dangling")
    if working and vein.remaining <= 0:
        raise VeinDepleted(key="mining-vein-depleted", vein=str(vein.id))
    return body, vein


def deplete(constants: Constants, vein: Vein, moment: datetime, extracted_before: int) -> None:
    """The vein depletes in tiers as it is worked out.

    Mining towns arise, grow rich and die -- as in reality (D-101). Public on
    purpose: the rig eats the same vein by the same rule (`engine.rig`), and a
    second copy of the tiers would drift from this one.
    """
    step = amount(constants[R.VEIN_DEPLETION_STEP])
    crossed = vein.extracted // step - extracted_before // step
    if crossed > 0:
        lost = constants[R.VEIN_RICHNESS_DECAY] * crossed
        vein.richness = Decimal(str(max(SCALE_MIN, float(vein.richness) - lost)))
    if vein.remaining <= 0 and vein.depleted_at is None:
        vein.depleted_at = moment


def _noise_of(mining: MiningSession) -> random.Random:
    """Sign noise bound to the face's state, not the moment of reading.

    Otherwise the sign can be read any number of times in a row, and the
    average of readings yields the hidden number to any precision. The roof
    changes only from a swing and a support -- so the sign must change only
    with them (D-143).
    """
    return random.Random(f"{mining.id}:{mining.swings}:{mining.timbers}")


async def _sight(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    body: Body,
) -> Sight:
    container = await session_container(session, mining)
    mined = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(Item.container_id == container.id)
    )
    return Sight(
        sign=sign_of(constants, float(mining.roof), _noise_of(mining)),
        mined=amount_float(int(mined or 0)),
        swings=mining.swings,
        timbers=mining.timbers,
        stamina=float(body.stamina),
        pace=mining.pace,
        state=mining.state,
    )


async def _tool(session: AsyncSession, mining: MiningSession) -> Item | None:
    if mining.tool_item_id is None:
        return None
    return await session.get(Item, mining.tool_item_id)


async def _wear_tool_for_session(
    session: AsyncSession, constants: Constants, tool: Item | None, *, extra: float
) -> None:
    """The tool wears per session, not per swing.

    Hence the acceptance benchmark: a tool runs out in `100 / wear.tool_per_session`
    sessions (07-implementation-map) -- of ordinary quality, because a good
    pickaxe lasts longer exactly as many times as it is better (`engine.wear`).
    """
    await wear.spend(
        session,
        constants,
        tool,
        constants[R.WEAR_TOOL_PER_SESSION] + extra,
        cause="mining_session",
    )
