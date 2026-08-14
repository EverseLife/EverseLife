"""Смерть и печать тела (D-012, D-013, D-028, D-032, D-033, D-040).

Личность бессмертна, тело — расходник. Гибель уничтожает оболочку вместе со
всем, что она несла, но не трогает ни знания, ни счёт, ни репутацию: они живут
в личности, а личность — в Сети (09-death).

## Что теряется и что нет

| Теряется | Сохраняется |
|---|---|
| Тело и весь карман: инструмент, снаряжение, монета | Личность, имя, знания, агротехника |
| Ресурсы, ушедшие на печать нового тела | Терракоин на счёте: телу он не принадлежит |
| Место в цепочке: ты снова у принтера | Товар в терминале, участки, ордера |

Настоящая цена смерти — **логистический откат**, а не таймер. Наказание уже
содержится в утрате, поэтому недоступность держится минуты, а не часы.

**Часть носимого остаётся на месте гибели** — `death.salvage_ratio`, и в
повреждённом виде. Это сделано ради экономики насилия, которая приедет вместе с
боем: ограбить живого выгоднее, чем убить, потому что мёртвый оставляет треть
и ту битую.

## Печать: две двери

* **городской принтер** — `energy.body_print` энергии из пула по тарифу плюс
  `death.iron_cost` железа со двора; готово через `death.print_time_city` минут;
* **Принтер Предтеч в столице** — бесплатно и без ограничений по числу тел, но
  `death.print_time_capital` часов.

Отсюда всё устройство рынка воскрешения: **город продаёт не жизнь, а
скорость**, и никто не заплатит за тело больше, чем стоят ему двенадцать часов
(D-028). Заложника не существует: печататься можно в любом узле Сети, где есть
принтер и чем заплатить (D-033).

**Кто платит** — решает код-закон `body_print` (D-032). «нет» — платит сам
игрок; «гражданам» или «всем» — платит казна. Энергия при этом списывается
одинаково: город не печатает энергию, он её отдаёт.

**Первое тело печатается мгновенно и бесплатно** (D-040): человек, впервые
запустивший игру, не ждёт полсуток. Это одноразовое исключение и живёт оно в
`world.spawn`, а не здесь.

## Чего здесь нет

* **кредита на тело** — приезжает с банком (Э4, D-030): пока нечем платить,
  остаётся бесплатная дверь столицы, и она всегда открыта;
* **страховщика** — это профессия поверх договоров, а не механика движка;
* **нимф** (`death.nymph_grow_multiplier`) — второй линии в альфе нет.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.engine import events, world
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node

#: Час в минутах — представление, а не баланс: двенадцать часов Предтеч и
#: три минуты города иначе не сравнить в одной колонке.
from src.units import MINUTES_PER_HOUR as MINUTES_IN_HOUR
from src.units import PERCENT, amount, amount_float, money_str

#: Станок, который печатает тела. Имя — из `build/recipes.json` (D-090).
PRINTER = "Биопринтер"
#: Металл процессора. Вольт называет цену «10 железа» (D-033).
IRON = "Слиток железа"
#: Свойство узла: тот самый Принтер Предтеч, печатающий бесплатно и медленно
#: (D-028). Свойство места, а не имя узла: имена меняются, устройство мира нет.
PRECURSOR = "предтечи"


class DeathError(Exception):
    pass


class Alive(DeathError):
    """Тело живо. Печатать второе нельзя: один аккаунт — одна личность (D-011)."""


class NoPrinter(DeathError):
    """В узле нет биопринтера. Печатаются там, где есть чем печатать."""


class AlreadyPrinting(DeathError):
    """Печать уже идёт. Двух тел одной личности не бывает."""


class CannotPay(DeathError):
    """Нечем платить. Дверь столицы при этом остаётся открытой всегда."""


# --- гибель -----------------------------------------------------------------


async def die(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    cause: str,
    now: datetime | None = None,
) -> float:
    """Убить тело. Возвращает, сколько единиц носимого уцелело на месте.

    Уцелевшее ложится в узел, где тело погибло: материя не исчезает вместе с
    хозяином и не переезжает за ним. Остальное — сток, и он честный: в вечном
    мире (D-007) утраченное не возвращается.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        return 0.0

    #: Бросок засеян телом: разбор эпизода после сбоя даёт то же самое.
    бросок = random.Random(str(body.id))
    доля = constants[R.DEATH_SALVAGE_RATIO] / PERCENT

    node = await session.get(Node, body.node_id)
    карман = await world.body_container(session, body)
    вещи = (
        await session.execute(select(Item).where(Item.container_id == карман.id))
    ).scalars().all()

    двор = await world.node_container(session, node) if node is not None else None
    уцелело = 0.0
    for вещь in вещи:
        осталось = amount(amount_float(вещь.amount) * доля)
        #: Неделимое уцелевает броском: половины кирки не бывает, а правило
        #: обязано быть одно на всё носимое.
        if осталось <= 0:
            осталось = вещь.amount if бросок.random() < доля else 0
        if осталось <= 0 or двор is None:
            await session.delete(вещь)
            continue
        вещь.amount = осталось
        вещь.container_id = двор.id
        #: «В повреждённом виде»: состояние падает в той же доле, в какой
        #: уцелело количество. Второго числа для этого вольт не даёт.
        вещь.condition = Decimal(str(float(вещь.condition) * доля))
        уцелело += amount_float(осталось)

    #: Идущий переход обрывается: мёртвое тело никуда не приходит.
    from src.models.travel import Travel, TravelState

    переходы = (
        await session.execute(
            select(Travel).where(
                Travel.body_id == body.id, Travel.state == TravelState.GOING
            )
        )
    ).scalars().all()
    for переход in переходы:
        переход.state = TravelState.CANCELLED

    #: Упряжка распадается: мёртвый ничего не тянет. Обоз с грузом остаётся
    #: стоять там, где встал, — как всякая материя без хозяина (D-157).
    from src.engine import transport

    await transport.unharness(session, body)

    body.state = BodyState.DEAD
    body.died_at = moment
    body.sleeping_since = None
    await session.flush()

    await events.record(
        session,
        EventKind.BODY_DIED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        body_id=str(body.id),
        cause=cause,
        salvaged=уцелело,
    )
    return уцелело


