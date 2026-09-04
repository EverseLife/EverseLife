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
import uuid
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
from src.units import (
    PERCENT,
    ROUND_ROOF,
    SCALE_MAX,
    SCALE_MIN,
    amount,
    amount_float,
    on_grid,
)


class MiningError(Refusal):
    pass


class NotHere(MiningError):
    """The body is not in the vein's node. Matter requires presence (D-044)."""


class SessionClosed(MiningError):
    pass


class NoTimber(MiningError):
    """No support. It costs timber and rope -- that is the whole point of the choice."""


class RoofBuried(MiningError):
    """The working is under its own rubble: there is no roof to prop yet (D-301).

    A cave-in leaves the stability below nought, and a support set there would
    lift the **rubble** -- a timber buying `mine.roof_per_timber` of it, some
    four swings' worth, and two of them opening the face again without the
    working ever starting over. D-301 gives the clearing to swings and prices
    it in them; the support is for the roof, and there is no roof until the
    rubble is out.
    """


class RoofHolds(MiningError):
    """The roof is already at or above what a support can hold it at (D-300).

    A support **raises** the roof (D-188), and `mine.roof_timber_cap` is the
    ceiling it raises to -- not a value it sets. A working nobody has shaken
    starts above that ceiling (`starting_roof` runs from `mine.roof_start`
    down to the cap), so shoring one used to spend a timber and bring the roof
    **down** to the cap. That was a loss the miner paid alone while the roof
    was the session's own copy; shared, it is the artel's working they spoil,
    and D-188 promises the opposite -- a support set by one holds for all.

    The refusal says the roof is at or above the ceiling -- and since D-302
    that is no longer a number the player has. The ceiling is the working's
    own, drawn from a salt that never leaves the server, so "not higher than
    this working goes" is all a support and its refusal can tell. Before that
    it told the roof outright: the ceiling was public, and a support raised
    from anywhere within one timber of it landed on the ceiling **exactly**.
    """


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


def _own_measure(constants: Constants, vein: Vein, what: str) -> float:
    """How far this working stands from the computed number, and which way (D-302).

    Drawn from the vein's salt, which never leaves the server, so it is the
    same for one vein for ever and unguessable from outside. Every quantity
    that would otherwise name the hidden roof out loud goes through here --
    the starting stability and the ceiling a support raises to -- each with
    its own draw off the same salt, so knowing one tells nothing of the other.

    Without it the roof was arithmetic and the sign's lie decoration: `look`
    gives the vein's richness as a number, `starting_roof` is a public formula
    over two public constants, `mine.roof_per_swing` is public and a miner
    counts their own swings. The lie hid nothing from anybody who multiplied.
    """
    spread = constants[R.MINE_ROOF_SPREAD]
    return random.Random(f"{vein.roof_salt}:{what}").uniform(-spread, spread)


def _folded(value: float, low: float, high: float) -> float:
    """A number brought inside `[low, high]` by reflection, not by a clamp.

    A clamp is the obvious way and the wrong one here: it takes every draw
    that fell outside and stacks it on the bound, so the bound -- a number the
    player can compute -- becomes the single likeliest value of a quantity
    that is supposed not to have one. Reflection sends the overshoot back the
    way it came and leaves the draw continuous.

    Looped, because a value far outside a narrow interval comes back out the
    other side; with the vault's numbers one turn is always enough, and the
    loop is there so that a retuning cannot make it not enough.
    """
    if high <= low:  # pragma: no cover -- the ceiling is above the cap by construction
        return low
    while value < low or value > high:
        value = low + (low - value) if value < low else high - (value - high)
    return value


