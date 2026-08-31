# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The words the whole counter speaks: refusals, keys, tiers, money arithmetic.

Nothing here touches the database. What may stand in the book (`_tradable`,
D-241), under which name (`goods_key`/`split_key`, D-209), in which quality
window (`tier_of`/`tier_span`, D-058) and at what floor (`_floor_of`, D-239)
are questions of vocabulary, and every other section of the market asks them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, case, func

from src.constants import Catalog, ConstantError, Constants
from src.constants import registry as R
from src.engine import craft, goods
from src.engine.errors import Refusal
from src.models.inventory import Item
from src.models.market import Order, Trade
from src.units import AMOUNT_SCALE, amount, amount_float


class MarketError(Refusal):
    pass


class NoTerminal(MarketError):
    """No terminal in the node. One marketplace per city (D-100)."""


class NotHere(MarketError):
    """The body is in the wrong node. Matter requires presence (D-044)."""


class NotYours(MarketError):
    pass


class NoGoods(MarketError):
    """The goods are not in the terminal, or already committed to another order."""


class BadOrder(MarketError):
    """The order is meaningless: zero volume, zero price, foreign tier."""


class Untradable(MarketError):
    """The goods cannot lie on a counter, so an order for them can never fill.

    Three ways that happens: the world knows no such thing at all, the thing is
    a relic of the Forerunners -- found, never made, never carried away
    (D-232) -- or it is a liquid, which exists inside a vessel and nowhere else
    (D-230, D-241). A buy order freezes money until it fills or expires, so an order
    that cannot fill is not a harmless typo but a stretch of somebody's purse
    locked for nothing.
    """


class NoMoney(MarketError):
    """Nothing to pay with. This is an in-game situation, not a server error."""


#: The thing class of marketplace terminals (D-100, D-215).
TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class Level:
    """One rung of the book: a price and the whole volume at it."""

    price: int
    amount: float


@dataclass(frozen=True, slots=True)
class Book:
    """The book for one position: goods plus quality tier."""

    node: uuid.UUID
    type_key: str
    tier: str
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()
    last: int | None = None
    #: The price step the rows are glued at, minor units. One means every
    #: price stands on its own row -- the book as the orders were written.
    step: int = 1

    @property
    def spread(self) -> int | None:
        if not self.bids or not self.asks:
            return None
        return self.asks[0].price - self.bids[0].price


@dataclass(frozen=True, slots=True)
class Fill:
    """What happened on placing an order."""

    order: Order
    trades: tuple[Trade, ...] = field(default_factory=tuple)

    @property
    def traded(self) -> float:
        return amount_float(sum(trade.amount for trade in self.trades))


# --- goods keys ----------------------------------------------------------------

#: A written knowledge carrier is a different good for every recipe on it
#: (D-209): a buyer of "Рецепт" must know **which**. On the counter it is
#: keyed as "Рецепт: Стекло" -- one string, so that orders, books and offers
#: work unchanged -- and split back into type and recipe where stacks are read.
CARRIER_SEP = ": "


def goods_key(item: Item) -> str:
    """The name the counter knows this stack by."""

    if item.type_key in craft.carrier_names() and item.recipe_key:
        return f"{item.type_key}{CARRIER_SEP}{item.recipe_key}"
    return item.type_key


def split_key(goods: str, catalog: Catalog | None = None) -> tuple[str, str | None]:
    """A counter name back into item type and, for a carrier, the recipe on it.

    The catalog is worth passing wherever the caller already holds one --
    whether a name is a carrier decides what the name **means**. The inner
    paths (`_stacks`, `_carrier`) deliberately ask the holder instead: they run
    where no catalog is threaded, and threading one to them is a change of its
    own rather than a line in this one.
    """

    head, sep, tail = goods.partition(CARRIER_SEP)
    if sep and head in craft.carrier_names(catalog) and tail:
        return head, tail
    return goods, None


# --- tiers -------------------------------------------------------------------


def tier_of(constants: Constants, quality: float | None) -> str:
    """The goods' quality tier. Five tiers are the book's shop window (D-058).

    A band stretches from its own start to the start of the next: bounds in the
    data are integers (..39, 40..), quality is fractional, and 39.5 must fall
    into the lower band rather than drop between them.
    """
    tiers = constants[R.QUALITY_TIERS]
    if quality is None:
        #: Energy and money have no quality at all -- the whole position is one.
        return tiers[0].name
    fitting = [tier for tier in sorted(tiers, key=lambda t: t.frm) if tier.frm <= quality]
    return fitting[-1].name if fitting else tiers[0].name


