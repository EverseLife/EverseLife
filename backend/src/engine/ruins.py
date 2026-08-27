# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The Forerunners' ruins: rooms opened inside a city, cities found beyond the
ice (D-232).

Aurora is the one place where exploring **reveals what is already there**
rather than creating what was not. Everywhere else a scout finds a clearing, a
vein, a lot -- places the world did not have until somebody walked to them.
Here they open a door: the city stood before anybody came, and the map merely
catches up with it.

## Inside a city

The goal is «помещение». From any opened node of a city the next room is
revealed, and it comes complete at once:

* **a type** -- архив, цех, квартира, склад, тоннель -- rolled by what the city
  **was**: Merid was a capital and keeps archives, Caldar was a foundry and
  keeps shops, Veyr was a hive and keeps flats (`ruins.room_types`);
* **a depth** -- steps from the spaceport. Deeper is richer (D-061), and the
  only way deeper is through what is already open;
* **contents**, rolled at the moment of opening and lying in the room from then
  on. A room is not a promise of a find later; it is the find.

**A city is finite.** It holds `ruins.city_rooms` rooms, and the more of them
are open the oftener a search comes back with nothing. When the stock is out
the city is **worked out**, exactly as a vein is worked out: it keeps
everything already opened in it and gives nothing new. What ends is the
finding, not the place -- the map is eternal (D-007), and the next city is
found by walking out onto the ice.

## Beyond the ice

The goal is «место», the same one that looks for city ground on Terra -- on
Aurora it finds **another city of the Forerunners**, generated from the planet
and the number, so the same world always finds the same city in the same place.

It comes **frozen**: its reactor died long before anybody arrived, its beacon is
dark, and no ship can land on it (`ship.beacon_lit`). The only way in is on
foot, across the snow, with a heat reserve, warmers and a brazier -- and the
walk gets longer the further one goes from what is already settled. That walk
is the brake on colonising the planet, and it is meant to be felt: reviving a
city means carrying energy into it.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current, current_catalog
from src.constants import registry as R
from src.engine import energy, luck, travel, world
from src.engine.errors import Refusal
from src.models.world import Layer, Node, Planet, Surface
from src.units import HOURS_PER_DAY

#: The mark of the Forerunners on everything they left: the node was theirs.
PRECURSOR = "предтечи"
#: What the city was -- «столица», «цех», «улей». The word is the key of the
#: room table in the vault: what a place was decides what its rooms hold.
KIND = "город"
#: Steps from the spaceport. The pier is nought, and the way deeper goes
#: through what is already open (D-061).
DEPTH = "глубина"
#: How many rooms of this city are already open. Lives on the city's node, like
#: the count of finds on an ordinary one: a city is worked out for everybody at
#: once, not for whoever opened it.
OPENED = "раскрыто"
#: What kind of room this is. Sent to the client as a place property.
ROOM_MARK = "помещение"

#: The search goal. A string, like the others (`explore.GOALS`).
ROOM = "room"

#: Thing classes of the Forerunners, by class (D-215): the seed and the ruins
#: place the same relics, and neither of them names a thing.
RELIC_YARD = "Верфь"
RELIC_PLANT = "ТЭЦ"


class RuinsError(Refusal):
    pass


class NotRuins(RuinsError):
    """Not a city of the Forerunners: there are no rooms to open here."""


# --- what is whose ------------------------------------------------------------


def is_precursor(node: Node) -> bool:
    """Whether this node is the Forerunners': theirs to open, never to build on."""
    return bool((node.properties or {}).get(PRECURSOR))


def depth_of(node: Node) -> int:
    """Steps from the spaceport of the city. The pier itself is nought."""
    return int((node.properties or {}).get(DEPTH, 0))


def opened(city: Node) -> int:
    return int((city.properties or {}).get(OPENED, 0))


