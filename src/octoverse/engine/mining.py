"""«Свод» — механика добычи Э1 (D-143).

Три кнопки, одно скрытое число, две-три развилки за сессию. Бить, ставить
крепь, уйти; плюс рычаг темпа. Устойчивость свода игроку не показывается
никогда — наружу идёт строка признака, и она врёт на `mine.sign_noise`.

## Откуда взялась каждая формула

В вольте заданы числа, но не порядок шагов: формулы — дело движка
(CLAUDE.md вольта). Ниже — вывод каждой, чтобы её можно было сверить с D-143,
а не принимать на веру.

**Длина удара.** `mine.roof_per_swing` описан так: «без единой крепи свод
держит около шестнадцати ударов, это и есть длина короткой сессии», а
`mining.iron_per_hour` — «единиц за час активной добычи». Значит полная сессия
без крепи и есть тот самый час, а один удар — его доля:

    swing_hours = mine.roof_per_swing / mine.roof_start

**Выход за удар.** Час добычи даёт `mining.iron_per_hour` на жиле обычного
богатства. Обычное — это `mining.rich_threshold`, граница между богатой и
бедной. Отсюда выход пропорционален богатству относительно этой границы:

    выход = mining.iron_per_hour × swing_hours × richness / mining.rich_threshold

**Стартовая устойчивость.** «Богатая жила даёт меньше — за богатство платят
риском». Шкала между двумя уже заданными величинами: бедная жила стартует с
`mine.roof_start`, богатая — с `mine.roof_timber_cap`, выше которого крепь всё
равно не поднимает:

    свод = mine.roof_start − (mine.roof_start − mine.roof_timber_cap) × richness / 100

**Темп.** «Быстрый темп — во столько раз больше и выхода, и просадки свода, и
расхода выносливости». Один множитель `mine.pace_k` на все три величины.

Ни одного числа сверх вольта здесь не появилось, и появиться не должно: если
формуле не хватает величины, её заводят в `data/constants.yaml`, а не в код
(D-065).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from octoverse.constants import Constants
from octoverse.constants import registry as R
from octoverse.engine import events
from octoverse.engine.world import body_container
from octoverse.models.event import EventKind
from octoverse.models.identity import Body, BodyState, Wound
from octoverse.models.inventory import Container, ContainerKind, Item
from octoverse.models.mining import MiningSession, Pace, SessionState
from octoverse.models.world import Vein
from octoverse.units import PERCENT, SCALE_MAX, SCALE_MIN, amount, amount_float


class MiningError(Exception):
    pass


class NotHere(MiningError):
    """Тело не в том узле, где жила. Материя требует присутствия (D-044)."""


class SessionClosed(MiningError):
    pass


class NoTimber(MiningError):
    """Крепи нет. Она стоит бруса и верёвки — в этом весь смысл выбора."""


class VeinDepleted(MiningError):
    """Жила выработана. Жилы конечны, и это неотменяемо (столп П2)."""


#: Имя шахтной крепи в `build/recipes.json`.
TIMBER = "Шахтная крепь"


@dataclass(frozen=True, slots=True)
class Sight:
    """Всё, что игрок видит о сессии. Числа свода здесь нет и быть не может."""

    sign: str
    mined: float
    swings: int
    timbers: int
    stamina: float
    pace: Pace
    state: SessionState


def swing_hours(constants: Constants) -> float:
    """Доля часа, приходящаяся на один удар."""
    return constants[R.MINE_ROOF_PER_SWING] / constants[R.MINE_ROOF_START]


def starting_roof(constants: Constants, richness: float) -> float:
    """За богатство платят риском: чем жирнее жила, тем короче сессия."""
    floor = constants[R.MINE_ROOF_TIMBER_CAP]
    ceiling = constants[R.MINE_ROOF_START]
    return ceiling - (ceiling - floor) * richness / SCALE_MAX


def pace_factor(constants: Constants, pace: Pace) -> float:
    return constants[R.MINE_PACE_K] if pace is Pace.FAST else 1.0


def sign_of(constants: Constants, roof: float, noise: random.Random) -> str:
    """Признак строкой, и он врёт.

    Без шума полосы обратимы в арифметику, и скрытого числа больше нет (D-143).
    """
    spread = constants[R.MINE_SIGN_NOISE]
    apparent = roof + noise.uniform(-spread, spread)
    #: Полоса задана нижней границей; берём самую высокую из подходящих.
    bands = sorted(constants[R.MINE_SIGN_BANDS].items(), key=lambda pair: pair[1], reverse=True)
    for name, floor in bands:
        if apparent >= floor:
            return name
    return bands[-1][0]


async def crowd_factor(constants: Constants, session: AsyncSession, vein: Vein) -> float:
    """Соседи по жиле (D-099).

    Движок не делит добычу — делёж остаётся договором. Но выход зависит от
    того, сколько человек работает на жиле: богатая делится хуже, бедная лучше.
    Одна строка баланса, два противоположных социальных режима.
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
        #: За богатую жилу дерутся: каждый лишний бьёт по всем.
        penalty = constants[R.MINING_CROWD_RICH_PENALTY] * neighbours / PERCENT
        return max(0.0, 1.0 - penalty)

    #: Бедная кормит артель, но не бесконечную.
    counted = min(neighbours, int(constants[R.MINING_CROWD_BONUS_CAP]) - 1)
    return 1.0 + constants[R.MINING_CROWD_POOR_BONUS] * counted / PERCENT


