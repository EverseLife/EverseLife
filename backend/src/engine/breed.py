"""Селекция: сорта, семена, скрещивание, вырождение (D-057, D-067).

Задача, ради которой всё это существует: **дать опытному фермеру настоящее
преимущество там, где нет ни навыков, ни уровней**. Преимущество здесь —
имущество (семена) и знание (агротехника), а не характеристика персонажа.

## Что чем является

* **Культура** — восемь базовых Терры, задана вольтом, неизменна;
* **Сорт** (`models.plant.Variety`) — линия внутри культуры со своими числами.
  Базовый сорт культуры ничей и заводится лениво; остальные выводят игроки;
* **Семена** — предмет (`type_key` из `plants.json`), несущий ссылку на сорт и
  **силу партии** `vigor`, %. Сила — это не качество: качество зерна решает,
  как оно кормит, сила — что вырастет из посева.

## Откуда взялась каждая формула

**Наследование.** `breed.inherit_drift` записан вольтом дословно:
`mean(parents) ± 0.15 * spread(parents)`. Признак потомка — среднее родителей
плюс случайное отклонение, пропорциональное тому, насколько родители разошлись.
Похожие родители дают предсказуемое потомство, разные — лотерею.

**Новый признак.** `breed.novel_trait_chance` — вероятность, что у потомка
появится то, чего не было ни у одного родителя. Реализован как заметный сдвиг
одного случайного признака: иначе «новый признак» пришлось бы выдумывать
списком, которого в вольте нет.

**Порог различимости.** `breed.distinctness_threshold` — новый сорт
жизнеспособен, только если заметно отличается от уже существующих в этой
культуре. Иначе **грядка просто не всходит**: гейт встроен в биологию, а не в
интерфейс, и отказ игрок получает полем, а не окном (D-067). Расстояние
считается как средняя относительная разница по признакам, в процентах.

**Расщепление и вырождение.** Семена гибрида нестабильны: без отбора следующее
поколение теряет `breed.hybrid_decay`. Любой семенной фонд без отбора теряет
`breed.degradation_per_gen` за поколение. Обе величины заданы отрицательными —
складываем, а не вычитаем: знак принадлежит вольту.

**Стабилизация.** `breed.generations_to_stabilize` поколений **отбора** — и
гибрид становится сортом, который создатель вправе назвать. Отбор при этом
удерживает силу партии: он и есть работа селекционера.

## Чего здесь пока нет

* **Агротехника как знание** (D-057): движок уже различает сорта, но интерфейс
  показывает нормы всем одинаково. Разделение «симптомы без знания — нормы со
  знанием» ждёт закрытия OQ о пяти параметрах ухода;
* **Дикие предки в диких узлах**: сбор первых семян руками приедет с разведкой;
* **Признаки сверх четырёх чисел**: болезни и загущение наследуются, но пока
  ни на что не влияют — их механики нет (OQ-098).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import events, travel, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity, Knowledge, KnowledgeKind
from src.models.inventory import Item
from src.models.plant import Nursery, Variety
from src.models.world import Node
from src.units import PERCENT, amount, amount_float

#: Постройка из `build/recipes.json`: скрещивают только в питомнике.
NURSERY = "Селекционный питомник"

#: Полная сила партии семян. Это не балансное число, а «сто процентов того,
#: что сорт умеет»: сами потери задаются `breed.*`.
FULL_VIGOR = PERCENT

#: Признаки, которые наследуются. Числа те же, что у культуры в plants.json:
#: сорт обязан подставляться на место культуры без пересчёта единиц.
TRAITS = ("yield_per_m2", "cycle_days", "fertility", "spoilage_k", "hardiness")


class BreedError(Exception):
    pass


class NotSeeds(BreedError):
    """Это не семена. Сеют и скрещивают семенами, а не урожаем."""


class WrongCulture(BreedError):
    """Скрещивают сорта одной культуры: межвидового в этой игре нет."""


class NoNursery(BreedError):
    """Питомника в узле нет: скрещивание требует места, как всякий станок."""


class NotStable(BreedError):
    """Гибрид ещё не сорт: имя даётся тому, что даёт постоянный результат."""


def traits_of_plant(plant: Plant) -> dict[str, float]:
    """Числа культуры в виде признаков сорта."""
    return {
        "yield_per_m2": plant.yield_per_m2,
        "cycle_days": plant.cycle_days,
        "fertility": plant.requires.fertility,
        "spoilage_k": plant.traits.spoilage_k,
        "hardiness": plant.traits.hardiness,
    }


async def landrace(session: AsyncSession, catalog: Catalog, culture_id: str) -> Variety:
    """Базовый сорт культуры: ничей, постоянный, заводится при первой нужде.

    Он и есть «то, что растёт у всех»: точка отсчёта, от которой селекционер
    уходит вверх, а заброшенный семенной фонд — вниз.
    """
    plant = catalog.plants.by_id(culture_id)
    found = (
        await session.execute(
            select(Variety).where(
                Variety.culture_id == culture_id,
                Variety.author_identity_id.is_(None),
                Variety.stable.is_(True),
            )
        )
    ).scalars().first()
    if found is not None:
        return found

    variety = Variety(
        culture_id=culture_id,
        name=plant.name,
        author_identity_id=None,
        generation=0,
        stable=True,
        traits=traits_of_plant(plant),
    )
    session.add(variety)
    await session.flush()
    return variety


def distance(one: dict[str, float], other: dict[str, float]) -> float:
    """Насколько два сорта различаются, процентов.

    Считается **наибольшая** относительная разница по признакам, а не средняя:
    «заметно отличается» — значит отличается хотя бы чем-то. Сорт с двойной
    урожайностью — другой сорт, даже если во всём прочем он копия родителя;
    усреднение по признакам топило бы такое различие в нулях.

    Метрику вольт не задаёт — он задаёт только порог
    (`breed.distinctness_threshold`), и выбор здесь принадлежит движку.
    """
    diffs: list[float] = []
    for key in TRAITS:
        a, b = one.get(key), other.get(key)
        if a is None or b is None:
            continue
        base = max(abs(a), abs(b))
        if base <= 0:
            continue
        diffs.append(abs(a - b) / base * PERCENT)
    return max(diffs) if diffs else 0.0


def inherit(
    constants: Constants,
    a: dict[str, float],
    b: dict[str, float],
    *,
    rng: random.Random,
) -> dict[str, float]:
    """Признаки потомка: `mean(parents) ± 0.15 * spread(parents)` (вольт).

    Формула записана в `breed.inherit_drift` как текст — движок обязан её
    исполнять, а не изобретать. Коэффициент отклонения читается оттуда же.
    """
    drift = _drift_share(constants)
    child: dict[str, float] = {}
    for key in TRAITS:
        one, other = a.get(key), b.get(key)
        if one is None or other is None:
            continue
        mean = fmean((one, other))
        spread = abs(one - other)
        child[key] = mean + rng.uniform(-drift, drift) * spread

    #: Изредка появляется признак, которого не было ни у кого из родителей.
    #: Показать это числами можно единственным честным способом — сдвинуть один
    #: признак **от середины**, а не внутри родительской вилки. Коэффициент
    #: сдвига тот же, что у наследования: второго вольт не задаёт.
    if rng.uniform(0, PERCENT) < constants[R.BREED_NOVEL_TRAIT_CHANCE] and child:
        key = rng.choice(sorted(child))
        child[key] *= 1 + rng.choice((-1, 1)) * drift
    return child


def _drift_share(constants: Constants) -> float:
    """Коэффициент отклонения из формулы вольта `breed.inherit_drift`.

    Формула хранится строкой («mean(parents) ± 0.15 * spread(parents)»), и
    число берётся из неё же: держать вторую копию коэффициента в коде значило
    бы завести балансное число мимо вольта (D-065).
    """
    formula = constants[R.BREED_INHERIT_DRIFT]
    text = formula if isinstance(formula, str) else str(formula)
    _, _, tail = text.partition("±")
    for token in tail.replace("*", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    raise BreedError(f"из формулы {text!r} не вычитать коэффициент отклонения")


async def cross(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    seeds_a: Item,
    seeds_b: Item,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> Nursery:
    """Скрестить два сорта в питомнике. Стоит семян, места и полного цикла.

    Результат приходит не сразу: селекция — занятие на недели, а не на вечер.
    Всходы проверяются на различимость только в конце (D-067) — до того
    селекционер не знает, вышло ли что-то новое.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise BreedError("мёртвое тело не сеет")
    await travel.require_here(session, body)

    node = await world.node_container(session, await _node(session, body))
    станки = (
        await session.execute(
            select(Item.type_key).where(Item.container_id == node.id).distinct()
        )
    ).scalars().all()
    if NURSERY not in станки:
        raise NoNursery(f"в узле нет постройки «{NURSERY}»")

    сорт_а = await _variety_of(session, seeds_a)
    сорт_б = await _variety_of(session, seeds_b)
    if сорт_а.culture_id != сорт_б.culture_id:
        raise WrongCulture(
            f"{сорт_а.culture_id} и {сорт_б.culture_id} — разные культуры: "
            "скрещивают сорта одной"
        )
    if seeds_a.id == seeds_b.id:
        raise BreedError("нужны две партии семян: сорт сам с собой не скрещивают")

    #: Норма высева питомника — та же, что у поля: это делянка и есть.
    норма = amount(constants[R.FARM_SEED_RATE] * constants[R.FARM_PLOT_MIN_AREA])
    for партия in (seeds_a, seeds_b):
        if партия.amount < норма:
            raise NotSeeds(
                f"на питомник нужно {amount_float(норма):g} семян каждого сорта"
            )
    for партия in (seeds_a, seeds_b):
        партия.amount -= норма
        if партия.amount <= 0:
            await session.delete(партия)
    await session.flush()

    plant = catalog.plants.by_id(сорт_а.culture_id)
    цикл = timedelta(hours=plant.cycle_days * constants[R.TIME_DAY_TERRA])
    питомник = Nursery(
        body_id=body.id,
        node_id=body.node_id,
        parent_a_id=сорт_а.id,
        parent_b_id=сорт_б.id,
        seeds=Decimal(str(amount_float(норма))),
        ready_at=moment + цикл,
    )
    session.add(питомник)
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_SOWN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        work="cross",
        nursery=str(питомник.id),
        culture=сорт_а.culture_id,
        parents=[str(сорт_а.id), str(сорт_б.id)],
    )
    return питомник


