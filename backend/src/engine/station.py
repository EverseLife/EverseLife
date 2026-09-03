# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Workstations and furniture are placed in a building and carried out of it (D-106, D-150).

In the player's language a workstation is «рабочая станция» -- it was «станок»
until D-200; the identifier stays `station`, which reads back as the same word.

A station is placed **in a building**: on an empty plot one builds first
(`estate.construct`) and only then furnishes. Stations and furniture take area
-- `build.slots_per_area` square metres per thing -- so a house's area is its
capacity, not decoration.

The ownership rule is simple and everything rests on it:

* **own node** -- the owner places and removes;
* **civic node** -- whoever the city gave the `laws` power places and removes:
  what the city is built up with is the authority's decision, not a random passer-by's;
* **nobody's node outside a city** -- open to all: the land there has no owner
  and never will (D-198), while what is placed belongs to whoever placed it.

A station is an item `kind: station`, furniture is `kind: furniture` from
`build/recipes.json`. The engine keeps no list of "what is a station": add a
new one in the vault and it is placeable without a code change (D-090). The
one difference between them: one works at a station, furniture furnishes the
household (a bed -- hibernation, a shelf -- storage), and the client shows
them in separate windows.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, current
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import city as town
from src.engine import craft, estate, events, storage, travel, world
from src.engine.errors import Refusal
from src.models.city import Power
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node, storey_of


class StationError(Refusal):
    pass


class NotStation(StationError):
    """Neither a machine nor furniture. Equipment is placed in a building, not a sack of grain."""


class NotYours(StationError):
    """The node is not yours. A machine is placed at your own place -- that is the point of a
    home."""


class Busy(StationError):
    """The machine is busy with work: it cannot be carried out from under a worker."""


class NotEmpty(StationError):
    """Things lie in the storage: unpack first, then carry away (D-181)."""


def is_station(catalog: Catalog, type_key: str) -> bool:
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.STATION
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


def is_furniture(catalog: Catalog, type_key: str) -> bool:
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.FURNITURE
    except Exception:  # noqa: BLE001
        return False


def placeable(catalog: Catalog, type_key: str) -> bool:
    """What is placeable in a building at all: a machine, furniture -- or a vessel.

    A vessel put up in a compartment stands on the hull's lines (D-288): the
    engines and the life support drink from what is installed and nothing
    else, so a canister or a cylinder is placed the way a chest is, whatever
    its kind says. Taken down, it is luggage again.
    """
    return (
        is_station(catalog, type_key)
        or is_furniture(catalog, type_key)
        or storage.is_vessel(catalog, type_key)
    )


async def may_build(session: AsyncSession, body: Body, node: Node) -> bool:
    """Whether this body may place and remove equipment in this node.

    A bought plot stays **on the territory** of the city -- taxes and household
    bills come from it -- but its owner is a person (D-089, D-116). So private
    ownership is checked first: the authority disposes of the city's buildings,
    not of somebody's house inside the city. Taking what is not yours is a
    matter for the court (D-166).

    Land outside a city belongs to nobody and is never privatized (D-198), yet
    work on it is open to everyone: whoever comes may put up a machine. What is
    placed belongs to whoever placed it -- the ground under it, to nobody.
    """

    if node.owner_identity_id is not None:
        return node.owner_identity_id == body.identity_id
    #: A storey with no holder of its own is disposed of by the plot under it
    #: (D-247). Read as land, a floor of a **civic** house was nobody's -- no
    #: holder on the row and no city either -- and any passer-by could carry a
    #: machine up into it.
    if node.owner_city_id is None and storey_of(node) is not None and node.parent_id is not None:
        place = await session.get(Node, node.parent_id)
        if place is not None:
            return await may_build(session, body, place)
    if node.owner_city_id is None:
        return True
    city = await town.by_id(session, node.owner_city_id)
    return city is not None and await town.may(session, body.identity_id, city, Power.LAWS)


