"""Задание глашатая: отнести новое из журнала событий в Discord.

Устроено как тик мира (`engine/tick.py`): задание ставит следующее себя же и
несёт курсор в своей полезной нагрузке. Отдельной таблицы под «докуда дочитано»
нет намеренно — состояние ленты не состояние мира, и переживать вайп ленты
никому не больно.

Три решения, о которых легко забыть через полгода:

* **Первый проход не досылает историю.** Курсора нет — значит лента начинается
  здесь: вываливать в канал весь журнал мира при первом запуске незачем. По
  той же причине простой глашатая не досылается: это лента, а не журнал, а
  журнал в базе и никуда не денется.
* **Отправка идёт внутри транзакции задания.** Сеть под открытой транзакцией —
  плата за то, что курсор и отправка фиксируются вместе. Проход ограничен
  `HERALD_BATCH` событиями и `HERALD_TIMEOUT` секундами, поэтому транзакция
  короткая. Обратная сторона честная: если Discord ответил, а фиксация не
  прошла, повтор задания пришлёт те же строки второй раз. Дубль в ленте
  дешевле пропуска.
* **Молчание при пустой настройке — не остановка.** Без вебхука задание всё
  равно двигает курсор: иначе включённый через месяц глашатай начал бы с
  месячного хвоста.

Известная неточность, записанная нарочно: курсор идёт по `event.id`, а номер
события берётся из последовательности **до** фиксации. Событие, получившее
номер раньше, но зафиксированное позже потолка прохода, в ленту не попадёт.
Для журнала это было бы браком, для ленты — нет: журнал полон и лежит в базе,
а лента ничего не доказывает.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine.jobs import enqueue, handler
from src.herald.chronicle import PUBLIC, compose
from src.herald.webhook import chunks, send
from src.models.event import Event
from src.models.job import Job, JobKind
from src.runtime import HERALD_BATCH, HERALD_PERIOD
from src.settings import settings

log = logging.getLogger(__name__)

Sender = Callable[[str, str], Awaitable[None]]


def _slot(moment: datetime) -> int:
    """Номер интервала от эпохи — он же ключ идемпотентности."""
    return int(moment.timestamp() // HERALD_PERIOD.total_seconds())


async def _last_event_id(session: AsyncSession) -> int:
    return (await session.execute(select(func.max(Event.id)))).scalar() or 0


async def run_once(
    session: AsyncSession,
    *,
    after: int | None,
    url: str,
    sender: Sender = send,
) -> int:
    """Один проход ленты. Возвращает новый курсор.

    Курсор двигается до последнего события журнала, а не до последнего
    **опубликованного**: иначе каждый следующий проход перечитывал бы всё
    молчаливое, накопившееся с тех пор, — а молчаливого в журнале большинство.
    """
    потолок = await _last_event_id(session)
    if after is None:
        log.info("глашатай начинает ленту с события %s", потолок)
        return потолок
    if not url:
        return потолок

    события = (
        await session.execute(
            select(Event)
            .where(Event.id > after, Event.id <= потолок, Event.kind.in_(PUBLIC))
            .order_by(Event.id)
            .limit(HERALD_BATCH)
        )
    ).scalars().all()

    строки = await compose(session, события)
    for кусок in chunks(строки):
        await sender(url, кусок)
    if строки:
        log.info("глашатай отнёс %s строк хроники", len(строки))

    #: Партия упёрлась в предел — значит за потолком осталось ещё; курсор
    #: встаёт на последнее взятое, и остаток уедет следующим проходом.
    if len(события) >= HERALD_BATCH:
        return события[-1].id
    return потолок


@handler(JobKind.HERALD_POST)
async def post(session: AsyncSession, job: Job) -> None:
    рубеж = await run_once(
        session,
        after=None if job.payload.get("after") is None else int(job.payload["after"]),
        url=settings().discord_webhook,
    )
    #: Следующее звено — не раньше, чем сейчас. Часы мира догоняют пропущенное
    #: тик за тиком, а ленте догонять нечего: за сутки простоя она иначе
    #: прокрутила бы семьсот пустых звеньев, чтобы прийти в ту же точку.
    следующее = max(job.run_at + HERALD_PERIOD, datetime.now(UTC))
    await _schedule(session, следующее, after=рубеж)


async def _schedule(session: AsyncSession, run_at: datetime, *, after: int) -> None:
    await enqueue(
        session,
        JobKind.HERALD_POST,
        run_at,
        payload={"after": after},
        dedup_key=f"herald.post:{_slot(run_at)}",
    )


async def ensure_scheduled(session: AsyncSession, now: datetime | None = None) -> None:
    """Завести глашатая при старте воркера.

    Пока цепочка жива, ключ текущего интервала уже занят её звеном, и второго
    глашатая здесь не появится. Если цепочка когда-то оборвалась насовсем
    (задание сдалось после всех попыток), этот вызов заводит её заново —
    с чистого листа, без хвоста за время простоя.
    """
    moment = now or datetime.now(UTC)
    await enqueue(
        session,
        JobKind.HERALD_POST,
        moment,
        dedup_key=f"herald.post:{_slot(moment)}",
    )
