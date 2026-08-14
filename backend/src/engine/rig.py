"""Буровая установка: непрерывная добыча без игрока (D-115).

Эндгейм добычи и второй после автоматического станка переход от труда к
капиталу. Устроена так, чтобы **не убить живого шахтёра**: машина проигрывает
человеку по всем показателям, кроме одного — она не спит.

| | Человек | Буровая |
|---|---|---|
| Выход | `mining.iron_per_hour` | `rig.output_per_hour`, заметно меньше |
| Качество | по жиле, до её богатства | не выше `rig.quality_cap` |
| Жилу выедает | по добытому | вдвое (`rig.depletion_multiplier`) |
| Требует присутствия | постоянно | только чтобы вывезти бункер |

Ремесленная добыча остаётся способом получить **хорошую руду**, буровая —
способом получить **много средней**.

## Три обязательства, и все три требуют людей

**Топливо.** `rig.fuel_per_hour` угля из узла, где установка стоит. Кончилось —
встала: отсюда постоянный контракт с углевозом, а не «бесплатная руда».

**Опустошение.** Бункер вмещает `rig.hopper_capacity` **часов работы**. Полон —
установка стоит, пока хозяин (или его возчик) не приедет и не заберёт. Ногами:
материя перемещается только физически (D-047).

**Обслуживание.** `rig.wear_per_day` износа в сутки. Заброшенная разваливается,
и чинится она тем же ремонтом, что всякая вещь.

## Чего здесь пока нет

* **Лицензии города и налога на добычу** (D-115): установка занимает узел и
  подчиняется городу — с Э3, вместе с самим городом;
* **Глубоких шахт** с их расходом энергии (`energy.deep_mine_draw`): это
  отдельная механика, а не свойство буровой.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, travel, wear, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.rig import Rig as RigRow
from src.models.world import Node, Vein
from src.units import (
    SCALE_MAX,
    SCALE_MIN,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
)

#: Станок из `build/recipes.json`. Веха лестницы: реально доступна к концу Э2.75.
RIG = "Буровая установка"
#: Топливо установки. Уголь возят люди — в этом всё предприятие.
FUEL = "Уголь"


class RigError(Exception):
    pass


class NoRig(RigError):
    pass


class NotYours(RigError):
    """Чужая установка: вывозит бункер хозяин либо его возчик по договору."""


def hopper_capacity(constants: Constants) -> float:
    """Ёмкость бункера в единицах руды: вольт задаёт её **часами работы**."""
    return constants[R.RIG_HOPPER_CAPACITY] * constants[R.RIG_OUTPUT_PER_HOUR]


async def place(
    session: AsyncSession,
    body: Body,
    item: Item,
    vein: Vein,
    *,
    now: datetime | None = None,
) -> RigRow:
    """Поставить установку на жилу. Присутственно: станок ставят руками."""
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RigError("мёртвое тело не работает")
    await travel.require_here(session, body)

    if item.type_key != RIG:
        raise NoRig(f"{item.type_key!r} — не буровая установка")
    if vein.node_id != body.node_id:
        raise RigError("жила не здесь: установку ставят на месте")

    существует = (
        await session.execute(select(RigRow).where(RigRow.item_id == item.id))
    ).scalar_one_or_none()
    if существует is not None:
        return существует

    #: Станок переезжает из рук в узел: он стационарный по определению.
    node = await session.get(Node, body.node_id)
    двор = await world.node_container(session, node)
    item.container_id = двор.id

    rig = RigRow(
        item_id=item.id,
        node_id=body.node_id,
        vein_id=vein.id,
        owner_identity_id=body.identity_id,
        hopper=Decimal(0),
        counted_at=moment,
    )
    session.add(rig)
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        work="rig",
        rig=str(rig.id),
        vein=str(vein.id),
    )
    return rig


async def advance(
    session: AsyncSession,
    constants: Constants,
    rig: RigRow,
    *,
    now: datetime | None = None,
) -> float:
    """Доработать установку до «сейчас». Возвращает добытое за это время.

    Три ограничителя, и любой из них останавливает машину: место в бункере,
    уголь в узле и остаток жилы. Ни один не является ошибкой — это и есть
    обязательства предприятия.
    """
    moment = now or datetime.now(UTC)
    часов = (moment - rig.counted_at).total_seconds() / SECONDS_PER_HOUR
    if часов <= 0:
        return 0.0

    станок = await session.get(Item, rig.item_id)
    vein = await session.get(Vein, rig.vein_id)
    if станок is None or vein is None:  # pragma: no cover — станок могли разобрать
        rig.counted_at = moment
        await session.flush()
        return 0.0

    #: Выход установки задан вольтом и от её состояния не зависит: изношенная
    #: машина не копает меньше — она копает **хуже**, и это видно в качестве
    #: руды при вывозе (15-quality: станок задаёт потолок).
    место = max(0.0, hopper_capacity(constants) - float(rig.hopper))
    выход_в_час = constants[R.RIG_OUTPUT_PER_HOUR]

    #: Уголь: сколько часов установка вообще могла жечь.
    топливо = constants[R.RIG_FUEL_PER_HOUR]
    двор = await world.node_container(session, await session.get(Node, rig.node_id))
    угля = await _coal_available(session, двор.id)
    часов_по_топливу = угля / топливо if топливо > 0 else часов
    часов_по_бункеру = место / выход_в_час if выход_в_час > 0 else 0.0
    часов_по_жиле = (
        amount_float(vein.remaining)
        / (выход_в_час * constants[R.RIG_DEPLETION_MULTIPLIER])
        if выход_в_час > 0
        else 0.0
    )
    рабочих = max(0.0, min(часов, часов_по_топливу, часов_по_бункеру, часов_по_жиле))

    добыто = 0.0
    if рабочих > 0:
        добыто = выход_в_час * рабочих
        await _burn(session, двор.id, топливо * рабочих)
        #: Жилу машина выедает вдвое быстрее: капитал ускоряет истощение мира.
        из_жилы = amount(добыто * constants[R.RIG_DEPLETION_MULTIPLIER])
        было = vein.extracted
        vein.extracted += min(из_жилы, vein.remaining)
        vein.remaining = max(0, vein.remaining - из_жилы)
        _deplete(constants, vein, moment, было)
        rig.hopper = Decimal(str(float(rig.hopper) + добыто))

    #: Износ идёт по времени, а не по добытому: заброшенная разваливается.
    сутки = constants[R.TIME_DAY_TERRA]
    if часов > 0:
        await wear.spend(
            session,
            constants,
            станок,
            constants[R.RIG_WEAR_PER_DAY] * часов / сутки,
            cause="работа буровой",
        )

    rig.counted_at = moment
    await session.flush()
    return добыто


async def empty_hopper(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    rig: RigRow,
    *,
    now: datetime | None = None,
) -> float:
    """Вывезти бункер. Присутственно и ногами: иначе машина стоит.

    Качество — по жиле, но **не выше `rig.quality_cap`**: человек подстраивается
    под пласт, машина работает по настройке (D-058, D-115).
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RigError("мёртвое тело не работает")
    await travel.require_here(session, body)
    if rig.node_id != body.node_id:
        raise RigError("установка не здесь: бункер вывозят ногами")
    if rig.owner_identity_id not in (None, body.identity_id):
        raise NotYours("чужая установка: вывоз — по договору с хозяином (D-116)")

    await advance(session, constants, rig, now=moment)
    взято = float(rig.hopper)
    if взято <= 0:
        return 0.0

    vein = await session.get(Vein, rig.vein_id)
    #: Бункер вывозят руками, а руки не бездонны: без повозки бункер не
    #: вывезти целиком, и это работа для возчика (D-146).
    if vein is not None:
        from src.constants import current_catalog
        from src.engine import gear

        await gear.check_carry(
            session, constants, current_catalog(), body, vein.resource, взято
        )

    станок = await session.get(Item, rig.item_id)
    #: Три потолка, и берётся наименьший: жила даёт не больше своего богатства,
    #: машина — не больше `rig.quality_cap` (она работает по настройке), а
    #: изношенная машина — не больше своего действующего качества (D-129).
    качество = min(
        constants[R.RIG_QUALITY_CAP],
        max(SCALE_MIN, min(SCALE_MAX, float(vein.richness) if vein else SCALE_MIN)),
        wear.effective(constants, станок),
    )
    карман = await world.body_container(session, body)
    session.add(
        Item(
            container_id=карман.id,
            type_key=vein.resource if vein else FUEL,
            amount=amount(взято),
            quality=Decimal(str(качество)),
        )
    )
    rig.hopper = Decimal(0)
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_LEFT,
        actor_identity_id=body.identity_id,
        node_id=rig.node_id,
        work="rig",
        rig=str(rig.id),
        got=взято,
        quality=качество,
    )
    return взято


