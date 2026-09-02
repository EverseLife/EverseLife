# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Founding a city: what a place must already be before it can become one.

A city is not declared, it is **recognised**. The engine checks that the four
roles a settlement cannot live without are filled by real machines on the node
(D-023, D-159), and only then writes the city down. That check is the whole of
this module, and it is why founding is separate from everything a city does
afterwards: the conditions are read from the vault and from the ground, not
from the city -- there is no city yet to ask.

The founding moment itself is here and not among the offices, though it makes
one: `install_founder` gives the founder both a post and a citizenship, and
those are two things rather than one. It borrows the primitive for each --
`office._office`, `citizen._enrol_founder` -- which is why nothing in the
package imports this module: it is the last thing built on the rest. Leaving
that function among
the offices is what made the four sections a cycle: offices reached into
citizenship to finish a founding they should not have been running.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import death, energy, events, market, props, travel, world
from src.engine.city._base import (
    FOUNDER_POWERS,
    FOUNDER_TITLE,
    HALL,
    CityError,
    NotReady,
    NotYours,
)
from src.engine.city.citizen import AlreadyCitizen, _enrol_founder, citizenship
from src.engine.city.land import _retire_deed
from src.engine.city.lookup import by_name, by_node, territory
from src.engine.city.office import _office
from src.engine.errors import Says
from src.engine.world import station_names
from src.models.city import (
    City,
    Office,
)
from src.models.event import EventKind
from src.models.identity import BodyState, Identity
from src.models.net import NetChannel
from src.models.world import Layer, Node
from src.runtime import CITY_NAME_LIMIT

log = logging.getLogger(__name__)


async def found(
    session: AsyncSession,
    catalog: Catalog,
    node: Node,
    name: str,
    founder: Identity | None = None,
) -> City:
    """Found a city on a delegate node. Repeated -- return the existing one.

    The charter is filled with `laws.json` defaults: the city arises working,
    not as an empty questionnaire of forty questions (D-130).

    **The name is taken as given, and nothing measures it here.** Two
    doors lead in and they are bounded in different places. `establish` is the
    player's, and it measures what was typed. This one is the seed's: the
    capital and the delegate cities are founded from node names written in the
    vault (`seed.py`, `seed_catchup.py`), and a vault name is content, not a
    typed one -- so it is bounded where content is checked, by
    `WORLD_CITY_NAME_LIMIT` in the vault's `tools/world.py`, which complains
    about a `city: true` node named longer. That is the flag
    `seed_world.city_nodes` selects on, and so the node every seed call arrives
    with; the catch-up's capital reaches it as `core.parent_id`, the same
    flagged node.

    What hangs on the bound is not the city card. `_open_channel` below gives
    the city its official channel named after it, straight from the model --
    past the ceiling `net.channel.create` applies to what a player types. So an
    unmeasured name here makes a channel no player could have created, and
    nothing along the way says so.

    Two things this does **not** promise. It is not "no city has an over-long
    name": nothing measures the `name` argument itself, a caller may pass
    anything, `City.name` is copied once at founding and never follows the node
    afterwards, and a world laid before the ceiling keeps the names it has
    (`seed_world.lay` leaves a standing node alone). And the vault's complaint
    only stops something where it is read as a failure -- the `build.py
    --check` step of the vault's own CI. A plain `tools/build.py`, which is how
    both `deploy/sync-vault.py` and this repository's vault action call it,
    prints the complaint and writes `world.json` regardless.

    What is promised is narrower and is pinned: the layout that actually
    arrives here carries city names within the ceiling, and no two of them
    alike -- both measured over `load_scenario()` in `test_seed_world`. The
    vault keeps its own copies of those rules because neither repository can
    import the other -- its build reads `data/`, and CI copies only
    `build/*.json` back -- but both can see the layout, so that is what the
    tests measure.

    The one thing the name does decide here is whether the city gets its
    channel. `_open_channel` below opens it under the city's name, and one
    name is one channel (D-284); if a player's channel already holds it, the
    city is founded **without** its official channel and the clash is logged.
    It is not refused, and deliberately so: this door is the seed's, the whole
    deploy waits on the seed container, and a refusal would hand any player a
    way to stop the next deploy. That trade is OQ-118.
    """
    existing_ = await by_node(session, node.id)
    if existing_ is not None:
        return existing_

    city = City(
        node_id=node.id,
        name=name,
        founder_identity_id=None if founder is None else founder.id,
        charter=dict(catalog.laws.charter_defaults()),
        charter_params={},
        laws={},
    )
    session.add(city)
    await session.flush()
    await _mark_gate(session, city, node)
    await _open_channel(session, city)

    if founder is not None:
        await _office(
            session,
            city,
            founder.id,
            title=FOUNDER_TITLE,
            powers=FOUNDER_POWERS,
            by=founder.id,
        )

    await events.record(
        session,
        EventKind.CITY_FOUNDED,
        actor_identity_id=None if founder is None else founder.id,
        node_id=node.id,
        city_id=str(city.id),
        name=name,
    )
    return city


