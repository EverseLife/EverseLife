"""Крафт: партия, качество, потери (D-092, D-133).

Пять условий одновременно, и все обязательны: знание, станок, инструмент,
входы, место (20-systems/03-crafting). Партия запускается присутственно и идёт
офлайн; вход списывается сразу, изделие появляется по сроку.

## Откуда взялась каждая формула

Числа заданы вольтом, порядок шагов — дело движка (CLAUDE.md вольта). Ниже
вывод каждой формулы, чтобы её можно было сверить с D-092 и D-133, а не
принимать на веру.

**Время партии.** `craft.time_per_unit` — «базовое время партии на единицу
выхода», `craft.time_growth_per_level` — «во столько раз дольше делается изделие
с каждым переделом вглубь». Обе величины вольт уже свёл в `labor_hours`:
трудоёмкость изделия равна собственному времени передела плюс труд входов.
Значит собственное время достаётся вычитанием и **не выводится в коде заново**:

    шаг(изделие) = labor_hours(изделие) − Σ amounts[j] × labor_hours(j)

У операции без рецепта своё время задано прямо — `hours_per_unit[выход]`.

**Скорость станка.** `craft.station_speed_k` — «множитель времени от качества
станка; разбитая наковальня работает медленно». Значит худший станок работает
по верхней границе множителя, лучший — по нижней:

    k = max − (max − min) × качество_станка / шкала

**Потолок качества.** Наименьшее из качества станка и инструмента: ограничивает
самое слабое звено (15-quality). Чего нет — то не ограничивает: рецепт «Руками»
без инструмента упирается только в сырьё.

**Приближение к потолку.** У сборки его определяют одни входы, у смеси — входы
и точность пропорции, с весами `quality.material_weight` и
`quality.ratio_weight` (D-092).

**Оптимум пропорции у смеси.** «Бедной руде нужно больше угля и флюса, чистой —
меньше». Количества из `recipes.json` — норма для обычного сырья, то есть для
середины шкалы качества; отклонение от неё симметрично:

    оптимум[добавка] = amounts[добавка] × (1 + (середина − качество основы) / шкала)

Основа — первый вход рецепта. Бедная основа требует до полутора норм добавок,
отличная — до половины.

**Потери и разброс.** `craft.waste_share` при верной работе,
`craft.waste_bad_ratio` при промахе; `quality.spread_good_ratio` и
`quality.spread_bad_ratio` — так же. Порога «попал / не попал» в вольте нет, и
выдумывать его здесь нельзя: обе величины идут по точности непрерывно.

**Премия ремесла.** `quality.hand_craft_bonus` — «до +10 за точное попадание в
пропорции». Только у смеси: у сборки пропорций нет вовсе, и премия там была бы
прибавкой из воздуха.

Ни одного числа сверх вольта здесь не появилось. Не хватает величины — её
заводят в `data/constants.yaml`, а не в код (D-065).

## Чего здесь пока нет

* **Блюда по ролям** (`roles: true`) — приезжают с готовкой на Э2 (D-119,
  D-128) вместе с `cook.*`;
* **Автоматический станок** — на Э2.5 вместе с энергией (D-035), тогда же
  включится `craft.auto_speed_k`;
* **Изобретение, ремонт и переработка** — отдельные действия реестра со своими
  константами.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants, current, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind, Operation, Recipe
from src.engine import events, travel, wear
from src.engine import world as world_engine
from src.engine.jobs import enqueue, handler
from src.engine.world import body_container, learn, node_container
from src.models.craft import BatchKind, BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity, Knowledge, KnowledgeKind
from src.models.inventory import Container, Item
from src.models.job import Job, JobKind
from src.models.world import Node
from src.units import MINUTES_PER_HOUR, PERCENT, amount, amount_float


class CraftError(Exception):
    pass


class NotLearned(CraftError):
    """Рецепта нет в личности. Знание берётся в Библиотеке бесплатно (D-053)."""


class NoStation(CraftError):
    """Станка нет в узле. Требование места и делает крафт градообразующим."""


class NoTool(CraftError):
    pass


class NoLibrary(CraftError):
    """Библиотеки в узле нет. Единственное её ограничение — географическое."""


class NotEnough(CraftError):
    """Не хватает входов. Материя не создаётся (И1)."""


class NoStrength(CraftError):
    """Сил не хватает. Работа платится телом, а не только материалами (D-148)."""


class Busy(CraftError):
    """Станок занят другим работником. Мест столько, сколько станков (D-150)."""


class CutOff(CraftError):
    """Узел отключён за неуплату: станки не работают, пока долг не закрыт (D-149)."""


class Unmakeable(CraftError):
    """Так не делают: нет такого способа, либо механика ещё не приехала."""


class TooBig(CraftError):
    """Партия больше `craft.batch_max`."""


class NotIngredient(CraftError):
    """В роль положили несъедобное. Что продукт — решают данные (16-cooking)."""


#: Станция «Руками» из `build/recipes.json` — это отсутствие станка, а не станок.
HANDS = "Руками"

#: «Стройка» — тоже не станок, а работа на месте (D-158). Предмета с таким
#: именем в данных вольта нет и быть не может: так помечено всё, что собирают
#: на участке, — от дорожного полотна до мастерской. Требовать под это станок
#: значило бы запретить целое семейство рецептов, что и было до D-158.
SITE = "Стройка"

#: Что читается как «станка не нужно». Список из двух, и оба — из данных.
BENCHLESS = (HANDS, SITE)

#: Автоматический станок (D-035, D-058). Промышленный уклад: работает вдвое
#: быстрее, потолок задаёт он сам, инструмент ему не нужен, результат ровный —
#: и за это он ест энергию из городского пула по тарифу.
#:
#: Какие переделы автоматизируются, вольт не перечисляет, поэтому автомат
#: подставляется вместо любого станка рецепта — по решению самого мастера, а
#: не молча: «поставить на автомат» это выбор между качеством и объёмом.
AUTO_BENCH = "Автоматический станок"


@dataclass(frozen=True, slots=True)
class Procedure:
    """Способ что-то изготовить: рецепт либо операция без рецепта.

    Дальше движку безразлично, откуда способ взялся, — кроме одного: рецепт
    требует знания, операция не требует его никогда (20-systems/03-crafting).
    """

    output: str
    #: Имя станка либо None, если делается руками.
    station: str | None
    #: Что должно быть в руках: имя предмета либо класс инструмента.
    tools: tuple[str, ...]
    inputs: tuple[str, ...]
    #: Сколько чего на единицу выхода.
    per_unit: dict[str, float]
    #: Собственное время передела, часов на единицу.
    step_hours: float
    #: Смесь: состав задан пропорцией, и точность попадания влияет на качество.
    mix: bool
    needs_recipe: bool
    #: Свойство узла, где способ возможен (D-177): «Рубка дерева» → `лес`.
    #: Пусто — способ не привязан к месту.
    place: str | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """Прогноз партии — то, что игрок видит **до** того, как потрачены материалы.

    Качество показано точным числом: без него игрок не свяжет действие с
    результатом и не выведет ни одной пропорции (D-092).
    """

    output: str
    units: float
    quality: float
    spread: float
    ceiling: float
    #: Точность попадания в пропорцию, 0…1. У сборки всегда 1: пропорций нет.
    accuracy: float
    #: Доля потерь на угар и брак, процентов от входов.
    waste: float
    minutes: float
    consumes: dict[str, float] = field(default_factory=dict)
    #: Промышленный уклад: партия идёт на автомате (D-035, D-058).
    auto: bool = False
    #: Сколько энергии съест автомат за партию и во что это обойдётся по
    #: тарифу города. У ручной партии — ноль: верстак не потребляет ничего.
    energy: float = 0.0
    energy_cost: int = 0


@dataclass(frozen=True, slots=True)
class _Pick:
    """Стопка, из которой берут, и сколько именно берут."""

    item: Item
    take: int


@dataclass(frozen=True, slots=True)
class _Ready:
    """Разобранная заявка на партию: прогноз плюс то, что под него отложено."""

    plan: Plan
    picks: tuple[_Pick, ...]
    station: Item | None
    auto: bool = False


# --- способ изготовления ----------------------------------------------------


def procedure(catalog: Catalog, output: str) -> Procedure:
    """Найти способ изготовить `output` — сперва среди рецептов, потом операций."""
    book = catalog.recipes
    name = book.resolve(output)
    found = next((recipe for recipe in book.recipes if recipe.name == name), None)
    if found is not None:
        return _from_recipe(catalog, found)

    for operation in book.operations:
        if name in {book.resolve(gives) for gives in operation.gives}:
            return _from_operation(catalog, operation, name)
    raise Unmakeable(f"{output!r} не делается ни по рецепту, ни операцией")


def _from_recipe(catalog: Catalog, recipe: Recipe) -> Procedure:
    if recipe.roles:
        raise Unmakeable(f"{recipe.name!r} — блюдо: его варят котлом, командой `cook`")
    if recipe.kind is ItemKind.MONEY:
        raise Unmakeable(
            f"{recipe.name!r} — монета: её чеканят, и металл считается по пробе "
            "(команда `coin.mint`)"
        )
    book = catalog.recipes
    return Procedure(
        output=recipe.name,
        #: Станок тоже ходит через синонимы: в рецептах он зовётся «Печью», а
        #: в узле стоит «Плавильная печь». Без разрешения имени вся химия и
        #: аффинаж оказывались неизготовимы — станка с таким именем нет нигде.
        station=None if recipe.station in (None, *BENCHLESS) else book.resolve(recipe.station),
        tools=(),
        inputs=tuple(book.resolve(name) for name in recipe.inputs),
        per_unit={book.resolve(name): value for name, value in recipe.amounts.items()},
        step_hours=step_hours(catalog, recipe),
        mix=recipe.mix,
        needs_recipe=True,
    )


def _from_operation(catalog: Catalog, operation: Operation, output: str) -> Procedure:
    book = catalog.recipes
    per_unit = {
        book.resolve(name): value for name, value in operation.amounts.get(output, {}).items()
    }
    #: Операция без расходов — добыча. С полем `place` это добыча места (D-177):
    #: рубка леса идёт партией без входов. Без поля — чужая механика (жила).
    if not per_unit and operation.place is None:
        raise Unmakeable(
            f"операция «{operation.name}» ничего не расходует: это добыча, а не крафт"
        )

    station: str | None = None
    tools: list[str] = []
    for requirement in operation.requires:
        canonical = book.resolve(requirement)
        if book.tools_of_class(canonical):
            tools.append(canonical)
        elif book.is_raw(canonical):
            #: «Жила» в требованиях добычи — не оборудование, а сама механика.
            continue
        elif book.recipe(canonical).kind is ItemKind.STATION:
            station = canonical
        else:
            tools.append(canonical)

    return Procedure(
        output=output,
        station=station,
        tools=tuple(tools),
        inputs=tuple(per_unit),
        per_unit=per_unit,
        step_hours=operation.hours_per_unit.get(output, 0.0),
        mix=False,
        needs_recipe=False,
        place=operation.place,
    )


def step_hours(catalog: Catalog, recipe: Recipe) -> float:
    """Собственное время передела: трудоёмкость изделия минус труд его входов.

    Вольт уже свёл сюда и `craft.time_per_unit`, и рост от глубины передела
    (D-133). Выводить их заново значило бы держать вторую копию формулы.
    """
    book = catalog.recipes
    spent = sum(value * book.labor_of(name) for name, value in recipe.amounts.items())
    return max(0.0, book.labor_of(recipe.name) - spent)


def batch_minutes(
    constants: Constants,
    proc: Procedure,
    units: float,
    station_quality: float,
    *,
    auto: bool = False,
) -> float:
    """Сколько идёт партия. Разбитая наковальня работает медленно.

    Автомат берёт объёмом: `craft.auto_speed_k` — во столько раз он быстрее.
    """
    speed = constants[R.CRAFT_STATION_SPEED_K]
    scale = constants[R.QUALITY_SCALE]
    k = speed.max - (speed.max - speed.min) * station_quality / scale.max
    minutes = proc.step_hours * MINUTES_PER_HOUR * units * k
    return minutes / constants[R.CRAFT_AUTO_SPEED_K] if auto else minutes


# --- качество ---------------------------------------------------------------


def optimal_amounts(
    constants: Constants, proc: Procedure, units: float, base_quality: float
) -> dict[str, float]:
    """Оптимальная пропорция для **этого** сырья.

    У сборки её нет: верстак — это бревно и верёвка, третьего не дано.
    """
    nominal = {name: value * units for name, value in proc.per_unit.items()}
    if not proc.mix or not proc.inputs:
        return nominal

    scale = constants[R.QUALITY_SCALE]
    correction = 1 + (scale.mid - base_quality) / scale.max
    base = proc.inputs[0]
    return {
        name: value if name == base else value * correction for name, value in nominal.items()
    }


def ratio_accuracy(actual: dict[str, float], optimal: dict[str, float]) -> float:
    """Насколько попали в пропорцию: 1 — точно, 0 — мимо совсем."""
    errors = [
        abs(actual.get(name, 0.0) - want) / want for name, want in optimal.items() if want > 0
    ]
    if not errors:
        return 1.0
    return max(0.0, 1 - sum(errors) / len(errors))


def waste_share(constants: Constants, accuracy: float) -> float:
    """Потери на угар и брак, процентов от входов.

    Порога «попал / не попал» в вольте нет, поэтому потери идут по точности
    непрерывно: от `craft.waste_share` при верной работе до
    `craft.waste_bad_ratio` при полном промахе.
    """
    good = constants[R.CRAFT_WASTE_SHARE]
    bad = constants[R.CRAFT_WASTE_BAD_RATIO]
    return good + (bad - good) * (1 - accuracy)


def spread_of(constants: Constants, accuracy: float) -> float:
    """Разброс результата: узкий при верных пропорциях, широкий при промахе."""
    good = constants[R.QUALITY_SPREAD_GOOD_RATIO]
    bad = constants[R.QUALITY_SPREAD_BAD_RATIO]
    return good + (bad - good) * (1 - accuracy)


def forecast_quality(
    constants: Constants,
    proc: Procedure,
    *,
    ceiling: float,
    material: float,
    accuracy: float,
    auto: bool = False,
) -> float:
    """Прогноз качества: потолок и то, насколько близко к нему подошли."""
    scale = constants[R.QUALITY_SCALE]
    if proc.mix:
        closeness = (
            constants[R.QUALITY_MATERIAL_WEIGHT] * material
            + constants[R.QUALITY_RATIO_WEIGHT] * accuracy * scale.max
        ) / PERCENT
    else:
        closeness = material
    value = ceiling * closeness / scale.max
    #: Премия ремесла: мастер видит, что руда сегодня хуже обычной, и меняет
    #: пропорции под неё. Станок работает по своей настройке всегда (15-quality),
    #: поэтому автомату премия не полагается — в этом и всё различие укладов.
    if proc.mix and not auto:
        value += constants[R.QUALITY_HAND_CRAFT_BONUS] * accuracy
    return scale.clamp(min(value, quality_cap(constants, proc, ceiling, auto=auto)))


def quality_cap(
    constants: Constants, proc: Procedure, ceiling: float, *, auto: bool = False
) -> float:
    """Выше потолка поднимает только премия ремесла, и только у смеси."""
    scale = constants[R.QUALITY_SCALE]
    bonus = (
        constants[R.QUALITY_HAND_CRAFT_BONUS] if proc.mix and not auto else 0.0
    )
    return min(scale.max, ceiling + bonus)


# --- партия -----------------------------------------------------------------


async def plan(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    units: float,
    *,
    tool_item_id: uuid.UUID | None = None,
    proportions: dict[str, float] | None = None,
    auto: bool = False,
) -> Plan:
    """Прогноз до партии. Ничего не меняет и ничего не резервирует."""
    ready = await _prepare(
        session,
        constants,
        catalog,
        body,
        output,
        units,
        tool_item_id=tool_item_id,
        proportions=proportions,
        auto=auto,
    )
    return ready.plan


async def start(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    units: float,
    *,
    tool_item_id: uuid.UUID | None = None,
    proportions: dict[str, float] | None = None,
    auto: bool = False,
    now: datetime | None = None,
) -> CraftBatch:
    """Запустить партию: вход списывается сразу, изделие приходит по сроку."""
    moment = now or datetime.now(UTC)
    ready = await _prepare(
        session,
        constants,
        catalog,
        body,
        output,
        units,
        tool_item_id=tool_item_id,
        proportions=proportions,
        auto=auto,
    )
    forecast = ready.plan

    #: Энергия автомата списывается вперёд, как и материалы: город отпускает
    #: её по тарифу, и платит тот, кто жжёт (D-085, D-135).
    if forecast.energy > 0:
        from src.engine import energy as power

        await power.draw_for_work(
            session, constants, body, forecast.energy,
            what=f"партия «{forecast.output}»", now=moment,
        )

    for pick in ready.picks:
        if pick.item.amount > pick.take:
            pick.item.amount -= pick.take
        else:
            await session.delete(pick.item)
    await session.flush()

    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        output=forecast.output,
        units=amount(forecast.units),
        station_item_id=None if ready.station is None else ready.station.id,
        tool_item_id=tool_item_id,
        quality=_num(forecast.quality),
        spread=_num(forecast.spread),
        spent=forecast.consumes,
        ready_at=moment + timedelta(minutes=forecast.minutes),
    )
    session.add(batch)
    await session.flush()
    await _occupy(session, ready.station, body, batch.ready_at)

    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        output=forecast.output,
        units=forecast.units,
        quality=forecast.quality,
        spent=forecast.consumes,
        waste=forecast.waste,
    )
    #: Партия — обычное задание журнала: переживает перезапуск процесса и
    #: выполняется ровно один раз (01-tech-notes, паттерн 1).
    await enqueue(
        session,
        JobKind.CRAFT_BATCH,
        batch.ready_at,
        payload={"batch": str(batch.id)},
        dedup_key=f"craft.batch:{batch.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return batch


#: Класс утвари из `build/recipes.json`: горшок и котёл задают потолок наравне
#: с очагом (D-119). Утварь — инструмент, а не тара.
UTENSILS = "Утварь"


async def cook(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    filling: dict[str, str | None],
    *,
    now: datetime | None = None,
) -> CraftBatch:
    """Сварить котёл: `cook.pot_portions` порций разом, поток, а не заказ.

    Состав задан ролями (D-119): в роль кладут продукт, какой достали, по
    одной единице на котёл. Качество — по D-128, дословно:

        потолок  = min(качество очага, качество утвари)
        основа   = Σ(качество входа × вес роли) ÷ Σ(весов закрытых ролей)
        качество = потолок × основа/100 × (1 − штраф × число пустых ролей)

    Незакрытая роль бьёт сильнее плохого продукта: дешёвый жир лучше, чем
    никакого жира. Сочетание решает **вид** блюда, а не качество — по виду
    работает разнообразие рациона, никакой таблицы совместимости нет.

    Что вообще продукт — решают данные: съедобные рецепты и список `edible`
    вольта. Годность конкретной роли — тоже содержание, но его пока нет:
    продукт идёт в любую роль, а кирка не идёт ни в какую (16-cooking).
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CraftError("мёртвое тело не готовит")
    await travel.require_here(session, body)

    recipe = catalog.recipes.recipe(output)
    if not recipe.roles:
        raise Unmakeable(f"{recipe.name!r} — не блюдо: это делают партией, не котлом")
    if not await _knows(session, body, recipe.name):
        raise NotLearned(f"рецепт {recipe.name!r} не скопирован в личность")

    #: Роли — из констант вольта, с весами. Лишняя роль в заявке — ошибка.
    weights = constants[R.COOK_ROLE_WEIGHTS]
    unknown = set(filling) - set(weights)
    if unknown:
        raise CraftError(f"нет таких ролей: {', '.join(sorted(unknown))}")

    proc = Procedure(
        output=recipe.name,
        station=None if recipe.station in (None, *BENCHLESS) else recipe.station,
        tools=(UTENSILS,),
        inputs=(),
        per_unit={},
        step_hours=step_hours(catalog, recipe),
        mix=False,
        needs_recipe=True,
    )
    station = await _station_item(session, body, proc)
    tools = await _tool_items(session, catalog, body, proc, None)
    ceiling = min(wear.effective(constants, item) for item in [station, *tools])

    #: В каждую закрытую роль идёт единица продукта на котёл целиком.
    pocket = await body_container(session, body)
    scale = constants[R.QUALITY_SCALE]
    one = amount(1)
    weighted = 0.0
    closed_weight = 0.0
    consumed: dict[str, float] = {}
    products: list[str] = []
    for role, weight in weights.items():
        product = filling.get(role)
        if not product:
            continue
        name = catalog.recipes.resolve(product)
        if not catalog.recipes.is_ingredient(name):
            raise NotIngredient(f"«{name}» — не продукт: в котёл кладут съедобное")
        stock = await _stock(session, pocket, (name,))
        picks = _pick(stock, {name: amount_float(one)})
        quality = _material_quality(picks, scale.mid)
        for pick in picks:
            if pick.item.amount > pick.take:
                pick.item.amount -= pick.take
            else:
                await session.delete(pick.item)
        weighted += quality * weight
        closed_weight += weight
        consumed[name] = consumed.get(name, 0) + 1
        products.append(name)
    await session.flush()

    if closed_weight <= 0:
        raise NotEnough("в котле пусто: закройте хотя бы одну роль")

    empty = len(weights) - len(products)
    base = weighted / closed_weight
    penalty = 1 - constants[R.COOK_EMPTY_ROLE_PENALTY] * empty / PERCENT
    quality = scale.clamp(ceiling * (base / scale.max) * max(0.0, penalty))

    portions = constants[R.COOK_POT_PORTIONS]
    minutes = batch_minutes(constants, proc, portions, wear.effective(constants, station))

    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        output=recipe.name,
        units=amount(portions),
        station_item_id=None if station is None else station.id,
        quality=_num(quality),
        spread=_num(constants[R.QUALITY_SPREAD_GOOD_RATIO]),
        spent=consumed,
        #: Вид решает сочетание: «похлёбка · бобы, овощи» и «похлёбка · репа» —
        #: разные блюда для рациона, хоть рецепт один (D-060 не нарушен).
        flavor=f"{recipe.name} · {', '.join(sorted(products))}",
        roles_filled=_num(len(products) / len(weights)),
        ready_at=moment + timedelta(minutes=minutes),
    )
    session.add(batch)
    await session.flush()
    await _occupy(session, station, body, batch.ready_at)

    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        work="cook",
        output=recipe.name,
        flavor=batch.flavor,
        quality=quality,
        spent=consumed,
    )
    await enqueue(
        session,
        JobKind.CRAFT_BATCH,
        batch.ready_at,
        payload={"batch": str(batch.id)},
        dedup_key=f"craft.batch:{batch.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return batch


async def repair(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> CraftBatch:
    """Починить вещь.

    Ремонт возвращает состояние, но **снижает потолок**: после починки состояние
    уже не поднимется до прежнего максимума. Так вещь остаётся конечной (столп
    П2), а починка — осмысленным выбором между «дёшево сейчас» и «дорого, зато
    новое» (15-quality).

    Стоит `craft.repair_cost_share` от новой вещи — и материалами, и временем:
    вольт даёт одну долю, и второй здесь взяться неоткуда.
    """
    share = constants[R.CRAFT_REPAIR_COST_SHARE] / PERCENT
    return await _work_on(
        session, constants, catalog, body, item, BatchKind.REPAIR, share, now=now
    )


async def recycle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> CraftBatch:
    """Разобрать вещь на часть материалов.

    Возврат всегда меньше вложенного, разница — сток (20-systems/03). Качество
    переходит на материалы долей `quality.recycle_carryover`: разобранная
    хорошая вещь даёт сырьё получше, но хуже, чем было.
    """
    from src.engine import coin

    if coin.is_coin(catalog, item.type_key):
        raise Unmakeable(
            "монету переплавляют командой `coin.melt`: металл возвращается "
            "по её пробе, а не по норме рецепта"
        )
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    return await _work_on(
        session, constants, catalog, body, item, BatchKind.RECYCLE, share, now=now
    )


@handler(JobKind.CRAFT_BATCH)
async def finish(session: AsyncSession, job: Job) -> None:
    """Работа окончена: изделия, починенная вещь либо горсть материалов."""
    batch = await session.get(CraftBatch, uuid.UUID(job.payload["batch"]))
    if batch is None:  # pragma: no cover — задание без партии это баг
        raise CraftError(f"задание {job.id}: партии нет")
    if batch.state is not BatchState.RUNNING:
        #: Задание могло повториться после сбоя — второй партии из этого не выйдет.
        return

    constants, catalog = current(), current_catalog()
    body = await session.get(Body, batch.body_id)
    node = await session.get(Node, batch.node_id)
    if body is None or node is None:  # pragma: no cover
        raise CraftError(f"партия {batch.id} ссылается в никуда")

    #: Мастер стоит у станка — забирает сам; ушёл или погиб — сделанное остаётся
    #: у станка. Материя не исчезает вместе с тем, кто её заказал.
    at_bench = body.state is BodyState.ALIVE and body.node_id == batch.node_id
    where = (
        await body_container(session, body) if at_bench else await node_container(session, node)
    )

    if batch.kind is BatchKind.REPAIR:
        made = await _finish_repair(session, constants, batch)
    elif batch.kind is BatchKind.RECYCLE:
        made = await _finish_recycle(session, constants, catalog, batch, where)
    else:
        made = await _finish_make(session, constants, catalog, batch, body, where, job.run_at)

    await _wear_station(session, constants, batch)
    #: Работа кончилась — станок свободен и ждёт следующего (D-150).
    await _release(session, batch.station_item_id)

    batch.state = BatchState.DONE
    batch.finished_at = job.run_at
    await session.flush()

    await events.record(
        session,
        EventKind.CRAFT_FINISHED,
        actor_identity_id=body.identity_id,
        node_id=batch.node_id,
        batch_id=str(batch.id),
        work=batch.kind.value,
        output=batch.output,
        units=amount_float(batch.units),
        quality=made,
    )


async def _finish_make(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    batch: CraftBatch,
    body: Body,
    where: Container,
    moment: datetime,
) -> list[float]:
    """Партия: изделия с клеймом и разбросом качества вокруг обещанного."""
    #: Зерно от партии: повтор задания после сбоя даёт то же самое, а не новый
    #: бросок. Разброс — свойство партии, а не удача воркера.
    noise = random.Random(str(batch.id))
    scale = constants[R.QUALITY_SCALE]
    spread = float(batch.spread)
    units = amount_float(batch.units)

    #: У монеты качества нет вовсе: её описывает проба, и приходит она с
    #: партии вместе с клеймом чеканщика (D-016).
    from src.engine import coin

    монета = coin.is_coin(catalog, batch.output)

    #: Еда получает срок жизни при изготовлении: готовое из котла портится в
    #: `cook.spoilage_multiplier` раз быстрее, сухое — с базовой скоростью.
    #: Выход операции (слиток, щебень) рецептом не описан вовсе — и это норма,
    #: а не повод уронить партию: плавка идёт без рецепта (20-systems/03).
    try:
        recipe = catalog.recipes.recipe(batch.output)
    except ConstantError:
        recipe = None
    spoils_at = None
    if recipe is not None and recipe.food:
        from src.engine import food

        spoils_at = (
            food.cooked_spoils_at(constants, now=moment)
            if batch.flavor is not None
            else moment + timedelta(hours=food.shelf_hours(constants, rate=1))
        )

    made: list[float] = []
    for piece in _pieces(catalog, batch.output, units):
        quality = scale.clamp(float(batch.quality) + noise.uniform(-spread, spread))
        made.append(float(batch.fineness) if монета else quality)
        session.add(
            Item(
                container_id=where.id,
                type_key=batch.output,
                amount=amount(piece),
                quality=None if монета else _num(quality),
                fineness=batch.fineness,
                maker_identity_id=body.identity_id,
                made_at=moment,
                made_node_id=batch.node_id,
                spoils_at=spoils_at,
                flavor=batch.flavor,
                roles_filled=batch.roles_filled,
            )
        )
    return made


async def _finish_repair(
    session: AsyncSession, constants: Constants, batch: CraftBatch
) -> list[float]:
    """Починка: состояние вернулось, потолок опустился."""
    item = await _target(session, batch)
    scale = constants[R.QUALITY_SCALE]
    #: `quality.repair_ceiling_loss` задан отрицательным — складываем, а не
    #: вычитаем: знак принадлежит вольту, а не движку.
    cap = scale.clamp(float(item.condition_cap) + constants[R.QUALITY_REPAIR_CEILING_LOSS])
    item.condition_cap = _num(cap)
    item.condition = _num(cap)
    await session.flush()
    return [cap]


async def _finish_recycle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    batch: CraftBatch,
    where: Container,
) -> list[float]:
    """Переработка: вещи больше нет, а материалы вернулись не все."""
    from src.engine import coin

    #: Монета плавится по своей пробе, а не по норме рецепта: в испорченной
    #: металла ровно столько, сколько в неё положили (D-016).
    if coin.is_coin(catalog, batch.output):
        return await coin.finish_melt(session, constants, catalog, batch, where)

    item = await _target(session, batch)
    proc = procedure(catalog, batch.output)
    scale = constants[R.QUALITY_SCALE]

    carryover = constants[R.QUALITY_RECYCLE_CARRYOVER] / PERCENT
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    quality = scale.max if item.quality is None else float(item.quality)
    back = scale.clamp(quality * carryover)

    returned: list[float] = []
    for name, per_unit in proc.per_unit.items():
        given = amount(per_unit * share)
        if given <= 0:
            continue
        session.add(
            Item(container_id=where.id, type_key=name, amount=given, quality=_num(back))
        )
        returned.append(back)

    await events.record(
        session,
        EventKind.ITEM_CONSUMED,
        item_id=str(item.id),
        type_key=item.type_key,
        cause="переработка",
    )
    await session.delete(item)
    await session.flush()
    return returned


async def _target(session: AsyncSession, batch: CraftBatch) -> Item:
    item = await session.get(Item, batch.target_item_id)
    if item is None:
        raise CraftError(f"работа {batch.id}: вещи больше нет")
    return item


async def _work_on(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    kind: BatchKind,
    share: float,
    *,
    now: datetime | None,
) -> CraftBatch:
    """Общий ход починки и переработки: обе — работа над готовой вещью.

    Обе идут у того же станка, что и изготовление: разбирают и чинят там же,
    где делают.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CraftError("мёртвое тело не работает")
    await travel.require_here(session, body)

    inventory = await body_container(session, body)
    if item.container_id != inventory.id:
        raise CraftError("вещь не в руках: чинят и разбирают своё, а не чужое")

    proc = procedure(catalog, item.type_key)
    station = await _station_item(session, body, proc)
    scale = constants[R.QUALITY_SCALE]

    spent: dict[str, float] = {}
    if kind is BatchKind.REPAIR:
        stock = await _stock(session, inventory, proc.inputs)
        spent = {name: value * share for name, value in proc.per_unit.items()}
        for pick in _pick(stock, spent):
            if pick.item.amount > pick.take:
                pick.item.amount -= pick.take
            else:
                await session.delete(pick.item)
        await session.flush()

    minutes = batch_minutes(constants, proc, share, wear.effective(constants, station))
    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        kind=kind,
        output=item.type_key,
        target_item_id=item.id,
        units=amount(1),
        station_item_id=None if station is None else station.id,
        quality=_num(scale.min if item.quality is None else float(item.quality)),
        spread=_num(scale.min),
        spent=spent,
        ready_at=moment + timedelta(minutes=minutes),
    )
    session.add(batch)
    await session.flush()
    await _occupy(session, station, body, batch.ready_at)

    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        work=kind.value,
        output=batch.output,
        item_id=str(item.id),
        spent=spent,
    )
    await enqueue(
        session,
        JobKind.CRAFT_BATCH,
        batch.ready_at,
        payload={"batch": str(batch.id)},
        dedup_key=f"craft.batch:{batch.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return batch


async def copy_recipe(
    session: AsyncSession, catalog: Catalog, body: Body, key: str
) -> Knowledge | None:
    """Скопировать рецепт из Библиотеки.

    Бесплатно деньгами, без условий и без гражданства — и **не работает
    удалённо**: единственное ограничение Библиотеки географическое (D-053).

    Но не даром: копирование стоит `craft.copy_stamina` выносливости (D-148).
    Платит тело, а не счёт, — и знание остаётся общественным благом, переставая
    при этом быть кнопкой «выучить весь список за один заход».
    """
    constants = current()
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    #: Библиотека — станок (D-176): рецепты берут там, где он стоит.
    if node is None or not await world_engine.is_library(session, node):
        raise NoLibrary("Библиотека не работает удалённо: за знанием надо прийти")

    recipe = catalog.recipes.recipe(key)
    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise CraftError("тело без личности")

    #: Уже известное не переписывают: за одно и то же тело не платит дважды.
    if await _knows(session, body, recipe.name):
        return None

    расход = constants[R.CRAFT_COPY_STAMINA]
    if расход > float(body.stamina):
        raise NoStrength(
            f"на переписывание нужно {расход:.0f} выносливости, а есть "
            f"{float(body.stamina):.1f}: знание бесплатно, но работа — нет"
        )
    body.stamina = Decimal(str(float(body.stamina) - расход))
    await session.flush()
    return await learn(session, identity, recipe.name)


# --- внутреннее -------------------------------------------------------------


async def _prepare(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    units: float,
    *,
    tool_item_id: uuid.UUID | None,
    proportions: dict[str, float] | None,
    auto: bool = False,
) -> _Ready:
    """Общий ход прогноза и запуска.

    Одна функция на оба случая намеренно: прогноз, посчитанный не тем же кодом,
    что и партия, рано или поздно разойдётся с ней — и обещание «игрок видит
    точное число до партии» перестанет быть правдой (D-092).
    """
    if body.state is not BodyState.ALIVE:
        raise CraftError("мёртвое тело не работает")
    await travel.require_here(session, body)
    if units <= 0:
        raise CraftError("партия из нуля единиц")
    if units > constants[R.CRAFT_BATCH_MAX]:
        raise TooBig(f"партия больше craft.batch_max: {units}")

    proc = procedure(catalog, output)
    if not _stackable(catalog, proc.output) and units != int(units):
        raise CraftError(f"{proc.output!r} — изделие, а не сырьё: партия считается штуками")
    if proc.needs_recipe and not await _knows(session, body, proc.output):
        raise NotLearned(f"рецепт {proc.output!r} не скопирован в личность")

    #: Добыча места (D-177): идёт там, где у узла есть названное свойство, и
    #: только на своей либо ничьей земле — чужой лес принадлежит хозяину.
    if proc.place is not None:
        node = await session.get(Node, body.node_id)
        if node is None or not (node.properties or {}).get(proc.place):
            raise CraftError(f"здесь нет: {proc.place}")
        чужой = (
            node.owner_identity_id is not None
            and node.owner_identity_id != body.identity_id
        ) or (node.owner_identity_id is None and node.owner_city_id is not None)
        if чужой:
            raise CraftError(f"{proc.place} на чужой земле: рубить может хозяин")

    if auto:
        #: Промышленный уклад: потолок задаёт станок, инструмент не нужен вовсе,
        #: пропорции — его настройка, а не решение мастера (D-058).
        station = await _named_station(session, body, AUTO_BENCH)
        tools: list[Item] = []
    else:
        station = await _station_item(session, body, proc)
        tools = await _tool_items(session, catalog, body, proc, tool_item_id)

    scale = constants[R.QUALITY_SCALE]
    #: Ограничивает **действующее** качество: разбитая наковальня даёт худший
    #: результат, а не только внезапно ломается (`engine.wear`).
    limiters = [wear.effective(constants, item) for item in [station, *tools] if item is not None]
    ceiling = min(limiters) if limiters else scale.max

    inventory = await body_container(session, body)
    stock = await _stock(session, inventory, proc.inputs)

    optimal = optimal_amounts(constants, proc, units, _base_quality(proc, stock, scale.max))
    actual = (
        {catalog.recipes.resolve(name): value * units for name, value in proportions.items()}
        if proportions and not auto
        else {name: value * units for name, value in proc.per_unit.items()}
    )
    #: У автомата пропорция — его настройка, сделанная однажды: он работает по
    #: норме рецепта всегда и потому попадает в неё ровно (D-058).
    accuracy = 1.0 if auto or not proc.mix else ratio_accuracy(actual, optimal)

    waste = waste_share(constants, accuracy)
    #: Угар — доля **входов**, поэтому он берётся сверх нормы, а не из выхода:
    #: партия из десяти гвоздей не даёт девять с половиной гвоздей.
    required = {name: value / (1 - waste / PERCENT) for name, value in actual.items()}

    picks = _pick(stock, required)
    minutes = batch_minutes(
        constants, proc, units, wear.effective(constants, station), auto=auto
    )
    #: Автомат ест энергию за время работы. Ручной верстак не потребляет
    #: ничего: ремесло остаётся доступным тому, у кого нет денег на счета.
    from src.engine import energy as power

    энергии = (
        constants[R.ENERGY_AUTO_BENCH_DRAW] * minutes / MINUTES_PER_HOUR if auto else 0.0
    )
    цена_энергии = 0
    if энергии > 0:
        node = await session.get(Node, body.node_id)
        цена_энергии = await power.price_of(session, constants, node, энергии)

    forecast = Plan(
        output=proc.output,
        units=units,
        quality=forecast_quality(
            constants,
            proc,
            ceiling=ceiling,
            material=_material_quality(picks, scale.max),
            accuracy=accuracy,
            auto=auto,
        ),
        spread=spread_of(constants, accuracy),
        ceiling=ceiling,
        accuracy=accuracy,
        waste=waste,
        minutes=minutes,
        consumes=dict(required),
        auto=auto,
        energy=энергии,
        energy_cost=цена_энергии,
    )
    return _Ready(plan=forecast, picks=tuple(picks), station=station, auto=auto)


async def _knows(session: AsyncSession, body: Body, key: str) -> bool:
    stmt = select(Knowledge).where(
        Knowledge.identity_id == body.identity_id,
        Knowledge.kind == KnowledgeKind.RECIPE,
        Knowledge.key == key,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _named_station(session: AsyncSession, body: Body, name: str) -> Item:
    """Станок с этим именем в узле, лучший из свободных."""
    return await _pick_station(session, body, name)


async def _station_item(session: AsyncSession, body: Body, proc: Procedure) -> Item | None:
    """Станок стоит в узле — именно это делает крафт градообразующим."""
    if proc.station is None:
        return None
    return await _pick_station(session, body, proc.station)


async def _pick_station(session: AsyncSession, body: Body, name: str) -> Item:
    """Лучший **свободный** станок с этим именем в узле (D-150).

    Станок занимает один работник: пока идёт партия, второму он не отдаётся.
    Отсюда следствие, ради которого правило и заведено, — городская мастерская
    перестаёт быть бесплатным цехом на весь город, и ремесленнику становится
    нужен свой станок у себя дома.

    Отключённый за неуплату узел не работает вовсе (D-149): счётчик — такое же
    условие работы, как сам станок.
    """
    from src.engine import utility

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise CraftError("тело вне узла")
    if await utility.cut_off(session, node):
        raise CutOff(
            f"«{node.name}» отключён за неуплату: станки не работают, пока долг не закрыт"
        )

    where = await node_container(session, node)
    moment = datetime.now(UTC)
    стоят = (
        await session.execute(
            select(Item)
            .where(Item.container_id == where.id, Item.type_key == name)
            .order_by(Item.quality.desc())
        )
    ).scalars().all()
    if not стоят:
        raise NoStation(f"в узле нет станка «{name}»")

    свой = False
    for станок in стоят:
        #: Занят — значит занят, в том числе тем же мастером: за станком идёт
        #: одна работа, а не столько, сколько успел заказать хозяин.
        #: Метка страхует от вечной занятости: партия могла исчезнуть мимо
        #: своего задания, и станок не обязан простаивать из-за этого вечно.
        if станок.busy_body_id is not None and (
            станок.busy_until is None or станок.busy_until > moment
        ):
            свой = свой or станок.busy_body_id == body.id
            continue
        return станок
    raise Busy(
        f"«{name}» занят"
        + (" вашей же работой: дождитесь конца партии" if свой else
           ": за станком работает один. Свой станок ставят у себя")
    )


async def _occupy(session: AsyncSession, station: Item | None, body: Body, until) -> None:
    """Занять станок на время работы (D-150)."""
    if station is None:
        return
    station.busy_body_id = body.id
    station.busy_until = until
    await session.flush()


async def _release(session: AsyncSession, station_item_id) -> None:
    """Освободить станок. Вызывается вместе с завершением работы."""
    if station_item_id is None:
        return
    station = await session.get(Item, station_item_id)
    if station is None:  # pragma: no cover — станок могли разобрать
        return
    station.busy_body_id = None
    station.busy_until = None
    await session.flush()


async def _tool_items(
    session: AsyncSession,
    catalog: Catalog,
    body: Body,
    proc: Procedure,
    tool_item_id: uuid.UUID | None,
) -> list[Item]:
    """Инструмент носится с собой и участвует в потолке качества."""
    inventory = await body_container(session, body)
    found: list[Item] = []

    for requirement in proc.tools:
        names = catalog.recipes.tools_of_class(requirement) or (requirement,)
        item = (
            await session.execute(
                select(Item)
                .where(Item.container_id == inventory.id, Item.type_key.in_(names))
                .order_by(Item.quality.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if item is None:
            raise NoTool(f"нужен инструмент: {requirement}")
        found.append(item)

    if tool_item_id is not None and all(item.id != tool_item_id for item in found):
        chosen = await session.get(Item, tool_item_id)
        if chosen is None or chosen.container_id != inventory.id:
            raise NoTool("инструмента нет в инвентаре")
        found.append(chosen)
    return found


async def _stock(
    session: AsyncSession, container: Container, names: Iterable[str]
) -> dict[str, list[Item]]:
    """Что лежит по каждому входу, худшее первым.

    Порядок не случаен: в дело идёт то, что похуже, а чистое сырьё остаётся на
    ту партию, ради которой его добывали. Выбор стопки руками приедет вместе с
    интерфейсом.
    """
    out: dict[str, list[Item]] = {}
    for name in names:
        rows = (
            (
                await session.execute(
                    select(Item)
                    .where(Item.container_id == container.id, Item.type_key == name)
                    .order_by(Item.quality.asc().nulls_first(), Item.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        out[name] = list(rows)
    return out


def _base_quality(proc: Procedure, stock: dict[str, list[Item]], default: float) -> float:
    """Качество основы — первого входа. От него зависит оптимум пропорции."""
    if not proc.inputs:
        return default
    graded = [item for item in stock.get(proc.inputs[0], []) if item.quality is not None]
    total = sum(item.amount for item in graded)
    if not total:
        return default
    return sum(float(item.quality) * item.amount for item in graded) / total


def _pick(stock: dict[str, list[Item]], required: dict[str, float]) -> list[_Pick]:
    """Набрать нужное по стопкам. Не хватило — партия не начнётся вовсе."""
    picks: list[_Pick] = []
    for name, want in required.items():
        left = amount(want)
        for item in stock.get(name, []):
            if left <= 0:
                break
            take = min(left, item.amount)
            picks.append(_Pick(item=item, take=take))
            left -= take
        if left > 0:
            raise NotEnough(f"не хватает «{name}»: нужно ещё {amount_float(left)}")
    return picks


def _material_quality(picks: Sequence[_Pick], default: float) -> float:
    """Качество входов, взвешенное по количеству.

    Вход без качества — вода, энергия, монета — в среднее не входит: качества у
    него нет вовсе, а не ноль (15-quality, открытые вопросы).
    """
    graded = [pick for pick in picks if pick.item.quality is not None]
    total = sum(pick.take for pick in graded)
    if not total:
        return default
    return sum(float(pick.item.quality) * pick.take for pick in graded) / total


async def _wear_station(session: AsyncSession, constants: Constants, batch: CraftBatch) -> None:
    """Станок изнашивается за партию: содержание обязательно (D-129)."""
    if batch.station_item_id is None:
        return
    station = await session.get(Item, batch.station_item_id)
    if station is None:  # pragma: no cover — станок могли разобрать
        return
    await wear.spend(
        session,
        constants,
        station,
        constants[R.WEAR_STATION_PER_BATCH],
        cause="партия крафта",
    )


def _pieces(catalog: Catalog, output: str, units: float) -> list[float]:
    """Во что превращается партия: одна стопка сырья или столько-то изделий.

    Сырьё складывается, изделия нет (04-items), и у каждого изделия свой бросок
    разброса — потому что клеймо и качество у каждого своё (D-058).
    """
    if _stackable(catalog, output):
        return [units]
    return [1.0] * int(units)


def _stackable(catalog: Catalog, output: str) -> bool:
    try:
        kind = catalog.recipes.recipe(output).kind
    except ConstantError:
        #: Выход операции рецептом не описан — это сырьё, и оно складывается.
        return True
    return kind in (ItemKind.MATERIAL, ItemKind.CONSUMABLE, ItemKind.MONEY)


def _num(value: float) -> Decimal:
    """Число на шкале 0…100 в том виде, в каком его хранит база."""
    return Decimal(str(value))
