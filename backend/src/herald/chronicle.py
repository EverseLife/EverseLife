"""What the world announces aloud.

The list of event kinds below is an **allowlist**: a new event is silent until
it is added here by hand. This is not a feed setting but a privacy decision,
and it is made by one rule: only what is public **inside** the game goes out.

Public in the game: a plot's owner is visible to anyone who enters (D-178),
the map is open (`/public/map`), the charter and code-laws lie in
`/public/laws`, court and elections happen before witnesses, the key rate is
announced together with an explanation (D-030).

Not public and will never get here: money and accounts, contents of pockets
and chests, conversations and circles (D-043), **the species of a found
vein**. The last is no trifle: a find is the scout's pay for risk and time,
and announcing it to the whole world would take that pay away. So the
exploration line has the node, which the world will not hide anyway, and not
what was found in it.

Lines are written without gendered past-tense verbs: no gender stands behind
an identity's name, and there is no reason to guess it with a chronicle line.
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

#: Discord markup characters. City and plot names are made up by players, and
#: an asterisk in a name must not turn half the chronicle into italics.
MARKDOWN = "\\*_~`|>#@"

UNKNOWN = "неизвестно кто"
NOWHERE = "где-то"


def plain(text: object) -> str:
    """Somebody's text in a chronicle line: without markup and without a screen-long length."""
    line = str(text or "").strip()[:HERALD_TEXT_LIMIT]
    return "".join("\\" + sign if sign in MARKDOWN else sign for sign in line)


def _number(value: object) -> str | None:
    """A number from the payload. Not a number -- not a chronicle line but silence."""
    try:
        return f"{float(value):g}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class Names:
    """Names by identifiers, with memory for one pass.

    A pass processes dozens of events, and half of them are about the same
    city. The cache lives exactly one pass: a plot's name changes (D-178), and
    there is no reason for this memory to outlive the pass.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._remembers: dict[str, str] = {}

    async def identity(self, ident: uuid.UUID | None) -> str:
        if ident is None:
            return UNKNOWN
        key = f"identity:{ident}"
        if key not in self._remembers:
            who = await self._db.get(Identity, ident)
            self._remembers[key] = plain(who.name) if who else UNKNOWN
        return self._remembers[key]

    async def node(self, node_id: uuid.UUID | None) -> str:
        if node_id is None:
            return NOWHERE
        key = f"node:{node_id}"
        if key not in self._remembers:
            node_row = await self._db.get(Node, node_id)
            self._remembers[key] = plain(node_row.name) if node_row else NOWHERE
        return self._remembers[key]

    async def city(self, city_id: object) -> str:
        if not city_id:
            return NOWHERE
        key = f"city:{city_id}"
        if key not in self._remembers:
            try:
                city_row = await self._db.get(City, uuid.UUID(str(city_id)))
            except ValueError:
                city_row = None
            self._remembers[key] = plain(city_row.name) if city_row else NOWHERE
        return self._remembers[key]

    async def vote(self, vote_id: object) -> Vote | None:
        if not vote_id:
            return None
        try:
            return await self._db.get(Vote, uuid.UUID(str(vote_id)))
        except ValueError:
            return None


#: What the subject of a vote is called in the feed. One machine, different subjects.
POLLS = {
    VoteKind.LAW: "голосование по закону",
    VoteKind.ELECTION: "выборы правителя",
    VoteKind.RECALL: "отзыв правителя",
    VoteKind.CHARTER: "правка устава",
    VoteKind.COUNCIL: "выборы в совет",
}


async def _city_founded(event: Event, names: Names) -> str | None:
    name = plain(event.payload.get("name")) or await names.city(event.payload.get("city_id"))
    where = await names.node(event.node_id)
    who = await names.identity(event.actor_identity_id)
    return f"🏛 **Основан город {name}** — {where}. Кто основал: {who}."


async def _city_law_set(event: Event, names: Names) -> str | None:
    city_row = await names.city(event.payload.get("city_id"))
    law = plain(event.payload.get("law")) or "закон"
    before = plain(event.payload.get("was"))
    after = plain(event.payload.get("now"))
    return f"📐 {city_row}: код-закон «{law}» — было {before or '—'}, стало {after or '—'}."


async def _city_charter_set(event: Event, names: Names) -> str | None:
    city_row = await names.city(event.payload.get("city_id"))
    question = plain(event.payload.get("question")) or "вопрос устава"
    choice = plain(event.payload.get("option")) or "—"
    return f"📜 {city_row}: устав — «{question}» теперь «{choice}»."


async def _vote_closed(event: Event, names: Names) -> str | None:
    city_row = await names.city(event.payload.get("city_id"))
    poll = await names.vote(event.payload.get("vote_id"))
    subject = "голосование" if poll is None else POLLS.get(
        poll.kind, "голосование"
    )
    result = "прошло" if event.payload.get("passed") else "не прошло"
    pro = plain(event.payload.get("yes"))
    contra = plain(event.payload.get("no"))
    census = plain(event.payload.get("electorate"))
    return f"🗳 {city_row}: {subject} — {result} (за {pro}, против {contra}, голосующих {census})."


async def _council_seated(event: Event, names: Names) -> str | None:
    city_row = await names.city(event.payload.get("city_id"))
    who = plain(event.payload.get("who")) or await names.identity(event.actor_identity_id)
    return f"🪑 {city_row}: место в совете занимает {who}."


async def _case_judged(event: Event, names: Names) -> str | None:
    city_row = await names.city(event.payload.get("city_id"))
    judge = await names.identity(event.actor_identity_id)
    verdict = plain(event.payload.get("verdict"))
    sanction = plain(event.payload.get("sanction"))
    line = f"⚖️ Суд города {city_row}. Судья: {judge}."
    if verdict:
        line += f" Приговор: «{verdict}»."
    line += f" Санкция: {sanction}." if sanction else " Без санкции."
    return line


async def _rate_decided(event: Event, names: Names) -> str | None:
    rate = _number(event.payload.get("rate"))
    if rate is None:
        return None
    before = _number(event.payload.get("was"))
    whose = ""
    if event.payload.get("by_council"):
        whose = f" (решение совета, {plain(event.payload.get('city'))})"
    tail = f" (было {before})" if before is not None else ""
    reason = plain(event.payload.get("why"))
    line = f"🏦 Ключевая ставка{whose}: **{rate}**{tail}."
    return f"{line} {reason}" if reason else line


async def _explore_found(event: Event, names: Names) -> str | None:
    """An exploration find -- without what was found.

    The world will show the node on the common map anyway, and the vein's
    species stays with the scout.
    """
    who = await names.identity(event.actor_identity_id)
    what = plain(event.payload.get("name")) or await names.node(event.node_id)
    origin = plain(event.payload.get("from_node"))
    place = f" от узла {origin}" if origin else ""
    return f"🧭 Разведка{place}: карта приросла — {what}. Разведчик: {who}."


Line = Callable[[Event, Names], Awaitable[str | None]]

#: The allowlist. Everything not here does not go out.
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
    """Chronicle lines by events. A silent event is simply skipped."""
    names = Names(session)
    ready_: list[str] = []
    for event in events:
        writes = LINES.get(str(event.kind))
        if writes is None:
            continue
        line = await writes(event, names)
        if line:
            ready_.append(line)
    return ready_