async def city_of(session: AsyncSession, node: Node, *, lock: bool = False) -> Node | None:
    """The Forerunner city this node belongs to, if it belongs to one.

    Membership is the `parent` hierarchy, the same one a city of Terra has over
    its locations (D-097): there is no second way to say what belongs where.

    `lock` takes the city's row for the transaction. Whoever **opens** a room
    asks for it: the count of what is open is a remainder of the city like ore
    in a vein (CLAUDE.md), and two scouts returning in the same second would
    otherwise both write the same number and one of the two rooms would be free.
    """
    if node.parent_id is None:
        return None
    city = await session.get(Node, node.parent_id, with_for_update=lock)
    if city is None or city.layer is not Layer.PLANET or not is_precursor(city):
        return None
    return city


async def left_by_precursors(session: AsyncSession, node: Node) -> bool:
    """Whether this node's **planet** is one the Forerunners left cities on.

    A mark on the planet's node, like its climate (D-231): what a planet holds
    is a fact of the world and not a name in the engine. On such a planet the
    search for city ground finds a city that already stands instead of an empty
    place to found one.
    """
    root = (
        await session.execute(select(Node).where(Node.key == node.planet.value))
    ).scalar_one_or_none()
    return root is not None and is_precursor(root)


def worked_out(constants: Constants, city: Node) -> float:
    """Chance multiplier for how much of the city is already open (D-232).

    A city holds `ruins.city_rooms` rooms and no more. Every one opened makes
    the next search worse, and when the stock is out the multiplier is nought:
    the city is **worked out**, exactly as a vein is worked out. It stays on
    the map with everything already opened in it -- what ends is the finding,
    not the place.

    Without this the city would be a faucet that speeds up as it runs: every
    room opened is a fresh node to search from, and every step deeper makes the
    next haul richer (`ruins.depth_bonus`). The stock is what makes "worked out
    like a vein" true in the code and not only in this docstring.
    """
    stock = constants[R.RUINS_CITY_ROOMS]
    if stock <= 0:  # pragma: no cover -- the vault always gives a city rooms
        return 1.0
    return max(0.0, 1 - opened(city) / stock)


def exhausted(constants: Constants, city: Node) -> bool:
    """Whether the city has nothing left to open."""
    return worked_out(constants, city) <= 0


# --- the relics ---------------------------------------------------------------


async def grant_relic(session: AsyncSession, node: Node, thing_class: str, *, origin: str) -> None:
    """Put a thing the Forerunners left into the node, with its provenance.

    Not assembled by recipe the way a city's machines are (D-216): nobody made
    these in this world, they were found. A relic has no recipe at all -- it
    lives in the registry of things that simply exist (D-215) -- so it can be
    neither built, nor taken down, nor carried away (D-232).
    """
    if await world.has_station(session, node, thing_class):
        return
    book = current_catalog().recipes
    relics = [name for name in book.of_class(thing_class) if book.is_relic(name)]
    if not relics:  # pragma: no cover -- the vault always names the relic
        raise RuinsError(f"в реестре нет реликвии класса «{thing_class}»")
    await world.grant_item(
        session,
        await world.node_container(session, node),
        relics[0],
        #: The middle of the scale: a thing found is neither new nor spent, and
        #: the number is the vault's, not this module's.
        quality=current()[R.QUALITY_SCALE].mid,
        origin=origin,
    )


# --- opening a room -----------------------------------------------------------


