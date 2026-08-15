"""Storage: chest and shelf (D-181).

Checked is what the mechanic exists for:

* a **vault field** makes a thing a storage, not a name in code;
* one puts and takes in person and in one's own node; nobody reaches into somebody's chest;
* the limit is mass, the same as hands and hold; the hands limit stays on withdrawal;
* a full storage is not carried away: otherwise furniture would become a way
  around the carry limit (D-146).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import station, storage, world
from src.models.estate import Building

CHEST = "Сундук"
GOODS = "Брус"


async def _yard(session: AsyncSession):
    """Own plot with a building: a chest is placed in a house, not under the open sky."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.home.{stamp}", "Дом", area_m2=200
    )
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    identity = await world.create_identity(session, f"Хозяин-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.claim_node(session, body, node)
    return node, identity, body


async def _chest(session: AsyncSession, node):
    yard = await world.node_container(session, node)
    return await world.grant_item(
        session, yard, CHEST, quality=60, origin="тест"
    )


async def _goods(session: AsyncSession, body, qty: float = 10):
    pocket = await world.body_container(session, body)
    return await world.grant_item(
        session, pocket, GOODS, amount=qty, quality=55, origin="тест"
    )


def test_vault_makes_storage(catalog: Catalog) -> None:
    """Not a single name in code: the engine reads `store` (D-090, D-181)."""
    assert storage.is_storage(catalog, CHEST)
    assert storage.capacity(catalog, CHEST) > 0
    #: A bed is furniture without capacity: nothing is put into it.
    assert not storage.is_storage(catalog, "Кровать")
    assert not storage.is_storage(catalog, "Верстак")


async def test_stored_lies_and_is_taken(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _yard(session)
    chest = await _chest(session, node)
    thing = await _goods(session, body, 10)

    put = await storage.put(session, constants, catalog, body, chest, thing, 6)
    assert put == pytest.approx(6)
    inner = await storage.content(session, chest)
    assert sum(float(v_.amount) / 1000 for v_ in inner) == pytest.approx(6)
    assert await storage.stored_mass(session, catalog, chest) > 0

    #: And back: a chest is not a grave, things are taken out of it.
    taken = await storage.take(
        session, constants, catalog, body, chest, inner[0], 2
    )
    assert taken == pytest.approx(2)


async def test_no_more_than_capacity_fits(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The limit is mass: a chest is not bottomless, like hands (D-146)."""
    from src.engine import gear

    node, _, body = await _yard(session)
    chest = await _chest(session, node)
    limit = storage.capacity(catalog, CHEST)
    per_piece = gear.mass_of(catalog, GOODS, 1)
    excess = limit / per_piece + 10
    thing = await _goods(session, body, excess)

    with pytest.raises(storage.Full):
        await storage.put(session, constants, catalog, body, chest, thing, excess)


async def test_no_reaching_into_foreign_chest(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Access follows the right to the node: breaking into what is not yours is a matter for the
    court (D-166)."""
    node, _, owner = await _yard(session)
    chest = await _chest(session, node)
    own_ = await _goods(session, owner, 4)
    await storage.put(session, constants, catalog, owner, chest, own_, 4)

    stamp = uuid.uuid4().hex[:6]
    guest = await world.create_identity(session, f"Гость-{stamp}")
    guest_body = await world.print_body(session, guest, node)
    foreign_thing = await world.grant_item(
        session,
        await world.body_container(session, guest_body),
        GOODS, amount=2, quality=55, origin="тест",
    )

    with pytest.raises(storage.NotYours):
        await storage.put(session, constants, catalog, guest_body, chest, foreign_thing, 2)
    lies = (await storage.content(session, chest))[0]
    with pytest.raises(storage.NotYours):
        await storage.take(session, constants, catalog, guest_body, chest, lies, 1)


async def test_full_chest_not_carried_away(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise "take the furniture" would become a way to carry a ton in the pocket."""
    node, _, body = await _yard(session)
    chest = await _chest(session, node)
    thing = await _goods(session, body, 5)
    await storage.put(session, constants, catalog, body, chest, thing, 5)

    with pytest.raises(station.NotEmpty):
        await station.take(session, catalog, body, chest)

    #: Unpacked -- carried away in the usual way.
    lies = (await storage.content(session, chest))[0]
    await storage.take(session, constants, catalog, body, chest, lies)
    await station.take(session, catalog, body, chest)
    pocket = await world.body_container(session, body)
    assert chest.container_id == pocket.id


async def test_hands_limit_stays_on_withdrawal(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A chest does not bypass the carry limit: one takes out as much as one can carry."""
    from src.engine import gear

    node, _, body = await _yard(session)
    chest = await _chest(session, node)
    hands_limit = await gear.capacity(session, constants, catalog, body)
    per_piece = gear.mass_of(catalog, GOODS, 1)
    many = hands_limit / per_piece + 5

    thing = await _goods(session, body, many)
    await storage.put(session, constants, catalog, body, chest, thing, many)
    lies = (await storage.content(session, chest))[0]

    with pytest.raises(gear.Overloaded):
        await storage.take(session, constants, catalog, body, chest, lies, many)