async def gather_cross(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    nursery: Nursery,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> Variety | None:
    """Забрать всходы питомника. Пусто — значит не взошло (D-067).

    Слишком похожий на существующие сорт не прорастает вовсе: селекционер
    получает не отказ движка, а пустую грядку.
    """
    moment = now or datetime.now(UTC)
    await travel.require_here(session, body)
    if nursery.done:
        raise BreedError("этот питомник уже разобран")
    if moment < nursery.ready_at:
        raise BreedError(f"питомник созреет к {nursery.ready_at.isoformat()}")

    бросок = rng or random.Random(str(nursery.id))
    отец = await session.get(Variety, nursery.parent_a_id)
    мать = await session.get(Variety, nursery.parent_b_id)
    if отец is None or мать is None:  # pragma: no cover
        raise BreedError("родительский сорт исчез")

    признаки = inherit(constants, отец.traits, мать.traits, rng=бросок)
    порог = constants[R.BREED_DISTINCTNESS_THRESHOLD]
    соседи = (
        await session.execute(
            select(Variety).where(Variety.culture_id == отец.culture_id)
        )
    ).scalars().all()
    похожий = next(
        (сосед for сосед in соседи if distance(признаки, сосед.traits) < порог), None
    )

    nursery.done = True
    if похожий is not None:
        await session.flush()
        await events.record(
            session,
            EventKind.PLOT_HARVESTED,
            actor_identity_id=body.identity_id,
            node_id=nursery.node_id,
            work="cross",
            nursery=str(nursery.id),
            sprouted=False,
            too_close_to=похожий.name or str(похожий.id),
        )
        return None

    гибрид = Variety(
        culture_id=отец.culture_id,
        name=None,
        author_identity_id=body.identity_id,
        parent_a_id=отец.id,
        parent_b_id=мать.id,
        generation=1,
        stable=False,
        traits=признаки,
    )
    session.add(гибрид)
    await session.flush()
    nursery.result_variety_id = гибрид.id

    #: Создатель знает агротехнику своего сорта, и больше её не знает никто
    #: (D-057). Не награда, а следствие: он его и вывел.
    identity = await session.get(Identity, body.identity_id)
    if identity is not None:
        await world.learn(
            session, identity, agrotech_key(гибрид),
            kind=KnowledgeKind.AGROTECH, discovered=True,
        )

    plant = catalog.plants.by_id(отец.culture_id)
    карман = await world.body_container(session, body)
    session.add(
        Item(
            container_id=карман.id,
            type_key=plant.seed,
            amount=amount(float(nursery.seeds)),
            variety_id=гибрид.id,
            vigor=Decimal(str(FULL_VIGOR)),
            maker_identity_id=body.identity_id,
            made_at=moment,
            made_node_id=nursery.node_id,
        )
    )
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_HARVESTED,
        actor_identity_id=body.identity_id,
        node_id=nursery.node_id,
        work="cross",
        nursery=str(nursery.id),
        sprouted=True,
        variety=str(гибрид.id),
    )
    return гибрид


