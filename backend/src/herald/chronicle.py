# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

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

## Why the words are in the locale and the channel is still Russian (D-251)

The chronicle goes to one Discord channel with one audience, so unlike a
refusal it is not said to each reader in their own language: it is rendered
once, in the world's default one. That is a property of the channel, not of
the chronicle -- and it is the reason the lines live in `locales/*/chronicle`
like everything else rather than in the f-strings they used to be. A second
channel in a second language needs a `locale=` here and nothing more; leaving
the sentences in Python would have needed the whole file written twice.

So a line names itself (`Says`) and `compose` says it. That also removes the
one place where a builder could quietly go missing: the rate line read
`payload["why"]`, the bank had moved to `why_said` (wave IV), and the
explanation had silently stopped coming out.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.engine.errors import Says
from src.models.city import City
from src.models.event import Event, EventKind
from src.models.identity import Identity
from src.models.vote import Vote, VoteKind
from src.models.world import Node
from src.runtime import HERALD_TEXT_LIMIT

#: Discord markup characters. City and plot names are made up by players, and
#: an asterisk in a name must not turn half the chronicle into italics.
MARKDOWN = "\\*_~`|>#@"

#: Nobody, and nowhere: what stands where a name would. They are words like
#: the rest of the line and live in the locale like the rest of it -- said
#: once, in the channel's language, by `_stands_for`. Written in Python they
#: were the two words that would have stayed Russian in an English channel,
#: which is exactly what the note at the top of this file promises they will
#: not be.
UNKNOWN = "chronicle-someone"
NOWHERE = "chronicle-somewhere"


def _stands_for(key: str) -> str:
    """The word that stands in for a name that is not there."""
    return i18n.render(key, locale=i18n.DEFAULT_LOCALE)


#: An empty value in a line that expects one. The dash belongs to the sentence
#: rather than to the data, so it goes in with the sentence.
NOTHING = "—"


def plain(text: object) -> str:
    """Somebody's text in a chronicle line: without markup and without a screen-long length.

    `None` is nothing; everything else is itself. Written the long way round
    because `str(text or "")` turned a **zero** into an empty string, and a
    vote with no votes for it announced «(за , против 9…)».
    """
    line = ("" if text is None else str(text)).strip()[:HERALD_TEXT_LIMIT]
    return "".join("\\" + sign if sign in MARKDOWN else sign for sign in line)


def _safe(value: object) -> object:
    """A stored argument, escaped if it is text and left alone if it is a number.

    A number must stay a number: the locale formats it, and `plain()` would
    hand Fluent the string `"12.0"` -- which renders, and renders wrong in the
    next language along.
    """
    return value if isinstance(value, int | float) and not isinstance(value, bool) else plain(value)


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
            return _stands_for(UNKNOWN)
        key = f"identity:{ident}"
        if key not in self._remembers:
            who = await self._db.get(Identity, ident)
            self._remembers[key] = plain(who.name) if who else _stands_for(UNKNOWN)
        return self._remembers[key]

    async def node(self, node_id: uuid.UUID | None) -> str:
        if node_id is None:
            return _stands_for(NOWHERE)
        key = f"node:{node_id}"
        if key not in self._remembers:
            node_row = await self._db.get(Node, node_id)
            self._remembers[key] = plain(node_row.name) if node_row else _stands_for(NOWHERE)
        return self._remembers[key]

    async def city(self, city_id: object) -> str:
        if not city_id:
            return _stands_for(NOWHERE)
        key = f"city:{city_id}"
        if key not in self._remembers:
            try:
                city_row = await self._db.get(City, uuid.UUID(str(city_id)))
            except ValueError:
                city_row = None
            self._remembers[key] = plain(city_row.name) if city_row else _stands_for(NOWHERE)
        return self._remembers[key]

    async def vote(self, vote_id: object) -> Vote | None:
        if not vote_id:
            return None
        try:
            return await self._db.get(Vote, uuid.UUID(str(vote_id)))
        except ValueError:
            return None


#: What the subject of a vote is called in the feed. One machine, different
#: subjects. The kind is an enum and the word for it is the locale's, so the
#: message selects on the value: `chronicle-vote-closed`.
POLLS = frozenset(kind.value for kind in VoteKind)


def _flag(value: object) -> str:
    """A Fluent variant key is an identifier, never a boolean: `"true"`/`"false"`."""
    return "true" if value else "false"


async def _city_founded(event: Event, names: Names) -> Says | None:
    name = plain(event.payload.get("name")) or await names.city(event.payload.get("city_id"))
    return Says(
        "chronicle-city-founded",
        {
            "city": name,
            "where": await names.node(event.node_id),
            "who": await names.identity(event.actor_identity_id),
        },
    )


async def _city_law_set(event: Event, names: Names) -> Says | None:
    return Says(
        "chronicle-city-law-set",
        {
            "city": await names.city(event.payload.get("city_id")),
            #: The law travels as its D-251 id and the message turns it into a
            #: word (`LAW()`). It used to be printed raw, so the line read
            #: «код-закон «tax_trade»». A payload without one is still a
            #: sentence: the line then says that a law moved, without naming it.
            #:
            #: Unescaped, unlike the text around it: this is a **key** on its
            #: way to a lookup, and `plain()` would turn `tax_trade` into
            #: `tax\_trade`, which no table has. Nothing of a player's writing
            #: reaches here -- `set_law` records only ids the catalog knows.
            "named": _flag(event.payload.get("law")),
            "law": str(event.payload.get("law") or ""),
            #: Both values are the rule **in force** on either side of the
            #: change (`city.shown`), not the city's own column: a law nobody
            #: had touched read «было —» while the world was charging the
            #: vault's default all along.
            "was": plain(event.payload.get("was")) or NOTHING,
            "now": plain(event.payload.get("now")) or NOTHING,
        },
    )


