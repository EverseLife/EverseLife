# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Foraging: the empty land of a place gives up what lies on it (D-210).

The bare-hand gathering of D-196 was three place operations -- deadwood in a
forest, stones on stony ground, flax in a meadow -- each a button and a batch,
and in practice only the wood ever got gathered. Foraging replaces all of it
with one occupation that lives on any plot with room to walk:

- **empty land** is the plot minus the building footprint (the first floor:
  storeys above the ground take nothing from it) and minus the strips marked
  out of it (D-246): a bed is worked land, not land to walk over. Below
  `forage.min_area` there is nowhere to forage and no window;
- **what turns up is not chosen.** One starts a search; by the deadline the
  land shows a single random find -- a handful of one thing. Take it and the
  foraging ends there: searching again is the player's decision, not the
  engine's (D-211). Pass it and the search goes on -- that is what passing
  means. What is passed is gone: a find, not a store;
- **one table sets both pace and mix.** `forage.finds` is finds per hour per
  `forage.reference_area` of empty land, one number per thing. Their sum
  scaled by the empty area is the pace; a thing's share of the sum is what it
  is that turns up. More land -- faster, but never under
  `forage.search_floor`;
- **the searcher stands here and the land is theirs or nobody's** -- the same
  rule as felling (D-177). Walking away abandons the search and whatever it
  found. Every search costs stamina; with none, nobody searches.

## Why no worker

The find is decided at the start -- a roll seeded by the row's id, the same on
any retry -- and merely **revealed** when `ready_at` passes. Nothing in the
world changes until the player decides, so the search needs no job in the
journal: it is a row in `forage`, and its state is read off the clock.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.spec import ConstantError
from src.engine import estate, events, food, frost, gear, luck, occupation, travel, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.forage import Forage
from src.models.identity import Body, BodyState
from src.models.world import Node
from src.units import ROUND_MASS, ROUND_QUALITY, SECONDS_PER_HOUR

#: The event's ground for the matter that appears (pillar P1).
ORIGIN = "собирательство"


class ForageError(Refusal):
    pass


class NoRoom(ForageError):
    """Not enough empty land here to forage on."""


class NotYours(ForageError):
    """Somebody else's land: what lies on it is theirs."""


class AlreadySearching(ForageError):
    """A search is already going: one body walks one plot."""


class NothingFound(ForageError):
    """There is no find to decide about: the search is still going, or none is."""


class NoStrength(ForageError):
    """No stamina left for the search."""


# --- the plot -----------------------------------------------------------------


async def empty_area(session: AsyncSession, node: Node) -> float:
    """Empty land of the plot: what neither a house nor a bed stands on (D-210, D-246).

    The footprint, not the usable area: a two-storey house of ten metres takes
    ten from the yard, not twenty (D-125). Marked-out strips take their metres
    too: a bed is worked land, and there is nothing left to gather on it -- the
    plot's whole area used to answer here as long as no wall stood on it, so a
    garden cut out of the yard cost the foraging nothing at all.
    """
    return max(0.0, await estate.spare_ground(session, node))


def is_yours(node: Node, body: Body) -> bool:
    """Whether this body may forage here: own land or nobody's, as with felling (D-177).

    Civic land not bought out is the city's, and what lies on it is not free.
    """
    if node.owner_identity_id is not None:
        return node.owner_identity_id == body.identity_id
    return node.owner_city_id is None


def finds(constants: Constants) -> dict[str, float]:
    """What is found at all, with its pace: the vault's table (D-210).

    The handful table must name the same things: a find without a handful
    would be a find of nothing, and the mismatch is a data error, not a roll.
    """
    paces = {name: pace for name, pace in constants[R.FORAGE_FINDS].items() if pace > 0}
    handfuls = constants[R.FORAGE_HANDFUL]
    missing = sorted(set(paces) - set(handfuls))
    if missing:
        raise ConstantError("forage.handful не называет горсть для: " + ", ".join(missing))
    return paces


def pace(constants: Constants, area: float) -> float:
    """Finds per hour on this much empty land: the table's sum scaled by area."""
    return sum(finds(constants).values()) * area / constants[R.FORAGE_REFERENCE_AREA]


def search_seconds(constants: Constants, area: float, dice: random.Random) -> float:
    """How long one search takes on this much empty land.

    The mean is the inverse of the pace; the jitter keeps it from reading as
    a timer; the floor keeps a big yard from becoming a tap.
    """
    per_hour = pace(constants, area)
    if per_hour <= 0:
        raise NoRoom("здесь нечего искать: таблица находок пуста")
    jitter = constants[R.FORAGE_SEARCH_JITTER]
    mean = SECONDS_PER_HOUR / per_hour
    return max(constants[R.FORAGE_SEARCH_FLOOR], mean * dice.uniform(jitter.min, jitter.max))


