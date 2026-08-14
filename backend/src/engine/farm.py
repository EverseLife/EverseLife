"""Земледелие делянками (D-118, D-105, D-057).

Сорта, семена и скрещивание живут рядом, в `engine/breed.py`: здесь — земля и
цикл, там — то, что на ней растёт.

Вторая педаль экономики: добычу ограничивает внимание игрока, земледелие —
земля и время. Цикл делянки: вспашка → посев → уход → рост → уборка → пар или
следующая культура. Рост идёт офлайн, уход — только ногами: полностью офлайн
фермерство не идёт, иначе это печатный станок (D-118).

## Откуда взялась каждая формула

Числа — из `farm.*` и `build/plants.json`, порядок шагов — дело движка.

**Сутки.** Все фермерские сроки заданы «в сутках», и сутки здесь планетарные:
`time.day_terra` часов (D-008). Другой длины суток у Терры нет.

**Уход.** Раз в сутки, делянке целиком. Время обхода — формула вольта:
`farm.plot_overhead + farm.care_time_per_m2 × площадь`; вода —
`farm.water_per_m2 × площадь`, и у реки её берут из реки, а в сухом месте
носят предметом. Пропущенные сутки не обнуляют урожай, а режут его на
`farm.neglect_penalty` каждые: за отпуск не наказывают, но небрежность видна.

**Урожай.** «Пропорционален площади, плодородию и качеству ухода»:

    выход = площадь × yield_per_m2 × (плодородие / требуемое) × доля ухода
    доля ухода = 1 − neglect_penalty × пропущенные сутки / 100  (не ниже нуля)

`yield_per_m2` не задан руками — он выведен вольтом из `harvest.rates` (D-136),
и движок берёт его готовым. Качество урожая — плодородие, взятое по доле ухода:
ухоженная земля отдаёт то, что в ней есть, запущенная — хуже.

**Истощение.** `farm.soil_depletion` за каждый цикл **той же культуры** подряд:
монокультура выедает землю, чередование — нет. Культура-восстановитель
возвращает своё `restores_fertility` из данных (бобы), пар — по
`farm.fallow_recovery` в сутки простоя, начисляется по факту времени при
следующем действии — тик земле не нужен, как и сну.

## Честные упрощения этой версии

* **Побочный продукт** (солома у полбы) не выдаётся: доля не задана данными,
  а выдумывать её здесь нельзя (D-065);
* **Болезни и пять параметров ухода** (OQ-098) сведены к одному суточному
  обходу: чем отвечает игрок при уходе — открытый вопрос пилотного экрана,
  и до его закрытия обход бинарен.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import events, travel, world
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.plant import Variety
from src.models.world import Node
from src.units import PERCENT, SCALE_MAX, SCALE_MIN, SECONDS_PER_HOUR, amount, amount_float

#: Имя воды в `build/recipes.json` — её носят руками там, где нет реки.
WATER = "Вода"


class FarmError(Exception):
    pass


class NoLand(FarmError):
    """Земля узла конечна: ёмкость под пашню не резиновая."""


class NotYours(FarmError):
    pass


class WrongState(FarmError):
    """Делянка не в том состоянии: незасеянное не убирают, спелое не пашут."""


class NoSeeds(FarmError):
    pass


class NoWater(FarmError):
    """В сухом месте воду носят руками (D-126)."""


class TooSmall(FarmError):
    """Меньше `farm.plot_min_area` межевать бессмысленно."""


def day_hours(constants: Constants) -> float:
    """Сутки Терры. Все фермерские сроки заданы в них (D-008)."""
    return constants[R.TIME_DAY_TERRA]


def ripe_at(constants: Constants, plot: Plot, plant: Plant) -> datetime:
    if plot.sown_at is None:  # pragma: no cover
        raise WrongState("делянка не засеяна")
    return plot.sown_at + timedelta(hours=plant.cycle_days * day_hours(constants))


def care_minutes(constants: Constants, area: float) -> float:
    """Время обхода: формула вольта. Земля масштабируется, руки — нет."""
    return constants[R.FARM_PLOT_OVERHEAD] + constants[R.FARM_CARE_TIME_PER_M2] * area


async def mark(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    name: str,
    area: float,
    now: datetime | None = None,
) -> Plot:
    """Разметить делянку. Присутственное: землю меряют ногами."""
    moment = now or datetime.now(UTC)
    await _here(session, body)
    if area < constants[R.FARM_PLOT_MIN_AREA]:
        raise TooSmall(
            f"меньше {constants[R.FARM_PLOT_MIN_AREA]} м² межевать бессмысленно"
        )

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise FarmError("тело вне узла")
    #: Хозяйство ведёт владелец участка: сначала займи землю (06-farming).
    #: Наём — это доступ плюс доля через договор (D-116), а не общая земля.
    if node.owner_identity_id != body.identity_id:
        raise NotYours(
            "участок не ваш: землю сначала занимают, а чужую — арендуют по договору"
        )

    taken = float(
        await session.scalar(
            select(func.coalesce(func.sum(Plot.area_m2), 0)).where(Plot.node_id == node.id)
        )
        or 0
    )
    if taken + area > float(node.area_m2):
        raise NoLand(
            f"в узле {node.key} свободно {float(node.area_m2) - taken:g} м², "
            f"просят {area:g}"
        )

    plot = Plot(
        node_id=node.id,
        owner_identity_id=body.identity_id,
        name=name.strip() or "без имени",
        area_m2=Decimal(str(area)),
        fertility=Decimal(str(_ground_fertility(node))),
        idle_since=moment,
    )
    session.add(plot)
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_MARKED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        plot_id=str(plot.id),
        area=area,
    )
    return plot


async def plow(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    plot: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Вспахать. Длительное: началось присутственно, идёт само."""
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.IDLE:
        raise WrongState(f"делянка {plot.name!r} не под паром: {plot.state.value}")

    _accrue_fallow(constants, plot, moment)
    plot.state = PlotState.PLOWING
    plot.idle_since = None
    await session.flush()

    ready = moment + timedelta(
        minutes=constants[R.FARM_PLOW_TIME_PER_M2] * float(plot.area_m2)
    )
    event = await events.record(
        session,
        EventKind.PLOT_PLOWED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
    )
    await enqueue(
        session,
        JobKind.FARM_PLOW,
        ready,
        payload={"plot": str(plot.id)},
        dedup_key=f"farm.plow:{plot.id}:{moment.timestamp()}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return plot


@handler(JobKind.FARM_PLOW)
async def plow_done(session: AsyncSession, job: Job) -> None:
    plot = await session.get(Plot, uuid.UUID(job.payload["plot"]))
    if plot is None:  # pragma: no cover
        raise FarmError(f"задание {job.id}: делянки нет")
    if plot.state is not PlotState.PLOWING:
        #: Повтор задания после сбоя пашню не удваивает.
        return
    plot.state = PlotState.PLOWED
    await session.flush()


async def sow(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    plot: Plot,
    seeds: Item,
    *,
    now: datetime | None = None,
) -> Plot:
    """Посеять семенами конкретного сорта (D-057).

    Сеют не урожаем, а семенами: у партии есть сорт и своя сила. И то и другое
    переезжает на делянку — урожай считается по ним, а не по числам культуры.
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.PLOWED:
        raise WrongState(f"делянка {plot.name!r} не вспахана")

    from src.engine import breed

    variety = await breed._variety_of(session, seeds)  # noqa: SLF001
    plant = catalog.plants.by_id(variety.culture_id)
    if seeds.type_key != plant.seed:  # pragma: no cover — сорт и семя из данных
        raise NoSeeds(f"{seeds.type_key!r} — не семена культуры {plant.name!r}")

    pocket = await world.body_container(session, body)
    if seeds.container_id != pocket.id:
        raise NoSeeds("семена не в руках: сеют своим")

    need = amount(constants[R.FARM_SEED_RATE] * float(plot.area_m2))
    if seeds.amount < need:
        raise NoSeeds(
            f"нужно {amount_float(need):g} «{plant.seed}» на посев, "
            f"есть {amount_float(seeds.amount):g}"
        )
    seeds.amount -= need
    сила = float(seeds.vigor) if seeds.vigor is not None else SCALE_MAX
    if seeds.amount <= 0:
        await session.delete(seeds)

    plot.state = PlotState.SOWN
    plot.culture_id = plant.id
    plot.variety_id = variety.id
    plot.seed_vigor = Decimal(str(сила))
    plot.sown_at = moment
    plot.care_credits = 0
    plot.cared_at = None
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_SOWN,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        culture=plant.id,
        variety=str(variety.id),
        vigor=сила,
        seeds=amount_float(need),
    )
    return plot


async def care(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    plot: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Обойти делянку: раз в сутки, ногами, с водой.

    У реки вода берётся из реки; в сухом месте — из инвентаря, и это делает
    воду товаром там, где её нет (D-126).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.sown_at is None:
        raise WrongState(f"на делянке {plot.name!r} ничего не растёт")

    day = timedelta(hours=day_hours(constants))
    if plot.cared_at is not None and moment - plot.cared_at < day:
        raise WrongState("сегодня уже ухожено: уход суточный, а не почасовой")

    node = await session.get(Node, plot.node_id)
    if node is None or node.properties.get("вода") != "река":
        need = amount(constants[R.FARM_WATER_PER_M2] * float(plot.area_m2))
        await _consume(session, body, WATER, need, why=NoWater(
            f"нужно {amount_float(need):g} воды: реки здесь нет, воду носят руками"
        ))

    plot.care_credits += 1
    plot.cared_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_CARED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        credits=plot.care_credits,
        minutes=care_minutes(constants, float(plot.area_m2)),
    )
    return plot


async def harvest(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    plot: Plot,
    *,
    select_seed: bool = False,
    now: datetime | None = None,
) -> float:
    """Убрать урожай. Возвращает собранное количество.

    Урожай пропорционален площади, плодородию, качеству ухода **и силе сорта**;
    истощение и восстановление земли начисляются здесь же — уборка закрывает
    цикл.

    Часть урожая (`farm.harvest_seed_share`) остаётся на семена. Если фермер
    вёл **отбор** — присутственная работа, где и проявляется мастерство, — фонд
    держит силу; если нет, семена вырождаются, а у гибрида ещё и расщепляются
    (D-057, D-067).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.culture_id is None:
        raise WrongState(f"на делянке {plot.name!r} нечего убирать")

    from src.engine import breed

    plant = catalog.plants.by_id(plot.culture_id)
    #: Сорт решает числа: у посеянного своим фондом они уже не те, что у
    #: культуры в справочнике. Старые делянки без сорта считаются базовым.
    variety = (
        await session.get(Variety, plot.variety_id)
        if plot.variety_id is not None
        else None
    ) or await breed.landrace(session, catalog, plant.id)
    признаки = variety.traits or breed.traits_of_plant(plant)
    цикл = float(признаки.get("cycle_days", plant.cycle_days))
    сила = float(plot.seed_vigor) if plot.seed_vigor is not None else SCALE_MAX

    ready = (plot.sown_at or moment) + timedelta(hours=цикл * day_hours(constants))
    if moment < ready:
        raise WrongState(
            f"культура дозреет к {ready.isoformat()}: цикл {цикл:g} суток"
        )

    area = float(plot.area_m2)
    fertility = float(plot.fertility)
    #: Пропущенные сутки ухода режут урожай, но не обнуляют его.
    missed = max(0, int(цикл) - plot.care_credits)
    care_share = max(0.0, 1 - constants[R.FARM_NEGLECT_PENALTY] * missed / PERCENT)
    soil_share = fertility / float(признаки.get("fertility", plant.requires.fertility))

    got = (
        area
        * float(признаки.get("yield_per_m2", plant.yield_per_m2))
        * soil_share
        * care_share
        * (сила / PERCENT)
    )
    quality = max(SCALE_MIN, min(SCALE_MAX, fertility * max(care_share, 0.0)))

    pocket = await world.body_container(session, body)
    if got > 0:
        from src.engine import food

        session.add(
            Item(
                container_id=pocket.id,
                type_key=plant.gives,
                amount=amount(got),
                quality=Decimal(str(quality)),
                #: Урожай портится со скоростью сорта: репа быстрее льна.
                spoils_at=food.harvest_spoils_at(
                    constants,
                    float(признаки.get("spoilage_k", plant.traits.spoilage_k)),
                    now=moment,
                ),
            )
        )

    #: Своё семя: доля урожая, оставленная на посев, а не на продажу.
    семян = got * constants[R.FARM_HARVEST_SEED_SHARE] / PERCENT
    if семян > 0:
        сила_семян = breed.next_vigor(constants, variety, сила, selected=select_seed)
        if select_seed:
            await breed.select_generation(session, constants, variety)
        await breed.seed_lot(
            session, catalog, pocket.id, variety, семян, сила_семян, now=moment
        )

    #: Земля помнит, что на ней росло: монокультура выедает её, чередование нет.
    depletion = (
        constants[R.FARM_SOIL_DEPLETION] if plot.last_culture == plant.id else 0.0
    )
    restored = plant.restores_fertility
    plot.fertility = Decimal(
        str(max(SCALE_MIN, min(SCALE_MAX, fertility - depletion + restored)))
    )
    plot.same_culture_cycles = (
        plot.same_culture_cycles + 1 if plot.last_culture == plant.id else 1
    )
    plot.last_culture = plant.id
    plot.culture_id = None
    plot.variety_id = None
    plot.seed_vigor = None
    plot.sown_at = None
    plot.care_credits = 0
    plot.cared_at = None
    plot.state = PlotState.IDLE
    plot.idle_since = moment
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_HARVESTED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        culture=plant.id,
        variety=str(variety.id),
        selected=select_seed,
        got=got,
        seeds=семян,
        quality=quality,
        missed_days=missed,
        fertility=float(plot.fertility),
    )
    return got


