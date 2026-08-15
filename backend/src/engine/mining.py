""""Roof" -- the E1 mining mechanic (D-143).

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
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, food, travel, wear
from src.engine.world import body_container
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Wound
from src.models.inventory import Container, ContainerKind, Item
from src.models.mining import MiningSession, Pace, SessionState
from src.models.world import Node, Vein
from src.units import PERCENT, SCALE_MAX, SCALE_MIN, amount, amount_float


class MiningError(Exception):
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


#: Name of the mine support in `build/recipes.json`.
TIMBER = "Шахтная крепь"


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
    constants: Constants, body: Body, pace: Pace, moment: datetime
) -> float:
    """The stamina price of one swing -- the same formula as the write-off.

    Computed before the swing: a body at zero does not hit the vein, it sleeps
    or eats (D-148). Otherwise stamina stops being a constraint at all: the
    floor is at zero, and the ore keeps coming.
    """
    return (
        constants[R.BODY_DRAIN_RATE].min
        * swing_hours(constants)
        * pace_factor(constants, pace)
        * food.drain_multiplier(constants, body, moment)
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


async def start(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    vein: Vein,
    *,
    tool_item_id: uuid.UUID | None = None,
    pace: Pace = Pace.STEADY,
) -> MiningSession:
    """Open a session. The device fee is checked before the call (`engine.pow`)."""
    if body.node_id != vein.node_id:
        raise NotHere("до жилы надо дойти ногами")
    if body.state is not BodyState.ALIVE:
        raise SessionClosed("мёртвое тело не работает")
    await travel.require_here(session, body)
    if vein.remaining <= 0:
        raise VeinDepleted(f"жила {vein.id} выработана")
    #: The penal face is only for those the prison holds (D-174, D-176): its
    #: vein is neither visible nor given to an outsider.
    from src.engine import justice

    node = await session.get(Node, body.node_id)
    if (
        node is not None
        and await justice.is_prison(session, node)
        and not await justice.held(session, constants, body.identity_id)
    ):
        raise SessionClosed("каторжный забой работает только на заключённых")
    #: A session is not opened by a body that cannot swing even once.
    first_hit = swing_cost(constants, body, pace, datetime.now(UTC))
    if float(body.stamina) < first_hit:
        raise NoStrength(
            f"на удар нужно {first_hit:.2f} выносливости, а есть "
            f"{float(body.stamina):.2f}: сначала сон или обед"
        )

    existing = await session.scalar(
        select(func.count())
        .select_from(MiningSession)
        .where(MiningSession.body_id == body.id, MiningSession.state == SessionState.ACTIVE)
    )
    if existing:
        raise SessionClosed("у тела уже открыта сессия: в двух забоях сразу не бьют")

    #: The roof belongs to the working, not to the session (D-188): rock does
    #: not knit back together while the miner is away. An untouched vein starts
    #: from its richness, a shaken one meets the next miner as it was left.
    mining = MiningSession(
        body_id=body.id,
        vein_id=vein.id,
        pace=pace,
        roof=Decimal(str(roof_of(constants, vein))),
        tool_item_id=tool_item_id,
    )
    session.add(mining)
    await session.flush()
    await session_container(session, mining)

    await events.record(
        session,
        EventKind.MINING_STARTED,
        actor_identity_id=body.identity_id,
        node_id=vein.node_id,
        session_id=str(mining.id),
        vein_id=str(vein.id),
        resource=vein.resource,
        pace=pace.value,
    )
    return mining


async def swing(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    *,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> Sight:
    """Dig: raw material, wear, roof sag.

    A collapse at stability <= 0 costs **everything mined during the session**
    -- that is the stake, growing as things go.
    """
    noise = rng or random.Random()
    moment = now or datetime.now(UTC)
    body, vein = await _require_active(session, mining)

    #: A swing costs stamina, and a body at zero does not swing: the vein is
    #: not mined by free willpower. The check comes before all effects so that a
    #: refusal changes nothing in the world.
    hit_price = swing_cost(constants, body, mining.pace, moment)
    if float(body.stamina) < hit_price:
        raise NoStrength(
            f"на удар нужно {hit_price:.2f} выносливости, а есть "
            f"{float(body.stamina):.2f}: сначала сон или обед"
        )

    factor = pace_factor(constants, mining.pace)
    crowd = await crowd_factor(constants, session, vein)

    #: An hour of mining gives `mining.iron_per_hour` on a vein of ordinary richness.
    per_swing = (
        constants[R.MINING_IRON_PER_HOUR]
        * swing_hours(constants)
        * float(vein.richness)
        / constants[R.MINING_RICH_THRESHOLD]
        * factor
        * crowd
    )
    mined = min(amount(per_swing), vein.remaining)

    extracted_before = vein.extracted
    vein.remaining -= mined
    vein.extracted += mined
    _deplete(constants, vein, moment, extracted_before)

    #: Raw material quality is determined by the vein (15-quality).
    container = await session_container(session, mining)
    quality = min(SCALE_MAX, max(SCALE_MIN, float(vein.richness)))
    session.add(
        Item(
            container_id=container.id,
            type_key=vein.resource,
            amount=mined,
            quality=Decimal(str(quality)),
        )
    )

    #: Satiety slows the spend: hot food does not add reserve (D-119).
    body.stamina = Decimal(
        str(
            max(
                SCALE_MIN,
                float(body.stamina)
                - constants[R.BODY_DRAIN_RATE].min
                * swing_hours(constants)
                * factor
                * food.drain_multiplier(constants, body, moment),
            )
        )
    )

    mining.swings += 1
    mining.roof = Decimal(str(float(mining.roof) - constants[R.MINE_ROOF_PER_SWING] * factor))
    #: The working remembers every swing (D-188): the sag stays after the miner
    #: leaves, so "leave and re-enter" no longer resets the risk.
    await remember_roof(session, mining, roof=float(mining.roof))
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_SWING,
        actor_identity_id=body.identity_id,
        node_id=vein.node_id,
        session_id=str(mining.id),
        mined=amount_float(mined),
        quality=quality,
        crowd=crowd,
    )

    if float(mining.roof) <= SCALE_MIN:
        return await _collapse(session, constants, mining, body, vein, noise, moment)

    return await _sight(session, constants, mining, body)


async def timber(
    session: AsyncSession, constants: Constants, mining: MiningSession
) -> Sight:
    """Set a support: spends timber and a turn, restores stability up to the ceiling.

    Whether shoring pays depends on the price of support against the price of
    raw material, and that floats with the market. There is no memorised
    sequence because the optimum moves (D-143).
    """
    body, _ = await _require_active(session, mining)

    inventory = await body_container(session, body)
    stock = (
        await session.execute(
            select(Item)
            .where(Item.container_id == inventory.id, Item.type_key == TIMBER)
            .limit(1)
        )
    ).scalar_one_or_none()
    if stock is None:
        raise NoTimber("нет шахтной крепи")

    one = amount(1)
    if stock.amount > one:
        stock.amount -= one
    else:
        await session.delete(stock)

    cap = constants[R.MINE_ROOF_TIMBER_CAP]
    raised = min(cap, float(mining.roof) + constants[R.MINE_ROOF_PER_TIMBER])
    mining.roof = Decimal(str(raised))
    mining.timbers += 1
    #: A support stands after the shift ends (D-188): that is what makes timber
    #: an investment in the working rather than a consumable of one visit.
    await remember_roof(session, mining, roof=raised)
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_TIMBERED,
        actor_identity_id=body.identity_id,
        session_id=str(mining.id),
        timbers=mining.timbers,
    )
    return await _sight(session, constants, mining, body)


async def set_pace(
    session: AsyncSession, constants: Constants, mining: MiningSession, pace: Pace
) -> Sight:
    body, _ = await _require_active(session, mining)
    mining.pace = pace
    await session.flush()
    return await _sight(session, constants, mining, body)


async def leave(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    *,
    now: datetime | None = None,
) -> float:
    """Leave: what was mined moves to the inventory. Returns the mined volume."""
    moment = now or datetime.now(UTC)
    body, vein = await _require_active(session, mining)

    #: Prison labour (D-174): in a prison node the insolvent's yield goes to
    #: the city, and the debt is repaid by the treasury at the reference price.
    #: Treasury empty or no price -- the ore stays with the prisoner, who sells it themselves.
    haul = await _prison_workoff(session, constants, mining, body, now=moment)
    if haul is None:
        haul = await _carry_out(session, mining, body)
    await _wear_tool_for_session(session, constants, await _tool(session, mining), extra=0.0)

    mining.state = SessionState.LEFT
    mining.ended_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_LEFT,
        actor_identity_id=body.identity_id,
        node_id=vein.node_id,
        session_id=str(mining.id),
        haul=haul,
        swings=mining.swings,
        timbers=mining.timbers,
    )
    return haul


async def sight(
    session: AsyncSession, constants: Constants, mining: MiningSession
) -> Sight:
    """Look at the face. Asking again is pointless: the sign does not change."""
    body = await session.get(Body, mining.body_id)
    if body is None:  # pragma: no cover
        raise MiningError("сессия без тела")
    return await _sight(session, constants, mining, body)


# --- internal ----------------------------------------------------------------


async def _require_active(
    session: AsyncSession, mining: MiningSession
) -> tuple[Body, Vein]:
    if mining.state is not SessionState.ACTIVE:
        raise SessionClosed(f"сессия {mining.id} закрыта: {mining.state.value}")
    body = await session.get(Body, mining.body_id)
    vein = await session.get(Vein, mining.vein_id)
    if body is None or vein is None:  # pragma: no cover
        raise MiningError("сессия ссылается в никуда")
    if vein.remaining <= 0:
        raise VeinDepleted(f"жила {vein.id} выработана")
    return body, vein


def _deplete(constants: Constants, vein: Vein, moment: datetime, extracted_before: int) -> None:
    """The vein depletes in tiers as it is worked out.

    Mining towns arise, grow rich and die -- as in reality (D-101).
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
        cause="сессия добычи",
    )


