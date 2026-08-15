"""Storage: chest, shelf and everything with `store` in the vault (D-181).

Carried load is bounded (D-146), the convoy hauls (D-157) -- yet until now
there was nowhere **to put things down**: possessions travelled in hands or
lay in the terminal, i.e. on sale. Home as a place where one stores starts here.

## What makes a thing a storage

A number in the vault, not a name in code: `store` is capacity in kilograms.
Add a new chest in the data and it works without an engine change (D-090).
Whether it is furniture or a machine the engine does not care: it looks at the field.

## Rules

* **in person** -- nobody reaches into somebody's chest from half a map away;
* **whoever may dispose of the node disposes** (`station.may_build`): the
  owner, and on civic land the authority. Breaking into somebody's chest is a
  matter for the court (D-166), not a button;
* **the limit is mass**, in the same kilograms as hands and hold: there is no
  third unit of capacity in the world;
* **a full storage is not carried away** (`station.take`): otherwise "take
  the furniture" would become a way to carry a ton of cargo in the pocket.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import events, travel, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import amount_float


class StorageError(Exception):
    pass


class NotStorage(StorageError):
    """Not a storage: the thing has no capacity in the vault."""


class NotYours(StorageError):
    """Somebody else's chest is not opened: access follows the right to the node (D-181)."""


class Full(StorageError):
    """No more fits. The limit is mass, as with hands and hold."""


def capacity(catalog: Catalog, type_key: str) -> float | None:
    """The thing's capacity as a storage, kg. `None` -- the thing is not a storage."""
    try:
        return catalog.recipes.recipe(type_key).store
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return None


def is_storage(catalog: Catalog, type_key: str) -> bool:
    limit = capacity(catalog, type_key)
    return limit is not None and limit > 0


async def inside(session: AsyncSession, chest: Item) -> Container:
    """The storage's inside. Created on first need."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.STORAGE, Container.owner_id == chest.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:
        container = Container(kind=ContainerKind.STORAGE, owner_id=chest.id)
        session.add(container)
        await session.flush()
    return container


async def content(session: AsyncSession, chest: Item) -> list[Item]:
    container = await inside(session, chest)
    return list(
        (
            await session.execute(select(Item).where(Item.container_id == container.id))
        ).scalars().all()
    )


async def stored_mass(
    session: AsyncSession, catalog: Catalog, chest: Item
) -> float:
    """How many kilograms already lie inside."""
    from src.engine import gear

    return sum(
        gear.mass_of(catalog, thing.type_key, amount_float(thing.amount))
        for thing in await content(session, chest)
    )


async def is_empty(session: AsyncSession, chest: Item) -> bool:
    """Whether it is empty inside. This decides whether the furniture is handed over."""
    container = await inside(session, chest)
    found = await session.scalar(
        select(Item.id).where(Item.container_id == container.id).limit(1)
    )
    return found is None


async def put(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    chest: Item,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Put from the hands into the storage. In person and only into your own."""
    await _allowed(session, catalog, body, chest)

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise StorageError("этой вещи нет в руках: кладут своё и из рук")

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise StorageError("класть нечего")

    from src.engine import gear

    bonus = gear.mass_of(catalog, item.type_key, qty)
    limit = capacity(catalog, chest.type_key) or 0.0
    free = limit - await stored_mass(session, catalog, chest)
    if bonus > free:
        raise Full(
            f"в «{chest.type_key}» свободно {free:.1f} кг, а это "
            f"{bonus:.1f} кг"
        )

    contents = await inside(session, chest)
    carried = await world.move_stack(session, item, contents, qty)
    await events.record(
        session,
        EventKind.STORAGE_PUT,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(chest.id),
        type_key=item.type_key,
        amount=carried,
    )
    return carried


async def take(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    chest: Item,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Take from the storage into the hands. The hands limit does not go anywhere."""
    await _allowed(session, catalog, body, chest)

    contents = await inside(session, chest)
    if item.container_id != contents.id:
        raise StorageError("этой вещи нет в хранилище")

    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise StorageError("забирать нечего")

    from src.engine import gear

    await gear.check_carry(session, constants, catalog, body, item.type_key, qty)

    pocket = await world.body_container(session, body)
    carried = await world.move_stack(session, item, pocket, qty)
    await events.record(
        session,
        EventKind.STORAGE_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(chest.id),
        type_key=item.type_key,
        amount=carried,
    )
    return carried


async def _allowed(
    session: AsyncSession, catalog: Catalog, body: Body, chest: Item
) -> Node:
    """The common door of both actions: alive, here, entitled, and this is a storage."""
    from src.engine import station

    if body.state is not BodyState.ALIVE:
        raise StorageError("мёртвое тело ничего не перекладывает")
    await travel.require_here(session, body)
    if not is_storage(catalog, chest.type_key):
        raise NotStorage(f"«{chest.type_key}» — не хранилище: в него не кладут")

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise StorageError("тело вне узла")
    yard = await world.node_container(session, node)
    if chest.container_id != yard.id:
        raise StorageError("этого хранилища здесь нет")
    if not await station.may_build(session, body, node):
        raise NotYours(
            "хранилище не ваше: в чужой сундук не лезут. Открыть его вправе "
            "хозяин узла, а на городской земле — власть"
        )
    return node