def next_vigor(
    constants: Constants, variety: Variety, vigor: float, *, selected: bool
) -> float:
    """Сила следующего поколения семян.

    Отбор — работа: он удерживает фонд. Без отбора фонд вырождается, а у
    гибрида вдобавок расщепляется. Обе величины вольта отрицательны.
    """
    if selected:
        return vigor
    потеря = constants[R.BREED_DEGRADATION_PER_GEN]
    if not variety.stable:
        потеря += constants[R.BREED_HYBRID_DECAY]
    return max(0.0, vigor + потеря)


async def select_generation(
    session: AsyncSession, constants: Constants, variety: Variety
) -> Variety:
    """Засчитать поколение отбора. Столько-то поколений — и гибрид стал сортом."""
    if variety.stable:
        return variety
    variety.generation += 1
    порог = constants[R.BREED_GENERATIONS_TO_STABILIZE]
    if variety.generation >= порог.max:
        variety.stable = True
    await session.flush()
    return variety


async def name_variety(
    session: AsyncSession, body: Body, variety: Variety, name: str
) -> Variety:
    """Назвать выведенный сорт. Имя автора закрепляется за ним навсегда."""
    if not variety.stable:
        raise NotStable(
            "сорт ещё не постоянен: имя даётся тому, что даёт тот же результат "
            "из раза в раз"
        )
    if variety.author_identity_id != body.identity_id:
        raise BreedError("называет сорт тот, кто его вывел")
    чистое = name.strip()
    if not чистое:
        raise BreedError("имя пустое")
    variety.name = чистое
    await session.flush()
    await events.record(
        session,
        EventKind.KNOWLEDGE_LEARNED,
        actor_identity_id=body.identity_id,
        work="variety",
        variety=str(variety.id),
        name=чистое,
    )
    return variety