async def _roll(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    dice: random.Random,
) -> tuple[str, int, float]:
    """What turns up: a thing dealt from the table's deck, its handful, its quality.

    **The thing is drawn, not tossed for** (D-213): the deck is built by the
    same `forage.finds` weights, and a card taken is not put back until the
    deck runs out. The proportions are the table's to the letter; what goes
    away is "ten stones in a row and not one stem of flax".

    Quality stays a plain roll -- triangular over `forage.quality` with the
    peak in the middle: what lies on the ground is mostly ordinary, and both a
    treasure and a piece of junk are rare. A magnitude has no droughts.
    """

    found = await luck.draw(
        session, body.identity_id, luck.FORAGE_WHAT, finds(constants), dice=dice
    )
    units = max(1, int(constants[R.FORAGE_HANDFUL][found]))
    grade = constants[R.FORAGE_QUALITY]
    quality = dice.triangular(grade.min, grade.max, grade.mid)
    return found, units, round(quality, ROUND_QUALITY)


# --- the search ---------------------------------------------------------------


async def current(session: AsyncSession, body: Body) -> Forage | None:
    """This body's search, if it is still about the place the body stands in.

    A search left behind -- the body walked away -- is abandoned here, on
    the first look, together with whatever it found: what was not picked up
    stayed on the plot, and the plot is behind.
    """
    row = (
        await session.execute(select(Forage).where(Forage.body_id == body.id))
    ).scalar_one_or_none()
    if row is not None and row.node_id != body.node_id:
        await session.delete(row)
        await session.flush()
        return None
    return row