# --- печать -----------------------------------------------------------------


async def printers(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID | None = None,
) -> list[dict]:
    """Где в мире можно напечататься и почём. Читается из облака, то есть всегда.

    Личность в Сети и видит всю сеть принтеров (D-033): выбор ограничен не
    доступом, а деньгами и географией. Кто спрашивает — важно: город печатает
    за свой счёт своим гражданам, а не всем подряд (D-160).
    """
    from src.engine import city as town
    from src.engine import energy

    узлы = (
        await session.execute(
            select(Node)
            .join(Container, Container.owner_id == Node.id)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE, Item.type_key == PRINTER)
            .distinct()
        )
    ).scalars().all()

    out: list[dict] = []
    for узел in узлы:
        #: Тюремный принтер — не ещё одна дверь в мир (D-174): он печатает
        #: только тех, кого тюрьма держит, и остальным не показывается вовсе.
        from src.engine import justice

        if await justice.is_prison(session, узел) and not (
            identity_id is not None
            and await justice.held(session, constants, identity_id)
        ):
            continue
        предтечи = bool(узел.properties.get(PRECURSOR))
        энергии = 0.0 if предтечи else constants[R.ENERGY_BODY_PRINT]
        железа = 0.0 if предтечи else constants[R.DEATH_IRON_COST]
        город = await town.of_node(session, узел)
        out.append(
            {
                "node": узел.key,
                "name": узел.name,
                "city": None if город is None else город.name,
                "precursor": предтечи,
                "energy": энергии,
                "iron": железа,
                "cost": (
                    0 if предтечи
                    else await energy.price_of(session, constants, узел, энергии)
                ),
                #: Минуты — общая единица показа: двенадцать часов у Предтеч и
                #: три минуты в городе иначе не сравнить.
                "minutes": (
                    constants[R.DEATH_PRINT_TIME_CAPITAL] * MINUTES_IN_HOUR
                    if предтечи
                    else constants[R.DEATH_PRINT_TIME_CITY]
                ),
                "iron_here": await _iron_here(session, узел),
                "at_city_expense": await _city_pays(
                    session, constants, узел, identity_id
                ),
            }
        )
    return sorted(out, key=lambda дверь: дверь["minutes"])