def timber_cap(constants: Constants, vein: Vein) -> float:
    """The stability a support raises this working to, and no higher (D-143, D-302).

    `mine.roof_timber_cap` with the working's own measure on it. Public, the
    ceiling said the roof out loud: a support raised from anywhere within one
    timber of it lands **exactly** there, and the number is in
    `/public/constants`. Shifted per vein, the same support says only "not
    higher than this working goes".

    On the grid the roof is kept at, because the roof lands on it: written
    off-grid, a capped roof rounds to the nearest hundredth and can come to
    rest a hundredth **below** its own ceiling -- and then the next support
    finds room to raise, spends a timber and buys that hundredth. A ceiling
    has to be a number the thing it caps can actually reach.
    """
    own = constants[R.MINE_ROOF_TIMBER_CAP] + _own_measure(constants, vein, "cap")
    return float(on_grid(own, ROUND_ROOF))


def starting_roof(constants: Constants, vein: Vein) -> float:
    """Richness is paid for with risk: the fatter the vein, the shorter the session.

    Plus the working's own measure (D-302), so that richness -- which stays a
    number in `look`, because trade and prospecting need it -- no longer names
    the roof. Never below what a support could hold this working at: above the
    ceiling is where an untouched working belongs (D-300), and the clamp keeps
    the measure from turning a fresh face into one a support may prop.
    """
    floor = constants[R.MINE_ROOF_TIMBER_CAP]
    ceiling = constants[R.MINE_ROOF_START]
    computed = ceiling - (ceiling - floor) * float(vein.richness) / SCALE_MAX
    own = computed + _own_measure(constants, vein, "start")
    #: Folded back inside the bounds rather than clamped to them. A clamp puts
    #: every draw that overshot **on** the bound, and a bound is a number the
    #: player has: at `mine.roof_spread` = 8 that is one working in twenty
    #: starting at exactly `mine.roof_start`, and one in ten at exactly its
    #: own ceiling -- known values, in a measure whose whole purpose is that
    #: there are none (D-302). Folding keeps the draw continuous and keeps the
    #: two ends of the working's measure independent, which the decision also
    #: promises. Repeated because a fold can overshoot the other way when the
    #: bounds sit closer together than the spread.
    #:
    #: On the roof's own grid, like the ceiling below it: an untouched working
    #: is read straight out of here and a shaken one out of the column, and
    #: the sign's lie is seeded by that number either way (`_noise_of`).
    return float(on_grid(_folded(own, timber_cap(constants, vein), ceiling), ROUND_ROOF))


def rubble_depth(constants: Constants, vein: Vein) -> float:
    """How deep a cave-in buries this working, in stability (D-301).

    `mine.rubble_swings` swings on a vein of ordinary richness
    (`mining.rich_threshold`) and proportionally more on a fatter one -- the
    same line the yield is drawn on, and the same bargain the starting roof
    makes: richness is paid for with risk, and now with the hours after.

    Said in stability rather than in a count of swings, because clearing is
    swinging: a swing lifts the rubble by exactly what it would have sagged
    the roof by, so the working needs no second number and no second column.
    """
    swings = (
        constants[R.MINE_RUBBLE_SWINGS] * float(vein.richness) / constants[R.MINING_RICH_THRESHOLD]
    )
    return swings * constants[R.MINE_ROOF_PER_SWING]


def roof_of(constants: Constants, vein: Vein) -> float:
    """The working's current stability (D-188). **The only place it is read.**

    Stored on the vein and shared by everyone who digs it (D-099): one miner
    shakes the roof -- it is dangerous for the next. An untouched vein has none
    yet, and its first session starts from richness and the working's own
    measure (D-302). **Below nought the working is not standing but buried**
    (D-301): the number is what is left of the rubble, and a swing clears it
    rather than mining.

    Read under the lock the caller already holds on the vein, and never
    remembered past the command: the session used to carry a copy taken at
    `start`, and every swing worked from that copy and wrote it back, so a
    neighbour who opened the face at a hundred put back ninety-four over
    somebody else's forty. Two bodies at one vein is the ordinary case --
    `crowd_factor` counts them, and `start` refuses only a second face of the
    **same** body -- so the copy erased the sag of whoever swung less recently
    and told them both a sign sixty points off the rock.
    """
    if vein.roof is None:
        return starting_roof(constants, vein)
    return float(vein.roof)