async def seed_lot(
    session: AsyncSession,
    catalog: Catalog,
    container_id: uuid.UUID,
    variety: Variety,
    quantity: float,
    vigor: float,
    *,
    now: datetime | None = None,
) -> Item:
    """Положить партию семян сорта в контейнер."""
    plant = catalog.plants.by_id(variety.culture_id)
    item = Item(
        container_id=container_id,
        type_key=plant.seed,
        amount=amount(quantity),
        variety_id=variety.id,
        vigor=Decimal(str(vigor)),
        made_at=now,
    )
    session.add(item)
    await session.flush()
    return item


def agrotech_key(variety: Variety) -> str:
    """Чем ключуется агротехника сорта.

    У базовых сортов — именем культуры: их агротехника общая и лежит в
    Библиотеке (D-053). У выведенного — его собственным id: такую знает только
    автор, пока сам не продаст носитель.
    """
    return variety.culture_id if variety.author_identity_id is None else str(variety.id)


async def knows_agrotech(
    session: AsyncSession, identity_id: uuid.UUID, variety: Variety
) -> bool:
    """Знает ли личность, чего этому сорту надо.

    Знание не запрещает сеять и не запрещает убирать — оно решает, **видит ли
    фермер нормы или только симптомы** (D-057). Стены «нельзя посадить» здесь
    нет и не будет: новичок упирается в собственное невежество, а оно лечится.
    """
    found = await session.execute(
        select(Knowledge).where(
            Knowledge.identity_id == identity_id,
            Knowledge.kind == KnowledgeKind.AGROTECH,
            Knowledge.key == agrotech_key(variety),
        )
    )
    return found.scalar_one_or_none() is not None


async def copy_agrotech(
    session: AsyncSession, catalog: Catalog, body: Body, culture_id: str
) -> Knowledge | None:
    """Взять агротехнику базовой культуры в Библиотеке: бесплатно, но ногами.

    Восемь базовых лежат там для всех (D-053). Агротехника выведенного сорта в
    Библиотеку не попадает — её знает только автор.
    """
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    if node is None or not node.properties.get("library"):
        raise BreedError("Библиотека не работает удалённо: за знанием надо прийти")

    plant = catalog.plants.by_id(culture_id)
    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise BreedError("тело без личности")
    return await world.learn(
        session, identity, plant.id, kind=KnowledgeKind.AGROTECH
    )


async def _variety_of(session: AsyncSession, item: Item) -> Variety:
    if item.variety_id is None:
        raise NotSeeds(f"{item.type_key!r} — не семена сорта")
    variety = await session.get(Variety, item.variety_id)
    if variety is None:  # pragma: no cover — сорт не удаляется
        raise BreedError("сорт этих семян исчез")
    return variety


async def _node(session: AsyncSession, body: Body):
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise BreedError("тело вне узла")
    return node
