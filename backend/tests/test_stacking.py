"""One and the same thing lies in one stack (04-items, D-214).

The vault has always said raw material stacks, and the engine has always put a
fresh row in the hands for every swing, every find and every batch. Ten swings
at one face gave ten lines of "Железная руда · 12" that nothing told apart.

Checked is what the rule was introduced for:

* what arrives joins what already lies there -- mined, harvested, bought,
  taken out of a chest;
* it joins only a stack **nothing** tells it apart from: quality, mark, shelf
  life, fineness and cultivar all keep stacks separate, so the fold can never
  average a number away or lend a lot somebody else's shelf life;
* what does not stack at all -- a tool, a suit of gear, a machine -- never
  folds, however alike two of them look;
* a stack somebody is working on is not swallowed out from under the work.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import mining, storage, world
from src.models.craft import BatchKind, BatchState, CraftBatch
from src.models.estate import Building
from src.models.inventory import Item
from src.units import amount, amount_float

ORE = "Железная руда"
PICK = "Железная кирка"
CHEST = "Сундук"


async def _person(session: AsyncSession):
    """Own plot with a building: the chest below stands in a house, not in a yard."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.stack.{stamp}", "Дом", area_m2=200)
    node.owner_city_id = uuid.uuid4()
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    identity = await world.create_identity(session, f"Хозяин-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.grant_node(session, node, identity)
    return node, identity, body


async def _hands(session: AsyncSession, body):
    return await world.body_container(session, body)


async def _stacks(session: AsyncSession, container, type_key: str = ORE) -> list[Item]:
    rows = await session.execute(
        select(Item)
        .where(Item.container_id == container.id, Item.type_key == type_key)
        .order_by(Item.created_at)
    )
    return list(rows.scalars().all())


# --- what folds --------------------------------------------------------------


async def test_two_identical_lots_are_one_stack(session: AsyncSession) -> None:
    """The bug itself: same thing, same quality, two lines in the hands."""
    _, _, body = await _person(session)
    hands = await _hands(session, body)

    await world.grant_item(session, hands, ORE, amount=5, quality=12, origin="тест")
    await world.grant_item(session, hands, ORE, amount=3, quality=12, origin="тест")

    lying = await _stacks(session, hands)
    assert len(lying) == 1, "одинаковая руда обязана лежать одной стопкой"
    assert amount_float(lying[0].amount) == pytest.approx(8)


async def test_a_swept_face_gives_one_heap(
    session: AsyncSession, constants: Constants
) -> None:
    """Ten swings at one vein are ten lots of the same ore -- and one heap."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.face.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=1_000_000)
    identity = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, identity, node)

    face = await mining.start(session, constants, body, vein)
    for _ in range(10):
        await mining.swing(session, constants, face)
    await mining.leave(session, constants, face)

    heaps = await _stacks(session, await _hands(session, body))
    assert len(heaps) == 1, "взмахи по одной жиле обязаны сложиться в одну кучу"
    assert amount_float(heaps[0].amount) > 0


async def test_what_is_taken_out_of_a_chest_joins_the_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A chest is not a way to end up with two identical stacks in one hand."""
    node, _, body = await _person(session)
    hands = await _hands(session, body)
    chest = await world.grant_item(
        session, await world.node_container(session, node), CHEST, quality=60, origin="тест"
    )

    put = await world.grant_item(session, hands, ORE, amount=6, quality=30, origin="тест")
    await storage.put(session, constants, catalog, body, chest, put)
    await world.grant_item(session, hands, ORE, amount=2, quality=30, origin="тест")

    inside = (await _stacks(session, await storage.inside(session, chest)))[0]
    await storage.take(session, constants, catalog, body, chest, inside)

    lying = await _stacks(session, hands)
    assert len(lying) == 1, "вернувшееся из сундука обязано влиться в то, что в руках"
    assert amount_float(lying[0].amount) == pytest.approx(8)


async def test_an_old_world_folds_itself_up(session: AsyncSession) -> None:
    """Duplicates piled up before the rule collapse on the first arrival after it."""
    _, _, body = await _person(session)
    hands = await _hands(session, body)
    for _ in range(5):
        session.add(
            Item(container_id=hands.id, type_key=ORE, amount=amount(2), quality=Decimal("40"))
        )
    await session.flush()
    assert len(await _stacks(session, hands)) == 5

    await world.grant_item(session, hands, ORE, amount=1, quality=40, origin="тест")

    lying = await _stacks(session, hands)
    assert len(lying) == 1, "накопленные дубли обязаны схлопнуться при поступлении"
    assert amount_float(lying[0].amount) == pytest.approx(11)


# --- what keeps stacks apart -------------------------------------------------


async def test_a_different_quality_is_a_different_stack(session: AsyncSession) -> None:
    """The fold never averages: a poorer lot must not hide inside a good one."""
    _, _, body = await _person(session)
    hands = await _hands(session, body)

    await world.grant_item(session, hands, ORE, amount=5, quality=12, origin="тест")
    await world.grant_item(session, hands, ORE, amount=5, quality=13, origin="тест")

    lying = await _stacks(session, hands)
    assert len(lying) == 2, "разное качество — разные стопки"
    assert {float(stack.quality) for stack in lying} == {12, 13}


async def test_the_mark_keeps_lots_apart(session: AsyncSession) -> None:
    """Whose work it is stays with the thing (D-058): a fold cannot erase it."""
    _, identity, body = await _person(session)
    hands = await _hands(session, body)

    await world.grant_item(session, hands, ORE, amount=5, quality=20, origin="тест")
    await world.grant_item(
        session,
        hands,
        ORE,
        amount=5,
        quality=20,
        origin="тест",
        maker_identity_id=identity.id,
    )

    lying = await _stacks(session, hands)
    assert len(lying) == 2, "клеймо отличает стопку — сливать их нельзя"


async def test_shelf_life_keeps_food_apart(session: AsyncSession) -> None:
    """Yesterday's bread must not ride on the shelf life of today's."""
    _, _, body = await _person(session)
    hands = await _hands(session, body)
    now = datetime.now(UTC)
    for hours in (10, 40):
        session.add(
            Item(
                container_id=hands.id,
                type_key="Зерно",
                amount=amount(1),
                quality=Decimal("50"),
                spoils_at=now + timedelta(hours=hours),
            )
        )
        await session.flush()
    fresh = Item(
        container_id=hands.id,
        type_key="Зерно",
        amount=amount(1),
        quality=Decimal("50"),
        spoils_at=now + timedelta(hours=40),
    )
    session.add(fresh)
    await world.stack_up(session, fresh)

    lying = await _stacks(session, hands, "Зерно")
    assert len(lying) == 2, "срок годности отличает стопку"
    assert sorted(amount_float(stack.amount) for stack in lying) == [1, 2]


async def test_a_tool_never_stacks(session: AsyncSession) -> None:
    """Two pickaxes are two pickaxes: each has its own wear and its own mark."""
    _, _, body = await _person(session)
    hands = await _hands(session, body)

    await world.grant_item(session, hands, PICK, quality=50, origin="тест")
    await world.grant_item(session, hands, PICK, quality=50, origin="тест")

    assert len(await _stacks(session, hands, PICK)) == 2


async def test_work_in_progress_is_not_swallowed(session: AsyncSession) -> None:
    """A batch finds its thing by id -- folding it away would leave work with none."""
    _, _, body = await _person(session)
    hands = await _hands(session, body)

    target = await world.grant_item(
        session, hands, ORE, amount=4, quality=25, origin="тест"
    )
    session.add(
        CraftBatch(
            body_id=body.id,
            node_id=body.node_id,
            kind=BatchKind.RECYCLE,
            output=ORE,
            target_item_id=target.id,
            units=amount(1),
            quality=Decimal("25"),
            spread=Decimal("0"),
            state=BatchState.RUNNING,
            remaining_seconds=Decimal("60"),
        )
    )
    await session.flush()

    await world.grant_item(session, hands, ORE, amount=4, quality=25, origin="тест")

    lying = await _stacks(session, hands)
    assert len(lying) == 2, "стопку, над которой идёт работа, трогать нельзя"
    assert await session.get(Item, target.id) is not None


# --- the sign itself ----------------------------------------------------------


def test_the_vault_says_what_stacks(catalog: Catalog) -> None:
    from src.engine import goods

    assert goods.stackable(ORE, catalog), "сырьё складывается"
    assert goods.stackable("Слиток железа", catalog), "материал складывается"
    assert goods.stackable("Золотая монета", catalog), "монета складывается"
    assert not goods.stackable(PICK, catalog), "инструмент не складывается"
    assert not goods.stackable(CHEST, catalog), "мебель не складывается"


async def test_the_fold_counts_nothing_twice(session: AsyncSession) -> None:
    """Matter is conserved: what folded weighs what the lots weighed (I2)."""
    _, _, body = await _person(session)
    hands = await _hands(session, body)
    for _ in range(4):
        await world.grant_item(session, hands, ORE, amount=2.5, quality=33, origin="тест")

    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(Item.container_id == hands.id)
    )
    assert amount_float(int(total)) == pytest.approx(10)
