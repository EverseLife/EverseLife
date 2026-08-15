"""Администрация города: должность, право, казна (D-127, D-130, D-154, D-155).

Устав и код-законы лежали данными с D-130, но менять их было некому: «власть
города» не существовала как сущность. Здесь появляются ровно три вещи и ни
одной больше.

**Должность** — запись «личность занимает пост в городе». Как пост называется,
решает город: движку всё равно, президент это или министр экономики.

**Право** — то, что проверяет движок, и оно бывает точечным:

    law:import_duty   править один код-закон
    laws              все код-законы разом; покрывает любой law:<id>
    charter           отвечать на вопросы устава
    treasury          тратить казну
    offices           назначать и снимать должности
    land              раздавать городские участки
    dashboard         полный срез экономической панели
    justice           суд и санкции (объявлено, механика отдельно)

Список конкретных законов в коде **не написан**: он ровно тот, что лежит в
`data/laws.yaml` вольта. Заведут новый код-закон — под него сразу появится
право. Ветвлений по названию должности здесь нет и не будет: иначе каждая новая
форма правления потребовала бы выката версии (01-tech-notes, паттерн 3).

**Казна** — существующий счёт `city_treasury`. Тратит тот, у кого `treasury`;
каждая трата — обычная проводка с основанием, то есть видна.

## Власть присутственна (D-155)

Решения принимаются **в администрации** города: изменение закона, ответ устава,
назначение, трата казны, раздача участков. Власть, осуществимая из-за океана,
не нуждается ни в столице, ни в дорогах к ней, а захват власти становится
вопросом одного нажатия, а не географии.

Чтение панели при этом остаётся удалённым (D-140): цифры — информация, она идёт
по Сети. Присутствие нужно, чтобы **решать**, а не чтобы смотреть.

## Откуда берётся значение закона

Три источника, и порядок между ними жёсткий:

1. решение города — то, что власть записала в `city.laws`;
2. умолчание вольта — `laws.json`, чтобы новый город работал, ничего не
   заполняя (D-130);
3. константа вольта, если умолчание записано ссылкой вида `` `energy.tariff_default` ``.

Город, не решивший ничего, живёт на умолчаниях. Это не «настройки по
умолчанию», а стартовое состояние: как только власть решила иначе, умолчание
перестаёт значить что-либо.

## Смена власти (D-160, D-161, D-162)

Гражданство, голосование и выборы живут рядом: реестр граждан здесь,
процедура — в `engine/vote.py`. Отсюда правитель определяется двумя способами:
назначением (умолчание «основатель бессрочно») и **выборами**, если устав
ответил `ruler_selection: elected_citizens`. Отзыв снимает должность, и город
тут же идёт на выборы.

Правитель для движка — не пост с именем, а **должность с самым широким
набором прав**: ветвлений по названию здесь нет и не будет (D-154).

## Чего здесь нет

Совета (`council_exists`), жребия и наследования власти, а также правки самого
устава голосованием. Устав их описывает, данные лежат, механика приедет своей
задачей.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import events, ledger
from src.engine.jobs import enqueue, handler
from src.models.city import (
    LAW_SCOPE,
    Citizen,
    CitizenshipRequest,
    City,
    CityGrant,
    Office,
    Power,
)
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import money, money_str


class CityError(Exception):
    pass


class NoCity(CityError):
    pass


class NotAllowed(CityError):
    """Полномочия нет. Власть — это запись, а не уверенность в себе."""


class NotEnoughTreasury(CityError):
    """В казне столько нет. Пустая казна — политическое событие."""


class NotReady(CityError):
    """Города ещё нет: не хватает построек, без которых он не город (D-023)."""


class NotYours(CityError):
    """Город закладывают на своей земле. Чужой двор для этого не годится."""


#: Полномочия основателя. Город возникает управляемым: если бы основатель
#: получал пустой набор, первый же город оказался бы без власти вовсе (D-130).
FOUNDER_POWERS: tuple[str, ...] = tuple(power.value for power in Power)
FOUNDER_TITLE = "Президент"

#: Станок, делающий узел администрацией: чем здание является, задаёт станок в
#: нём (D-106). Имя — из `build/recipes.json`, а не из головы.
HALL = "Администрация"


# --- поиск ------------------------------------------------------------------


async def by_id(session: AsyncSession, city_id: uuid.UUID) -> City | None:
    return await session.get(City, city_id)


async def by_node(session: AsyncSession, node_id: uuid.UUID) -> City | None:
    """Город, чей узел-представитель это."""
    return (
        await session.execute(select(City).where(City.node_id == node_id))
    ).scalar_one_or_none()


async def of_node(session: AsyncSession, node: Node) -> City | None:
    """Город, на территории которого стоит узел.

    Территория города — его дети в иерархии показа (D-045). Пойма и шахта висят
    прямо на планете и ничьей властью не накрыты — там законов нет, и это не
    недоделка, а география.
    """
    if node.owner_city_id is not None:
        return await by_id(session, node.owner_city_id)
    #: Узел-представитель — территория собственного города (D-159). Иначе
    #: стоящий в нём человек формально вне города, и присутственная власть в
    #: только что основанном городе оказывается недоступна.
    свой = await by_node(session, node.id)
    if свой is not None:
        return свой
    if node.parent_id is None:
        return None
    родитель = await session.get(Node, node.parent_id)
    if родитель is None or родитель.layer is not Layer.PLANET:
        return None
    return await by_node(session, родитель.id)


# --- основание и должности --------------------------------------------------


async def found(
    session: AsyncSession,
    catalog: Catalog,
    node: Node,
    name: str,
    founder: Identity | None = None,
) -> City:
    """Основать город на узле-представителе. Повторно — вернуть существующий.

    Устав заполняется умолчаниями `laws.json`: город возникает работающим, а не
    пустой анкетой на сорок вопросов (D-130).
    """
    существующий = await by_node(session, node.id)
    if существующий is not None:
        return существующий

    city = City(
        node_id=node.id,
        name=name,
        founder_identity_id=None if founder is None else founder.id,
        charter=dict(catalog.laws.charter_defaults()),
        charter_params={},
        laws={},
    )
    session.add(city)
    await session.flush()

    if founder is not None:
        await _office(
            session,
            city,
            founder.id,
            title=FOUNDER_TITLE,
            powers=FOUNDER_POWERS,
            by=founder.id,
        )

    await events.record(
        session,
        EventKind.CITY_FOUNDED,
        actor_identity_id=None if founder is None else founder.id,
        node_id=node.id,
        city_id=str(city.id),
        name=name,
    )
    return city


#: Без чего город не город (D-023, D-159). Список — четыре роли, а не четыре
#: имени: источник энергии годится любой, лишь бы пул кто-то наполнял.
#: Склада здесь нет не потому, что он не нужен, а потому что предмета «склад»
#: вольт не описывает: требовать несуществующее движок не вправе.
def foundation_needs() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Что обязано стоять в узле до основания: роль → чем закрывается."""
    from src.engine import death, energy, market

    return (
        ("биопринтер", (death.PRINTER,)),
        ("администрация", (HALL,)),
        ("рынок", (market.TERMINAL,)),
        ("источник энергии", (energy.WHEEL, energy.WINDMILL, energy.COAL_PLANT)),
    )


