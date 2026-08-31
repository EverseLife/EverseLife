# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: invention (D-064, D-209).

Split out of `engine/craft.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import events, goods, occupation, travel
from src.engine.craft._base import BENCHLESS, CraftError, NotEnough, TooBig, Unmakeable
from src.engine.craft._internal import _knows, _pick, _pick_station, _stock, _tiers_by
from src.engine.craft.batch import start
from src.engine.world import body_container, learn
from src.models.craft import CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.units import (
    PERCENT,
    ROUND_RATIO,
    amount_float,
)


@dataclass(frozen=True, slots=True)
class Invention:
    """What came of an attempt: the recipes it opened, the prototype batch, and
    what burned if nothing came together."""

    learned: tuple[str, ...]
    batch: CraftBatch | None
    burned: dict[str, float]
    #: Why there is no batch, when there is none -- as a message key and its
    #: arguments, not as a sentence (D-251 wave III): the engine does not know
    #: which language is being read, so the words are assembled at the edge.
    note_key: str | None = None
    note_args: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return bool(self.learned)


async def invent(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    composition: dict[str, float],
    units: float,
    *,
    station: str | None,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> Invention:
    """Try to make something without a recipe (D-064).

    The player lays out a composition -- up to `invent.max_ingredients` kinds
    of things from the hands, so much of each **per unit of output** -- at a
    machine (or by hand), and says how many units to make. The engine looks for
    a recipe of **this** machine with exactly this composition and exactly
    these amounts (`invent.exact_match_required`); the vault guarantees there
    is at most one (D-209).

    Came together -- the recipe goes into the identity with the discoverer's
    mark, and the laid-out materials become the first batch, by the ordinary
    flow: for a mix the composition is the proportion, and quality follows it
    (D-092). Did not -- `invent.material_loss` of what was laid out burns, and
    the answer says only that. No hints of closeness: guessing is meant to be
    hard, and to be shared.

    What is laid out must be in the hands **before** anything is decided:
    otherwise a guess with materials one does not own would learn for free
    when right and lose nothing when wrong.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CraftError(key="craft-dead-works")
    await travel.require_here(session, body)
    if units <= 0:
        raise CraftError(key="craft-zero-batch")
    #: One body does one thing (D-211), and a queued batch is not a second.
    await occupation.require_free(session, body, besides=frozenset({occupation.CRAFT}))
    if units > constants[R.CRAFT_BATCH_MAX]:
        raise TooBig(key="craft-batch-too-big", units=units)

    book = catalog.recipes
    laid: dict[str, float] = {}
    for name, value in composition.items():
        if float(value) <= 0:
            continue
        laid[book.resolve(name)] = laid.get(book.resolve(name), 0.0) + float(value)
    if not laid:
        raise CraftError(key="craft-empty-composition")
    if len(laid) > constants[R.INVENT_MAX_INGREDIENTS]:
        raise CraftError(key="craft-too-many-ingredients", max=constants[R.INVENT_MAX_INGREDIENTS])
    bench = None if station in (None, *BENCHLESS) else book.resolve(station)

    #: The machine must stand here: an attempt is work at it, even a failed one.
    if bench is not None:
        await _pick_station(session, body, bench, allow_own=True)

    #: What is laid out is in the hands, whatever comes of it.
    inventory = await body_container(session, body)
    #: Which stacks: the chosen tier per kind, or worst first (D-058).
    picked_tiers = _tiers_by(catalog, tiers)
    stock = await _stock(session, inventory, laid, tiers=picked_tiers)
    #: What is actually taken out of the hands is whole pieces (D-212). The
    #: per-unit composition stays as it was written -- that is what the recipe
    #: is matched against, and a recipe norm is fractional by right (D-133).
    total = {
        name: goods.whole(name, value * units, up=True, catalog=catalog)
        for name, value in laid.items()
    }
    _pick(stock, total)

    #: An operation everybody knows is not invented: smelting ore with coal at
    #: the furnace is on the list already, and burning the ore for it would be
    #: a trap, not a rule.
    for operation in book.operations:
        if set(map(book.resolve, operation.consumes)) == set(laid) and any(
            book.resolve(need) == bench for need in operation.requires
        ):
            raise Unmakeable(key="craft-known-operation", operation=operation.id or operation.name)

    found = _match(catalog, bench, laid)
    if found is not None and await _knows(session, body, found):
        raise CraftError(key="craft-already-known", recipe=found)

    if found is None:
        #: The price of a try, not an execution: of each kind laid out a
        #: random share burns, rolled within `invent.material_loss` -- the
        #: same wrong guess costs a little one day and a lot the next, and
        #: nobody can budget an exhaustive search to the ingot.
        loss = constants[R.INVENT_MATERIAL_LOSS]
        dice = random.Random()
        #: A counted thing burns whole (D-212), and upwards: what a work spends
        #: rounds up. Downwards a small roll would burn nothing at all, and a
        #: free wrong guess is exactly what the price of a try exists against
        #: (D-209). Never more than was laid out: the laid amount is whole too.
        lost = {
            name: goods.whole(
                name,
                value * dice.uniform(loss.min, loss.max) / PERCENT,
                up=True,
                catalog=catalog,
            )
            for name, value in total.items()
        }
        burned: dict[str, float] = {}
        for pick in _pick(stock, lost):
            burned[pick.item.type_key] = burned.get(pick.item.type_key, 0.0) + amount_float(
                pick.take
            )
            if pick.item.amount > pick.take:
                pick.item.amount -= pick.take
            else:
                await session.delete(pick.item)
        await session.flush()
        await events.record(
            session,
            EventKind.CRAFT_INVENTED,
            actor_identity_id=body.identity_id,
            node_id=body.node_id,
            station=bench,
            composition=laid,
            units=units,
            success=False,
            burned=burned,
        )
        return Invention(learned=(), batch=None, burned=burned, note_key="craft-invent-failed")

    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise CraftError(key="craft-body-without-identity")
    await learn(session, identity, found, discovered=True)
    await events.record(
        session,
        EventKind.CRAFT_INVENTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        station=bench,
        composition=laid,
        units=units,
        success=True,
        recipe=found,
    )
    #: The laid-out materials become the prototype -- by the ordinary flow, so
    #: that quality, waste and time are the same numbers the list will show
    #: from now on. Waste is taken on top of the norm, so the hands may prove a
    #: little short: then the recipe is opened, the materials stay, and the
    #: batch waits for the player to add what is missing.
    try:
        batch = await start(
            session,
            constants,
            catalog,
            body,
            found,
            units,
            proportions=dict(laid),
            tiers=picked_tiers,
            now=moment,
        )
    except NotEnough as short:
        #: The refusal's own key travels on: "the recipe opened, the hands are
        #: a little short" is the same sentence whether it stops a batch or
        #: merely postpones the prototype.
        return Invention(
            learned=(found,),
            batch=None,
            burned={},
            note_key=short.key,
            note_args=dict(short.params),
        )
    return Invention(learned=(found,), batch=batch, burned={})


def _match(catalog: Catalog, bench: str | None, laid: dict[str, float]) -> str | None:
    """The one recipe of this machine with exactly this composition, or nothing.

    Amounts are compared to the precision the vault writes them with: a
    thousandth is the build's rounding, not a game number.
    """
    book = catalog.recipes
    want = {name: round(value, ROUND_RATIO) for name, value in laid.items()}
    for recipe in book.recipes:
        if recipe.roles or recipe.kind is ItemKind.MONEY:
            continue
        station = None if recipe.station in (None, *BENCHLESS) else book.resolve(recipe.station)
        if station != bench:
            continue
        norm = {
            book.resolve(name): round(value, ROUND_RATIO) for name, value in recipe.amounts.items()
        }
        if norm == want:
            return recipe.type_key
    return None