async def _mark_gate(session: AsyncSession, city: City, node: Node) -> None:
    """A founded city gets a gate at once (D-206).

    Without it the city would have no door: a road from beyond the walls could
    be tied nowhere, and exploration from inside would refuse instead of laying
    a trail. The node the city stands on becomes the gate -- for a city founded
    on one node it is the only node there is, and that node **is** the whole
    city.

    A city that already has a gate keeps it: the capital's gate is a node of its
    own, and the seed marked it long before founding.
    """

    ground = await territory(session, city)
    if any((place.properties or {}).get(travel.EXIT) for place in ground):
        return
    await props.stamp(session, node, {travel.EXIT: True})


def _violated(clash: IntegrityError) -> str | None:
    """Which unique index refused, by name -- or `None` if it cannot be told.

    Two wrappers deep: SQLAlchemy's asyncpg dialect raises its own
    `IntegrityError` and hangs the driver's `UniqueViolationError`, which is
    what carries `constraint_name`, on `__cause__`. Read rather than guessed
    from a second look: a table has more than one unique index, and a guess
    would answer "that name is taken" to a clash that was about something else.
    """
    return getattr(getattr(clash.orig, "__cause__", None), "constraint_name", None)


async def _channel_named(session: AsyncSession, name: str) -> uuid.UUID | None:
    """The channel of that name, case ignored -- the Net's own way of telling
    names apart (`net.channels`), and the rule its unique index holds.

    Asked of the model rather than of `engine.net`: the import would close the
    `city <-> net` cycle `_open_channel` keeps open on purpose.
    """
    return await session.scalar(
        select(NetChannel.id).where(func.lower(NetChannel.name) == name.lower())
    )


async def _open_channel(session: AsyncSession, city: City) -> None:
    """A founded city gets its official voice at once (D-222).

    The Net used to open the channel on the first ask for it, and the first ask
    is `look`: the unread count for the tab walks the reader's channels, so the
    first citizen's first look at a young city wrote a row. A city's channel is
    not a consequence of somebody reading; it is part of what a city is, like
    the gate above.

    Written from the model and not through `engine.net` on purpose. The Net
    already names the city -- it asks who holds the `channel` power and who the
    citizens are -- and a door back the other way would make `city <-> net` one
    more mutual cycle of the kind wave 3 is spending itself on removing. Cities
    founded before this are given theirs by migration `a1f7d3c58e26`.
    """

    #: **Founding is never refused from here.** The name may be a channel's
    #: already -- one name is one channel (D-284) -- and the obvious answer,
    #: refusing, is the wrong one on this path: `found` is also the seed's,
    #: the seed runs as one container the whole deploy waits on
    #: (`compose.yaml`: backend and worker want it `completed_successfully`),
    #: and a refusal here would let any player stop the next deploy of the
    #: whole server by taking the name of a city the vault has yet to add.
    #: So the city is founded and it is the channel that is missing -- a state
    #: the Net already has words for (`city_channel` returns `None` for cities
    #: older than D-222) -- and the clash is shouted at whoever runs the world.
    #: The player's door does not reach this: `establish` asks first and
    #: refuses with words, so an unopened channel means the seed's path or a
    #: race, never somebody typing a name they could have been warned about.
    try:
        async with session.begin_nested():
            session.add(NetChannel(name=city.name, city_id=city.id))
            await session.flush()
    except IntegrityError:
        log.error(
            "city %r founded without its official channel: the name is taken in the Net. "
            "Free it (there is no rename in the game -- an UPDATE on net_channel.name) "
            "and the channel opens on the next founding of this city",
            city.name,
        )


