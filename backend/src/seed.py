"""Стартовый мир альфы: столица Терры, какой она встречает первого игрока.

Запуск из `backend/`: `python -m src.seed`. Повторный запуск ничего не портит —
мир создаётся один раз и дальше живёт своей жизнью.

**Это сценарий разработки, а не часть игры.** Всё, что он кладёт в руки, идёт
через `world.grant_item` с явным основанием: материя в мире не появляется
безымянно (столп П1), и такой приход обязан быть виден в телеметрии.

## Форма мира

Город — не место, а **группа локаций, связанных короткими рёбрами** (D-045,
D-089). Отсюда стартовая карта: ядро с Принтером Предтеч, первое кольцо с
Библиотекой, терминалом и администрацией, второе с кузнечным двором и
свободными участками под дома, забой у стены и отдельный узел «выход из
города» (D-097), за которым начинается настоящая логистика.

Шаг внутри города — секунды (`travel.city_step`), выезд за стены — `даль 1`,
то есть `travel.frontier_step` по **дороге**, а не по бездорожью: по ней возят
уголь, и город, который топит электростанцию, эту дорогу себе построил. Дальше
каждое кольцо дали дороже предыдущего (D-180) — вот и вся география: за станком
сходить дёшево, за углём выехать, до фронтира снаряжать экспедицию.

Столица заводится **городом-институтом** (D-154): у неё есть устав, код-законы
и казна, а первый игрок становится её президентом. Всё, что власть потом
меняет, она меняет сама — сид только ставит начальное положение.

Два допущения на время разработки, и оба честно названы:

* **деньги выданы городу, а не игроку** — банка и кредита нет до Э4, а без
  денег в казне не с чего платить подъёмные. Выпуск идёт через счёт `genesis`,
  то есть виден в проверке инвариантов. Игроки печатаются с нулём и получают
  подъёмные по решению города (D-153);
* **уголь выдан Тэрну** — угольная шахта на карте есть, но идти за ним двадцать
  минут, а посмотреть плавку хочется сегодня.
"""

from __future__ import annotations

import asyncio
import logging
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import bootstrap, current, current_catalog
from src.constants import registry as R
from src.db.base import dispose, session_factory
from src.engine import city as town
from src.engine import death, justice, ledger, market, tick, travel, utility, world
from src.models.identity import Identity
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Edge, Layer, Node, Surface
from src.settings import settings
from src.units import PERCENT, money

log = logging.getLogger("octoverse.seed")

CORE = "terra.capital.core"
#: Порода, а не «руда вообще» (D-151): у железа и меди разные жилы.
IRON = "Железная руда"
COPPER = "Медная руда"
COAL = "Уголь"
PICK = "Железная кирка"
TIMBER = "Шахтная крепь"
#: Деньги кладутся **в казну столицы**, а не в карман игроку (D-153). Игрок
#: печатается с нулём и получает подъёмные по решению города — то есть по
#: механике, а не по сценарию.
CITY_TREASURY_START = 5_000
#: Подъёмные, которые столица решила платить. Это решение власти, записанное
#: сидом за неимением живого президента в первую секунду мира.
NEWCOMER_GRANT = "120"
#: Почта и пароль стартовых личностей — тестовые данные разработки (D-187):
#: под ними разработчики входят в альфу. В бою пароли меняются из кабинета.
FOUNDERS = {
    "Тэрн": {
        "email": "tern@octoverse.world",
        "password": "tern-terra-2026",
        "surname": "Первопечатный",
        "age": 34,
        "about": "Шахтёр и основатель столицы: первый, кого напечатала машина.",
    },
    "Хём": {
        "email": "hem@octoverse.world",
        "password": "hem-terra-2026",
        "surname": "Торговый",
        "age": 29,
        "about": "Торговец у терминала: первый стакан столицы — его железо.",
    },
}