async def _carry_out(session: AsyncSession, mining: MiningSession, body: Body) -> float:
    container = await session_container(session, mining)
    inventory = await body_container(session, body)
    items = (
        await session.execute(select(Item).where(Item.container_id == container.id))
    ).scalars().all()

    haul = 0.0
    for item in items:
        haul += amount_float(item.amount)
        item.container_id = inventory.id
    await session.flush()
    return haul


async def _collapse(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    body: Body,
    vein: Vein,
    noise: random.Random,
    moment: datetime,
) -> Sight:
    """Collapse: everything mined during the session is lost, plus wear and maybe a wound."""
    container = await session_container(session, mining)
    lost_items = (
        await session.execute(select(Item).where(Item.container_id == container.id))
    ).scalars().all()
    lost = sum(amount_float(item.amount) for item in lost_items)
    for item in lost_items:
        await session.delete(item)

    await _wear_tool_for_session(
        session, constants, await _tool(session, mining), extra=constants[R.MINE_COLLAPSE_WEAR]
    )

    #: A cave-in kills -- newcomer and oldtimer alike (08-danger, D-111). The
    #: environment is the only source of death in the alpha, and without this
    #: roll death in the game never comes. Rarer than it wounds: death is not an
    #: ordinary end of the day.
    killed = noise.uniform(0, PERCENT) < constants[R.MINE_COLLAPSE_DEATH_CHANCE]
    wounded = not killed and noise.uniform(0, PERCENT) < constants[
        R.MINE_COLLAPSE_WOUND_CHANCE
    ]
    if wounded:
        recovery = constants[R.WOUND_RECOVERY_HOURS]
        session.add(
            Wound(
                body_id=body.id,
                cause="обрушение свода",
                heals_at=moment + timedelta(hours=noise.uniform(recovery.min, recovery.max)),
            )
        )

    mining.state = SessionState.COLLAPSED
    mining.ended_at = moment
    #: The rubble is cleared and the working starts over (D-188): otherwise a
    #: collapsed vein would be locked forever, and veins are finite already (P2).
    await remember_roof(session, mining, roof=None)
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_COLLAPSED,
        actor_identity_id=body.identity_id,
        node_id=vein.node_id,
        session_id=str(mining.id),
        lost=lost,
        swings=mining.swings,
        wounded=wounded,
        killed=killed,
    )
    if killed:
        #: The summary is assembled **before** death: the player must see how
        #: the session ended, not an empty screen. The body is dead after that.
        sight = await _sight(session, constants, mining, body)
        from src.engine import death

        await death.die(session, constants, body, cause="обрушение свода", now=moment)
        return sight
    return await _sight(session, constants, mining, body)


