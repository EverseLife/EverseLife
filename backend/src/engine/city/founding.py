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
from src.engine.city.citizen import _enrol_founder
from src.engine.city.land import _retire_deed
from src.engine.city.lookup import by_node, territory
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

    **The name is taken as given, and nothing measures it here.** This is the
    seed's door: the capital and the delegate cities are founded from node
    names written in the vault (`seed.py`, `seed_catchup.py`), and a vault name
    is the build's business, not a refusal's -- a content bug should stop the
    build, not reach a player as words written for a human in a window. So the
    ceiling for this door stands in the vault: `WORLD_CITY_NAME_LIMIT` in its
    `tools/world.py` refuses to build a `city: true` node named longer -- the
    flag `seed_world.city_nodes` selects on, and so the node every call below
    arrives with (the catch-up's capital reaches it as `core.parent_id`, which
    is that same flagged node).

    The player's door is `establish`, and it measures what was typed. The two
    guarantees are therefore the same one -- "no city has an over-long name",
    not merely "no player founds one" -- each door bounded at the layer where
    its names are written.

    What hangs on that is not the city card. The city's official channel takes
    its name from the city -- `net.city_channel` builds the row directly, past
    the ceiling `net.channel.create` applies to what a player types -- so an
    unmeasured name here would have made a channel no player could have
    created, and nothing along the way would have said so.

    The vault holds its own copy of the number, because its build reads `data/`
    and knows nothing of this repository. The other end of that copy is
    `test_seed_world`, which measures the shipped layout against the Net's own
    ceiling: neither side can import the other, but both see the layout.
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

    identity = await session.get(Identity, body.identity_id)
    city = await found(session, catalog, node, title, founder=identity)

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
    #: newcomer's rate at the bank, a visitor's duties. Any previous
    #: citizenship ends -- there is one per person (D-160).
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