#: What a city cannot be without (D-023, D-159). The list is four roles, not
#: four names: any energy source will do, as long as somebody fills the pool.
#: There is no warehouse here not because it is unneeded but because the vault
#: describes no "warehouse" item: the engine may not require the nonexistent.
#: The roles a city cannot be founded without. Written down rather than
#: inferred: each owes the locale a word under `city-role-<role>`, and a role
#: added without one would show the player the key.
FOUNDATION_ROLES: tuple[str, ...] = ("bioprinter", "administration", "market", "power")


def foundation_needs() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """What must stand in the node before founding: role -> what satisfies it."""

    return (
        ("bioprinter", station_names(death.PRINTER)),
        ("administration", station_names(HALL)),
        ("market", station_names(market.TERMINAL)),
        (
            "power",
            tuple(
                name
                for thing_class in energy.GENERATOR_CLASSES
                for name in station_names(thing_class)
            ),
        ),
    )


async def missing_for_foundation(session: AsyncSession, node: Node) -> tuple[str, ...]:
    """What the node lacks to become a city. Empty -- founding is possible."""

    #: What stands (D-278): a hall lying in its crate founds nothing.
    costs = set(await world.thing_kinds(session, node))
    return tuple(role for role, with_what in foundation_needs() if not set(with_what) & costs)