def remember_roof(vein: Vein, roof: float) -> float:
    """Write the working's stability onto the vein, and answer with what stands there.

    Rounded to the scale the column keeps (`Vein.roof`, two decimals) and
    handed back rounded, so that the caller goes on with the number the
    database has rather than the one it computed. They must be one number: the
    sign's lie is seeded by the roof (`_noise_of`), and a seed that changed on
    the way through the column would redraw the lie on a re-read of a face
    nobody has touched -- which is the averaging D-143 forbids.
    """
    vein.roof = on_grid(roof, ROUND_ROOF)
    return float(vein.roof)


def clear_roof(vein: Vein) -> None:
    """The rubble is cleared and the working starts over (D-188).

    Its own verb rather than an empty roof passed to the writer above: this is
    not a value, it is the absence of one, and `roof_of` reads it as a working
    nobody has shaken yet.
    """
    vein.roof = None


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
    """What was mined during the session lies apart: leave -- take it, collapse -- lose it.

    Opens the container when the face has none, so this belongs to the write
    paths alone -- `face.start`, `swing`, `leave`, `abandon`, `collapse`. To
    only look at the haul there is `_session_haul`, which writes nothing.
    """
    stmt = select(Container).where(
        Container.kind == ContainerKind.MINING_SESSION, Container.owner_id == mining.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:
        container = Container(kind=ContainerKind.MINING_SESSION, owner_id=mining.id)
        session.add(container)
        await session.flush()
    return container


async def _session_haul(session: AsyncSession, mining: MiningSession) -> int:
    """How much lies at the face, counted without opening a container.

    A read creates no row -- `look`, `*.view`, `*.status` (review 2026-08-23).
    `session_container` opens one where it finds none, and the sight wants the
    total rather than the row, so the sum goes through the container by its
    owner: a face without one reads as empty instead of gaining a container.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0))
        .select_from(Item)
        .join(Container, Container.id == Item.container_id)
        .where(
            Container.kind == ContainerKind.MINING_SESSION,
            Container.owner_id == mining.id,
        )
    )
    return int(total or 0)


async def active(session: AsyncSession, body: Body) -> MiningSession | None:
    """The open working of this body, if any: one body swings in one face."""
    stmt = select(MiningSession).where(
        MiningSession.body_id == body.id, MiningSession.state == SessionState.ACTIVE
    )
    return (await session.execute(stmt)).scalars().first()


async def _relock(
    session: AsyncSession, mining: MiningSession, *, working: bool = True
) -> tuple[Body, Vein]:
    """Take the face's row for the transaction, then check what it now says.

    **After the vein, and never before it.** The eruption takes the veins of a
    shaken node before the sessions at them, precisely because a swing goes the
    other way round: a command holding this row while it waits for the vein
    closes that circle into a deadlock. `vein -> session` is the eruption's own
    direction and crosses nobody -- no closer of a face asks for a body or a
    vein while already holding a session row (`plates._close_faces`,
    `face.leave`, `face.abandon`, `death.die`, `engine.rig`).

    Taken rather than merely read, because `leave` from a second socket of one
    identity holds this row and nothing else -- no body, no vein -- so it
    shares no other lock with a swing. A reread would only narrow the window
    in which it walks off with the haul and leaves the newest ore in a
    container its own state then refuses to open; with a heap already at the
    face the two do not even lose quietly, they cross on `stack_up`'s twins.
    Pinned by `test_races_face.py` and `test_races_mining.py`.
    """
    await session.execute(
        select(MiningSession)
        .where(MiningSession.id == mining.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return await _require_active(session, mining, working=working)


async def _require_active(
    session: AsyncSession, mining: MiningSession, *, working: bool = True, fresh: bool = False
) -> tuple[Body, Vein]:
    """The open session, its body and its vein.

    `working=False` allows a **worked-out** vein. Every kind of work at a face
    needs rock left in it, but leaving one does not: the last swing is the one
    that takes the remainder to nought, and it leaves the session open with the
    haul in it. Refusing to leave then would shut the miner in a face they
    could neither work nor walk out of -- and the ore mined by the swing before
    would stay in a container nobody can ever open again.

    `fresh=True` takes the session's row as the database has it now before
    reading it, and locks nothing -- a plain read, which waits for nobody and
    under READ COMMITTED sees whatever the winner of the row committed. It is
    what a command may do **before** it holds the vein; to hold the row as
    well, and only after the vein, there is `_relock`, which says why.
    """
    if fresh:
        await session.execute(
            select(MiningSession)
            .where(MiningSession.id == mining.id)
            .execution_options(populate_existing=True)
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


def _noise_of(salt: uuid.UUID, roof: float) -> random.Random:
    """Sign noise bound to the working and its roof, not to who is reading.

    Otherwise the sign can be read any number of times in a row, and the
    average of readings yields the hidden number to any precision. The roof
    changes only from a swing and a support -- so the sign must change only
    with them, and now with **whosever** swing and support they were (D-188).

    It used to be seeded by this session's own counters, which was the same
    thing while the roof was the session's own copy. Shared, it is not, and
    two things had to change with it.

    **The lie moves with the roof.** A lie frozen across a roof that moves
    under a neighbour is the averaging attack back again, with a partner
    rather than patience: the neighbour shores to `mine.roof_timber_cap` -- a
    public number -- and swings a counted number of times while I only look;
    my noise never moves, so every band the sign crosses is one more
    inequality on it, and a few crossings pin the hidden number for good.
    Seeded by the roof, each value of it draws its own lie, so reading twice
    at one roof still says one thing.

    **And the lie belongs to the working, not to the reader.** Seeded by the
    session, one roof would have as many independent lies as there are ways to
    look at it -- and each is a fresh sample of one number, which is the
    average again by another road. Two of those roads are cheap: leave and
    walk back in, which costs an evaluation of the device fee and nothing
    else, and a second body at the same face, which is the ordinary case
    (D-099). Seeded by the vein, both give the answer already given: one face,
    one roof, one sign, whoever is standing in it and however many times they
    ask. That is why what comes in here is the working's id and not the
    session standing at it -- and since D-302 the working's salt rather than
    its id: the session is the thing that must not reach the seed, and the id
    is the thing the client already has.

    The roof is formatted to the scale of the column it lives in, so that the
    same roof seeds the same lie before and after a trip through the database
    (`remember_roof` puts it on that grid for the same reason).

    **The salt is what makes the lie a lie** (D-302). Seeded by the vein's id,
    as it was for a day, every part of the seed was already the client's --
    the id goes out with the node, `mine.sign_noise` and `mine.sign_bands` are
    in `/public/constants` -- so a program could try every roof the grid
    allows, draw the lie each one would tell and keep the candidates whose
    sign matched. Off a salt the client never sees, a candidate roof gives no
    candidate lie, and there is nothing to sieve.

    What it still does not close is patience: the measure is the working's for
    life, so a face dug long enough converges on it. Renewing it would take
    something that moves -- the count of cave-ins the working has lived
    through, say -- and that is a decision, not a formula.
    """
    return random.Random(f"{salt}:{roof:.{ROUND_ROOF}f}")


async def _sight(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    body: Body,
    roof: float,
    salt: uuid.UUID,
) -> Sight:
    """What the player sees, about the roof the caller names.

    The roof is passed rather than read here, because the caller knows which
    one it means and this function cannot: a swing shows the roof it has just
    written, a collapse the roof that came down -- not the fresh one the
    cleared rubble leaves on the vein -- and a look shows what is on the vein
    now (`roof_of`). Reading the vein here would have shown the miner buried
    by a cave-in a working as good as new.

    The haul is counted through `_session_haul`, which opens no container: a
    look does not write, and this function is on the read path.

    The salt comes in beside the roof, because the lie is the working's and
    the session is the thing that must not reach the seed (D-302, D-188).
    """
    return Sight(
        sign=sign_of(constants, roof, _noise_of(salt, roof)),
        mined=amount_float(await _session_haul(session, mining)),
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