async def seed(session: AsyncSession) -> Node:
    """Создать стартовый мир, если его ещё нет, иначе — довести до сегодняшнего."""
    existing = (
        await session.execute(select(Node).where(Node.key == CORE))
    ).scalar_one_or_none()
    if existing is not None:
        log.info("стартовый мир уже есть: %s", existing.key)
        await догнать(session, existing)
        return existing

    constants = current()
    бросок = random.Random(CORE)
    шаг = constants[R.TRAVEL_CITY_STEP]

    #: Слои — абстракция показа над одним графом (D-045): Терра видна из
    #: космоса, столица — с планеты, застройка — в городе. Ходят по листьям.
    терра = await world.create_node(
        session, "terra", "Терра", area_m2=1,
        layer=Layer.SPACE, properties={},
    )
    столица = await world.create_node(
        session, "terra.capital", "Столица Терры", area_m2=1,
        layer=Layer.PLANET, parent=терра, properties={},
    )

    #: Ядро: к биопринтеру приходит каждый умерший, поэтому город начинается с него.
    #: Свойство «предтечи» — не украшение: по нему движок узнаёт ту самую вечную
    #: машину, которая печатает бесплатно и медленно (D-028).
    ядро = await world.create_node(
        session, CORE, "Ядро: Принтер Предтеч", area_m2=120,
        parent=столица, properties={"кольцо": 0, "лес": False, "предтечи": True},
    )
    библиотека = await world.create_node(
        session, "terra.capital.library", "Библиотека", area_m2=200,
        parent=столица, properties={"library": True, "кольцо": 1},
    )
    рынок = await world.create_node(
        session, "terra.capital.market", "Торговый двор", area_m2=200,
        parent=столица, properties={"кольцо": 1},
    )
    кузница = await world.create_node(
        session, "terra.capital.forge", "Кузнечный двор", area_m2=260,
        parent=столица, properties={"кольцо": 2},
    )
    забой = await world.create_node(
        session, "terra.capital.pit", "Забой у стены", area_m2=300,
        parent=столица, properties={"кольцо": 3},
    )
    #: Администрация — узел, в котором стоит станок «Администрация»: чем
    #: здание является, задаёт станок в нём (D-106). Власть тоже где-то живёт.
    ратуша = await world.create_node(
        session, "terra.capital.hall", "Администрация", area_m2=180,
        parent=столица, properties={"кольцо": 1},
    )
    ворота = await world.create_node(
        session, "terra.capital.gate", "Выход из города", area_m2=80,
        parent=столица, properties={"кольцо": 3, "выход": True},
    )
    #: Первое кольцо за стенами: даль 1 (D-180) — двадцать секунд ходу, а не
    #: двадцать минут. Ближний ресурс возят каждый день, и в этом весь смысл.
    шахта = await world.create_node(
        session, "terra.coal", "Угольная шахта", area_m2=400,
        layer=Layer.PLANET, parent=терра,
        properties={"лес": True, "вода": "нет", travel.REACH: 1},
    )
    #: Каторга столицы (D-174, D-176): жила, принтер и терминал за одной
    #: стеной. Тюрьмой узел делает станок «Каторга», а не свойство: власть
    #: строит новые каторги сама, как всякое здание.
    тюрьма = await world.create_node(
        session, "terra.capital.jail", "Каторжный забой", area_m2=120,
        parent=столица, properties={"кольцо": 3},
    )
    #: Свободные участки второго кольца: город раздаёт их жителям (D-089), и
    #: только на своей земле ремесленник ставит свой станок (D-150).
    участки = [
        await world.create_node(
            session, f"terra.capital.lot{номер}", f"Свободный участок {номер}",
            area_m2=бросок.uniform(*_кольцо(constants)),
            parent=столица, properties={"кольцо": 2, "участок": True},
        )
        for номер in (1, 2, 3)
    ]
    #: Пашня — за городом: земля в кольцах слишком дорога для полбы (D-089).
    #: Свойства места разыграны рукой сида — генератор узлов приедет с
    #: разведкой (D-126, D-132).
    пойма = await world.create_node(
        session, "terra.floodplain", "Пойма у реки", area_m2=400,
        layer=Layer.PLANET, parent=терра,
        properties={"вода": "река", "плодородие": 55, travel.REACH: 1},
    )

    #: Внутри города — короткие рёбра, снаружи — настоящий переход.
    for один, другой in (
        (ядро, библиотека),
        (ядро, рынок),
        (ядро, ратуша),
        (библиотека, рынок),
        (библиотека, ратуша),
        (рынок, кузница),
        (кузница, забой),
        (ядро, ворота),
        (забой, ворота),
        *((кузница, участок) for участок in участки),
    ):
        await travel.connect(
            session, один, другой,
            base_seconds=бросок.uniform(шаг.min, шаг.max),
            surface=Surface.PAVED,
        )
    #: К шахте и пойме ведёт дорога, а не бездорожье: по ней возят уголь и
    #: хлеб, и город эти дороги себе построил. Длина — по дали узла (D-180).
    for куда in (шахта, пойма):
        await travel.connect(
            session, ворота, куда,
            base_seconds=travel.frontier_seconds(constants, travel.reach_of(куда)),
            surface=Surface.ROAD,
        )
    await travel.connect(
        session, ворота, тюрьма,
        base_seconds=бросок.uniform(шаг.min, шаг.max),
        surface=Surface.PAVED,
    )

    #: Порода у каждой жилы своя (D-151): железо у стены, уголь и попутная медь
    #: в шахте. Медный узел и железный — разные места с разной ценой.
    await world.create_vein(session, забой, IRON, richness=62, remaining=50_000)
    await world.create_vein(session, шахта, COAL, richness=48, remaining=30_000)
    await world.create_vein(session, шахта, COPPER, richness=41, remaining=18_000)
    await world.create_vein(session, тюрьма, IRON, richness=35, remaining=20_000)
    await _станок(session, тюрьма, death.PRINTER, 50)
    await _станок(session, тюрьма, market.TERMINAL, 50)
    await _станок(session, тюрьма, justice.KATORGA, 55)

    await _станок(session, ратуша, "Администрация", 65)
    #: Библиотека — станок (D-176): окно знаний показывается там, где он стоит.
    await _станок(session, библиотека, world.LIBRARY, 70)
    #: Принтер Предтеч: бесплатно и двенадцать часов (D-028). Он же — единственная
    #: дверь в мир, которая не закрывается никогда, и потому стоит в ядре.
    await _станок(session, ядро, death.PRINTER, 90)
    #: Городской принтер при кузнице: минуты вместо часов, но за энергию и
    #: железо. Город продаёт не жизнь, а скорость (D-028, D-033).
    await _станок(session, кузница, death.PRINTER, 60)
    await _станок(session, рынок, market.TERMINAL, 70)
    #: Монетный станок при торговом дворе: монету чеканят там, где ей ходить
    #: (D-016). Постройка городская.
    await _станок(session, рынок, "Монетный станок", 60)
    await _станок(session, кузница, "Плавильная печь", 55)
    await _станок(session, кузница, "Верстак", 60)
    await _станок(session, кузница, "Кузница", 65)
    #: Кровать при мастерской: своих построек нет до Э3, мастер живёт при деле.
    await _станок(session, кузница, "Кровать", 50)
    #: Сундук при кузнице (D-181): городская мастерская — первое место, где
    #: игрок увидит, что нажитое можно положить, а не таскать в руках.
    await _станок(session, кузница, "Сундук", 55)
    #: Электростанция городская (D-082): стоит в застройке и питает весь город
    #: из одного пула. Уголь к ней возят игроки — без подвоза она мертва.
    await _станок(session, кузница, "Угольная станция", 60)
    двор_кузницы = await world.node_container(session, кузница)
    await world.grant_item(
        session, двор_кузницы, COAL, amount=200, quality=55,
        origin="стартовый мир: первый подвоз угля на станцию",
    )
    #: Запас железа в принтере: город обязан его держать, иначе печатать не из
    #: чего (D-013). Дальше пополняют игроки — это и делает приток населения
    #: политическим вопросом, а не фоном.
    await world.grant_item(
        session, двор_кузницы, death.IRON, amount=50, quality=55,
        origin="стартовый мир: запас процессоров в биопринтере",
    )

    #: Столица — город-институт (D-154): устав из умолчаний вольта, казна и
    #: код-законы. Всё, что здесь ставится, власть потом меняет сама.
    город = await town.found(session, current_catalog(), столица, столица.name)
    #: Тюрьма — тоже земля города (D-176): государственная локация не бывает
    #: «свободным участком».
    for узел in (ядро, библиотека, рынок, кузница, забой, ратуша, ворота, тюрьма, *участки):
        узел.owner_city_id = город.id
    await session.flush()
    await _казна(session, город)
    #: Первое решение города: платить новичкам подъёмные. Записано сидом за
    #: неимением живого президента в первую секунду мира — дальше это обычный
    #: код-закон, и меняется он из администрации (D-153).
    город.laws = {"newcomer_grant": NEWCOMER_GRANT}
    await session.flush()

    #: Пул города заводится сразу: он есть у города по построению (D-071).
    from src.engine import energy

    await energy.ensure_pools(session, constants)

    тэрн, тело_тэрна = await world.spawn(session, "Тэрн", ядро, **_учётка("Тэрн"))
    #: Первый игрок и есть основатель: власть в городе появляется вместе с
    #: первым человеком, а не отдельным сценарием (D-154).
    await town.install_founder(session, город, тэрн)
    карман = await world.body_container(session, тело_тэрна)
    await world.grant_item(
        session, карман, PICK, quality=55, origin="стартовый мир: снаряжение шахтёра"
    )
    await world.grant_item(
        session, карман, TIMBER, amount=10, quality=50, origin="стартовый мир: снаряжение шахтёра"
    )
    await world.grant_item(
        session, карман, COAL, amount=30, quality=58, origin="стартовый мир: запас угля"
    )
    #: Семенной фонд новичка: семена — предмет, отдельный от урожая (D-057).
    #: Сорт базовый, ничей: с него начинают все, а дальше фермер либо ведёт
    #: отбор, либо смотрит, как фонд вырождается.
    from src.engine import breed

    for культура, сколько in (("spelt", 300), ("turnip", 200)):
        сорт = await breed.landrace(session, current_catalog(), культура)
        await breed.seed_lot(
            session, current_catalog(), карман.id, сорт, сколько, PERCENT
        )
    #: Кухня новичка: готовка не требует ни земли, ни капитала (D-119) —
    #: очаг на пойме, утварь и продукты в карман.
    await _станок(session, пойма, "Очаг", 70)
    #: Питомник при пойме: скрещивание требует места, как всякая работа (D-057).
    await _станок(session, пойма, "Селекционный питомник", 60)
    await world.grant_item(
        session, карман, "Глиняный горшок", quality=65, origin="стартовый мир: утварь"
    )
    for продукт, сколько, качество in (
        ("Бобы", 10, 60), ("Овощи", 10, 55), ("Масло", 3, 50), ("Соль", 3, 50),
    ):
        await world.grant_item(
            session, карман, продукт, amount=сколько, quality=качество,
            origin="стартовый мир: продукты",
        )

    #: Аффинированный металл Тэрну: золотоносной породы в стартовых жилах нет,
    #: а посмотреть на чеканку и на пробу хочется сегодня — то же допущение,
    #: что с углём.
    for металл, сколько in (("Аффинированное золото", 20), ("Аффинированное серебро", 60)):
        await world.grant_item(
            session, карман, металл, amount=сколько, quality=60,
            origin="стартовый мир: металл на пробу",
        )

    хём, тело_хёма = await world.spawn(session, "Хём", рынок, **_учётка("Хём"))
    карман_хёма = await world.body_container(session, тело_хёма)
    await world.grant_item(
        session, карман_хёма, IRON, amount=30, quality=64,
        origin="стартовый мир: запас торговца",
    )
    #: Чтобы в стакане было на что посмотреть с первой минуты.
    await market.load(session, constants, тело_хёма, IRON, 30)
    await market.sell(
        session, constants, current_catalog(), хём, рынок,
        type_key=IRON,
        tier=market.tier_of(constants, 64),
        price=money(3),
        quantity=30,
    )

    #: Аккумулятор Тэрну: энергия не лежит в мешке, её возят в нём (D-071).
    await world.grant_item(
        session, карман, "Аккумулятор", quality=55,
        origin="стартовый мир: аккумулятор",
    )

    #: Здания городских узлов: станок ставится в здание и занимает площадь
    #: (D-106), и сид обязан дать зданию встать раньше, чем станку. Городская
    #: застройка считается застроенной целиком.
    await _здания(session)

    await tick.ensure_scheduled(session)
    #: Счётчик быта тикает наравне с часами мира: содержание идёт временем и
    #: без игроков (D-149).
    await utility.ensure_scheduled(session)
    log.info(
        "стартовый мир создан: столица Терры с администрацией, шахта, игроки Тэрн и Хём"
    )
    return ядро