async def establish(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body,
    name: str,
) -> City:
    """Found a city on a planet node that is nobody's or one's own (D-023, D-098,
    D-159). Outside a city land is never privatized (D-198), so a wild node needs
    no title -- only the buildings.

    The entry threshold is buildings, not a coin: `city.foundation_cost` in the
    vault is an estimate of materials and labour, there is nobody to pay it to
    and no reason. Expensive founding cuts off fly-by-night cities, and every
    founding becomes an event.

    The land under the city stops being private: the node is registered to the
    city and the deed for it is cancelled -- civic land is handed out by the
    authority, not the market (D-089).
    """

    if body.state is not BodyState.ALIVE:
        raise CityError(key="city-found-dead")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        raise CityError(key="city-body-off-node")
    if node.layer is not Layer.PLANET:
        raise CityError(key="city-found-planet-only")
    #: Nobody's land needs no title before founding -- outside a city there is
    #: none to be had (D-198). Somebody else's plot is still somebody else's:
    #: a city is not founded over a living owner's head.
    if node.owner_identity_id not in (None, body.identity_id):
        raise NotYours(key="city-found-foreign-land")
    if await by_node(session, node.id) is not None:
        raise CityError(key="city-found-already-city")
    if node.owner_city_id is not None:
        raise CityError(key="city-found-already-civic")
    #: Founding is entering a citizenship like any other, and there is one to a
    #: person (D-281): a citizen of another city leaves it first -- which, with
    #: a loan open, means settling up. The check stands here rather than at the
    #: enrolment three steps down, because by then the city exists: a founding
    #: refused halfway would leave a city standing with no founder in it.
    if await citizenship(session, body.identity_id) is not None:
        raise AlreadyCitizen(key="city-found-while-citizen")

    shortfall = await missing_for_foundation(session, node)
    if shortfall:
        #: What is missing is as many messages as there are gaps, and how a
        #: list of them is strung together is the language's business (D-251).
        raise NotReady(
            key="city-found-not-ready",
            inner={"missing": [Says(f"city-role-{role}") for role in shortfall]},
        )

    title = name.strip()
    if not title:
        raise CityError(key="city-found-no-name")
    #: The bound sits on this door and not in `found`, because this is where a
    #: player names a city -- `found` is also the seed's, and vault names are
    #: the vault build's business, not a refusal's. There is no renaming
    #: afterwards, so this is the only moment. What hangs on the bound is not
    #: only the card: the city's official channel takes its name from the city,
    #: and `CITY_NAME_LIMIT <= NET_NAME_LIMIT` (pinned in `test_city_founding`)
    #: is what makes that name one the Net could have accepted itself.
    if len(title) > CITY_NAME_LIMIT:
        raise CityError(key="city-found-name-too-long", limit=CITY_NAME_LIMIT)
    #: One name, one city. Asked here so that a person gets words rather than a
    #: database error; the rule itself is held by the `uq_city_name_lower` index,
    #: because two foundings racing on one name pass this check together. Case
    #: is ignored for the reason `by_name` gives: the name goes on to be a
    #: channel's, and the Net tells channel names apart that way.
    if await by_name(session, title) is not None:
        raise CityError(key="city-found-name-taken", name=title)
    #: And not a name somebody's channel already holds: the city would open a
    #: channel of that name a moment later, and the Net keeps one name to one
    #: channel. Asked here as well as in `_open_channel`, so a person is
    #: refused before a city is raised and rolled back under them.
    if await _channel_named(session, title) is not None:
        raise CityError(key="city-found-name-in-the-net", name=title)

    identity = await session.get(Identity, body.identity_id)
    #: The checks above are for the words; this is for the race. Two foundings
    #: with one name pass them together, and only `uq_city_name_lower` refuses
    #: the second -- inside a savepoint, so the loser's transaction survives to
    #: carry a refusal out instead of a server error. Which index refused is
    #: read off the violation and not guessed from a second look: this table
    #: has other unique ones (the node, the offices), and a guess would answer
    #: "the name is taken" to a clash that was about something else. The
    #: channel's index cannot arrive here -- `_open_channel` swallows its own,
    #: because that path is the seed's too and may not refuse.
    try:
        async with session.begin_nested():
            city = await found(session, catalog, node, title, founder=identity)
    except IntegrityError as clash:
        if _violated(clash) != "uq_city_name_lower":
            raise
        raise CityError(key="city-found-name-taken", name=title) from clash

    #: The location becomes city territory (40-society/00). The deed for it is
    #: cancelled: civic land is not traded by deed, otherwise there would be a
    #: shadow way to change the city's owner past the charter (D-159).
    node.owner_city_id = city.id
    await world.hand_over(session, node, None)
    await _retire_deed(session, node, city)
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_FOUNDED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(city.id),
        name=title,
        founded_by_player=True,
    )
    return city


async def install_founder(session: AsyncSession, city: City, who: Identity) -> Office:
    """Put the founder at the head of the city.

    The only way to establish authority where there is none yet: a city without
    offices has nobody who could appoint the first. From then on authority is
    passed only by appointment or by charter.
    """
    if city.founder_identity_id is not None:
        raise CityError(key="city-founder-exists", city=city.name)
    city.founder_identity_id = who.id
    office = await _office(
        session, city, who.id, title=FOUNDER_TITLE, powers=FOUNDER_POWERS, by=who.id
    )
    #: Founding makes the founder a citizen of this city (D-195). Otherwise the
    #: ruler is a stranger at home: no vote (the franchise is for citizens), a
    #: newcomer's rate at the bank, a visitor's duties. A citizenship of
    #: another city is **not** ended here any more but refuses the founding
    #: outright (D-281): one leaves before entering, and founding is entering.
    await _enrol_founder(session, city, who)
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=who.name,
        whom_identity_id=str(who.id),
        title=FOUNDER_TITLE,
        powers=list(FOUNDER_POWERS),
        founder=True,
    )
    return office