async def missing_for_foundation(
    session: AsyncSession, node: Node
) -> tuple[str, ...]:
    """Чего не хватает узлу, чтобы стать городом. Пусто — можно основывать."""
    from src.engine.world import node_container
    from src.models.inventory import Item

    двор = await node_container(session, node)
    стоит = set(
        (
            await session.execute(
                select(Item.type_key).where(Item.container_id == двор.id).distinct()
            )
        )
        .scalars()
        .all()
    )
    return tuple(
        роль for роль, чем in foundation_needs() if not set(чем) & стоит
    )


async def establish(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body,
    name: str,
) -> City:
    """Основать город на своём узле планеты (D-023, D-098, D-159).

    Порог входа — постройки, а не монета: `city.foundation_cost` в вольте это
    оценка материалов и труда, платить её некому и незачем. Дорогое основание
    отсекает города-однодневки, и каждое основание становится событием.

    Земля под городом перестаёт быть частной: узел записывается за городом, а
    ценная бумага на него гасится — городскую землю раздаёт власть, а не
    рынок (D-089).
    """
    from src.engine import travel
    from src.models.identity import BodyState

    if body.state is not BodyState.ALIVE:
        raise CityError("мёртвое тело городов не основывает")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover — тело всегда стоит в узле
        raise CityError("тело вне узла")
    if node.layer is not Layer.PLANET:
        raise CityError(
            "город закладывают на узле планеты: в чужой застройке города не заводят"
        )
    if node.owner_identity_id != body.identity_id:
        raise NotYours(
            "город закладывают на своей земле: сначала занять участок либо выкупить"
        )
    if await by_node(session, node.id) is not None:
        raise CityError("здесь уже стоит город")
    if node.owner_city_id is not None:
        raise CityError("это уже городская земля")

    не_хватает = await missing_for_foundation(session, node)
    if не_хватает:
        raise NotReady(
            "для города не хватает: " + ", ".join(не_хватает) +
            ". Порог входа — постройки, а не монета (D-023)"
        )

    название = name.strip()
    if not название:
        raise CityError("у города должно быть имя")

    identity = await session.get(Identity, body.identity_id)
    city = await found(session, catalog, node, название, founder=identity)

    #: Локация становится территорией города (40-society/00). Бумага на неё
    #: гасится: городская земля бумагой не торгуется, иначе появился бы
    #: теневой способ сменить хозяина города мимо устава (D-159).
    node.owner_city_id = city.id
    node.owner_identity_id = None
    await _retire_deed(session, node, city)
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_FOUNDED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(city.id),
        name=название,
        founded_by_player=True,
    )
    return city


async def _retire_deed(session: AsyncSession, node: Node, city: City) -> None:
    """Погасить бумагу на узел, ушедший городу."""
    from src.models.estate import Deed

    бумага = (
        await session.execute(select(Deed).where(Deed.node_id == node.id))
    ).scalar_one_or_none()
    if бумага is None:
        return
    await session.delete(бумага)
    await session.flush()
    await events.record(
        session,
        EventKind.DEED_RETIRED,
        node_id=node.id,
        city_id=str(city.id),
        why="земля ушла городу при основании",
    )


async def _office(
    session: AsyncSession,
    city: City,
    identity_id: uuid.UUID,
    *,
    title: str,
    powers: tuple[str, ...],
    by: uuid.UUID | None,
) -> Office:
    office = Office(
        city_id=city.id,
        identity_id=identity_id,
        title=title,
        powers=list(powers),
        appointed_by_identity_id=by,
    )
    session.add(office)
    await session.flush()
    return office


async def install_founder(session: AsyncSession, city: City, who: Identity) -> Office:
    """Поставить основателя во главе города.

    Единственный способ завести власть там, где её ещё нет: у города без
    должностей нет никого, кто мог бы назначить первого. Дальше власть
    передаётся только назначением либо уставом.
    """
    if city.founder_identity_id is not None:
        raise CityError(f"у города «{city.name}» уже есть основатель")
    city.founder_identity_id = who.id
    office = await _office(
        session, city, who.id, title=FOUNDER_TITLE, powers=FOUNDER_POWERS, by=who.id
    )
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=who.name,
        title=FOUNDER_TITLE,
        powers=list(FOUNDER_POWERS),
        founder=True,
    )
    return office