async def догнать(session: AsyncSession, ядро: Node) -> None:
    """Довести уже существующий мир до сегодняшнего устройства.

    Мир вечный, вайпов не бывает (D-007): «пересоздайте базу» — не ответ. Сюда
    попадает то, что нельзя доложить миграцией, потому что это не схема, а
    содержание: город как институт, здание администрации, свободные участки.

    Всё до одного шага идемпотентно: повторный запуск ничего не удваивает.
    """
    constants = current()
    бросок = random.Random(f"{CORE}:догнать")
    шаг = constants[R.TRAVEL_CITY_STEP]

    столица = await session.get(Node, ядро.parent_id)
    if столица is None:  # pragma: no cover — ядро без города это баг
        return

    #: Вход по почте и паролю (D-187): личности, заведённые до него, получают
    #: тестовые учётки сида. Только те, у кого почты ещё нет — назначенное
    #: руками или из кабинета догон не трогает.
    await _учётки_догоном(session)

    город = await town.by_node(session, столица.id)
    if город is None:
        город = await town.found(session, current_catalog(), столица, столица.name)
        город.laws = {"newcomer_grant": NEWCOMER_GRANT}
        await _казна(session, город)
        log.info("город основан на существующем мире: %s", город.name)

    #: Городская земля: застройка столицы принадлежит городу, и с неё же он
    #: получает налоги и на неё же тратит энергию (D-149).
    дети = (
        await session.execute(select(Node).where(Node.parent_id == столица.id))
    ).scalars().all()
    for узел in дети:
        if узел.owner_city_id is None and узел.owner_identity_id is None:
            узел.owner_city_id = город.id
    await session.flush()

    #: Права дробятся (D-155), и у должностей, заведённых до этого, набор
    #: старый: `dashboard`, `charter` и `land` в нём просто отсутствуют. У
    #: основателя полномочия полны по построению — дополняем, а не переписываем.
    for пост in await town.offices(session, город):
        if пост.identity_id == город.founder_identity_id:
            пост.powers = list(town.FOUNDER_POWERS)
    await session.flush()

    #: Президент: первый игрок мира. Власть появляется вместе с человеком, а
    #: не отдельным сценарием (D-154).
    if город.founder_identity_id is None:
        первый = (
            await session.execute(select(Identity).order_by(Identity.created_at))
        ).scalars().first()
        if первый is not None:
            await town.install_founder(session, город, первый)

    #: Принтер Предтеч и городской принтер: без них смерть стала бы билетом в
    #: один конец, а мир существует дольше, чем механика печати (D-028).
    свойства = dict(ядро.properties or {})
    if not свойства.get(death.PRECURSOR):
        свойства[death.PRECURSOR] = True
        ядро.properties = свойства
    await _станок_если_нет(session, ядро, death.PRINTER, 90)

    ратуша = await _узел_если_нет(
        session, "terra.capital.hall", "Администрация", 180, столица, {"кольцо": 1}
    )
    if ратуша is not None:
        await _станок(session, ратуша, "Администрация", 65)
        ратуша.owner_city_id = город.id
        await travel.connect(
            session, ядро, ратуша,
            base_seconds=бросок.uniform(шаг.min, шаг.max), surface=Surface.PAVED,
        )

    #: Библиотека и каторга — станки (D-176): миры, обставленные до этого,
    #: получают их задним числом, и «свободный участок» на их месте исчезает.
    библиотека = (
        await session.execute(
            select(Node).where(Node.key == "terra.capital.library")
        )
    ).scalar_one_or_none()
    if библиотека is not None:
        await _станок_если_нет(session, библиотека, world.LIBRARY, 70)
    #: Каторга столицы (D-174, D-176): мир, заведённый до неё, получает узел
    #: целиком — жила, принтер, терминал и сам станок «Каторга».
    новая_тюрьма = await _узел_если_нет(
        session, "terra.capital.jail", "Каторжный забой", 120, столица,
        {"кольцо": 3},
    )
    тюрьма = новая_тюрьма or (
        await session.execute(
            select(Node).where(Node.key == "terra.capital.jail")
        )
    ).scalar_one_or_none()
    if тюрьма is not None:
        тюрьма.owner_city_id = город.id
        await _станок_если_нет(session, тюрьма, justice.KATORGA, 55)
        await _станок_если_нет(session, тюрьма, death.PRINTER, 50)
        await _станок_если_нет(session, тюрьма, market.TERMINAL, 50)
    if новая_тюрьма is not None:
        await world.create_vein(session, новая_тюрьма, IRON, richness=35, remaining=20_000)
        выход = (
            await session.execute(
                select(Node).where(Node.key == "terra.capital.gate")
            )
        ).scalar_one_or_none()
        if выход is not None:
            await travel.connect(
                session, выход, новая_тюрьма,
                base_seconds=бросок.uniform(шаг.min, шаг.max),
                surface=Surface.PAVED,
            )

    кузница = (
        await session.execute(
            select(Node).where(Node.key == "terra.capital.forge")
        )
    ).scalar_one_or_none()
    if кузница is not None:
        await _станок_если_нет(session, кузница, death.PRINTER, 60)
        #: Сундук при кузнице (D-181): миры, заведённые до хранилищ, получают
        #: его задним числом — иначе положить вещи по-прежнему негде.
        await _станок_если_нет(session, кузница, "Сундук", 55)
        двор = await world.node_container(session, кузница)
        if not await _есть_в(session, двор, death.IRON):
            await world.grant_item(
                session, двор, death.IRON, amount=50, quality=55,
                origin="догоняющий сид: запас процессоров в биопринтере",
            )
    for номер in (1, 2, 3):
        участок = await _узел_если_нет(
            session, f"terra.capital.lot{номер}", f"Свободный участок {номер}",
            бросок.uniform(*_кольцо(constants)), столица,
            {"кольцо": 2, "участок": True},
        )
        if участок is not None:
            участок.owner_city_id = город.id
            if кузница is not None:
                await travel.connect(
                    session, кузница, участок,
                    base_seconds=бросок.uniform(шаг.min, шаг.max),
                    surface=Surface.PAVED,
                )

    #: Даль узлов и длина выездов (D-180): первое кольцо за стенами — двадцать
    #: секунд ходу, а не двадцать минут. Мир, заведённый до этого решения,
    #: получает даль задним числом, и его рёбра пересчитываются по ней.
    ворота = (
        await session.execute(select(Node).where(Node.key == "terra.capital.gate"))
    ).scalar_one_or_none()
    for ключ in ("terra.coal", "terra.floodplain"):
        узел = (
            await session.execute(select(Node).where(Node.key == ключ))
        ).scalar_one_or_none()
        if узел is None or ворота is None:
            continue
        if travel.reach_of(узел) == 0:
            узел.properties = {**(узел.properties or {}), travel.REACH: 1}
        ребро = (
            await session.execute(
                select(Edge).where(
                    ((Edge.node_a_id == ворота.id) & (Edge.node_b_id == узел.id))
                    | ((Edge.node_a_id == узел.id) & (Edge.node_b_id == ворота.id))
                )
            )
        ).scalars().first()
        секунд = travel.frontier_seconds(constants, travel.reach_of(узел))
        if ребро is None:
            await travel.connect(
                session, ворота, узел, base_seconds=секунд, surface=Surface.ROAD
            )
        else:
            ребро.base_seconds = int(секунд)
            ребро.surface = Surface.ROAD

    #: Монетный двор переименован в монетный станок, а пробу отменили: монета
    #: всегда 900-й (D-016). Существующие станки узнают новое имя здесь.
    переименовать = (
        await session.execute(
            select(Item).where(Item.type_key == "Монетный двор")
        )
    ).scalars().all()
    for станок in переименовать:
        станок.type_key = "Монетный станок"

    #: Здания под уже стоящими станками: станок живёт в здании (D-106), и
    #: узлы, обставленные до зданий, получают их задним числом.
    await _здания(session)

    #: Бумаги задним числом: земля, занятая до реформы титулов, тоже оформлена
    #: документом (D-116). Только там, где бумаги ещё нет: повторный запуск не
    #: трогает выставленные на продажу.
    from src.engine import estate
    from src.models.estate import Deed

    владения = (
        await session.execute(select(Node).where(Node.owner_identity_id.is_not(None)))
    ).scalars().all()
    for узел in владения:
        есть_бумага = await session.scalar(
            select(Deed.id).where(Deed.node_id == узел.id).limit(1)
        )
        if есть_бумага is None:
            await estate.issue_deed(session, узел, узел.owner_identity_id)

    from src.engine import energy

    await energy.ensure_pools(session, constants)
    await utility.ensure_meters(session, constants)
    await tick.ensure_scheduled(session)
    await utility.ensure_scheduled(session)
    await session.flush()


