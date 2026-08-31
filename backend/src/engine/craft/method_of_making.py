# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: method of making.

Split out of `engine/craft.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind, Operation, Recipe
from src.engine.craft._base import BENCHLESS, Procedure, Unmakeable
from src.units import (
    MINUTES_PER_HOUR,
)


def procedure(catalog: Catalog, output: str, *, way: str | None = None) -> Procedure:
    """Find a way to make `output` -- first among recipes, then operations.

    One thing can come from several operations (D-196 introduced that with
    felling against deadwood; the vault today lists one way per thing, but the
    door stays open). Which one is the worker's choice, not the resolver's, so
    `way` names the operation. Without it the first listed wins -- the vault
    lists the proper, faster way first.
    """
    book = catalog.recipes
    name = book.resolve(output)
    found = next((recipe for recipe in book.recipes if recipe.type_key == name), None)
    if found is not None:
        return _from_recipe(catalog, found)

    ways = [
        operation
        for operation in book.operations
        if name in {book.resolve(gives) for gives in operation.gives}
    ]
    if way is not None:
        chosen = [operation for operation in ways if (operation.id or operation.name) == way]
        if not chosen:
            #: The ways that do make it are named in the refusal: they have just
            #: been computed, and a refusal that only says "not this way" leaves
            #: the asker guessing the next word. An AI citizen (D-224) guessed
            #: `forge`, `smelt` and `forge` again, twenty-eight refusals in ten
            #: minutes, while the catalog calls the operation «Плавка».
            known = ", ".join(sorted((operation.id or operation.name) for operation in ways))
            raise Unmakeable(
                key="craft-unknown-way",
                goods=name,
                way=way,
                known="true" if known else "false",
                ways=known,
            )
        ways = chosen
    if ways:
        return _from_operation(catalog, ways[0], name)
    raise Unmakeable(key="craft-unmakeable", goods=name)


def _from_recipe(catalog: Catalog, recipe: Recipe) -> Procedure:
    if recipe.roles:
        raise Unmakeable(key="craft-is-a-dish", goods=recipe.type_key)
    if recipe.kind is ItemKind.MONEY:
        raise Unmakeable(key="craft-is-a-coin", goods=recipe.type_key)
    book = catalog.recipes
    return Procedure(
        output=recipe.type_key,
        #: The machine also goes through synonyms: recipes call it "Furnace",
        #: while in the node stands a "Smelting furnace". Without name resolution
        #: all chemistry and refining were unmakeable -- no machine has that name.
        station=None if recipe.station in (None, *BENCHLESS) else book.resolve(recipe.station),
        tools=(),
        inputs=tuple(book.resolve(name) for name in recipe.inputs),
        per_unit={book.resolve(name): value for name, value in recipe.amounts.items()},
        step_hours=step_hours(catalog, recipe),
        mix=recipe.mix,
        needs_recipe=True,
    )


def _from_operation(catalog: Catalog, operation: Operation, output: str) -> Procedure:
    book = catalog.recipes
    per_unit = {
        book.resolve(name): value for name, value in operation.amounts.get(output, {}).items()
    }
    #: An operation without spends is extraction. With a `place` field it is
    #: place extraction (D-177): felling runs as a batch without inputs.
    #: Without the field it is somebody else's mechanic (a vein).
    if not per_unit and operation.place is None:
        raise Unmakeable(key="craft-operation-extracts", operation=operation.id or operation.name)

    station: str | None = None
    tools: list[str] = []
    for requirement in operation.requires:
        canonical = book.resolve(requirement)
        if book.of_class(canonical):
            tools.append(canonical)
        elif book.is_raw(canonical):
            #: "Vein" in extraction requirements is not equipment but the mechanic itself.
            continue
        elif book.recipe(canonical).kind is ItemKind.STATION:
            station = canonical
        else:
            tools.append(canonical)

    #: Since D-210 what lies on the ground is found by foraging (`engine/forage.py`),
    #: not by a bare-hand operation: every place operation left works with a tool.
    hours = operation.hours_per_unit.get(output, 0.0)

    return Procedure(
        output=output,
        station=station,
        tools=tuple(tools),
        inputs=tuple(per_unit),
        per_unit=per_unit,
        step_hours=hours,
        mix=False,
        needs_recipe=False,
        place=operation.place,
    )


def step_hours(catalog: Catalog, recipe: Recipe) -> float:
    """Own processing time per unit.

    Since D-215 the vault ships it ready in `step_hours`; the subtraction
    below is a fallback for a book built before that. Re-deriving
    `craft.time_per_unit` and depth growth here would be a second copy of the
    vault's formula either way.
    """
    book = catalog.recipes
    ready = book.step_hours.get(recipe.type_key)
    if ready is not None:
        return ready
    spent = sum(value * book.labor_of(name) for name, value in recipe.amounts.items())
    return max(0.0, book.labor_of(recipe.type_key) - spent)


def batch_minutes(
    constants: Constants,
    proc: Procedure,
    units: float,
    station_quality: float,
    *,
    auto: bool = False,
) -> float:
    """How long a batch takes. A broken anvil works slowly.

    The automaton wins on volume: `craft.auto_speed_k` -- that many times faster.
    """
    speed = constants[R.CRAFT_STATION_SPEED_K]
    scale = constants[R.QUALITY_SCALE]
    k = speed.max - (speed.max - speed.min) * station_quality / scale.max
    minutes = proc.step_hours * MINUTES_PER_HOUR * units * k
    return minutes / constants[R.CRAFT_AUTO_SPEED_K] if auto else minutes