async def offices(session: AsyncSession, city: City) -> list[Office]:
    """Действующие должности города. Сложенные остаются в журнале, но не здесь."""
    rows = (
        await session.execute(
            select(Office).where(
                Office.city_id == city.id, Office.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    return list(rows)


async def powers_of(
    session: AsyncSession, identity_id: uuid.UUID, city: City
) -> set[str]:
    """Права этой личности в этом городе, строками (D-155).

    Право бывает крупным (`treasury`) и точечным (`law:import_duty`). Движок
    хранит их одинаково — строкой, — потому что список конкретных законов
    приходит из вольта и в коде его нет.
    """
    найдено: set[str] = set()
    for office in await offices(session, city):
        if office.identity_id != identity_id:
            continue
        найдено.update(str(raw) for raw in office.powers or ())
    return найдено


def covers(held: set[str], needed: str) -> bool:
    """Покрывает ли набор прав требуемое. `laws` покрывает любой `law:<id>`."""
    if needed in held:
        return True
    return needed.startswith(LAW_SCOPE) and Power.LAWS.value in held


async def may(
    session: AsyncSession, identity_id: uuid.UUID, city: City, power: Power | str
) -> bool:
    нужно = power.value if isinstance(power, Power) else str(power)
    return covers(await powers_of(session, identity_id, city), нужно)


async def require(
    session: AsyncSession, identity_id: uuid.UUID, city: City, power: Power | str
) -> None:
    нужно = power.value if isinstance(power, Power) else str(power)
    if not await may(session, identity_id, city, нужно):
        raise NotAllowed(
            f"нет права «{нужно}» в городе «{city.name}»: "
            "власть — это должность, а не намерение"
        )


async def require_at_hall(
    session: AsyncSession, body, city: City
) -> None:
    """Управление делается **в администрации** этого города (D-155).

    Власть, которую можно осуществлять из-за океана, не нуждается ни в
    столице, ни в дорогах к ней: администрация становится декорацией, а
    захват власти — вопросом одного нажатия, а не географии.

    Читать панель это не касается: цифры идут по Сети (D-140).
    """
    from src.engine import travel, utility, world
    from src.models.identity import BodyState
    from src.models.inventory import Item
    from src.models.world import Node

    if body is None or body.state is not BodyState.ALIVE:
        raise NotAllowed("управлять городом можно только живым телом")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise NotAllowed("тело вне узла")
    if node.owner_city_id != city.id:
        raise NotAllowed(
            f"это не территория города «{city.name}»: власть осуществляется у себя"
        )
    двор = await world.node_container(session, node)
    стоит = await session.scalar(
        select(Item.id)
        .where(Item.container_id == двор.id, Item.type_key == HALL)
        .limit(1)
    )
    if стоит is None:
        raise NotAllowed(
            "здесь нет администрации: решения города принимаются в ней (D-155)"
        )
    if await utility.cut_off(session, node):
        raise NotAllowed(
            "администрация отключена за неуплату: город без неё слеп и нем"
        )


async def appoint(
    session: AsyncSession,
    by: Identity,
    city: City,
    whom: Identity,
    *,
    title: str,
    powers: tuple[str, ...],
    body=None,
) -> Office:
    """Назначить должность. Присутственно: решения принимаются в ратуше (D-155).

    Отдать можно только то, что есть у самого — с учётом покрытия: держащий
    `laws` вправе выдать `law:toll`, держащий `law:toll` — нет. Иначе любой,
    кому дали `offices`, назначил бы себе всё остальное.
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.OFFICES)
    свои = await powers_of(session, by.id, city)
    лишние = {право for право in powers if not covers(свои, право)}
    if лишние:
        raise NotAllowed(
            "нельзя передать то, чего нет у себя: "
            + ", ".join(sorted(лишние))
        )
    if not powers:
        raise CityError("должность без полномочий — это не должность")

    #: Повторное назначение переписывает должность, а не заводит вторую.
    for прежняя in await offices(session, city):
        if прежняя.identity_id == whom.id:
            прежняя.revoked_at = datetime.now(UTC)
    await session.flush()

    office = await _office(
        session, city, whom.id, title=title, powers=tuple(powers), by=by.id
    )
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=whom.name,
        title=title,
        powers=list(powers),
    )
    return office


async def revoke(
    session: AsyncSession, by: Identity, city: City, office: Office, *, body=None
) -> Office:
    """Снять должность. Основателя снять нельзя: это дело устава, а не движка."""
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.OFFICES)
    if office.city_id != city.id:
        raise CityError("должность не этого города")
    if office.identity_id == city.founder_identity_id:
        raise NotAllowed(
            "основателя снимает устав, а не приказ: см. `ruler_recall` и "
            "`charter.silence_days`"
        )
    office.revoked_at = datetime.now(UTC)
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        office_id=str(office.id),
    )
    return office


# --- законы и устав ---------------------------------------------------------


def law(catalog: Catalog, city: City, law_id: str):
    """Значение код-закона: решение города, иначе умолчание вольта.

    Возвращается **как есть**. Закон бывает не только числом и словом: у
    пошлины это карта «товар → ставка и норма» (D-123), и приводить её к
    строке значило бы ломать закон ради единообразия. Потребитель разбирает
    своё значение сам — ветвлений по типу закона здесь нет (D-094).
    """
    свои = city.laws or {}
    if law_id in свои:
        return свои[law_id]
    return catalog.laws.code_law_defaults().get(law_id)


def law_number(
    constants: Constants, catalog: Catalog, city: City | None, law_id: str
) -> float:
    """То же число. Умолчание вида `` `energy.tariff_default` `` разворачивается
    в константу вольта: закон вправе ссылаться на неё, движок — нет (D-065)."""
    raw = (
        catalog.laws.code_law_defaults().get(law_id)
        if city is None
        else law(catalog, city, law_id)
    )
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if text.startswith("`") and text.endswith("`"):
        from src.constants.spec import Num

        return float(constants[Num(text.strip("`"))])
    try:
        return float(text)
    except ValueError:
        #: «нет», «пусто», «свободный» — закон, заданный не числом. Для
        #: числового потребителя это ноль, и это честнее исключения.
        return 0.0


async def set_law(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    by: Identity,
    city: City,
    law_id: str,
    value: str,
    *,
    body=None,
) -> City:
    """Записать код-закон. Проверяется только полномочие и существование закона.

    Значение движок не осмысляет: закон — интерпретируемые данные, и ветвления
    по его смыслу живут у потребителя (налог у стакана, тариф у пула), а не
    здесь. Единственное исключение — тариф: он лежит в пуле отдельной колонкой,
    и её надо подвинуть, иначе решение власти не доедет до счётчика.
    """
    await require_at_hall(session, body, city)
    известные = {закон.id for закон in catalog.laws.code_laws}
    if law_id not in известные:
        raise CityError(f"нет такого код-закона: {law_id}")
    #: Право точечное: «министр экономики» правит пошлины и не трогает налог
    #: (D-155). Держащий `laws` покрыт этой же проверкой. Устав может добавить
    #: к власти совет: «вносит закон совет» — значит законодателей столько,
    #: сколько мест, и правитель среди них не единственный (D-164).
    from src.engine import vote as ballots

    if not await ballots.may_propose(session, city, by.id):
        await require(session, by.id, city, f"{LAW_SCOPE}{law_id}")

    #: Устав может отдать утверждение гражданам (D-161). Тогда власть не
    #: меняет закон, а созывает голосование: право вносить закон и право его
    #: утвердить — разные вещи, и это ровно то, что спрашивает `law_approval`.
    #: Утверждать может и совет, и все граждане — устав решает, кто именно
    #: (D-161, D-164). Правитель в обоих случаях созывает, а не решает.
    from src.models.vote import VoteKind

    голосуют = ballots.voters_for(city, VoteKind.LAW)
    if ballots.by_citizens(city) or голосуют == ballots.COUNCIL_VOTERS:
        await ballots.open_law(session, constants, city, by, law_id, value)
        return city

    законы = dict(city.laws or {})
    было = законы.get(law_id)
    законы[law_id] = value
    city.laws = законы
    await session.flush()

    if law_id == "energy_tariff":
        await _apply_tariff(session, constants, catalog, city)

    await events.record(
        session,
        EventKind.CITY_LAW_SET,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        law=law_id,
        was=было,
        now=value,
    )
    return city


async def _apply_tariff(
    session: AsyncSession, constants: Constants, catalog: Catalog, city: City
) -> None:
    """Довести тариф до пула: у пула он лежит колонкой (D-085)."""
    from decimal import Decimal

    from src.models.energy import EnergyPool

    node = await session.get(Node, city.node_id)
    if node is None:  # pragma: no cover
        return
    pool = (
        await session.execute(select(EnergyPool).where(EnergyPool.node_id == node.id))
    ).scalar_one_or_none()
    if pool is None:
        return
    pool.tariff = Decimal(str(law_number(constants, catalog, city, "energy_tariff")))
    await session.flush()


async def set_charter(
    session: AsyncSession,
    catalog: Catalog,
    by: Identity,
    city: City,
    question_id: str,
    option_id: str,
    param: float | None = None,
    *,
    body=None,
) -> City:
    """Ответить на вопрос устава. Вопрос и вариант обязаны существовать в вольте."""
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.CHARTER)
    вопрос = next((q for q in catalog.laws.charter if q.id == question_id), None)
    if вопрос is None:
        raise CityError(f"нет такого вопроса устава: {question_id}")
    вариант = next((o for o in вопрос.options if o.id == option_id), None)
    if вариант is None:
        raise CityError(f"нет такого варианта: {option_id}")
    if вариант.requires_option is not None:
        #: Вариант, зависящий от другого ответа, без него бессмыслен: «совет
        #: решает» при отсутствующем совете — это не устав, а описка.
        нужен = (city.charter or {}).get(вариант.requires_option)
        if нужен in (None, "none"):
            raise CityError(
                f"вариант «{вариант.label}» требует ответа на «{вариант.requires_option}»"
            )

    #: Устав правится по процедуре, которую сам же и называет (D-163):
    #: `never` — не правится вовсе, две трети либо единогласие — голосованием
    #: граждан. Иначе правитель единолично запрещал бы собственный отзыв.
    from src.constants import current
    from src.engine import vote as ballots

    if ballots.sealed(city):
        raise ballots.Sealed("устав этого города не меняется: так решил он сам")
    if ballots.amends_by_vote(city):
        await ballots.open_charter(
            session, current(), city, by, question_id, option_id, param
        )
        return city

    устав = dict(city.charter or {})
    устав[question_id] = option_id
    city.charter = устав
    if param is not None:
        параметры = dict(city.charter_params or {})
        параметры[question_id] = param
        city.charter_params = параметры
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_CHARTER_SET,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        question=question_id,
        option=option_id,
        param=param,
    )
    return city


#: Условия печати — то, что новичок принимает, выбрав дверь города (D-184).
SPAWN_CITIZENSHIP = "spawn_citizenship"
SPAWN_TERM = "spawn_term"
TRADE_TAX = "tax_trade"


def spawn_terms(
    constants: Constants, catalog: Catalog, city: City | None
) -> tuple[bool, float]:
    """Условия печати города: обязательно ли гражданство и на сколько суток.

    Срок без обязательного гражданства ни к чему не относится и потому равен
    нулю: держать нечего, если никто никуда не вступал.
    """
    if city is None:
        return False, 0.0
    решение = str(law(catalog, city, SPAWN_CITIZENSHIP) or "").strip().lower()
    обязательно = решение.startswith("обяз")
    if not обязательно:
        return False, 0.0
    return True, max(law_number(constants, catalog, city, SPAWN_TERM), 0.0)


async def bind(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    who: Identity,
    *,
    now: datetime | None = None,
) -> Citizen | None:
    """Исполнить условия печати: зачислить в граждане на срок (D-184).

    Приёма не требуется: согласие человек дал выбором двери, и спрашивать его
    второй раз незачем. Срок **записывается сюда**, а не вычитывается из закона
    потом: город, поднявший срок задним числом, не удлиняет уже взятое
    обязательство.

    Ничего не делает, если город условия не ставит либо человек уже где-то
    состоит: печать не вправе сорваться из-за кадрового вопроса.
    """
    обязательно, суток = spawn_terms(constants, catalog, city)
    if not обязательно:
        return None
    if await citizenship(session, who.id) is not None:
        return None

    момент = now or datetime.now(UTC)
    return await _enroll(
        session,
        city,
        who.id,
        why="печать",
        bound_until=None if суток <= 0 else момент + timedelta(days=суток),
    )


async def describe(
    session: AsyncSession, by: Identity, city: City, text: str, *, body=None
) -> City:
    """Написать слово города новичку — то, что стоит на карточке двери (D-183).

    Правит его тот, кто принимает в граждане (D-160): объявление — вербовка, и
    распоряжаться им должен тот, кто отвечает за приток людей, а не казначей.
    Присутственно, как всякое решение города (D-155).

    Движок **не разбирает** написанное и ничего по нему не исполняет. «Участок
    каждому» — обещание, а не код-закон; не сдержали — это иск (D-004), а не
    ошибка движка. Иначе пришлось бы либо читать обещания кодом, либо запретить
    их вовсе, оставив город без голоса.
    """
    from src.runtime import CITY_ABOUT_LIMIT

    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.CITIZENS)

    слово = text.strip()
    if len(слово) > CITY_ABOUT_LIMIT:
        raise CityError(
            f"слово города длиннее {CITY_ABOUT_LIMIT} знаков: карточку читают "
            "за десять секунд"
        )

    было, city.about = city.about, слово
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_DESCRIBED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        was=было,
        now=слово,
    )
    return city


# --- казна ------------------------------------------------------------------


async def treasury(session: AsyncSession, city: City):
    return await ledger.account_for(session, AccountKind.CITY_TREASURY, city.node_id)


async def treasury_balance(session: AsyncSession, city: City) -> int:
    счёт = await treasury(session, city)
    return await ledger.balance(session, счёт.id)


async def spend(
    session: AsyncSession,
    by: Identity,
    city: City,
    to: Identity,
    amount: int,
    *,
    memo: str = "",
    body=None,
) -> int:
    """Заплатить из казны личности. Возвращает уплаченное минорными единицами.

    Ни жалованье, ни награда, ни подряд отдельными механиками не являются:
    все они — это перевод из казны с названным основанием. Названия придумывают
    люди, движку достаточно проводки.
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.TREASURY)
    if amount <= 0:
        raise CityError("трата на ноль — это не трата")

    счёт_казны = await treasury(session, city)
    остаток = await ledger.balance(session, счёт_казны.id)
    if остаток < amount:
        raise NotEnoughTreasury(
            f"в казне {money_str(остаток)} ₭, а нужно {money_str(amount)} ₭"
        )

    кому = await ledger.account_for(session, AccountKind.IDENTITY, to.id)
    await ledger.transfer(
        session,
        PostingReason.SALARY,
        debit=счёт_казны.id,
        credit=кому.id,
        amount=amount,
        memo={"город": city.name, "кому": to.name, "основание": memo},
    )
    await events.record(
        session,
        EventKind.CITY_TREASURY_SPENT,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        to=to.name,
        amount=amount,
        memo=memo,
    )
    return amount


# --- подъёмные новичку (D-153) ----------------------------------------------


async def welcome(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    who: Identity,
) -> int:
    """Выдать подъёмные новичку. Возвращает выданное, ноль — норма.

    Это **перевод, а не эмиссия**: в мире не появляется ни монеты. Город платит
    из своей казны потому, что новый житель — это ВВП: он покупает, продаёт и
    платит налоги. Окупается ли вложение — решает город, а не движок.

    Один раз на личность в одном городе. Переехал — вправе получить в новом:
    так города и конкурируют за людей.
    """
    сколько = money(law_number(constants, catalog, city, "newcomer_grant"))
    if сколько <= 0:
        return 0

    было = (
        await session.execute(
            select(CityGrant).where(
                CityGrant.city_id == city.id, CityGrant.identity_id == who.id
            )
        )
    ).scalar_one_or_none()
    if было is not None:
        return 0

    счёт_казны = await treasury(session, city)
    остаток = await ledger.balance(session, счёт_казны.id)
    if остаток < сколько:
        #: Пустая казна не платит. Это не ошибка новичка и не повод печатать
        #: деньги: город просто беден, и это видно.
        return 0

    кому = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    await ledger.transfer(
        session,
        PostingReason.SALARY,
        debit=счёт_казны.id,
        credit=кому.id,
        amount=сколько,
        memo={"подъёмные": city.name, "кому": who.name},
    )
    session.add(CityGrant(city_id=city.id, identity_id=who.id, amount=сколько))
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_GRANT_PAID,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        amount=сколько,
    )
    return сколько


# --- земля города -----------------------------------------------------------


async def allot(
    session: AsyncSession,
    by: Identity,
    city: City,
    node: Node,
    to: Identity,
    *,
    body=None,
) -> Node:
    """Выделить городской участок жителю (D-089).

    Городскую землю не занимают — её даёт город: кто вправе занимать участки в
    кольцах, отвечает код-закон `build_permit`. Движок проверяет право `land`:
    раздача земли — отдельное решение, а не законотворчество и не трата казны
    (D-155).
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.LAND)
    if node.owner_city_id != city.id:
        raise CityError("это не городской участок")
    if node.owner_identity_id is not None:
        raise CityError("участок уже за кем-то")

    node.owner_identity_id = to.id
    await session.flush()

    #: Выданный участок оформляется бумагой, как и купленный (D-116).
    from src.engine import estate

    await estate.issue_deed(session, node, to.id)

    await events.record(
        session,
        EventKind.LAND_CLAIMED,
        actor_identity_id=to.id,
        node_id=node.id,
        city_id=str(city.id),
        allotted_by=by.name,
    )
    return node


async def survey(
    session: AsyncSession, constants: Constants, catalog: Catalog, city: City
) -> dict:
    """Сводка города: устав, законы, должности, казна. Удалённое чтение.

    Что видно и кому — вопрос устава (`treasury_publicity`), и он лежит рядом.
    Сегодня движок отдаёт всё: скрывать казну не от кого, пока нет второго
    города, а притворяться, что приватность работает, хуже, чем её не иметь.
    """
    люди = {}
    for office in await offices(session, city):
        личность = await session.get(Identity, office.identity_id)
        люди[str(office.id)] = {
            "id": str(office.id),
            "who": "?" if личность is None else личность.name,
            "identity": str(office.identity_id),
            "title": office.title,
            "powers": list(office.powers or ()),
        }

    return {
        "id": str(city.id),
        "name": city.name,
        #: Слово города новичку (D-183): его правит власть, а видят все.
        "about": city.about,
        "node": (await session.get(Node, city.node_id)).key,
        "treasury": await treasury_balance(session, city),
        "offices": list(люди.values()),
        "charter": dict(city.charter or {}),
        "charter_params": dict(city.charter_params or {}),
        #: Вопросы устава словами: клиент не обязан знать, что `ruler_recall` —
        #: это «можно ли отозвать правителя досрочно». Текст живёт в вольте.
        "charter_questions": [
            {
                "id": вопрос.id,
                "section": вопрос.section,
                "question": вопрос.question,
                "options": [
                    {"id": вариант.id, "label": вариант.label} for вариант in вопрос.options
                ],
            }
            for вопрос in catalog.laws.charter
        ],
        #: Законы отдаются **действующими**: своё решение либо умолчание вольта.
        #: Клиенту не нужно знать, откуда взялось значение, — ему нужно знать,
        #: по какому правилу он живёт.
        "laws": {
            закон.id: {
                "name": закон.name,
                "unit": закон.unit,
                "note": закон.note,
                #: Умолчание вида `` `energy.tariff_default` `` разворачивается
                #: в число: игрок обязан видеть действующую ставку, а не ссылку
                #: на константу вольта.
                "value": _shown(constants, catalog, city, закон.id),
                "own": закон.id in (city.laws or {}),
            }
            for закон in catalog.laws.code_laws
        },
    }


def _shown(
    constants: Constants, catalog: Catalog, city: City, law_id: str
) -> str | None:
    raw = law(catalog, city, law_id)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        #: Составной закон (пошлина) уходит клиенту как есть: показывать его
        #: строкой — значит заставлять клиент разбирать её обратно.
        import json

        return json.dumps(raw, ensure_ascii=False)
    text = str(raw).strip()
    if text.startswith("`") and text.endswith("`"):
        return _plain(law_number(constants, catalog, city, law_id))
    return text


def _plain(value: float) -> str:
    """Число без хвоста нулей: тариф «5», а не «5.0»."""
    целое = int(value)
    return str(целое) if value == целое else str(value)


# --- гражданство (D-160) ------------------------------------------------------

#: Вопрос устава «как принимают в граждане» и его варианты (`laws.json`).
ADMISSION = "citizenship_admission"
OPEN, APPLICATION, INVITE = "open", "application", "invite"


class NotCitizen(CityError):
    """Это положено гражданам. Кому именно — решает город, а не движок."""


class AlreadyCitizen(CityError):
    """Гражданство одно на человека: сначала выйти из прежнего города."""


class Bound(CityError):
    """Срок обязательства, принятого при печати, ещё не вышел (D-184)."""


def admission(city: City) -> str:
    """Как этот город принимает в граждане: ответ устава либо «свободно»."""
    return str((city.charter or {}).get(ADMISSION) or OPEN)


async def citizenship(session: AsyncSession, identity_id: uuid.UUID) -> Citizen | None:
    """Гражданство личности, если оно есть. Оно одно — так устроена запись."""
    return (
        await session.execute(select(Citizen).where(Citizen.identity_id == identity_id))
    ).scalar_one_or_none()


async def is_citizen(
    session: AsyncSession, identity_id: uuid.UUID, city: City
) -> bool:
    запись = await citizenship(session, identity_id)
    return запись is not None and запись.city_id == city.id


async def citizens_of(session: AsyncSession, city: City) -> list[Citizen]:
    return list(
        (
            await session.execute(select(Citizen).where(Citizen.city_id == city.id))
        ).scalars().all()
    )


async def request_of(
    session: AsyncSession, identity_id: uuid.UUID, city: City
) -> CitizenshipRequest | None:
    return (
        await session.execute(
            select(CitizenshipRequest).where(
                CitizenshipRequest.identity_id == identity_id,
                CitizenshipRequest.city_id == city.id,
            )
        )
    ).scalar_one_or_none()


async def requests_of(
    session: AsyncSession, city: City
) -> list[CitizenshipRequest]:
    """Очередь: кто просится и кого позвали. Справка, а не решение."""
    return list(
        (
            await session.execute(
                select(CitizenshipRequest).where(CitizenshipRequest.city_id == city.id)
            )
        ).scalars().all()
    )


async def join(
    session: AsyncSession, body, city: City
) -> Citizen | CitizenshipRequest:
    """Проситься в граждане. Что выйдет — решает устав города (D-160).

    Присутственно, в администрации: в граждане записываются там же, где город
    принимает всякое решение (D-155). Возвращается либо гражданство, либо
    заявка — по ответу устава на `citizenship_admission`.
    """
    from src.engine import travel

    await travel.require_here(session, body)
    await require_at_hall(session, body, city)

    имеющееся = await citizenship(session, body.identity_id)
    if имеющееся is not None:
        if имеющееся.city_id == city.id:
            raise AlreadyCitizen("вы уже гражданин этого города")
        raise AlreadyCitizen(
            "гражданство одно на человека: сначала выйти из прежнего города"
        )

    порядок = admission(city)
    зов = await request_of(session, body.identity_id, city)
    #: Приглашение бьёт порядок: позвали — значит принимают, каким бы строгим
    #: устав ни был.
    if порядок == OPEN or (зов is not None and зов.kind == INVITE):
        if зов is not None:
            await session.delete(зов)
        return await _enroll(session, city, body.identity_id, why=порядок)

    if порядок == INVITE:
        raise NotAllowed(
            "в этот город принимают только по приглашению: ждите зова власти"
        )

    #: Остаётся заявка: она ложится и ждёт решения власти.
    if зов is not None:
        return зов
    заявка = CitizenshipRequest(
        identity_id=body.identity_id, city_id=city.id, kind=APPLICATION
    )
    session.add(заявка)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_REQUESTED,
        actor_identity_id=body.identity_id,
        node_id=city.node_id,
        city_id=str(city.id),
        kind_of_request=APPLICATION,
    )
    return заявка


async def invite(
    session: AsyncSession, by: Identity, city: City, who: Identity
) -> CitizenshipRequest:
    """Позвать в граждане. Приглашение ждёт, пока человек придёт и примет."""
    await require(session, by.id, city, Power.CITIZENS)
    if await is_citizen(session, who.id, city):
        raise AlreadyCitizen(f"{who.name} уже гражданин")

    существует = await request_of(session, who.id, city)
    if существует is not None:
        return существует
    зов = CitizenshipRequest(
        identity_id=who.id, city_id=city.id, kind=INVITE, by_identity_id=by.id
    )
    session.add(зов)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_REQUESTED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        who=who.name,
        kind_of_request=INVITE,
    )
    return зов


async def admit(
    session: AsyncSession, by: Identity, city: City, who: Identity
) -> Citizen:
    """Одобрить заявку. Право `citizens`: кадры города — это тоже власть."""
    await require(session, by.id, city, Power.CITIZENS)
    заявка = await request_of(session, who.id, city)
    if заявка is None or заявка.kind != APPLICATION:
        raise CityError("заявки от этого человека нет")
    if await citizenship(session, who.id) is not None:
        raise AlreadyCitizen(f"{who.name} уже состоит в городе")
    await session.delete(заявка)
    return await _enroll(session, city, who.id, why=APPLICATION, by=by.id)


async def leave(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    *,
    now: datetime | None = None,
) -> Citizen:
    """Заявить о выходе. Гражданство спадёт через `city.exit_delay` (D-160).

    Удалённое: заявление идёт по Сети. Задержка существует затем, чтобы нельзя
    было выйти из города прямо перед приговором.
    """
    from src.constants import registry as R
    from src.engine.jobs import enqueue
    from src.models.job import JobKind

    moment = now or datetime.now(UTC)
    запись = await citizenship(session, identity.id)
    if запись is None:
        raise NotCitizen("вы нигде не состоите")
    if запись.leaving_at is not None:
        return запись
    #: Обязательство, принятое при печати (D-184), держит до своего срока.
    #: Держит оно человека, а не город: изгнание обрывает его в любой миг.
    if запись.bound_until is not None and запись.bound_until > moment:
        raise Bound(
            "гражданство взято условием печати и держит до "
            f"{запись.bound_until:%d.%m %H:%M} UTC. Этот срок вы приняли, "
            "выбрав дверь города"
        )

    запись.leaving_at = moment + timedelta(days=constants[R.CITY_EXIT_DELAY])
    await session.flush()
    event = await events.record(
        session,
        EventKind.CITIZENSHIP_LEAVING,
        actor_identity_id=identity.id,
        city_id=str(запись.city_id),
        leaves_at=запись.leaving_at.isoformat(),
    )
    await enqueue(
        session,
        JobKind.CITIZENSHIP_EXIT,
        запись.leaving_at,
        payload={"citizen": str(запись.id)},
        dedup_key=f"citizenship.exit:{запись.id}",
        cause_event_id=event.id,
    )
    return запись


async def exile(
    session: AsyncSession, by: Identity, city: City, who: Identity
) -> None:
    """Изгнать из города. Санкция, а не кадровое решение: право `justice`.

    Варианты устава `court` и `citizens_vote` не исполняются, пока нет суда и
    голосований: движок проверяет право, а кто им обладает — дело города.
    """
    await require(session, by.id, city, Power.JUSTICE)
    запись = await citizenship(session, who.id)
    if запись is None or запись.city_id != city.id:
        raise NotCitizen(f"{who.name} не гражданин этого города")
    await session.delete(запись)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_ENDED,
        actor_identity_id=by.id,
        city_id=str(city.id),
        who=who.name,
        why="изгнание",
    )


async def _enroll(
    session: AsyncSession,
    city: City,
    identity_id: uuid.UUID,
    *,
    why: str,
    by: uuid.UUID | None = None,
    bound_until: datetime | None = None,
) -> Citizen:
    запись = Citizen(
        identity_id=identity_id, city_id=city.id, bound_until=bound_until
    )
    session.add(запись)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_GRANTED,
        actor_identity_id=by or identity_id,
        node_id=city.node_id,
        city_id=str(city.id),
        how=why,
        bound_until=None if bound_until is None else bound_until.isoformat(),
    )
    return запись


def may_take_city_land(catalog: Catalog, city: City, citizen: bool) -> bool:
    """Вправе ли этот человек занимать городские участки (`build_permit`).

    Значение закона — слово города, а не перечисление движка: варианты живут в
    вольте и растут без правки кода (D-094), поэтому читается написанное.
    """
    решение = str(law(catalog, city, "build_permit") or "").strip().lower()
    if not решение:
        return True
    if решение.startswith("никто") or решение in ("нет", "-"):
        return False
    if "гражд" in решение:
        return citizen
    return True


@handler(JobKind.CITIZENSHIP_EXIT)
async def exited(session: AsyncSession, job: Job) -> None:
    """Срок вышел: гражданство спадает (D-160).

    Заявление можно было отозвать — тогда записи уже нет либо срок снят, и
    задание не делает ничего: повтор после сбоя вторым выходом не станет.
    """
    запись = await session.get(Citizen, uuid.UUID(job.payload["citizen"]))
    if запись is None or запись.leaving_at is None:
        return
    город = await by_id(session, запись.city_id)
    await session.delete(запись)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_ENDED,
        actor_identity_id=запись.identity_id,
        city_id=str(запись.city_id),
        why="выход по заявлению",
        city=None if город is None else город.name,
    )


# --- смена власти (D-162) -----------------------------------------------------


async def ruler(session: AsyncSession, city: City) -> Office | None:
    """Действующий правитель: должность с самым широким набором прав.

    Движок знает права, а не посты (D-154): «правитель» — это тот, у кого
    больше всего власти, а как он называется, решает город. При равенстве —
    тот, кто назначен раньше: старшинство разрешает спор без выдумок.
    """
    посты = (
        await session.execute(
            select(Office).where(
                Office.city_id == city.id, Office.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    if not посты:
        return None
    return sorted(посты, key=lambda пост: (-len(пост.powers or []), пост.created_at))[0]


async def hand_over(session: AsyncSession, city: City, who: Identity) -> Office:
    """Передать власть избранному (D-162).

    Новый правитель получает набор прежнего, а не абстрактную «власть»: движок
    знает права, а не посты. Прежняя должность складывается — не удаляется:
    кто чем распоряжался в прошлом месяце, вопрос суда.
    """
    прежний = await ruler(session, city)
    права = tuple(прежний.powers or ()) if прежний is not None else FOUNDER_POWERS
    название = прежний.title if прежний is not None else FOUNDER_TITLE

    if прежний is not None:
        if прежний.identity_id == who.id:
            return прежний
        прежний.revoked_at = datetime.now(UTC)
        await session.flush()
        await events.record(
            session,
            EventKind.CITY_OFFICE_REVOKED,
            node_id=city.node_id,
            city_id=str(city.id),
            title=прежний.title,
            why="выборы",
        )

    пост = await _office(session, city, who.id, title=название, powers=права, by=None)
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=who.name,
        title=название,
        powers=list(права),
        elected=True,
    )
    await schedule_term(session, city, пост)
    return пост


async def schedule_term(
    session: AsyncSession, city: City, office: Office, *, now: datetime | None = None
) -> None:
    """Поставить срок полномочий, если устав его назначил (D-163).

    `ruler_term: fixed` в сутках: по сроку должность снимается сама. Иначе
    «избирается на тридцать суток» значит «пока сам не вспомнит».
    """
    from src.engine import vote as ballots

    if ballots.answer(city, ballots.TERM, "unlimited") != ballots.FIXED_TERM:
        return
    суток = ballots.param(city, ballots.TERM)
    if суток <= 0:
        return
    конец = (now or datetime.now(UTC)) + timedelta(days=суток)
    await enqueue(
        session,
        JobKind.RULER_TERM,
        конец,
        payload={"city": str(city.id), "office": str(office.id)},
        dedup_key=f"city.term:{office.id}",
    )


@handler(JobKind.RULER_TERM)
async def term_ended(session: AsyncSession, job: Job) -> None:
    """Срок вышел: должность снимается, и город идёт на выборы, если умеет."""
    from src.constants import current
    from src.engine import vote as ballots

    office = await session.get(Office, uuid.UUID(job.payload["office"]))
    city = await by_id(session, uuid.UUID(job.payload["city"]))
    if office is None or city is None or office.revoked_at is not None:
        #: Должность сложена раньше срока — отзывом либо выборами. Повтор
        #: задания после сбоя второй отставкой не станет.
        return

    office.revoked_at = job.run_at
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        node_id=city.node_id,
        city_id=str(city.id),
        title=office.title,
        why="срок полномочий вышел",
    )
    if ballots.elects_ruler(city):
        await ballots.open_election(session, current(), city, None)


async def dismiss(session: AsyncSession, city: City) -> Office | None:
    """Снять правителя: отзыв прошёл. Город остаётся без власти до выборов."""
    прежний = await ruler(session, city)
    if прежний is None:
        return None
    прежний.revoked_at = datetime.now(UTC)
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        node_id=city.node_id,
        city_id=str(city.id),
        title=прежний.title,
        why="отзыв",
    )
    return прежний
