"""Разведка: карта прирастает ногами, а не патчем (D-152).

Мир был задан сидом и не рос: занять участок можно было только там, где узел
уже нарисован, а новых жил не появлялось вовсе. Разведка отвечает на вопрос,
откуда берётся мир за стенами, и ответ «его нарисовали разработчики»
противоречит замыслу.

## Три цели поиска, и они разные

Ищут не «что-нибудь», а то, что нужно. Цель выбирается до выхода, и от неё
зависит, что окажется на карте:

| Цель | Где ищут | Что находят |
|---|---|---|
| `lot` | на слое города | свободный участок под постройку — городская земля (D-089) |
| `site` | на планете | место под будущий город: дикий узел со свойствами |
| `vein` | на планете | жила; можно назвать породу заранее |

**Названная порода ищется хуже безымянной.** Шанс множится на её долю в темпе
добычи (`harvest.rates`): медь встречается реже железа, и целиться в редкое —
значит чаще возвращаться ни с чем. Иначе все искали бы только самое дорогое, а
разведка превратилась бы в кран.

## Как устроен заход

Заход — обычное задание журнала: идёт офлайн, переживает перезапуск и
срабатывает ровно один раз. По сроку бросок на шанс; у жилы без названной
породы работает ещё и `explore.vein_share`.

**Пустой заход — норма.** Без него карта росла бы кликом, и разведка стала бы
формальностью.

## Цена захода — свойство места, а не игрока (D-156)

У каждого узла есть счёт находок, сделанных, когда из него выходили. Пока
окрестность нехожена, заход длится `explore.attempt_minutes` — минуты, — и шанс
`explore.find_chance` близок к верному. Каждая находка от этого узла умножает
длительность на `explore.effort_growth`, а шанс на `explore.find_decay`, пока
длительность не упрётся в потолок `explore.attempt_hours`, а шанс — в пол
`explore.find_floor`.

**Выносливость берётся по времени в поле:** `explore.attempt_stamina` — цена
захода полной длины, минутный стоит соответственно меньше. Иначе выносливость
запирала бы ранние заходы вместо часов, и правка свелась бы к смене одного
замка на другой.

Счёт живёт на узле, а не на игроке: уровень разведки был бы прогрессом
персонажа и превратил бы мир в фон для прокачки. Исхоженная окрестность беднеет
для всех сразу, а заход от свежей находки снова дёшев — поэтому карта растёт
вширь, а не звездой из точки рождения.

**Шанс обещан на выходе, а не на возвращении.** Он считается в момент выхода и
едет в задании: пока разведчик в поле, соседи могут исходить окрестность, но
цена уже названа, и менять её задним числом нечестно.

## Что именно находится

Порода жилы выбирается из того, что вообще добывается, — списком `gives`
операции «Добыча» в вольте. Вес породы равен её темпу в `harvest.rates`:
редкое добывается медленнее, значит и попадается реже. Списка «какие бывают
руды» движок не держит: заведут в вольте пятую породу — она начнёт находиться
без правки кода (D-151).

Достоинства места разыгрываются под общий бюджет `site.quality_budget`: место,
хорошее во всём, не выпадает никогда (D-126). Река съедает часть бюджета, и
плодородию остаётся тем меньше, чем больше воды.

**Найденное ничьё.** Нашедший получает право первой ночи, а не право
собственности: участок занимают присутственно, как всякую дикую землю.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import events, travel, world
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind, JobState
from src.models.world import Layer, Node, Surface
from src.units import MINUTES_PER_HOUR, PERCENT

#: Операция вольта, по которой движок узнаёт, что в этом мире вообще добывают.
MINING_OPERATION = "Добыча"

#: Счёт находок, сделанных от этого узла. Лежит в свойствах узла: истощение —
#: свойство места, а не игрока, и миграции под него не нужно (D-156).
FOUND_HERE = "разведано"

#: Цели поиска. Строкой, а не перечислением: список растёт вместе с картой, и
#: клиент называет цель тем же словом, что и движок.
LOT = "lot"
SITE = "site"
VEIN = "vein"
GOALS = (LOT, SITE, VEIN)


class ExploreError(Exception):
    pass


class AlreadyOut(ExploreError):
    """Заход уже идёт. Разведывать в двух направлениях одним телом нельзя."""


class NotOut(ExploreError):
    """Тело не в разведке: возвращаться неоткуда."""


async def pending(session: AsyncSession, body: Body) -> Job | None:
    """Идущий заход этого тела, если он есть."""
    return (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.EXPLORE_SURVEY.value,
                Job.body_id == body.id,
                Job.state == JobState.PENDING,
            )
        )
    ).scalars().first()


async def survey(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    goal: str = SITE,
    resource: str | None = None,
    now: datetime | None = None,
) -> Job:
    """Уйти в разведку от этого узла за названным. Находка придёт по сроку.

    Разведчик **уходит сам**: пока заход идёт, тело в поле и недоступно для
    всего присутственного — как во сне (`travel.require_here`). Вернуться
    раньше срока можно командой `cancel`, но находка тогда не состоится.

    Длительность и шанс зависят от того, насколько окрестность уже исхожена
    (D-156): первый заход отсюда — минуты и почти верная находка, шестой —
    часы и бросок. Выносливость списывается вперёд, как материалы партии, —
    но нехватка сил заход не запирает: чего не хватило, разведчик досыпает в
    поле, и заход просто идёт дольше — на время сна по `body.hibernation_rate`.
    """
    moment = now or datetime.now(UTC)
    if goal not in GOALS:
        raise ExploreError(f"неизвестная цель поиска: {goal}")
    if body.state is not BodyState.ALIVE:
        raise ExploreError("мёртвое тело не разведывает")
    if resource is not None and resource not in mineable(current_catalog()):
        raise ExploreError(f"такой породы в этом мире не добывают: {resource}")
    await travel.require_here(session, body)

    откуда = await session.get(Node, body.node_id)
    if откуда is None:  # pragma: no cover — тело всегда стоит в узле
        raise ExploreError("разведка идёт из узла, а тело стоит в никуда")

    #: Отказ обязан прийти сразу, а не по возвращении: невозможную цель видно
    #: до выхода, и тратить на неё выносливость игрок не должен.
    if goal == LOT:
        from src.engine import city as town

        if await town.of_node(session, откуда) is None:
            raise ExploreError(
                "участок ищут в городе: за стенами городской застройки нет"
            )
    if await pending(session, body) is not None:
        raise AlreadyOut("заход уже идёт: дождитесь возвращения")

    from src.engine import food

    минут = _minutes(constants, откуда, random.Random())
    расход = _stamina(constants, минут) * food.drain_multiplier(
        constants, body, moment
    )
    #: Нехватка сил не запирает заход, а удлиняет его: чего не хватило,
    #: разведчик досыпает в поле по `body.hibernation_rate` и продолжает.
    есть = float(body.stamina)
    if расход > есть:
        дефицит = расход - есть
        минут += дефицит / constants[R.BODY_HIBERNATION_RATE] * MINUTES_PER_HOUR
        body.stamina = Decimal("0")
    else:
        body.stamina = Decimal(str(есть - расход))
    await session.flush()

    #: Шанс называется на выходе и едет в задании: пока разведчик в поле,
    #: окрестность могут исходить соседи, но обещанную цену это не меняет.
    шанс = chance(constants, откуда) * _aim(constants, current_catalog(), goal, resource)
    вернётся = moment + timedelta(minutes=минут)
    event = await events.record(
        session,
        EventKind.EXPLORE_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        stamina=расход,
        goal=goal,
        resource=resource,
        minutes=минут,
        chance=шанс,
        explored=found_here(откуда),
        returns_at=вернётся.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.EXPLORE_SURVEY,
        вернётся,
        payload={
            "body": str(body.id),
            "from": str(body.node_id),
            "goal": goal,
            "resource": resource,
            "chance": шанс,
        },
        dedup_key=f"explore.survey:{body.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover — ключ уникален по событию
        raise AlreadyOut("заход уже поставлен")
    return job


@handler(JobKind.EXPLORE_SURVEY)
async def returned(session: AsyncSession, job: Job) -> None:
    """Разведчик вернулся. Бросок один и засеян заданием: повтор даёт то же."""
    body = await session.get(Body, uuid.UUID(job.payload["body"]))
    откуда = await session.get(Node, uuid.UUID(job.payload["from"]))
    if body is None or откуда is None:  # pragma: no cover
        raise ExploreError(f"заход {job.id} ссылается в никуда")

    constants, catalog = current(), current_catalog()
    бросок = random.Random(str(job.id))
    цель = str(job.payload.get("goal") or SITE)
    заказано = job.payload.get("resource")

    #: Шанс назван на выходе (D-156). Старые задания его не несут — им считаем
    #: по месту, как считалось на выходе.
    шанс = job.payload.get("chance")
    if шанс is None:  # pragma: no cover — заходы, поставленные до D-156
        шанс = chance(constants, откуда) * _aim(constants, catalog, цель, заказано)
    if бросок.random() * PERCENT >= float(шанс):
        await events.record(
            session,
            EventKind.EXPLORE_EMPTY,
            actor_identity_id=body.identity_id,
            node_id=откуда.id,
            goal=цель,
            resource=заказано,
        )
        return

    #: У жилы без названной породы работает прежняя доля `explore.vein_share`:
    #: искал «что-нибудь» — получил что попалось.
    с_жилой = цель == VEIN and (
        заказано is not None
        or бросок.random() * PERCENT < constants[R.EXPLORE_VEIN_SHARE]
    )
    найдено = await _place(session, constants, бросок, откуда, цель=цель, vein=с_жилой)

    порода = None
    if с_жилой:
        порода = заказано or _resource(constants, catalog, бросок)
        богатство = constants[R.EXPLORE_VEIN_RICHNESS]
        запас = constants[R.EXPLORE_VEIN_STOCK]
        await world.create_vein(
            session,
            найдено,
            порода,
            richness=бросок.uniform(богатство.min, богатство.max),
            remaining=бросок.uniform(запас.min, запас.max),
        )
        найдено.name = f"Жила: {порода.lower()}"
        await session.flush()

    #: Участок в городе — шаг по кварталу, находка за стеной — тропа, и её
    #: длину задаёт даль находки (D-180): чем дальше от города, тем дороже шаг.
    if цель == LOT:
        шаг = constants[R.TRAVEL_CITY_STEP]
        секунд = бросок.uniform(шаг.min, шаг.max)
        покрытие = Surface.PAVED
        минут = секунд / MINUTES_PER_HOUR
    else:
        секунд = travel.frontier_seconds(constants, travel.reach_of(найдено))
        минут = секунд / MINUTES_PER_HOUR
        покрытие = Surface.TRAIL
    await travel.connect(
        session, откуда, найдено, base_seconds=секунд, surface=покрытие
    )

    #: Окрестность стала на находку беднее — для всех, кто выйдет отсюда
    #: следующим (D-156). Считается только удача: пустой заход ничего не
    #: исчерпывает, иначе невезение наказывало бы дважды.
    откуда.properties = {**(откуда.properties or {}), FOUND_HERE: found_here(откуда) + 1}
    await session.flush()

    #: Нашёл — значит стоишь там (D-185): разведчик дошёл до места ногами, и
    #: возвращать его в узел выхода значило бы отменить пройденный путь.
    #: Обратная дорога — его решение, и тропу он себе уже проложил.
    body.node_id = найдено.id
    body.node_since = job.run_at
    await session.flush()

    #: Обоз приходит следом, как при обычном переходе (D-157): иначе он
    #: остался бы стоять в узле выхода, а тело оказалось бы «впряжено» в
    #: повозку за полкарты отсюда.
    from src.engine import transport

    обоз = await transport.harnessed(session, body)
    if обоз is not None:
        await transport.follow(session, обоз, найдено)

    await events.record(
        session,
        EventKind.EXPLORE_FOUND,
        actor_identity_id=body.identity_id,
        node_id=найдено.id,
        from_node=откуда.key,
        found=найдено.key,
        name=найдено.name,
        goal=цель,
        resource=порода,
        minutes=минут,
        explored=found_here(откуда),
    )


async def cancel(session: AsyncSession, body: Body) -> Job:
    """Повернуть назад: заход отменяется, тело снова в узле выхода.

    Потраченная выносливость не возвращается — ноги уже пройдены, — а находка
    не состоится: бросок был назначен на срок возвращения, и до него разведчик
    не дошёл. Узел тела не менялся с выхода, поэтому «вернуться» — это снять
    задание, и тело свободно сразу.
    """
    заход = await pending(session, body)
    if заход is None:
        raise NotOut("тело не в разведке: возвращаться неоткуда")
    заход.state = JobState.CANCELLED
    заход.finished_at = datetime.now(UTC)
    await session.flush()

    await events.record(
        session,
        EventKind.EXPLORE_CANCELLED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        goal=str(заход.payload.get("goal") or SITE),
        resource=заход.payload.get("resource"),
    )
    return заход


def found_here(node: Node) -> int:
    """Сколько находок уже сделано от этого узла (D-156)."""
    return int((node.properties or {}).get(FOUND_HERE, 0))


def chance(constants: Constants, node: Node) -> float:
    """Шанс захода отсюда, в процентах. Падает с каждой находкой до пола.

    Пол существует, чтобы исхоженное место беднело, а не запиралось: узел, из
    которого больше нельзя выйти в поле, — тупик, а карта вечная (D-007).
    """
    спад = constants[R.EXPLORE_FIND_DECAY] ** found_here(node)
    return max(
        constants[R.EXPLORE_FIND_FLOOR], constants[R.EXPLORE_FIND_CHANCE] * спад
    )


def _cap(constants: Constants) -> float:
    """Потолок длительности захода в минутах: дальше истощение не растит."""
    return constants[R.EXPLORE_ATTEMPT_HOURS] * MINUTES_PER_HOUR


def _minutes(constants: Constants, node: Node, бросок: random.Random) -> float:
    """Сколько займёт заход отсюда. Каждая находка удлиняет следующий."""
    заход = constants[R.EXPLORE_ATTEMPT_MINUTES]
    истощение = constants[R.EXPLORE_EFFORT_GROWTH] ** found_here(node)
    return min(_cap(constants), бросок.uniform(заход.min, заход.max) * истощение)


def _stamina(constants: Constants, минут: float) -> float:
    """Цена захода выносливостью: по времени в поле, а не поштучно.

    `explore.attempt_stamina` — цена захода полной длины. Поштучная цена
    заперла бы ранние заходы выносливостью ровно там, где D-156 отпирает их
    временем.
    """
    return constants[R.EXPLORE_ATTEMPT_STAMINA] * минут / _cap(constants)


async def outlook(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    goal: str = SITE,
    resource: str | None = None,
) -> dict | None:
    """Во что обойдётся заход отсюда — до выхода.

    Цена разведки меняется от места к месту (D-156), а цена, которую нельзя
    увидеть заранее, читается как случайность движка. Прицельность считается
    здесь же: заказанная порода ищется тем хуже, чем она реже (D-151), и
    показывать «шанс 90%» тому, кто идёт за золотом, значило бы врать.
    """
    узел = await session.get(Node, body.node_id)
    if узел is None:  # pragma: no cover — тело всегда стоит в узле
        return None
    заход = constants[R.EXPLORE_ATTEMPT_MINUTES]
    истощение = constants[R.EXPLORE_EFFORT_GROWTH] ** found_here(узел)
    короткий = min(_cap(constants), заход.min * истощение)
    длинный = min(_cap(constants), заход.max * истощение)
    прицел = _aim(constants, current_catalog(), goal, resource)
    return {
        "explored": found_here(узел),
        "minutes": {"min": короткий, "max": длинный},
        #: Наибольшая из возможных: игрок должен знать потолок, а не среднее.
        "stamina": _stamina(constants, длинный),
        "chance": chance(constants, узел) * прицел,
        #: Во сколько раз заказ породы сузил шанс: игрок видит не только
        #: «мало», но и почему мало (D-151).
        "aim": прицел,
        "resource": resource,
    }


def mineable(catalog: Catalog) -> tuple[str, ...]:
    """Что в этом мире вообще добывают — списком `gives` операции «Добыча».

    Список пород движок не держит: заведут в вольте пятую — она появится и в
    выборе цели, и в находках, без правки кода (D-151).
    """
    операция = next(
        (op for op in catalog.recipes.operations if op.name == MINING_OPERATION), None
    )
    return tuple(операция.gives) if операция is not None else ()


def _aim(
    constants: Constants, catalog: Catalog, цель: str, заказано: str | None
) -> float:
    """Множитель шанса за прицельность.

    Названная порода ищется хуже безымянной, и ровно во столько раз, во
    сколько она реже: доля её темпа в `harvest.rates` от самого быстрого.
    Второй таблицы редкости не заводим — она разошлась бы с первой (D-151).
    """
    if цель != VEIN or заказано is None:
        return 1.0
    темпы = constants[R.HARVEST_RATES]
    добывают = [имя for имя in mineable(catalog) if float(темпы.get(имя, 0)) > 0]
    if заказано not in добывают:
        return 1.0
    самое_частое = max(float(темпы[имя]) for имя in добывают)
    return float(темпы[заказано]) / самое_частое


async def _place(
    session: AsyncSession,
    constants: Constants,
    бросок: random.Random,
    откуда: Node,
    *,
    цель: str,
    vein: bool,
) -> Node:
    """Завести найденный узел рядом с тем, откуда вышли.

    Участок города встаёт **в городе** и принадлежит ему: городскую землю не
    занимают, её раздаёт власть (D-089). Всё прочее висит на планете и остаётся
    ничьим — нашедший получает право первой ночи, а не собственность (D-152).
    """
    from src.engine import city as town

    #: Ключ узла обязан быть устойчивым и уникальным навсегда: карта вечная,
    #: вайпов не бывает (D-007), а «дикий участок 3» рано или поздно совпадёт.
    ключ = f"terra.wild.{uuid.uuid4().hex}"

    if цель == LOT:
        город = await town.of_node(session, откуда)
        if город is None:
            raise ExploreError("участок ищут в городе: за стенами застройки нет")
        представитель = await session.get(Node, город.node_id)
        кольцо = constants[R.LAND_AREA_RING1]
        участок = await world.create_node(
            session,
            ключ,
            "Свободный участок",
            area_m2=бросок.uniform(кольцо.min, кольцо.max),
            layer=Layer.CITY,
            parent=представитель,
            planet=откуда.planet,
            properties={"участок": True, "кольцо": откуда.properties.get("кольцо", 0)},
        )
        участок.owner_city_id = город.id
        await session.flush()
        return участок

    корень = await _planet_root(session, откуда)
    площадь = constants[R.EXPLORE_NODE_AREA]
    return await world.create_node(
        session,
        ключ,
        "Место под город" if цель == SITE else "Дикий участок",
        area_m2=бросок.uniform(площадь.min, площадь.max),
        layer=Layer.PLANET,
        parent=корень,
        planet=откуда.planet,
        #: Даль растёт на шаг от того узла, откуда вышли (D-180): фронтир
        #: удаляется сам, по мере того как его двигают.
        properties=_properties(constants, бросок, vein=vein)
        | {travel.REACH: travel.reach_of(откуда) + 1},
    )


def _properties(
    constants: Constants, бросок: random.Random, *, vein: bool
) -> dict:
    """Свойства места под общий бюджет достоинств (D-126).

    Идеального места не бывает: река съедает часть бюджета, и плодородию
    остаётся тем меньше, чем больше воды.
    """
    бюджет = constants[R.SITE_QUALITY_BUDGET]
    река = бросок.random() * PERCENT < constants[R.SITE_RIVER_SHARE]
    на_воду = бросок.uniform(0, бюджет) if река else 0.0
    на_землю = max(0.0, бюджет - на_воду)

    температура = constants[R.SITE_TEMP_RANGE]
    осадки = constants[R.SITE_RAIN_RANGE]
    return {
        "вода": "река" if река else "нет",
        #: На жильной находке пашня ни при чём: порода не родит хлеба.
        "плодородие": 0 if vein else round(PERCENT * на_землю / бюджет),
        "температура": round(бросок.uniform(температура.min, температура.max)),
        "осадки": round(бросок.uniform(осадки.min, осадки.max)),
        "дикий": True,
    }


def _resource(constants: Constants, catalog: Catalog, бросок: random.Random) -> str:
    """Что за порода. Список — из вольта, вес — из темпа добычи (D-151).

    Редкое добывается медленнее, значит и попадается реже. Второй таблицы
    редкости не заводим: она разошлась бы с первой.
    """
    темпы = constants[R.HARVEST_RATES]
    операция = next(
        (op for op in catalog.recipes.operations if op.name == MINING_OPERATION), None
    )
    если_нет = "Камень"
    if операция is None:  # pragma: no cover — операция добычи есть по построению
        return если_нет
    порода = [имя for имя in операция.gives if float(темпы.get(имя, 0)) > 0]
    if not порода:  # pragma: no cover
        return если_нет
    веса = [float(темпы[имя]) for имя in порода]
    return бросок.choices(порода, weights=веса)[0]


async def _planet_root(session: AsyncSession, node: Node) -> Node | None:
    """Планета, на которой стоит узел: идём вверх по иерархии показа."""
    текущий = node
    while текущий.parent_id is not None:
        родитель = await session.get(Node, текущий.parent_id)
        if родитель is None:  # pragma: no cover
            return None
        if родитель.layer is Layer.SPACE:
            return родитель
        текущий = родитель
    return None