async def split(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    plot: Plot,
    cut_area: float,
    *,
    name: str,
    now: datetime | None = None,
) -> Plot:
    """Разделить делянку. Обе части наследуют плодородие и историю как есть."""
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    _recuttable(plot)

    rest = float(plot.area_m2) - cut_area
    if cut_area < constants[R.FARM_PLOT_MIN_AREA] or rest < constants[R.FARM_PLOT_MIN_AREA]:
        raise TooSmall("обе части обязаны быть не меньше farm.plot_min_area")

    _accrue_fallow(constants, plot, moment)
    plot.area_m2 = Decimal(str(rest))
    #: Перекроенное пашут заново.
    plot.state = PlotState.IDLE
    plot.idle_since = moment

    piece = Plot(
        node_id=plot.node_id,
        owner_identity_id=plot.owner_identity_id,
        name=name.strip() or "отрез",
        area_m2=Decimal(str(cut_area)),
        fertility=plot.fertility,
        last_culture=plot.last_culture,
        same_culture_cycles=plot.same_culture_cycles,
        idle_since=moment,
    )
    session.add(piece)
    await session.flush()
    return piece


async def merge(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    one: Plot,
    other: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Слить две делянки: плодородие взвешенно, история — самая тяжёлая.

    Анти-эксплойт (D-118): иначе передел границ сбрасывал бы истощение.
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(one, body)
    _owned(other, body)
    _recuttable(one)
    _recuttable(other)
    if one.node_id != other.node_id:
        raise FarmError("сливают соседние делянки, а не землю из разных узлов")

    _accrue_fallow(constants, one, moment)
    _accrue_fallow(constants, other, moment)

    a, b = float(one.area_m2), float(other.area_m2)
    one.area_m2 = Decimal(str(a + b))
    one.fertility = Decimal(
        str((float(one.fertility) * a + float(other.fertility) * b) / (a + b))
    )
    heavier = max((one, other), key=lambda p: p.same_culture_cycles)
    one.last_culture = heavier.last_culture
    one.same_culture_cycles = heavier.same_culture_cycles
    #: Перекроенное пашут заново.
    one.state = PlotState.IDLE
    one.idle_since = moment

    await session.delete(other)
    await session.flush()
    return one


async def survey(
    session: AsyncSession, constants: Constants, catalog: Catalog, identity_id: uuid.UUID
) -> list[dict]:
    """Сводка хозяйства. Удалённое: читается откуда угодно, уход — ногами."""
    now = datetime.now(UTC)
    plots = (
        await session.execute(
            select(Plot, Node.name, Node.key)
            .join(Node, Node.id == Plot.node_id)
            .where(Plot.owner_identity_id == identity_id)
            .order_by(Plot.created_at)
        )
    ).all()

    out: list[dict] = []
    for plot, node_name, node_key in plots:
        row: dict = {
            "id": str(plot.id),
            "name": plot.name,
            "node": node_name,
            "node_key": node_key,
            "area": float(plot.area_m2),
            "state": plot.state.value,
            "fertility": float(plot.fertility),
            "culture": plot.culture_id,
        }
        if plot.state is PlotState.SOWN and plot.culture_id is not None and plot.sown_at:
            from src.engine import breed

            plant = catalog.plants.by_id(plot.culture_id)
            variety = (
                await session.get(Variety, plot.variety_id)
                if plot.variety_id is not None
                else None
            ) or await breed.landrace(session, catalog, plant.id)
            признаки = variety.traits or breed.traits_of_plant(plant)
            цикл = float(признаки.get("cycle_days", plant.cycle_days))
            надо_плодородия = float(
                признаки.get("fertility", plant.requires.fertility)
            )

            ready = plot.sown_at + timedelta(hours=цикл * day_hours(constants))
            day = timedelta(hours=day_hours(constants))
            просит_ухода = plot.cared_at is None or now - plot.cared_at >= day
            #: Потери набегают в тот день, когда набегают, а не сюрпризом при
            #: уборке (D-118).
            elapsed = (now - plot.sown_at).total_seconds() / (
                day_hours(constants) * SECONDS_PER_HOUR
            )
            пропущено = max(0, min(int(цикл), int(elapsed)) - plot.care_credits)
            спелое = now >= ready

            row["culture_name"] = plant.name
            row["variety"] = variety.name or f"гибрид, поколение {variety.generation}"
            row["ripe"] = спелое

            #: Знание превращает угадайку в решённую задачу (D-057). С
            #: агротехникой видны нормы и остаток до них; без неё — только
            #: симптомы, общие для всех культур, и что с ними делать, фермер
            #: выясняет опытом, покупкой знания или упрямством.
            знает = await breed.knows_agrotech(session, identity_id, variety)
            row["agrotech"] = знает
            if знает:
                row["ripe_at"] = ready.isoformat()
                row["asks_care"] = просит_ухода
                row["missed_days"] = пропущено
                row["cycle_days"] = цикл
                row["fertility_required"] = надо_плодородия
                row["water_need"] = (
                    constants[R.FARM_WATER_PER_M2] * float(plot.area_m2)
                )
            else:
                #: Движок называет признак, слово подбирает клиент: симптом —
                #: это то, что видно, а не то, что посчитано.
                симптомы: list[str] = []
                if просит_ухода:
                    симптомы.append("thirst")
                if float(plot.fertility) < надо_плодородия:
                    симптомы.append("pale")
                if пропущено > 0:
                    симптомы.append("stunted")
                if спелое:
                    симптомы.append("ripe")
                row["symptoms"] = симптомы
        out.append(row)
    return out


# --- внутреннее -------------------------------------------------------------


async def _here(session: AsyncSession, body: Body) -> None:
    if body.state is not BodyState.ALIVE:
        raise FarmError("мёртвое тело не работает")
    await travel.require_here(session, body)


def _owned(plot: Plot, body: Body) -> None:
    if plot.owner_identity_id != body.identity_id:
        raise NotYours("чужая делянка: аренда и наём — через договор (D-116)")


def _recuttable(plot: Plot) -> None:
    if plot.state not in (PlotState.IDLE, PlotState.PLOWED):
        raise WrongState("перекроить можно только незасеянное (D-118)")


def _ground_fertility(node: Node) -> float:
    """Стартовое плодородие — свойство места (D-126). Нет свойства — не родит."""
    raw = node.properties.get("плодородие", 0)
    try:
        return max(SCALE_MIN, min(SCALE_MAX, float(raw)))
    except (TypeError, ValueError):
        return SCALE_MIN


def _accrue_fallow(constants: Constants, plot: Plot, moment: datetime) -> None:
    """Пар: восстановление по факту простоя. Тик земле не нужен, как и сну."""
    if plot.idle_since is None:
        return
    days = max(
        0.0,
        (moment - plot.idle_since).total_seconds()
        / (day_hours(constants) * SECONDS_PER_HOUR),
    )
    if days <= 0:
        return
    healed = constants[R.FARM_FALLOW_RECOVERY] * days
    plot.fertility = Decimal(str(min(SCALE_MAX, float(plot.fertility) + healed)))
    plot.idle_since = moment


async def _consume(
    session: AsyncSession, body: Body, type_key: str, need: int, *, why: FarmError
) -> None:
    """Списать из кармана, худшее первым. Не хватило — действие не началось."""
    pocket = await world.body_container(session, body)
    stacks = (
        (
            await session.execute(
                select(Item)
                .where(Item.container_id == pocket.id, Item.type_key == type_key)
                .order_by(Item.quality.asc().nulls_first())
            )
        )
        .scalars()
        .all()
    )
    have = sum(stack.amount for stack in stacks)
    if have < need:
        raise why
    left = need
    for stack in stacks:
        if left <= 0:
            break
        take = min(left, stack.amount)
        if take == stack.amount:
            await session.delete(stack)
        else:
            stack.amount -= take
        left -= take
    await session.flush()
