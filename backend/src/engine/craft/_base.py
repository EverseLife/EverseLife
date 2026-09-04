# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Craft: batch, quality, losses (D-092, D-133).

Five conditions at once, all mandatory: knowledge, machine, tool, inputs, place
(20-systems/03-crafting). A batch is started in person and runs offline; the
input is written off at once, the product appears on schedule.

## Where each formula came from

Numbers are set by the vault, the order of steps is the engine's business
(vault CLAUDE.md). Below is the derivation of each formula so it can be checked
against D-092 and D-133 rather than taken on faith.

**Batch time.** `craft.time_per_unit` is "base batch time per unit of output",
`craft.time_growth_per_level` is "how many times longer a product takes with
each processing level deeper". The vault has already folded both into
`labor_hours`: a product's labour equals its own processing time plus the
labour of its inputs. So the own time is obtained by subtraction and **is not
re-derived in code**:

    step(product) = labor_hours(product) - sum(amounts[j] * labor_hours(j))

An operation without a recipe has its own time set directly --
`hours_per_unit[output]`.

**Machine speed.** `craft.station_speed_k` is "time multiplier from machine
quality; a broken anvil works slowly". So the worst machine works at the upper
bound of the multiplier, the best at the lower:

    k = max - (max - min) * machine_quality / scale

**Quality ceiling.** The lesser of machine and tool quality: the weakest link
limits (15-quality). What is absent does not limit: a "By hand" recipe without a
tool is bounded only by the raw material.

**Approach to the ceiling.** For an assembly it is set by inputs alone, for a
mix by inputs and proportion accuracy, with weights `quality.material_weight`
and `quality.ratio_weight` (D-092).

**Optimal proportion for a mix.** "Poor ore needs more coal and flux, pure ore
less". Amounts from `recipes.json` are the norm for ordinary raw material, i.e.
for the middle of the quality scale; deviation from it is symmetric:

    optimum[additive] = amounts[additive] * (1 + (middle - base_quality) / scale)

The base is the recipe's first input. A poor base needs up to one and a half
norms of additives, an excellent one down to half.

**Losses and spread.** `craft.waste_share` for correct work,
`craft.waste_bad_ratio` for a miss; `quality.spread_good_ratio` and
`quality.spread_bad_ratio` likewise. There is no "hit / miss" threshold in the
vault, and inventing one here is not allowed: both quantities follow accuracy
continuously.

**Craft premium.** `quality.hand_craft_bonus` is "up to +10 for hitting the
proportions exactly". Only for a mix: an assembly has no proportions at all, and
the premium there would be a bonus out of thin air.

Not one number beyond the vault appeared here. If a quantity is missing, it is
added to `data/constants.yaml`, not to code (D-065).

## What is not here yet

* **Dishes by roles** (`roles: true`) -- arrive with cooking on E2 (D-119,
  D-128) together with `cook.*`;
* **The unattended automat** lives in `engine/automat.py` (D-253): the
  D-035 attended mode is gone -- one machine, one meaning;
* **Invention, repair and recycling** -- separate registry actions with their
  own constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.constants import Catalog, ConstantError, current_catalog
from src.engine.errors import Refusal
from src.models.inventory import Item

"""Craft: batch, quality, losses (D-092, D-133).

Five conditions at once, all mandatory: knowledge, machine, tool, inputs, place
(20-systems/03-crafting). A batch is started in person and runs offline; the
input is written off at once, the product appears on schedule.

## Where each formula came from

Numbers are set by the vault, the order of steps is the engine's business
(vault CLAUDE.md). Below is the derivation of each formula so it can be checked
against D-092 and D-133 rather than taken on faith.

**Batch time.** `craft.time_per_unit` is "base batch time per unit of output",
`craft.time_growth_per_level` is "how many times longer a product takes with
each processing level deeper". The vault has already folded both into
`labor_hours`: a product's labour equals its own processing time plus the
labour of its inputs. So the own time is obtained by subtraction and **is not
re-derived in code**:

    step(product) = labor_hours(product) - sum(amounts[j] * labor_hours(j))

An operation without a recipe has its own time set directly --
`hours_per_unit[output]`.

**Machine speed.** `craft.station_speed_k` is "time multiplier from machine
quality; a broken anvil works slowly". So the worst machine works at the upper
bound of the multiplier, the best at the lower:

    k = max - (max - min) * machine_quality / scale

**Quality ceiling.** The lesser of machine and tool quality: the weakest link
limits (15-quality). What is absent does not limit: a "By hand" recipe without a
tool is bounded only by the raw material.

**Approach to the ceiling.** For an assembly it is set by inputs alone, for a
mix by inputs and proportion accuracy, with weights `quality.material_weight`
and `quality.ratio_weight` (D-092).

**Optimal proportion for a mix.** "Poor ore needs more coal and flux, pure ore
less". Amounts from `recipes.json` are the norm for ordinary raw material, i.e.
for the middle of the quality scale; deviation from it is symmetric:

    optimum[additive] = amounts[additive] * (1 + (middle - base_quality) / scale)

The base is the recipe's first input. A poor base needs up to one and a half
norms of additives, an excellent one down to half.

**Losses and spread.** `craft.waste_share` for correct work,
`craft.waste_bad_ratio` for a miss; `quality.spread_good_ratio` and
`quality.spread_bad_ratio` likewise. There is no "hit / miss" threshold in the
vault, and inventing one here is not allowed: both quantities follow accuracy
continuously.

**Craft premium.** `quality.hand_craft_bonus` is "up to +10 for hitting the
proportions exactly". Only for a mix: an assembly has no proportions at all, and
the premium there would be a bonus out of thin air.

Not one number beyond the vault appeared here. If a quantity is missing, it is
added to `data/constants.yaml`, not to code (D-065).

## What is not here yet

* **Dishes by roles** (`roles: true`) -- arrive with cooking on E2 (D-119,
  D-128) together with `cook.*`;
* **The unattended automat** lives in `engine/automat.py` (D-253): the
  D-035 attended mode is gone -- one machine, one meaning;
* **Invention, repair and recycling** -- separate registry actions with their
  own constants.
"""


