"""Что мир объявляет вслух.

Список видов событий ниже — **белый**: новое событие молчит, пока его сюда не
внесли руками. Это не настройка ленты, а решение о приватности, и принимается
оно по одному правилу: наружу идёт только то, что публично **внутри** игры.

Публично в игре: владелец участка виден всякому вошедшему (D-178), карта
открыта (`/public/map`), устав и код-законы лежат в `/public/laws`, суд и
выборы происходят при свидетелях, ключевая ставка объявляется вместе с
объяснением (D-030).

Не публично и сюда не попадёт никогда: деньги и счета, содержимое карманов и
сундуков, разговоры и кружки (D-043), **порода найденной жилы**. Последнее не
мелочь: находка — плата разведчику за риск и время, и объявить её всему миру
значит эту плату у него отобрать. Поэтому в строке о разведке есть узел,
которого мир и так не скроет, и нет того, что в нём нашли.

Строки пишутся без глаголов прошедшего времени в мужском роде: пол за именем
личности не стоит, и угадывать его строкой хроники незачем.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.city import City
from src.models.event import Event, EventKind
from src.models.identity import Identity
from src.models.vote import Vote, VoteKind
from src.models.world import Node
from src.runtime import HERALD_TEXT_LIMIT

#: Знаки разметки Discord. Имена городов и участков придумывают игроки, и
#: звёздочка в имени не должна превращать половину хроники в курсив.
MARKDOWN = "\\*_~`|>#@"

НЕИЗВЕСТНО = "неизвестно кто"
НИГДЕ = "где-то"


def plain(text: object) -> str:
    """Чужой текст в строке хроники: без разметки и без длины на весь экран."""
    строка = str(text or "").strip()[:HERALD_TEXT_LIMIT]
    return "".join("\\" + знак if знак in MARKDOWN else знак for знак in строка)


def _число(value: object) -> str | None:
    """Число из полезной нагрузки. Не число — не строка хроники, а молчание."""
    try:
        return f"{float(value):g}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class Names:
    """Имена по идентификаторам, с памятью на один проход.

    Проход разбирает десятки событий, и половина из них — про один и тот же
    город. Кэш живёт ровно один проход: имя участка меняется (D-178), и
    переживать проход этой памяти незачем.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._помнит: dict[str, str] = {}

    async def identity(self, ident: uuid.UUID | None) -> str:
        if ident is None:
            return НЕИЗВЕСТНО
        ключ = f"identity:{ident}"
        if ключ not in self._помнит:
            кто = await self._db.get(Identity, ident)
            self._помнит[ключ] = plain(кто.name) if кто else НЕИЗВЕСТНО
        return self._помнит[ключ]

    async def node(self, node_id: uuid.UUID | None) -> str:
        if node_id is None:
            return НИГДЕ
        ключ = f"node:{node_id}"
        if ключ not in self._помнит:
            узел = await self._db.get(Node, node_id)
            self._помнит[ключ] = plain(узел.name) if узел else НИГДЕ
        return self._помнит[ключ]

    async def city(self, city_id: object) -> str:
        if not city_id:
            return НИГДЕ
        ключ = f"city:{city_id}"
        if ключ not in self._помнит:
            try:
                город = await self._db.get(City, uuid.UUID(str(city_id)))
            except ValueError:
                город = None
            self._помнит[ключ] = plain(город.name) if город else НИГДЕ
        return self._помнит[ключ]

    async def vote(self, vote_id: object) -> Vote | None:
        if not vote_id:
            return None
        try:
            return await self._db.get(Vote, uuid.UUID(str(vote_id)))
        except ValueError:
            return None


#: Как называется предмет голосования в ленте. Машина одна, предметы разные.
ГОЛОСОВАНИЯ = {
    VoteKind.LAW: "голосование по закону",
    VoteKind.ELECTION: "выборы правителя",
    VoteKind.RECALL: "отзыв правителя",
    VoteKind.CHARTER: "правка устава",
    VoteKind.COUNCIL: "выборы в совет",
}


async def _city_founded(event: Event, names: Names) -> str | None:
    имя = plain(event.payload.get("name")) or await names.city(event.payload.get("city_id"))
    где = await names.node(event.node_id)
    кто = await names.identity(event.actor_identity_id)
    return f"🏛 **Основан город {имя}** — {где}. Кто основал: {кто}."