async def order(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    identity: Identity,
    node: Node,
    *,
    now: datetime | None = None,
) -> Job:
    """Заказать печать тела в этом узле. Тело придёт заданием журнала.

    Плата снимается **вперёд**, как материалы партии: печать, за которую не
    заплачено, не начинается. Бесплатная дверь Предтеч не требует ничего, кроме
    терпения.
    """
    moment = now or datetime.now(UTC)
    живое = await alive_body(session, identity.id)
    if живое is not None:
        raise Alive("тело живо: второго одной личности не бывает (D-011)")
    if await pending(session, identity.id) is not None:
        raise AlreadyPrinting("печать уже идёт")

    двор = await world.node_container(session, node)
    есть_принтер = await session.scalar(
        select(Item.id)
        .where(Item.container_id == двор.id, Item.type_key == PRINTER)
        .limit(1)
    )
    if есть_принтер is None:
        raise NoPrinter(f"в узле «{node.name}» нет биопринтера")

    предтечи = bool(node.properties.get(PRECURSOR))
    if предтечи:
        минут = constants[R.DEATH_PRINT_TIME_CAPITAL] * MINUTES_IN_HOUR
        уплачено = 0
    else:
        уплачено = await _charge(session, constants, identity, node, moment=moment)
        минут = constants[R.DEATH_PRINT_TIME_CITY]

    готово = moment + timedelta(minutes=минут)
    event = await events.record(
        session,
        EventKind.BODY_PRINT_ORDERED,
        actor_identity_id=identity.id,
        node_id=node.id,
        precursor=предтечи,
        paid=уплачено,
        ready_at=готово.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BODY_PRINT,
        готово,
        payload={"identity": str(identity.id), "node": str(node.id)},
        dedup_key=f"body.print:{identity.id}:{event.id}",
        cause_event_id=event.id,
    )
    if job is None:  # pragma: no cover — ключ уникален по событию
        raise AlreadyPrinting("печать уже поставлена")
    return job


