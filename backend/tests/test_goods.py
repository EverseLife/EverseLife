# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Piece or weight: in what quantities a thing exists (D-212).

Checked is what the rule was introduced for -- half an ingot lying in the
hands, "Сталь 0.5" in a bill, a quarter of a board on the counter:

* the vault decides which is which, and the engine only obeys the list;
* a counted thing arrives, moves and is spent in whole pieces, and the two
  roundings go in the directions the decision names: what arrives is floored,
  what is spent is raised;
* what is measured is left alone -- half a kilo of ore is an honest amount.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import craft, estate, goods, market, world
from src.units import amount_float

INGOT = "iron_ingot"
NAILS = "nails"
ORE = "iron_ore"
FORGE = "forge"
TERMINAL = "market_terminal"


async def _workshop(session: AsyncSession, *, machine: str | None = FORGE):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.pieces.{stamp}", "workshop", area_m2=100)
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    if machine is not None:
        await world.grant_item(session, yard, machine, quality=60, origin="сценарий теста")
    return node, identity, body


async def _give(session: AsyncSession, body, type_key: str, quantity: float):
    pocket = await world.body_container(session, body)
    return await world.grant_item(
        session, pocket, type_key, amount=quantity, quality=60, origin="сценарий теста"
    )


# --- the sign itself ---------------------------------------------------------


def test_the_vault_says_which_is_which(catalog: Catalog) -> None:
    assert goods.counted(INGOT, catalog), "слиток — штука"
    assert goods.counted("Доски", catalog)
    assert goods.counted("gold_coin", catalog), "монету считают"
    assert not goods.counted(ORE, catalog), "руду мерят весом"
    assert not goods.counted("water", catalog)
    #: A synonym is the same thing: "Железо" is the ingot (`synonyms`).
    assert goods.counted("Железо", catalog)


def test_rounding_goes_both_ways(catalog: Catalog) -> None:
    assert goods.whole(INGOT, 2.5, catalog=catalog) == 2, "пришедшее — вниз"
    assert goods.whole(INGOT, 2.5, up=True, catalog=catalog) == 3, "потраченное — вверх"
    assert goods.whole(ORE, 2.5, catalog=catalog) == pytest.approx(2.5), "весовое не трогаем"
    #: Float dust is not a piece: 2.9999999 boards are three boards.
    assert goods.whole("Доски", 2.9999999, up=True, catalog=catalog) == 3


# --- moving ------------------------------------------------------------------


async def test_a_piece_moves_whole_and_a_fraction_of_one_is_refused(
    session: AsyncSession,
) -> None:
    node, _, body = await _workshop(session, machine=None)
    stack = await _give(session, body, INGOT, 3)
    yard = await world.node_container(session, node)

    moved = await world.move_stack(session, stack, yard, 1.7)
    assert moved == 1, "просили полтора слитка — ушёл один"
    assert amount_float(stack.amount) == 2

    with pytest.raises(goods.NotWhole):
        await world.move_stack(session, stack, yard, 0.5)


async def test_the_measured_still_moves_in_parts(session: AsyncSession) -> None:
    node, _, body = await _workshop(session, machine=None)
    stack = await _give(session, body, ORE, 3)
    yard = await world.node_container(session, node)

    assert await world.move_stack(session, stack, yard, 1.5) == pytest.approx(1.5)
    assert amount_float(stack.amount) == pytest.approx(1.5)


async def test_matter_arrives_in_whole_pieces(session: AsyncSession) -> None:
    _, _, body = await _workshop(session, machine=None)
    piece = await _give(session, body, INGOT, 2.7)
    assert amount_float(piece.amount) == 2, "трёх четвертей слитка не бывает"


# --- work --------------------------------------------------------------------


async def test_a_batch_of_a_counted_thing_is_whole(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, identity, body = await _workshop(session)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 10)
    with pytest.raises(craft.CraftError):
        await craft.plan(session, constants, catalog, body, NAILS, 2.5)


async def test_a_work_spends_whole_pieces(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The norm may be fractional (D-133); what leaves the hands may not."""
    _, identity, body = await _workshop(session)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 10)

    forecast = await craft.plan(session, constants, catalog, body, NAILS, 1)
    spend = forecast.consumes[INGOT]
    assert spend == int(spend), f"списание штучного — целое, а не {spend}"

    before = await _at_hand(session, body, INGOT)
    await craft.start(session, constants, catalog, body, NAILS, 1)
    gone = before - await _at_hand(session, body, INGOT)
    assert gone == pytest.approx(spend), "прогноз и списание — одно число"
    assert gone == int(gone)


async def test_the_waste_on_a_counted_input_is_dust_until_it_is_a_piece(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Five per cent of two ingots is not a third ingot (playtest 2026-09-02).

    The waste share is taken on top of the norm, and rounding that sum up
    charged a whole extra piece for a fraction of one: two wires cost three
    copper, one frame cost two. The norm still rounds up (D-212); the waste
    rounds to the nearest piece and reaches one only over a big enough batch.
    """
    _, identity, body = await _workshop(session)
    await world.learn(session, identity, NAILS)
    await _give(session, body, INGOT, 40)

    two = await craft.plan(session, constants, catalog, body, NAILS, 2)
    assert two.consumes[INGOT] == 2, "5% от двух слитков -- не третий слиток"
    twenty = await craft.plan(session, constants, catalog, body, NAILS, 20)
    assert twenty.consumes[INGOT] == 21, "а с двадцати -- уже целый слиток"


def test_spent_never_goes_below_the_norm_in_pieces(catalog: Catalog) -> None:
    #: A norm of a third of a piece is a whole piece to spend, whatever the
    #: waste on it rounds to -- the recipe cannot be worked for nothing.
    assert goods.spent(INGOT, 0.3, 0.316, catalog=catalog) == 1
    assert goods.spent(INGOT, 2.0, 2.105, catalog=catalog) == 2
    assert goods.spent(INGOT, 2.0, 2.6, catalog=catalog) == 3
    assert goods.spent(ORE, 2.0, 2.105, catalog=catalog) == pytest.approx(2.105), (
        "весовое не трогаем"
    )


async def test_the_bill_of_a_house_is_in_whole_pieces(constants: Constants) -> None:
    lot = estate.bill(constants, footprint=7, floors=1, kind=estate.kinds(constants)[0])
    for name, qty in lot.items():
        if goods.counted(name):
            assert qty == int(qty), f"{name}: {qty} — штучное считается штуками"


# --- the counter -------------------------------------------------------------


async def test_the_book_takes_no_half_ingots(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.book.{stamp}", "Торг", area_m2=100)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, TERMINAL, quality=70, origin="сценарий теста")

    identity = await world.create_identity(session, f"Купец-{stamp}")
    body = await world.print_body(session, identity, node)
    await _give(session, body, INGOT, 4)
    await market.load(session, constants, body, INGOT, 4)

    with pytest.raises(goods.NotWhole):
        await market.sell(
            session,
            constants,
            catalog,
            identity,
            node,
            type_key=INGOT,
            #: Asked of the world rather than written by hand: the book takes
            #: only its own tiers, and "обычная" was never one of them.
            tier=market.tier_of(constants, 50),
            price=100,
            quantity=0.5,
        )


async def _at_hand(session: AsyncSession, body, type_key: str) -> float:
    from sqlalchemy import func, select

    from src.models.inventory import Item

    pocket = await world.body_container(session, body)
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == type_key
        )
    )
    return amount_float(int(total or 0))
