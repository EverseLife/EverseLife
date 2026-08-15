"""Глашатай: что мир объявляет вслух — и чего не объявляет никогда.

Проверяется прежде всего граница, а не доставка. Сломанная доставка видна сразу
и чинится за вечер; сломанная граница обнаруживается в тот день, когда в общий
канал уедет чужой кошелёк, и обратно её уже не соберёшь.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import herald
from src.constants import Catalog
from src.engine import events, jobs, world
from src.herald import chronicle, webhook
from src.herald.job import run_once
from src.models.event import EventKind
from src.models.job import Job, JobKind, JobState
from src.models.world import Layer
from src.runtime import DISCORD_CONTENT_LIMIT

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
ВЕБХУК = "https://discord.invalid/api/webhooks/тест"

#: То, чему наружу хода нет ни при каких настройках: деньги, вещи, тело,
#: разговоры, знания и репорты. Список нарочно избыточен — он и есть договор.
ЛИЧНОЕ = {
    EventKind.LEDGER_POSTED,
    EventKind.TRADE_EXECUTED,
    EventKind.ITEM_MOVED,
    EventKind.ITEM_CREATED,
    EventKind.MINING_SWING,
    EventKind.BODY_DIED,
    EventKind.BODY_PRINTED,
    EventKind.LOAN_TAKEN,
    EventKind.DEBT_WITHHELD,
    EventKind.KNOWLEDGE_LEARNED,
    EventKind.REPORT_FILED,
    EventKind.EXPLORE_STARTED,
    EventKind.TRAVEL_ARRIVED,
    EventKind.STORAGE_PUT,
}


async def _узел(session: AsyncSession, имя: str = "Медный склон"):
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    return await world.create_node(
        session, f"terra.{метка}.core", имя, area_m2=100, parent=планета
    )


# --- граница ----------------------------------------------------------------


def test_личное_наружу_не_уходит() -> None:
    утечка = chronicle.PUBLIC & {str(kind) for kind in ЛИЧНОЕ}
    assert not утечка, (
        "эти события личные: деньги, вещи, тело и разговоры остаются в игре. "
        f"Наружу собрались: {sorted(утечка)}"
    )


def test_список_белый() -> None:
    """Вид события, которому не написали строку, молчит сам собой."""
    assert {str(kind) for kind in chronicle.LINES} == chronicle.PUBLIC
    assert str(EventKind.TICK_RAN) not in chronicle.PUBLIC


def test_упоминания_отключены() -> None:
    """Город можно назвать `@everyone` — и это не должно дёргать сервер."""
    тело = webhook.payload("основан город @everyone")
    assert тело["allowed_mentions"] == {"parse": []}


def test_разметка_в_чужом_имени_обезврежена() -> None:
    строка = chronicle.plain("**@everyone** `код`")
    assert "**@" not in строка
    assert строка.count("\\") >= 4


def test_длинная_лента_режется_по_пределу_discord() -> None:
    строки = ["ю" * 900] * 5
    куски = webhook.chunks(строки)
    assert len(куски) > 1, "пять таких строк в одно сообщение не влезают"
    assert all(len(кусок) <= DISCORD_CONTENT_LIMIT for кусок in куски)
    assert sum(кусок.count("ю") for кусок in куски) == 4500, "ни одна строка не потеряна"


# --- строки хроники ---------------------------------------------------------


async def test_основание_города_называет_город_и_место(
    session: AsyncSession, catalog: Catalog
) -> None:
    узел = await _узел(session)
    кто = await world.create_identity(session, f"Ким-{uuid.uuid4().hex[:6]}")
    событие = await events.record(
        session,
        EventKind.CITY_FOUNDED,
        actor_identity_id=кто.id,
        node_id=узел.id,
        city_id=str(uuid.uuid4()),
        name="Рудный",
        founded_by_player=True,
    )

    строки = await chronicle.compose(session, [событие])

    assert len(строки) == 1
    assert "Рудный" in строки[0]
    assert "Медный склон" in строки[0]
    assert кто.name in строки[0]


async def test_находка_разведки_не_называет_породу(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Порода жилы — плата разведчику за риск, а не новость для всех."""
    узел = await _узел(session, "Пойма")
    кто = await world.create_identity(session, f"Вей-{uuid.uuid4().hex[:6]}")
    событие = await events.record(
        session,
        EventKind.EXPLORE_FOUND,
        actor_identity_id=кто.id,
        node_id=узел.id,
        from_node="terra.city",
        found="terra.wild",
        name="Пойма",
        resource="медь",
        minutes=12,
    )

    строка = (await chronicle.compose(session, [событие]))[0]

    assert "Пойма" in строка
    assert "медь" not in строка


async def test_молчаливое_событие_строки_не_даёт(
    session: AsyncSession, catalog: Catalog
) -> None:
    событие = await events.record(session, EventKind.TICK_RAN, kind_of_tick="world")
    assert await chronicle.compose(session, [событие]) == []


# --- проход ленты -----------------------------------------------------------


async def test_первый_проход_историю_не_досылает(
    session: AsyncSession, catalog: Catalog
) -> None:
    узел = await _узел(session)
    событие = await events.record(
        session, EventKind.CITY_FOUNDED, node_id=узел.id, name="Рудный"
    )

    отправленное: list[str] = []

    async def _шлёт(url: str, text: str) -> None:
        отправленное.append(text)

    рубеж = await run_once(session, after=None, url=ВЕБХУК, sender=_шлёт)

    assert отправленное == [], "лента начинается сейчас, а не с сотворения мира"
    assert рубеж == событие.id


async def test_проход_шлёт_и_двигает_курсор(
    session: AsyncSession, catalog: Catalog
) -> None:
    узел = await _узел(session)
    событие = await events.record(
        session, EventKind.CITY_FOUNDED, node_id=узел.id, name="Рудный"
    )

    отправленное: list[str] = []

    async def _шлёт(url: str, text: str) -> None:
        отправленное.append(text)

    рубеж = await run_once(session, after=0, url=ВЕБХУК, sender=_шлёт)

    assert len(отправленное) == 1
    assert "Рудный" in отправленное[0]
    assert рубеж == событие.id

    #: Второй проход с новым курсором молчит: то же событие не уходит дважды.
    отправленное.clear()
    assert await run_once(session, after=рубеж, url=ВЕБХУК, sender=_шлёт) == рубеж
    assert отправленное == []


async def test_без_вебхука_курсор_всё_равно_едет(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Иначе включённый через месяц глашатай начал бы с месячного хвоста."""
    узел = await _узел(session)
    событие = await events.record(
        session, EventKind.CITY_FOUNDED, node_id=узел.id, name="Рудный"
    )

    assert await run_once(session, after=0, url="") == событие.id


async def test_глашатай_ставит_следующее_звено(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session, session.begin():
        await herald.ensure_scheduled(session, NOW)

    сделано = await jobs.run_one(factory, now=NOW)
    assert сделано is not None
    assert сделано.state is JobState.DONE

    async with factory() as session:
        стоит = (
            await session.execute(
                select(Job).where(
                    Job.state == JobState.PENDING, Job.kind == JobKind.HERALD_POST
                )
            )
        ).scalars().all()

    assert len(стоит) == 1, "лента обязана продолжаться сама"
    assert стоит[0].run_at > NOW
    assert стоит[0].payload.get("after") is not None, "курсор едет в задании"