async def session_container(session: AsyncSession, mining: MiningSession) -> Container:
    """Добытое за сессию лежит отдельно: уходишь — забираешь, обрушилось — теряешь."""
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
    """Открыть сессию. Плата устройства проверяется до вызова (`engine.pow`)."""
    if body.node_id != vein.node_id:
        raise NotHere("до жилы надо дойти ногами")
    if body.state is not BodyState.ALIVE:
        raise SessionClosed("мёртвое тело не работает")
    if vein.remaining <= 0:
        raise VeinDepleted(f"жила {vein.id} выработана")

    existing = await session.scalar(
        select(func.count())
        .select_from(MiningSession)
        .where(MiningSession.body_id == body.id, MiningSession.state == SessionState.ACTIVE)
    )
    if existing:
        raise SessionClosed("у тела уже открыта сессия: в двух забоях сразу не бьют")

    mining = MiningSession(
        body_id=body.id,
        vein_id=vein.id,
        pace=pace,
        roof=Decimal(str(starting_roof(constants, float(vein.richness)))),
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
    """Бить: сырьё, износ, просадка свода.

    Обрушение при устойчивости ≤ 0 стоит **всего добытого за сессию** — в этом
    и состоит ставка, растущая по ходу дела.
    """
    noise = rng or random.Random()
    moment = now or datetime.now(UTC)
    body, vein = await _require_active(session, mining)

    factor = pace_factor(constants, mining.pace)
    crowd = await crowd_factor(constants, session, vein)

    #: Час добычи даёт `mining.iron_per_hour` на жиле обычного богатства.
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

    #: Качество сырья определяется жилой (15-quality).
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

    body.stamina = Decimal(
        str(
            max(
                SCALE_MIN,
                float(body.stamina)
                - constants[R.BODY_DRAIN_RATE].min * swing_hours(constants) * factor,
            )
        )
    )

    mining.swings += 1
    mining.roof = Decimal(str(float(mining.roof) - constants[R.MINE_ROOF_PER_SWING] * factor))
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

    return await _sight(session, constants, mining, body, noise)


async def timber(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    *,
    rng: random.Random | None = None,
) -> Sight:
    """Ставить крепь: тратит брус и ход, возвращает устойчивость до потолка.

    Выгодно ли крепить — зависит от цены крепи против цены сырья, а она плавает
    вместе с рынком. Заученной последовательности не существует, потому что
    оптимум двигается (D-143).
    """
    noise = rng or random.Random()
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
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_TIMBERED,
        actor_identity_id=body.identity_id,
        session_id=str(mining.id),
        timbers=mining.timbers,
    )
    return await _sight(session, constants, mining, body, noise)


async def set_pace(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    pace: Pace,
    *,
    rng: random.Random | None = None,
) -> Sight:
    body, _ = await _require_active(session, mining)
    mining.pace = pace
    await session.flush()
    return await _sight(session, constants, mining, body, rng or random.Random())


async def leave(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    *,
    now: datetime | None = None,
) -> float:
    """Уйти: добытое переезжает в инвентарь. Возвращает объём добычи."""
    moment = now or datetime.now(UTC)
    body, vein = await _require_active(session, mining)

    haul = await _carry_out(session, mining, body)
    _wear_tool_for_session(constants, await _tool(session, mining), extra=0.0)

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
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    *,
    rng: random.Random | None = None,
) -> Sight:
    body = await session.get(Body, mining.body_id)
    if body is None:  # pragma: no cover
        raise MiningError("сессия без тела")
    return await _sight(session, constants, mining, body, rng or random.Random())


# --- внутреннее -------------------------------------------------------------


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
    """Жила беднеет ступенями по мере выработки.

    Шахтёрские города возникают, богатеют и умирают — как в реальности (D-101).
    """
    step = amount(constants[R.VEIN_DEPLETION_STEP])
    crossed = vein.extracted // step - extracted_before // step
    if crossed > 0:
        lost = constants[R.VEIN_RICHNESS_DECAY] * crossed
        vein.richness = Decimal(str(max(SCALE_MIN, float(vein.richness) - lost)))
    if vein.remaining <= 0 and vein.depleted_at is None:
        vein.depleted_at = moment


async def _sight(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    body: Body,
    noise: random.Random,
) -> Sight:
    container = await session_container(session, mining)
    mined = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(Item.container_id == container.id)
    )
    return Sight(
        sign=sign_of(constants, float(mining.roof), noise),
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


def _wear_tool_for_session(constants: Constants, tool: Item | None, *, extra: float) -> None:
    """Инструмент изнашивается за сессию, а не за удар.

    Отсюда ориентир приёмки: инструмент кончается за `100 / wear.tool_per_session`
    сессий (07-implementation-map).
    """
    if tool is None:
        return
    spent = constants[R.WEAR_TOOL_PER_SESSION] + extra
    tool.condition = Decimal(str(max(SCALE_MIN, float(tool.condition) - spent)))


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
    """Обрушение: теряется всё добытое за сессию, плюс износ и, может быть, рана."""
    container = await session_container(session, mining)
    lost_items = (
        await session.execute(select(Item).where(Item.container_id == container.id))
    ).scalars().all()
    lost = sum(amount_float(item.amount) for item in lost_items)
    for item in lost_items:
        await session.delete(item)

    _wear_tool_for_session(
        constants, await _tool(session, mining), extra=constants[R.MINE_COLLAPSE_WEAR]
    )

    wounded = noise.uniform(0, PERCENT) < constants[R.MINE_COLLAPSE_WOUND_CHANCE]
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
    )
    return await _sight(session, constants, mining, body, noise)