class CraftError(Refusal):
    pass


class NotLearned(CraftError):
    """The recipe is not in the identity. Knowledge is taken in the Library for free (D-053)."""


class NoStation(CraftError):
    """The machine is not in the node. The place requirement is what makes craft city-forming."""


class NoTool(CraftError):
    pass


class NoLibrary(CraftError):
    """No library in the node. Its only restriction is geographic."""


class NotEnough(CraftError):
    """Not enough inputs. Matter is not created (I1)."""


class NoStrength(CraftError):
    """Not enough strength. Work is paid with the body, not only with materials (D-148)."""


class Busy(CraftError):
    """The machine is taken by another worker. As many places as machines (D-150)."""


class CutOff(CraftError):
    """The node is disconnected for non-payment: machines do not work until the debt is settled
    (D-149)."""


class Unmakeable(CraftError):
    """Not done that way: no such method, or the mechanic has not arrived yet."""


class TooBig(CraftError):
    """The batch is larger than `craft.batch_max`."""


class NotIngredient(CraftError):
    """Something inedible was put in a role. What counts as a product is decided by data
    (16-cooking)."""


#: The "By hand" station from `build/recipes.json` is the absence of a machine,
#: not a machine -- and it is the **only** such word (D-216).
#:
#: There used to be a second, «Стройка», left over from the recipe kind D-106
#: abolished. It meant exactly what this one means, so the two branches of code
#: were one branch -- while the client knew only one of the names and quietly
#: offered none of the eighteen recipes written with the other. A word that
#: behaves identically to another is not a concept but a synonym, and a synonym
#: half the system knows about is a hole.
HANDS = "by_hand"


#: What reads as "no machine needed".
BENCHLESS = (HANDS,)


#: The knowledge-carrier and blank thing classes (D-209, D-215). Concrete
#: items come from the vault by class membership. A written carrier keeps the
#: recipe's name in `Item.recipe_key`; wiping it turns it back into its blank.
#: Both are ordinary things beyond that: made, carried, sold, lost with the body.
CARRIER = "carrier"


BLANK = "blank"


def carrier_names(catalog: Catalog | None = None) -> tuple[str, ...]:
    """Concrete carrier item names (D-215). One place asks, everybody agrees."""
    book = (catalog or current_catalog()).recipes
    return book.of_class(CARRIER) or (CARRIER,)


def blank_of(catalog: Catalog, carrier_type: str) -> str:
    """The blank a wiped carrier becomes: the blank-class input of its recipe.

    A carrier is its blank plus a write (D-209), so the way back is written in
    the recipe itself -- no name table in code.
    """
    book = catalog.recipes
    blanks = set(book.of_class(BLANK))
    try:
        recipe = book.recipe(carrier_type)
    except ConstantError:
        recipe = None
    if recipe is not None:
        for name in recipe.inputs:
            if book.resolve(name) in blanks:
                return book.resolve(name)
    if blanks:
        return sorted(blanks)[0]
    raise Unmakeable(key="craft-no-blank", carrier=carrier_type, cls=BLANK)


@dataclass(frozen=True, slots=True)
class Procedure:
    """A way to make something: a recipe, or an operation without a recipe.

    From here on the engine does not care where the method came from -- except
    for one thing: a recipe requires knowledge, an operation never does
    (20-systems/03-crafting).
    """

    output: str
    #: Machine name, or None if done by hand.
    station: str | None
    #: What must be in the hands: an item name or a tool class.
    tools: tuple[str, ...]
    inputs: tuple[str, ...]
    #: How much of what per unit of output.
    per_unit: dict[str, float]
    #: Own processing time, hours per unit.
    step_hours: float
    #: Mix: composition is given as a proportion, and hit accuracy affects quality.
    mix: bool
    needs_recipe: bool
    #: Node property where the method is possible (D-177): "Felling" -> `forest`.
    #: Empty -- the method is not tied to a place.
    place: str | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """Batch forecast -- what the player sees **before** materials are spent.

    Quality is shown as an exact number: without it the player cannot connect
    action with result and will not derive a single proportion (D-092).
    """

    output: str
    units: float
    quality: float
    spread: float
    ceiling: float
    #: Proportion hit accuracy, 0..1. Always 1 for an assembly: no proportions.
    accuracy: float
    #: Loss share for waste and scrap, percent of inputs.
    waste: float
    minutes: float
    consumes: dict[str, float] = field(default_factory=dict)
    #: Electricity for a machine on it (D-269) and what the grid bills for it.
    #: Absent at a machine driven by the hands: the wire carries no zero for a
    #: question that does not arise (D-225).
    energy: float | None = None
    price: int | None = None


@dataclass(frozen=True, slots=True)
class _Pick:
    """The stack taken from, and exactly how much is taken."""

    item: Item
    take: int


@dataclass(frozen=True, slots=True)
class _Ready:
    """A parsed batch request: the forecast plus what was set aside for it."""

    plan: Plan
    picks: tuple[_Pick, ...]
    station: Item | None
    #: The operation the request resolved to, and the stacks it may eat from --
    #: only the ones a write may touch, when it is a write. Kept because the
    #: same preparation answers "and how much of it would fit at most"
    #: (`craft.most`) without reading the hands a second time.
    proc: Procedure
    stock: dict[str, list[Item]]
    auto: bool = False
    #: For a knowledge carrier: the canonical name of the recipe going onto it.
    recipe_key: str | None = None