async def _charge(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    node: Node,
    *,
    moment: datetime,
) -> int:
    """Списать энергию и железо за печать. Возвращает уплаченное деньгами.

    Энергия идёт из пула города и оплачивается по его тарифу; железо берётся со
    двора узла — его туда кто-то привёз, и в этом весь смысл D-013: город
    обязан держать в принтере запас, иначе печатать нечем.
    """
    from src.engine import energy, ledger

    pool = await energy.pool_of(session, constants, node)
    if pool is None:
        raise CannotPay(
            "городской сети здесь нет: печать требует энергии, а её негде взять"
        )
    await energy.produce(session, constants, pool, now=moment)

    надо_энергии = constants[R.ENERGY_BODY_PRINT]
    if float(pool.stored) < надо_энергии:
        raise CannotPay(
            f"в пуле {float(pool.stored):.0f} энергии, а печать требует "
            f"{надо_энергии:.0f}: город без топлива не печатает"
        )

    надо_железа = constants[R.DEATH_IRON_COST]
    двор = await world.node_container(session, node)
    слитки = (
        await session.execute(
            select(Item).where(Item.container_id == двор.id, Item.type_key == IRON)
        )
    ).scalars().all()
    есть = sum(amount_float(слиток.amount) for слиток in слитки)
    if есть < надо_железа:
        raise CannotPay(
            f"в принтере {есть:.0f} железа из {надо_железа:.0f}: "
            "процессор не из чего собрать (D-013)"
        )

    from src.engine import justice

    if await justice.is_prison(session, node) and not await justice.held(
        session, constants, identity.id
    ):
        raise DeathError(
            "тюремный принтер печатает только заключённых: это не дверь в мир"
        )

    цена = await energy.price_of(session, constants, node, надо_энергии)
    за_счёт_города = await _city_pays(session, constants, node, identity.id)
    if цена > 0 and not за_счёт_города:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        остаток = await ledger.balance(session, счёт.id)
        if остаток < цена:
            raise CannotPay(
                f"печать стоит {money_str(цена)} ₭, а на счету {money_str(остаток)} ₭. "
                "Принтер Предтеч в столице печатает бесплатно — но двенадцать часов"
            )
        казна = await ledger.account_for(
            session, AccountKind.CITY_TREASURY, pool.node_id
        )
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=счёт.id,
            credit=казна.id,
            amount=цена,
            memo={"печать тела": node.key, "энергии": надо_энергии},
        )
    else:
        #: За счёт города деньги не двигаются: казна платит энергией, которую
        #: могла бы продать, — тем же порядком, что и за свои постройки (D-149).
        цена = 0

    осталось = amount(надо_железа)
    for слиток in слитки:
        if осталось <= 0:
            break
        взять = min(осталось, слиток.amount)
        if взять == слиток.amount:
            await session.delete(слиток)
        else:
            слиток.amount -= взять
        осталось -= взять

    pool.stored = Decimal(str(float(pool.stored) - надо_энергии))
    await session.flush()
    return цена


async def _city_pays(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    identity_id: uuid.UUID | None = None,
) -> bool:
    """Печатает ли город за свой счёт. Ответ — в его код-законе (D-032).

    «гражданам» значит **гражданам** (D-160): до появления гражданства движок
    читал этот вариант как «всем», и город платил за чужих.
    """
    from src.engine import city as town

    город = await town.of_node(session, node)
    if город is None:
        return False
    решение = (town.law(current_catalog(), город, "body_print") or "").strip().lower()
    if решение in ("", "нет", "-"):
        return False
    if "гражд" in решение:
        return identity_id is not None and await town.is_citizen(
            session, identity_id, город
        )
    return True


async def _iron_here(session: AsyncSession, node: Node) -> float:
    двор = await world.node_container(session, node)
    слитки = (
        await session.execute(
            select(Item).where(Item.container_id == двор.id, Item.type_key == IRON)
        )
    ).scalars().all()
    return sum(amount_float(слиток.amount) for слиток in слитки)


@handler(JobKind.BODY_PRINT)
async def printed(session: AsyncSession, job: Job) -> None:
    """Тело готово. Личность возвращается в мир — там, где заказывала печать."""
    identity = await session.get(Identity, uuid.UUID(job.payload["identity"]))
    node = await session.get(Node, uuid.UUID(job.payload["node"]))
    if identity is None or node is None:  # pragma: no cover
        raise DeathError(f"печать {job.id} ссылается в никуда")
    if await alive_body(session, identity.id) is not None:
        #: Повтор задания после сбоя вторым телом не станет (D-011).
        return
    await world.print_body(session, identity, node)


# --- вспомогательное --------------------------------------------------------


async def alive_body(session: AsyncSession, identity_id: uuid.UUID) -> Body | None:
    return (
        await session.execute(
            select(Body).where(
                Body.identity_id == identity_id, Body.state == BodyState.ALIVE
            )
        )
    ).scalars().first()


async def pending(session: AsyncSession, identity_id: uuid.UUID) -> Job | None:
    """Идущая печать этой личности, если она есть."""
    return (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.BODY_PRINT.value,
                Job.state == JobState.PENDING,
                Job.payload["identity"].astext == str(identity_id),
            )
        )
    ).scalars().first()


