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

#: The index that holds one name to one city (`models.city.City`). Named here
#: because the refusal below has to tell it from the node's.
NAME_INDEX = "uq_city_name_lower"


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

    **The name is taken as given, and nothing checks its length here.** This
    is the seed's door: the capital and the delegate cities are founded from
    node names written in the vault (`seed.py`, `seed_catchup.py`). The
    player's door is `establish`, and the ceiling is there -- so what is
    guaranteed is "no player founds a city with an over-long name", not "no
    city has one".

    The difference is not covered by anything today, only unexercised: the
    vault build checks that a name exists in every language, not how long it
    is, and the longest node name it writes is 22 characters against a
    ceiling of 40. Closing it belongs in the vault build rather than in a
    refusal here -- a content bug should stop the build, not reach a player
    as words written for a human in a window.
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

    session.add(NetChannel(name=city.name, city_id=city.id))
    await session.flush()


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
    #: And the same name held by somebody's own channel. A city's name becomes
    #: its channel's, and `net.channel.create` refuses a name a channel already
    #: has -- so without this the door the player types a city into could hand
    #: the Net what the door they type a channel into would not. Asked against
    #: the row rather than through `engine.net`: the Net names the city, and a
    #: door back the other way is the `city <-> net` cycle wave 3 is removing.
    spoken_for = await session.scalar(
        select(NetChannel.id).where(func.lower(NetChannel.name) == func.lower(title))
    )
    if spoken_for is not None:
        raise CityError(key="city-found-name-in-the-net", name=title)

    identity = await session.get(Identity, body.identity_id)
    #: The check above is for the words; this is for the race. Two foundings
    #: with one name pass the check together, and only `uq_city_name_lower`
    #: refuses the second -- inside a savepoint, so the loser's transaction
    #: survives to carry a refusal out instead of a server error. The index
    #: guards the node as well, so what refused is asked rather than assumed.
    try:
        async with session.begin_nested():
            city = await found(session, catalog, node, title, founder=identity)
    except IntegrityError as clash:
        #: Which index refused, asked of the error itself rather than guessed
        #: from what the table holds afterwards: a double click on `city.found`
        #: races on the node as well as on the name, and `uq_city_node_id`
        #: answering would otherwise be reported as a name somebody took. The
        #: driver's own error carries the name, one cause below SQLAlchemy's.
        cause = getattr(clash.orig, "__cause__", None)
        if getattr(cause, "constraint_name", None) != NAME_INDEX:
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