async def _prison_workoff(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    body: Body,
    *,
    now: datetime,
) -> float | None:
    """Give the yield to the city and credit the debt, if this is a prison (D-174).

    Returns the mined volume if the labour counted, and None if this is an
    ordinary face or there is nothing to credit with: then the yield goes to
    the prisoner in the usual way -- the vault forbids traps without exit (D-063).
    """
    from src.engine import bank, customs, justice
    from src.engine import city as town
    from src.engine.world import node_container
    from src.models.world import Node

    node = await session.get(Node, body.node_id)
    if node is None or not await justice.is_prison(session, node):
        return None
    if await bank.restrained(session, constants, body.identity_id, now=now) is None:
        return None
    city = await town.of_node(session, node)
    if city is None:
        return None

    container = await session_container(session, mining)
    items = (
        await session.execute(select(Item).where(Item.container_id == container.id))
    ).scalars().all()
    if not items:
        return 0.0

    #: The reference price is the median of real deals: it cannot be set by
    #: collusion. No price -- no credit: first the market, then the penal colony (D-174).
    cost = 0
    for item in items:
        price = await customs.reference_price(
            session, constants, city, item.type_key, now=now
        )
        if price is None:
            return None
        cost += int(price * amount_float(item.amount))
    if cost <= 0:
        return None

    credited = await bank.prison_credit(
        session, constants, city, body.identity_id, cost, now=now
    )
    if credited <= 0:
        #: The treasury is empty -- the ore stays with the prisoner: a prison is
        #: the city's investment, and an insolvent city earns nothing from penal labour.

        return None

    yard = await node_container(session, node)
    haul = 0.0
    for item in items:
        haul += amount_float(item.amount)
        item.container_id = yard.id
    await session.flush()
    return haul