async def tick_rigs(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float:
    """Доработать все установки мира. Машина не спит — в этом вся её сила."""
    moment = now or datetime.now(UTC)
    rigs = (await session.execute(select(RigRow))).scalars().all()
    итог = 0.0
    for rig in rigs:
        итог += await advance(session, constants, rig, now=moment)
    return итог


async def status(
    session: AsyncSession, constants: Constants, node_id: uuid.UUID
) -> list[dict]:
    """Что стоит в узле и в каком оно состоянии — для сцены локации."""
    rigs = (
        await session.execute(select(RigRow).where(RigRow.node_id == node_id))
    ).scalars().all()
    out: list[dict] = []
    for rig in rigs:
        await advance(session, constants, rig)
        станок = await session.get(Item, rig.item_id)
        vein = await session.get(Vein, rig.vein_id)
        двор = await world.node_container(
            session, await session.get(Node, rig.node_id)
        )
        уголь = await _coal_available(session, двор.id)
        out.append(
            {
                "id": str(rig.id),
                "resource": vein.resource if vein else None,
                "hopper": float(rig.hopper),
                "capacity": hopper_capacity(constants),
                "full": float(rig.hopper) >= hopper_capacity(constants),
                "fuel": уголь,
                "hours_of_fuel": уголь / constants[R.RIG_FUEL_PER_HOUR],
                "condition": float(станок.condition) if станок else 0.0,
                "vein_left": amount_float(vein.remaining) if vein else 0.0,
            }
        )
    return out


# --- внутреннее -------------------------------------------------------------


async def _coal_available(session: AsyncSession, container_id: uuid.UUID) -> float:
    стопки = (
        await session.execute(
            select(Item).where(Item.container_id == container_id, Item.type_key == FUEL)
        )
    ).scalars().all()
    return sum(amount_float(стопка.amount) for стопка in стопки)


async def _burn(session: AsyncSession, container_id: uuid.UUID, сколько: float) -> None:
    осталось = amount(сколько)
    стопки = (
        await session.execute(
            select(Item).where(Item.container_id == container_id, Item.type_key == FUEL)
        )
    ).scalars().all()
    for стопка in стопки:
        if осталось <= 0:
            break
        взять = min(осталось, стопка.amount)
        if взять == стопка.amount:
            await session.delete(стопка)
        else:
            стопка.amount -= взять
        осталось -= взять
    await session.flush()


def _deplete(
    constants: Constants, vein: Vein, moment: datetime, extracted_before: int
) -> None:
    """Жила беднеет теми же ступенями, что и от кирки: правило одно на всех."""
    from src.engine.mining import _deplete as по_общему_правилу

    по_общему_правилу(constants, vein, moment, extracted_before)