def _учётка(имя: str) -> dict:
    """Почта, пароль и самоописание стартовой личности из `FOUNDERS`."""
    данные = FOUNDERS[имя]
    return {
        "email": данные["email"],
        "password": данные["password"],
        "profile": {
            "surname": данные["surname"],
            "age": данные["age"],
            "about": данные["about"],
        },
    }


async def _учётки_догоном(session: AsyncSession) -> None:
    from src.engine import account as accounts
    from src.models.identity import Account, Identity

    for имя in FOUNDERS:
        личность = (
            await session.execute(select(Identity).where(Identity.name == имя))
        ).scalar_one_or_none()
        if личность is None:
            continue
        аккаунт = await session.get(Account, личность.account_id)
        if аккаунт is None or аккаунт.email:
            continue
        учётка = _учётка(имя)
        await accounts.set_credentials(session, аккаунт, учётка["email"], учётка["password"])
        if not личность.surname and not личность.about:
            accounts.apply_profile(личность, учётка["profile"])
        log.info("учётка назначена догоном: %s → %s", имя, учётка["email"])
    await session.flush()


async def _здания(session: AsyncSession) -> None:
    """Поставить здание всюду, где стоит станок либо мебель, а здания нет.

    Идемпотентно: второй запуск ничего не добавляет. Площадь — весь участок:
    городская застройка и есть здание, двора у неё нет.
    """
    from src.constants.catalog import ItemKind
    from src.engine import estate
    from src.models.inventory import Container, ContainerKind

    книга = current_catalog().recipes
    rows = (
        await session.execute(
            select(Node, Item.type_key)
            .join(Container, (Container.owner_id == Node.id))
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE)
        )
    ).all()
    обставлены: dict[str, Node] = {}
    for узел, вещь in rows:
        try:
            рецепт = книга.recipe(вещь)
        except Exception:  # noqa: BLE001 — сырьё рецептом не описано
            continue
        if рецепт.kind in (ItemKind.STATION, ItemKind.FURNITURE):
            обставлены[узел.key] = узел
    for узел in обставлены.values():
        if await estate.built_area(session, узел) <= 0:
            from src.models.estate import Building

            session.add(Building(node_id=узел.id, area_m2=float(узел.area_m2)))
    await session.flush()


