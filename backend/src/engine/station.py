"""Machines and furniture are placed in a building and carried out of it (D-106, D-150).

A machine is placed **in a building**: on an empty plot one builds first
(`estate.construct`) and only then furnishes. Machines and furniture take area
-- `build.slots_per_area` square metres per thing -- so a house's area is its
capacity, not decoration.

The ownership rule is simple and everything rests on it:

* **own node** -- the owner places and removes;
* **civic node** -- whoever the city gave the `laws` power places and removes:
  what the city is built up with is the authority's decision, not a random passer-by's;
* **unowned node** -- no way: first the plot is taken or bought.

A machine is an item `kind: station`, furniture is `kind: furniture` from
`build/recipes.json`. The engine keeps no list of "what is a machine": add a
new one in the vault and it is placeable without a code change (D-090). The
one difference between them: one works at a machine, furniture furnishes the
household (a bed -- hibernation, a shelf -- storage), and the client shows
them in separate windows.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.constants.catalog import ItemKind
from src.engine import events, travel, world
from src.models.city import Power
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node


class StationError(Exception):
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
    """What is placeable in a building at all: a machine or furniture."""
    return is_station(catalog, type_key) or is_furniture(catalog, type_key)


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
    from src.engine import city as town

    if node.owner_identity_id is not None:
        return node.owner_identity_id == body.identity_id
    if node.owner_city_id is None:
        return True
    city = await town.by_id(session, node.owner_city_id)
    return city is not None and await town.may(
        session, body.identity_id, city, Power.LAWS
    )


async def place(
    session: AsyncSession, catalog: Catalog, body: Body, item: Item
) -> Item:
    """Place a machine or furniture from the hands into the node's building.

    In person: machines are not teleported. Requires a building with free
    room: a machine takes area, and it does not stand in a yard under the open
    sky (D-106).
    """
    from src.constants import current
    from src.engine import estate

    if body.state is not BodyState.ALIVE:
        raise StationError("мёртвое тело ничего не ставит")
    await travel.require_here(session, body)

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise StationError("этой вещи нет в руках")
    if not placeable(catalog, item.type_key):
        raise NotStation(
            f"«{item.type_key}» — не станок и не мебель: в здание ставят оборудование"
        )

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StationError("тело вне узла")
    if not await may_build(session, body, node):
        raise NotYours(
            "узел не ваш: оборудование ставят у себя. Пустой городской участок "
            "выкупают, дикий — занимают"
        )

    #: The building is capacity: `build.slots_per_area` m2 per thing. No
    #: building -- no room; the yard stays a yard.
    constants = current()
    in_total, occupied = await estate.slots(session, constants, node)
    if in_total <= 0:
        raise estate.NoBuilding(
            "на участке нет здания: сначала строят, потом обставляют (D-106)"
        )
    if occupied >= in_total:
        from src.constants import registry as R

        raise estate.NoRoom(
            f"в здании {in_total} мест по {constants[R.BUILD_SLOTS_PER_AREA]:g} м², "
            "и все заняты: стройте больше либо уносите лишнее"
        )

    yard = await world.node_container(session, node)
    item.container_id = yard.id
    await session.flush()

    await events.record(
        session,
        EventKind.STATION_PLACED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
    )
    return item


async def take(
    session: AsyncSession, catalog: Catalog, body: Body, item: Item
) -> Item:
    """Take a machine or furniture back into the hands. One busy with work is not given up."""
    if body.state is not BodyState.ALIVE:
        raise StationError("мёртвое тело ничего не уносит")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StationError("тело вне узла")
    yard = await world.node_container(session, node)
    if item.container_id != yard.id:
        raise StationError("этой вещи нет в этом узле")
    if not placeable(catalog, item.type_key):
        raise NotStation(f"«{item.type_key}» — не станок и не мебель")
    if not await may_build(session, body, node):
        raise NotYours("узел не ваш: чужое оборудование не уносят")
    if item.busy_body_id is not None:
        raise Busy("за станком работают: дождитесь конца партии")
    #: A full chest is not carried away (D-181): otherwise "take the furniture"
    #: would become a way to carry a ton of cargo in the pocket past the carry limit (D-146).

    from src.engine import storage

    if storage.is_storage(catalog, item.type_key) and not await storage.is_empty(
        session, item
    ):
        raise NotEmpty(
            f"в «{item.type_key}» лежат вещи: сначала разберите, потом уносите"
        )

    pocket = await world.body_container(session, body)
    item.container_id = pocket.id
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