async def open_room(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    origin: Node,
    *,
    who: uuid.UUID | None = None,
) -> Node:
    """Reveal the next room of the city, with what is in it.

    Hung on the node it was opened from: one goes deeper through what is
    already open, and a corridor that appeared out of the pier would make the
    depth of a city meaningless.
    """
    city = await city_of(session, origin, lock=True)
    if city is None:  # pragma: no cover -- the caller refuses before the run
        raise NotRuins("здесь нечего вскрывать: это не город Предтеч")
    if exhausted(constants, city):
        raise NotRuins(f"«{city.name}» выработан: вскрывать больше нечего")

    kind = str((city.properties or {}).get(KIND) or "")
    types: dict[str, float] = constants[R.RUINS_ROOM_TYPES].get(kind) or _any_room(constants)
    #: The deck is per **kind of city** (D-213): the names of the rooms are the
    #: same everywhere, only the weights differ, and one shared deck would deal
    #: Merid's archives in Caldar's foundry for half a city.
    room_type = await luck.draw(session, who, f"{luck.RUINS_ROOM}:{kind}", types, dice=dice)
    depth = depth_of(origin) + 1

    area = constants[R.EXPLORE_NODE_AREA]
    room = await world.create_node(
        session,
        f"{city.key}.room.{uuid.uuid4().hex}",
        room_type.capitalize(),
        planet=city.planet,
        area_m2=dice.uniform(area.min, area.max),
        layer=Layer.CITY,
        parent=city,
        #: Next to the corridor it opened off, as it is joined to it: one goes
        #: deeper through what is already open, and the map says so (D-237).
        anchor=origin,
        properties={
            #: No `предтечи` here on purpose: the mark says "the Forerunners'
            #: **building**", and the engine reads it for the capital's core
            #: and its printer. A room belongs to its city by `parent`, and
            #: that is the only thing that decides what it is (D-097).
            ROOM_MARK: room_type,
            DEPTH: depth,
            #: The city ring the rest of the engine reads (D-089, D-220): a
            #: room deeper in is a room further out from the pier.
            "кольцо": depth,
            travel.REACH: travel.reach_of(origin),
        },
    )
    await _fill(session, constants, dice, room, room_type, depth, who=who)

    #: The city is one room poorer -- for everybody who searches it next.
    city.properties = {**(city.properties or {}), OPENED: opened(city) + 1}
    await session.flush()
    return room


def _any_room(constants: Constants) -> dict[str, float]:
    """Every kind of room there is, for a city whose own kind is unknown."""
    every: dict[str, float] = {}
    for by_kind in constants[R.RUINS_ROOM_TYPES].values():
        for name, weight in by_kind.items():
            every[name] = every.get(name, 0.0) + weight
    return every


async def _fill(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    room: Node,
    room_type: str,
    depth: int,
    *,
    who: uuid.UUID | None,
) -> None:
    """What lies in the room, rolled at the moment it is opened.

    Deeper is richer (D-061), and by one line rather than by a second table:
    the haul grows `ruins.depth_bonus` per step from the pier. What may lie in
    a room of this type is the vault's business (`ruins.room_finds`) -- the
    blueprints, catalysts and artefacts of the deep come with the digging
    content and will be added there, not here.
    """
    finds: dict[str, float] = constants[R.RUINS_ROOM_FINDS].get(room_type) or {}
    if not finds:
        return
    #: And per **kind of room**, for the same reason: a deck built in an archive
    #: must not go on dealing in a flat.
    goods = await luck.draw(session, who, f"{luck.RUINS_FIND}:{room_type}", finds, dice=dice)
    haul = constants[R.RUINS_ROOM_HAUL]
    amount = dice.uniform(haul.min, haul.max) * (1 + depth * constants[R.RUINS_DEPTH_BONUS])
    quality = constants[R.QUALITY_SCALE]
    await world.grant_item(
        session,
        await world.node_container(session, room),
        goods,
        amount=amount,
        quality=quality.mid,
        origin=f"наследие Предтеч: {room_type}, глубина {depth}",
    )


# --- a city beyond the ice ----------------------------------------------------