async def _станок_если_нет(
    session: AsyncSession, node: Node, имя: str, качество: float
) -> None:
    """Поставить станок, если его в узле ещё нет. Второго не заводит."""
    двор = await world.node_container(session, node)
    if not await _есть_в(session, двор, имя):
        await world.grant_item(
            session, двор, имя, quality=качество, origin="догоняющий сид"
        )


async def _есть_в(session: AsyncSession, container, имя: str) -> bool:
    from src.models.inventory import Item

    found = await session.scalar(
        select(Item.id)
        .where(Item.container_id == container.id, Item.type_key == имя)
        .limit(1)
    )
    return found is not None


async def _узел_если_нет(
    session: AsyncSession,
    key: str,
    name: str,
    area: float,
    parent: Node,
    properties: dict,
) -> Node | None:
    """Завести узел, если его ещё нет. Иначе — ничего: мир не переписывают."""
    существующий = (
        await session.execute(select(Node).where(Node.key == key))
    ).scalar_one_or_none()
    if существующий is not None:
        return None
    return await world.create_node(
        session, key, name, area_m2=area, parent=parent, properties=properties
    )


def _кольцо(constants) -> tuple[float, float]:
    """Разброс площади участка первого кольца (D-125). Числа — из вольта."""
    площадь = constants[R.LAND_AREA_RING1]
    return площадь.min, площадь.max


async def _станок(session: AsyncSession, node: Node, имя: str, качество: float) -> None:
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, имя, quality=качество, origin="стартовый мир")


async def _казна(session: AsyncSession, город) -> None:
    """Положить в казну столицы стартовые деньги.

    Единственное допущение сида про деньги, и оно честное: подъёмные платятся
    **из казны**, а взяться в казне первого города им неоткуда — налоги ещё не
    собраны. Прирост денежной массы идёт через `genesis`, то есть виден в
    проверке инвариантов (И1).
    """
    казна = await town.treasury(session, город)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=казна.id,
        amount=money(CITY_TREASURY_START),
        memo={"основание": "стартовый мир: казна столицы"},
    )


async def main() -> None:
    conf = settings()
    logging.basicConfig(level=conf.log_level, format="%(levelname)s %(name)s %(message)s")
    bootstrap(conf.vault_build_path)

    factory = session_factory()
    async with factory() as session, session.begin():
        await seed(session)
    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