async def _city_charter_set(event: Event, names: Names) -> Says | None:
    return Says(
        "chronicle-city-charter-set",
        {
            "city": await names.city(event.payload.get("city_id")),
            "named": _flag(plain(event.payload.get("question"))),
            "question": plain(event.payload.get("question")),
            "choice": plain(event.payload.get("option")) or NOTHING,
        },
    )


async def _vote_closed(event: Event, names: Names) -> Says | None:
    poll = await names.vote(event.payload.get("vote_id"))
    #: A vote whose row is gone is still worth announcing: the count is in the
    #: event. `unknown` is a variant of its own rather than a missing one, so
    #: the sentence stays a sentence.
    kind = poll.kind if poll is not None and str(poll.kind) in POLLS else "unknown"
    return Says(
        "chronicle-vote-closed",
        {
            "city": await names.city(event.payload.get("city_id")),
            "kind": str(kind),
            "passed": _flag(event.payload.get("passed")),
            "yes": plain(event.payload.get("yes")),
            "no": plain(event.payload.get("no")),
            "electorate": plain(event.payload.get("electorate")),
        },
    )


async def _council_seated(event: Event, names: Names) -> Says | None:
    who = plain(event.payload.get("who")) or await names.identity(event.actor_identity_id)
    return Says(
        "chronicle-council-seated",
        {"city": await names.city(event.payload.get("city_id")), "who": who},
    )


async def _case_judged(event: Event, names: Names) -> Says | None:
    return Says(
        "chronicle-case-judged",
        {
            "city": await names.city(event.payload.get("city_id")),
            "judge": await names.identity(event.actor_identity_id),
            #: Two clauses that may or may not be there. Flags rather than
            #: three keys: it is one sentence, and which half of it is said is
            #: the sentence's own business.
            "sentenced": _flag(plain(event.payload.get("verdict"))),
            "verdict": plain(event.payload.get("verdict")),
            "sanctioned": _flag(plain(event.payload.get("sanction"))),
            "sanction": plain(event.payload.get("sanction")),
        },
    )


async def _rate_decided(event: Event, names: Names) -> Says | None:
    """The key rate, and only when it moved.

    The rate is reviewed on a period of its own (D-167) and most reviews change
    nothing: the sensors read the same inflation and the algorithm returns the
    same number. Announced anyway, those filled the chronicle with a line that
    said "the rate is twelve, and it was twelve" over and over -- the journal
    keeps every review, and the chronicle is for what is worth telling.

    A decision of a council always goes out, changed or not: people took it,
    and that is news even when the number stayed (D-172).
    """
    rate = _number(event.payload.get("rate"))
    if rate is None:
        return None
    before = _number(event.payload.get("was"))
    if not event.payload.get("by_council") and before is not None and rate == before:
        return None
    #: The explanation is stored as keys and said here (D-251 wave IV). The
    #: old text field is read as a fallback for events written before that:
    #: an announcement with no reason in it is worse than an old one.
    #:
    #: Escaped **before** rendering, not after: one of the clauses names the
    #: city whose council decided (`bank-why-council`), and a city is named by
    #: a player -- a hull called `**@everyone**` would go into the channel as
    #: a ping. Escaping the finished phrase instead would eat the locale's own
    #: punctuation, so it is the arguments that go through `plain()`.
    reason = i18n.join(
        [
            Says(
                str(row["say"]), {name: _safe(one) for name, one in (row.get("args") or {}).items()}
            )
            for row in event.payload.get("why_said") or []
        ],
        locale=i18n.DEFAULT_LOCALE,
    ) or plain(event.payload.get("why"))
    return Says(
        "chronicle-rate-decided",
        {
            "rate": rate,
            "by_council": _flag(event.payload.get("by_council")),
            "city": plain(event.payload.get("city")),
            "known": _flag(before is not None),
            "was": before or "",
            "explained": _flag(reason),
            "why": reason,
        },
    )


async def _explore_found(event: Event, names: Names) -> Says | None:
    """An exploration find -- without what was found.

    The world will show the node on the common map anyway, and the vein's
    species stays with the scout.
    """
    origin = plain(event.payload.get("from_node"))
    return Says(
        "chronicle-explore-found",
        {
            "what": plain(event.payload.get("name")) or await names.node(event.node_id),
            "who": await names.identity(event.actor_identity_id),
            "from_known": _flag(origin),
            "from_node": origin,
        },
    )


Line = Callable[[Event, Names], Awaitable[Says | None]]

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
    """Chronicle lines by events. A silent event is simply skipped.

    The one place the words appear. Every builder above names its line and
    hands over the values; here it is said, in the channel's language -- see
    the note at the top of the file on why that is one language and not the
    reader's.
    """
    names = Names(session)
    ready_: list[str] = []
    for event in events:
        writes = LINES.get(str(event.kind))
        if writes is None:
            continue
        said = await writes(event, names)
        if said is not None:
            ready_.append(i18n.render(said.key, said.params, locale=i18n.DEFAULT_LOCALE))
    return ready_