def tier_span(constants: Constants, tier: str) -> tuple[int, int]:
    """The quality a tier covers, both ends included. The floor rules live off it (D-239)."""
    for step in constants[R.QUALITY_TIERS]:
        if step.name == tier:
            #: The bounds are floats in the constants; the floor rules speak
            #: whole qualities, and the annotation must not promise otherwise.
            return int(step.frm), int(step.to)
    raise BadOrder(key="market-no-such-tier", tier=tier)


def _floor_of(constants: Constants, order: Order) -> int:
    """What the order will not go below.

    A buy written before the floor existed says nothing, and its tier's own
    start is read as the floor -- exactly what its tier button meant (D-239).
    A sell has no floor at all: it offers a lot, not a demand.
    """
    if order.min_quality is not None:
        return order.min_quality
    #: Down, never up: a band that starts at 39.5 must not have a floor that
    #: refuses the very stacks standing in it.
    return int(tier_span(constants, order.tier)[0])


def _floor_sql(constants: Constants) -> ColumnElement[int]:
    """The same floor, asked of the database: rows are picked by it, not read one by one."""
    return func.coalesce(
        Order.min_quality,
        case(
            {step.name: int(step.frm) for step in constants[R.QUALITY_TIERS]},
            value=Order.tier,
            else_=0,
        ),
    )


# --- order sanity ------------------------------------------------------------


def _sane(price: int, want: int) -> None:
    if price <= 0:
        raise BadOrder(key="market-price-not-positive")
    if want <= 0:
        raise BadOrder(key="market-volume-not-positive")


def _floor_sane(constants: Constants, floor: int) -> None:
    """The floor is a quality, and quality is the world's scale -- not any number."""
    tiers = sorted(constants[R.QUALITY_TIERS], key=lambda step: step.frm)
    if not tiers[0].frm <= floor <= tiers[-1].to:
        raise BadOrder(key="market-floor-off-scale", frm=tiers[0].frm, to=tiers[-1].to, floor=floor)


def _tradable(constants: Constants, catalog: Catalog, type_key: str, tier: str) -> str:
    """What may stand in the book at all, and under which name it stands.

    Called by the entrances rather than by `_place`, and before the volume is
    counted: a name nobody knows must be answered as a name, not as a fraction
    of a piece by `_volume`.

    A written knowledge carrier is one position per recipe -- "Рецепт: Стекло"
    (D-209) -- so the name splits first and both halves are asked about: a
    carrier of a recipe this world does not have is as undeliverable as a
    thing this world does not have.

    The canonical name is **returned**, not merely checked. "Железо" is a name
    of the iron ingot like any other, but stacks are stored under the ingot's
    own name and orders are matched by string: an order left as it came would
    rest in the book against nothing and hold the money until its term ran
    out. One name per position, and it is the world's name.

    Only the two entrances that **invent** a position ask this. `load` and
    `take` move a thing that already exists, and a thing that exists is by
    that fact deliverable: hanging the check on them would refuse a lot the
    world itself put in somebody's hands.
    """
    book = catalog.recipes
    kind, recipe = split_key(type_key, catalog)
    if not book.exists(kind):
        raise Untradable(key="market-no-such-goods", goods=kind)
    if book.is_relic(kind):
        raise Untradable(key="market-goods-relic", goods=kind)
    if book.is_liquid(kind):
        raise Untradable(key="market-goods-liquid", goods=kind)
    if tier not in {step.name for step in constants[R.QUALITY_TIERS]}:
        raise BadOrder(key="market-no-such-tier", tier=tier)
    if recipe is None:
        return book.resolve(kind)
    try:
        written = book.recipe(recipe)
    except ConstantError as unknown:
        raise Untradable(key="market-no-such-recipe", recipe=recipe) from unknown
    return f"{book.resolve(kind)}{CARRIER_SEP}{written.type_key}"


def _volume(catalog: Catalog, type_key: str, quantity: float) -> int:
    """The order's volume in internal units: a counted thing trades whole (D-212).

    Half an ingot cannot be delivered, so it cannot be offered either -- and an
    order for it would sit in the book unfillable, which is worse than a refusal.
    """

    return amount(goods.at_least_one(type_key, quantity, catalog=catalog))


def _cost(price: int, quantity: int) -> int:
    """What a volume costs at a price. Integer: not a cent is lost."""
    return price * quantity // AMOUNT_SCALE