async def start(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    now: datetime | None = None,
) -> Forage:
    """Begin searching the plot the body stands on. The find shows by the deadline.

    Refused where there is no room, on somebody else's land, when a search is
    already going and when the body has no strength for it. Every search costs
    `forage.search_stamina` up front, found or passed: more land means more
    finds an hour and more stamina an hour -- area buys time, not free things.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise ForageError("мёртвое тело ничего не собирает")
    await travel.require_here(session, body)
    if await current(session, body) is not None:
        raise AlreadySearching("поиск уже идёт: дождитесь находки или закончите")
    #: A search is an occupation (D-211): one does not walk the plot while a
    #: batch of one's own runs at a bench or a plot lies under the plough.

    await occupation.require_free(session, body, besides=frozenset({occupation.FORAGE}))

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        raise ForageError("тело стоит в никуда")
    if not is_yours(node, body):
        raise NotYours("чужая земля: что на ней лежит, принадлежит хозяину")
    room = await empty_area(session, node)
    if room < constants[R.FORAGE_MIN_AREA]:
        raise NoRoom(
            f"пустой земли {room:.0f} м², а собирать есть где от {constants[R.FORAGE_MIN_AREA]:.0f}"
        )

    #: Seeded by the row's id: what this search finds is settled now and does
    #: not change on a re-read.
    row_id = uuid.uuid4()
    dice = random.Random(str(row_id))
    seconds = search_seconds(constants, room, dice)

    spend = (
        constants[R.FORAGE_SEARCH_STAMINA]
        * food.drain_multiplier(constants, body, moment)
        * await frost.drain_multiplier(session, constants, body)
    )
    if spend > float(body.stamina):
        raise NoStrength(f"нет сил на поиск: нужно {spend:.2f}, есть {float(body.stamina):.2f}")
    body.stamina = Decimal(str(float(body.stamina) - spend))

    found, units, quality = await _roll(session, constants, body, dice)
    row = Forage(
        id=row_id,
        body_id=body.id,
        node_id=node.id,
        started_at=moment,
        ready_at=moment + timedelta(seconds=seconds),
        found=found,
        units=units,
        quality=Decimal(str(quality)),
    )
    session.add(row)
    await session.flush()

    await events.record(
        session,
        EventKind.FORAGE_STARTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        area=room,
        seconds=seconds,
        stamina=spend,
        ready_at=row.ready_at.isoformat(),
    )
    return row


def revealed(row: Forage, now: datetime | None = None) -> bool:
    """Whether the find is showing: the deadline has passed."""
    return (now or datetime.now(UTC)) >= row.ready_at


async def _offer(session: AsyncSession, body: Body, now: datetime) -> Forage:
    """The find waiting for a decision -- or the refusal saying why there is none."""
    await travel.require_here(session, body)
    row = await current(session, body)
    if row is None:
        raise NothingFound("поиск не идёт: сначала начать")
    if not revealed(row, now):
        raise NothingFound(f"ещё ищете: находка покажется в {row.ready_at.isoformat()}")
    return row


async def take(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    now: datetime | None = None,
) -> None:
    """Pick the find up: the handful goes into the hands, the foraging ends.

    Refused when it does not fit in the hands (`gear.Overloaded`) -- and then
    the find keeps lying and waiting: put something down and try again.

    **The next search does not start by itself** (D-211, amending D-210): the
    find is taken, and whether to walk the plot again is the player's decision,
    made by the same button that started the first search. A search restarting
    on its own spent stamina nobody asked for it to spend, and turned a walk
    over the land into a conveyor that had to be stopped rather than started.
    Passing a find is a different matter: "search on" is what that answer
    means, and there `pass_` goes on searching.
    """
    moment = now or datetime.now(UTC)
    row = await _offer(session, body, moment)
    await gear.check_carry(session, constants, catalog, body, row.found, row.units)

    pocket = await world.body_container(session, body)
    await world.grant_item(
        session,
        pocket,
        row.found,
        amount=row.units,
        quality=float(row.quality),
        origin=ORIGIN,
    )
    await events.record(
        session,
        EventKind.FORAGE_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=row.node_id,
        found=row.found,
        units=row.units,
        quality=float(row.quality),
    )
    await session.delete(row)
    await session.flush()


async def pass_(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    now: datetime | None = None,
) -> Forage:
    """Leave the find lying and search on. What is passed is gone.

    Passing means "search on", so the next search's refusal -- no strength left
    -- is this command's refusal: the transaction rolls back and the find stays
    on offer, to be taken or ended. Unlike `take`, nothing was gained to keep.
    """
    moment = now or datetime.now(UTC)
    row = await _offer(session, body, moment)
    await events.record(
        session,
        EventKind.FORAGE_PASSED,
        actor_identity_id=body.identity_id,
        node_id=row.node_id,
        found=row.found,
        units=row.units,
    )
    await session.delete(row)
    await session.flush()
    return await start(session, constants, body, now=moment)


async def stop(session: AsyncSession, body: Body) -> None:
    """End the foraging: the search under way, or the find on offer, is dropped.

    Spent stamina does not come back -- the walking is walked. Stopping does
    not need presence: a body that walked off cannot come back to stop, and
    the row it left behind is already abandoned by `current`.
    """
    row = await current(session, body)
    if row is None:
        raise NothingFound("собирательство не идёт: заканчивать нечего")
    await events.record(
        session,
        EventKind.FORAGE_STOPPED,
        actor_identity_id=body.identity_id,
        node_id=row.node_id,
        found=row.found if revealed(row) else None,
    )
    await session.execute(delete(Forage).where(Forage.id == row.id))
    await session.flush()


# --- what the player sees ---------------------------------------------------------


async def view(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    node: Node,
    *,
    now: datetime | None = None,
) -> dict | None:
    """The foraging window's contents, or nothing when there is no window.

    No window where the land is somebody else's or too built up -- unless a
    search is already going here: what it found must still be decidable.
    The find is named only once the deadline has passed; before that the
    client sees a search and its term.
    """
    moment = now or datetime.now(UTC)
    row = await current(session, body)
    room = await empty_area(session, node)
    least = constants[R.FORAGE_MIN_AREA]
    allowed = is_yours(node, body) and room >= least
    if not allowed and row is None:
        return None

    table = finds(constants)
    total = sum(table.values()) or 1.0
    per_hour = pace(constants, room)
    seen: dict = {
        "area": room,
        "min_area": least,
        "allowed": allowed,
        #: The mean length of one search here, seconds: what "more land is
        #: faster" means in numbers.
        "seconds": (
            max(constants[R.FORAGE_SEARCH_FLOOR], SECONDS_PER_HOUR / per_hour)
            if per_hour > 0
            else None
        ),
        "stamina": constants[R.FORAGE_SEARCH_STAMINA],
        #: What is found here at all and how often, in shares: the player must
        #: know what the land can give before spending an hour on it.
        "finds": [
            {
                "goods": name,
                "share": table[name] / total,
                "units": int(constants[R.FORAGE_HANDFUL][name]),
            }
            for name in sorted(table, key=lambda name: -table[name])
        ],
        "state": "idle",
        "started_at": None,
        "ready_at": None,
        "found": None,
    }
    if row is None:
        return seen
    seen["started_at"] = row.started_at.isoformat()
    seen["ready_at"] = row.ready_at.isoformat()
    if not revealed(row, moment):
        seen["state"] = "searching"
        return seen
    seen["state"] = "found"
    seen["found"] = {
        "goods": row.found,
        "units": row.units,
        "quality": float(row.quality),
        "mass": round(gear.mass_of(catalog, row.found, row.units), ROUND_MASS),
    }
    return seen