async def place(session: AsyncSession, catalog: Catalog, body: Body, item: Item) -> Item:
    """Put a machine or furniture up in the node's building: from the hands, or
    off the floor it lies on (D-278).

    In person: machines are not teleported. Requires a building with free
    room: a machine takes area, and it does not stand in a yard under the open
    sky (D-106). Putting up is what makes a thing a machine here -- dropped on
    the floor it is cargo, however heavy: it takes no slot, nobody works at
    it, and the scene does not see it (D-278). A station built in place never
    comes this way: it stands where the batch made it (`craft.batch.finish`),
    and the slots answer to it after the fact -- a furnace has no hands to
    pass through.
    """

    if body.state is not BodyState.ALIVE:
        raise StationError(key="station-dead-places")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StationError(key="station-body-off-node")
    pocket = await world.body_container(session, body)
    #: The thing's own row is taken first: two hands putting up one machine
    #: from the same floor must not both read it lying. And it may be gone
    #: -- picked up, burnt, fallen with the house -- between the look and the
    #: click: that is the world's ordinary answer, said in words (D-011), not
    #: a failed refresh. The name is read first: a failed refresh leaves none.
    named = item.type_key
    try:
        await session.refresh(item, with_for_update=True)
    except InvalidRequestError as gone:
        raise StationError(key="thing-gone", goods=named) from gone
    yard_now = await world.node_yard(session, node)
    lying = yard_now is not None and item.container_id == yard_now.id and not item.installed
    if item.container_id != pocket.id and not lying:
        raise StationError(key="station-not-in-hands")
    if not placeable(catalog, item.type_key):
        raise NotStation(key="station-not-placeable", goods=item.type_key)
    if not await may_build(session, body, node):
        raise NotYours(key="station-node-not-yours")

    #: The building is capacity: `build.slots_per_area` m2 per thing. No
    #: building -- no room; the yard stays a yard. The plot row is taken for
    #: the transaction first: two hands putting up into the last place must
    #: not both count it free (CLAUDE.md, the remainder rule).
    await session.execute(select(Node.id).where(Node.id == node.id).with_for_update())
    constants = current()
    in_total, occupied = await estate.slots(session, constants, node)
    if in_total <= 0:
        raise estate.NoBuilding(key="station-no-building")
    if occupied >= in_total:
        raise estate.NoRoom(
            key="station-no-room", slots=in_total, per=constants[R.BUILD_SLOTS_PER_AREA]
        )

    yard = await world.node_container(session, node)
    item.container_id = yard.id
    #: Into the **building**, so under the roof (D-244). A machine carried in
    #: from the yard still bears the mark it was put down with, and left on it
    #: the thing would stand in the house and be spared by its collapse.
    item.outdoors = False
    item.installed = True
    await session.flush()

    await events.record(
        session,
        EventKind.STATION_PLACED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
    )
    #: A machine appeared: whoever stood here waiting for one gets it (D-209).

    await craft.wake_node(session, node)
    return item


async def take(session: AsyncSession, catalog: Catalog, body: Body, item: Item) -> Item:
    """Take a machine or furniture back into the hands. One busy with work is not given up."""
    if body.state is not BodyState.ALIVE:
        raise StationError(key="station-dead-takes")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StationError(key="station-body-off-node")
    yard = await world.node_container(session, node)
    if item.container_id != yard.id:
        raise StationError(key="station-not-in-node")
    #: What lies is picked up like any cargo (`storage.pick`); this door is for
    #: what stands (D-278).
    if not item.installed:
        raise StationError(key="station-not-installed", goods=item.type_key)
    #: Named before the general refusal: a relic **is** machinery, and being
    #: told it is "not a workstation" would read as a bug rather than as the
    #: rule that the Forerunners' things stay where they were found (D-232).
    if catalog.recipes.is_relic(item.type_key):
        raise NotYours(key="station-relic", goods=item.type_key)
    if not placeable(catalog, item.type_key):
        raise NotStation(key="station-not-a-station", goods=item.type_key)
    #: Built in place (D-268): a furnace, a column, a printer stand where they
    #: were made and do not fit in anybody's hands.
    if catalog.recipes.built(item.type_key):
        raise NotStation(key="station-built-in-place", goods=item.type_key)
    if not await may_build(session, body, node):
        raise NotYours(key="station-take-not-yours")
    if item.busy_body_id is not None:
        raise Busy(key="station-busy")
    #: A full chest is not carried away (D-181): otherwise "take the furniture"
    #: would become a way to carry a ton of cargo in the pocket past the carry limit (D-146).

    if storage.is_storage(catalog, item.type_key) and not await storage.is_empty(session, item):
        raise NotEmpty(key="station-not-empty", chest=item.type_key)

    pocket = await world.body_container(session, body)
    item.container_id = pocket.id
    #: In the hands there is no sky to be under: the mark means nothing here,
    #: and a stale one would travel back out with the thing (D-244).
    item.outdoors = False
    item.installed = False
    await session.flush()

    await events.record(
        session,
        EventKind.STATION_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
    )
    return item