async def _city_law_set(event: Event, names: Names) -> str | None:
    город = await names.city(event.payload.get("city_id"))
    закон = plain(event.payload.get("law")) or "закон"
    было = plain(event.payload.get("was"))
    стало = plain(event.payload.get("now"))
    return f"📐 {город}: код-закон «{закон}» — было {было or '—'}, стало {стало or '—'}."


async def _city_charter_set(event: Event, names: Names) -> str | None:
    город = await names.city(event.payload.get("city_id"))
    вопрос = plain(event.payload.get("question")) or "вопрос устава"
    выбор = plain(event.payload.get("option")) or "—"
    return f"📜 {город}: устав — «{вопрос}» теперь «{выбор}»."


async def _vote_closed(event: Event, names: Names) -> str | None:
    город = await names.city(event.payload.get("city_id"))
    голосование = await names.vote(event.payload.get("vote_id"))
    предмет = "голосование" if голосование is None else ГОЛОСОВАНИЯ.get(
        голосование.kind, "голосование"
    )
    итог = "прошло" if event.payload.get("passed") else "не прошло"
    за = plain(event.payload.get("yes"))
    против = plain(event.payload.get("no"))
    ценз = plain(event.payload.get("electorate"))
    return f"🗳 {город}: {предмет} — {итог} (за {за}, против {против}, голосующих {ценз})."


async def _council_seated(event: Event, names: Names) -> str | None:
    город = await names.city(event.payload.get("city_id"))
    кто = plain(event.payload.get("who")) or await names.identity(event.actor_identity_id)
    return f"🪑 {город}: место в совете занимает {кто}."


async def _case_judged(event: Event, names: Names) -> str | None:
    город = await names.city(event.payload.get("city_id"))
    судья = await names.identity(event.actor_identity_id)
    приговор = plain(event.payload.get("verdict"))
    санкция = plain(event.payload.get("sanction"))
    строка = f"⚖️ Суд города {город}. Судья: {судья}."
    if приговор:
        строка += f" Приговор: «{приговор}»."
    строка += f" Санкция: {санкция}." if санкция else " Без санкции."
    return строка


async def _rate_decided(event: Event, names: Names) -> str | None:
    ставка = _число(event.payload.get("rate"))
    if ставка is None:
        return None
    было = _число(event.payload.get("was"))
    чей = ""
    if event.payload.get("by_council"):
        чей = f" (решение совета, {plain(event.payload.get('city'))})"
    хвост = f" (было {было})" if было is not None else ""
    почему = plain(event.payload.get("why"))
    строка = f"🏦 Ключевая ставка{чей}: **{ставка}**{хвост}."
    return f"{строка} {почему}" if почему else строка


async def _explore_found(event: Event, names: Names) -> str | None:
    """Находка разведки — без того, что найдено.

    Узел мир и так покажет на общей карте, а порода жилы остаётся разведчику.
    """
    кто = await names.identity(event.actor_identity_id)
    что = plain(event.payload.get("name")) or await names.node(event.node_id)
    откуда = plain(event.payload.get("from_node"))
    место = f" от узла {откуда}" if откуда else ""
    return f"🧭 Разведка{место}: карта приросла — {что}. Разведчик: {кто}."


Line = Callable[[Event, Names], Awaitable[str | None]]

#: Белый список. Всё, чего здесь нет, наружу не уходит.
LINES: dict[str, Line] = {
    EventKind.CITY_FOUNDED: _city_founded,
    EventKind.CITY_LAW_SET: _city_law_set,
    EventKind.CITY_CHARTER_SET: _city_charter_set,
    EventKind.VOTE_CLOSED: _vote_closed,
    EventKind.COUNCIL_SEATED: _council_seated,
    EventKind.CASE_JUDGED: _case_judged,
    EventKind.RATE_DECIDED: _rate_decided,
    EventKind.EXPLORE_FOUND: _explore_found,
}

PUBLIC = frozenset(str(kind) for kind in LINES)


async def compose(session: AsyncSession, events: Sequence[Event]) -> list[str]:
    """Строки хроники по событиям. Молчаливое событие просто пропускается."""
    имена = Names(session)
    готово: list[str] = []
    for event in events:
        пишет = LINES.get(str(event.kind))
        if пишет is None:
            continue
        строка = await пишет(event, имена)
        if строка:
            готово.append(строка)
    return готово