async def lost_city(
    session: AsyncSession,
    constants: Constants,
    origin: Node,
    *,
    who: uuid.UUID | None = None,
) -> Node:
    """Find another city of the Forerunners. Returns its **pier**: that is where
    a walker arrives, and the hall is one step further in.

    Generated from the planet and the number, so a world always finds the same
    city under the same number -- the map is eternal and must not be rerolled
    (D-007). It comes frozen: the reactor died `ruins.new_city_age` lifetimes
    ago, the beacon is dark, and only a walk gets anybody in.
    """
    #: The planet's own row is taken first: the number is counted and then
    #: written into a unique key, and two scouts returning in the same second
    #: would otherwise pick the same number -- one of them losing an already
    #: paid-for run to a collision.
    root = await _planet_root(session, origin, lock=True)
    number = await _lost_so_far(session, origin.planet) + 1
    #: **Everything** about the city comes off this seed and not off the
    #: scout's dice: the planet and the number decide what stands there, so the
    #: same world always finds the same city under the same number. The map is
    #: eternal (D-007), and a city that differed by who happened to find it
    #: would be a map rerolled.
    seed = random.Random(f"{origin.planet.value}:{number}")
    kinds = sorted(constants[R.RUINS_ROOM_TYPES])
    kind = seed.choice(kinds) if kinds else ""

    city = await world.create_node(
        session,
        f"{origin.planet.value}.lost.{number:03d}",
        f"{kind.capitalize()} Предтеч №{number}".strip(),
        planet=origin.planet,
        area_m2=1,
        layer=Layer.PLANET,
        parent=root,
        #: A find stands next to what it was found from, on the planet's map
        #: (D-206, D-237): the scout walked there from somewhere.
        anchor=origin,
        properties={
            PRECURSOR: True,
            KIND: kind,
            #: The frontier recedes by a step, as with any find (D-180): the
            #: further from what is settled, the longer the walk.
            travel.REACH: travel.reach_of(origin) + 1,
        },
    )
    area = constants[R.EXPLORE_NODE_AREA]
    port = await world.create_node(
        session,
        f"{city.key}.port",
        "Космодром",
        planet=origin.planet,
        area_m2=seed.uniform(area.min, area.max),
        layer=Layer.CITY,
        parent=city,
        properties={PRECURSOR: True, DEPTH: 0, "кольцо": 0, travel.REACH: travel.reach_of(city)},
    )
    hall = await world.create_node(
        session,
        f"{city.key}.hall",
        "Зал",
        planet=origin.planet,
        area_m2=seed.uniform(area.min, area.max),
        layer=Layer.CITY,
        parent=city,
        anchor=port,
        properties={
            PRECURSOR: True,
            DEPTH: 1,
            "кольцо": 1,
            travel.REACH: travel.reach_of(city),
            #: Long dead: the anchor is pushed back past the reactor's whole
            #: life, so its output is nought from the first minute anybody sees
            #: it. Nobody was waiting here.
            energy.REACTOR_SINCE: _long_dead(constants).isoformat(),
        },
    )
    step = constants[R.TRAVEL_CITY_STEP]
    await travel.connect(
        session,
        port,
        hall,
        base_seconds=seed.uniform(step.min, step.max),
        surface=Surface.PAVED,
    )
    await grant_relic(session, port, RELIC_YARD, origin=f"наследие Предтеч: {city.name}")
    await grant_relic(session, hall, RELIC_PLANT, origin=f"наследие Предтеч: {city.name}")
    await grant_relic(session, hall, energy.REACTOR, origin=f"наследие Предтеч: {city.name}")
    return port


def _long_dead(constants: Constants) -> datetime:
    """When the reactor of a found city was started: long enough ago to be silent."""
    lifetimes = constants[R.RUINS_NEW_CITY_AGE] * constants[R.REACTOR_LIFETIME]
    return datetime.now(UTC) - timedelta(hours=lifetimes * HOURS_PER_DAY)


async def _lost_so_far(session: AsyncSession, planet: Planet) -> int:
    """How many cities have been found on this planet already."""
    #: The cities themselves, not their piers and halls: those carry the same
    #: key prefix, and counting them would skip three numbers per find.
    found = await session.scalar(
        select(func.count())
        .select_from(Node)
        .where(
            Node.planet == planet.value,
            Node.layer == Layer.PLANET,
            Node.key.like(f"{planet.value}.lost.%"),
        )
    )
    return int(found or 0)


async def _planet_root(session: AsyncSession, node: Node, *, lock: bool = False) -> Node:
    """The planet's node on the space layer: everything found hangs under it."""
    stmt = select(Node).where(Node.key == node.planet.value)
    if lock:
        stmt = stmt.with_for_update()
    root = (await session.execute(stmt)).scalar_one_or_none()
    if root is None:  # pragma: no cover -- the seed lays every planet
        raise RuinsError(f"у планеты «{node.planet.value}» нет узла: миру нечего расширять")
    return root
