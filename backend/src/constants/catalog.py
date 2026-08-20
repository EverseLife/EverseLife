"""World catalogs: recipes, crops, laws.

The engine reads all of this from `build/*.json` and never stores it itself.
The models below are a parse of the finished thing, not a second
specification: if fields diverge from the vault, the vault is right (07-implementation-map).
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.constants.spec import ConstantError


class ItemKind(StrEnum):
    """The item's behaviour in the engine (D-090)."""

    STATION = "station"
    #: Furniture furnishes a building but is not a machine: nobody works at it.
    #: A bed is hibernation, a shelf is storage.
    FURNITURE = "furniture"
    TOOL = "tool"
    GEAR = "gear"
    VEHICLE = "vehicle"
    MATERIAL = "material"
    CONSUMABLE = "consumable"
    MONEY = "money"


class Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Recipe(Strict):
    name: str
    level: int
    section: str | None = None
    kind: ItemKind
    key: bool = False
    mix: bool = False
    roles: bool = False
    #: Edibility and "hot" come from data, not from the engine's guesses (D-119).
    food: bool = False
    hot: bool = False
    #: Which slot it is worn in: `back`, `body`, `frame`. Empty for non-gear --
    #: a pickaxe cannot be worn (D-146).
    slot: str | None = None
    #: How many kilograms it holds as a storage (D-181). Empty -- the thing is
    #: not a storage: a number in the vault makes it a chest, not a name in code.
    store: float | None = None
    inputs: tuple[str, ...] = ()
    amounts: dict[str, float] = Field(default_factory=dict)
    manual_amounts: bool = False
    #: Labour is not repeated here: `RecipeBook.labor_hours` holds it for every
    #: name at once -- raw material and operation products included -- and
    #: `labor_of()` is the one way to ask. A copy on the recipe was read by nobody.
    station: str | None = None

    @property
    def is_assembly(self) -> bool:
        """An assembly is neither a mix nor a dish: quality is determined by inputs alone
        (D-092)."""
        return not self.mix and not self.roles


class Operation(Strict):
    """An operation without a recipe: what everyone can do (20-systems/03-crafting)."""

    name: str
    requires: tuple[str, ...] = ()
    gives: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    #: Node property where the operation is possible (D-177): "Felling" -> `forest`.
    #: Empty -- the operation is not tied to a place.
    place: str | None = None
    amounts: dict[str, dict[str, float]] = Field(default_factory=dict)
    hours_per_unit: dict[str, float] = Field(default_factory=dict)
    yields: dict[str, float] = Field(default_factory=dict, alias="yield")
    manual_amounts: dict[str, bool] = Field(default_factory=dict)


class RecipeBook(Strict):
    synonyms: dict[str, str] = Field(default_factory=dict)
    tool_classes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    operations: tuple[Operation, ...] = ()
    raw: tuple[str, ...] = ()
    #: What goes into the pot beyond edible recipes: raw material and semi-finished goods.
    edible: tuple[str, ...] = ()
    #: Gear slots: one thing is worn in each (D-146).
    gear_slots: tuple[str, ...] = ()
    #: What is measured by weight rather than counted: ore, grain, water,
    #: liquids (D-212). Everything not named here is counted in pieces, and a
    #: piece is whole -- there is no half an ingot.
    bulk: tuple[str, ...] = ()
    #: What to draw next to a quantity: "5 шт", "3 м". Display only -- whether a
    #: quantity may be fractional is decided by `bulk`, not by the word (D-212).
    #: The engine never reads it; it travels so the client need not invent it.
    units: dict[str, str] = Field(default_factory=dict)
    #: Unit mass, kg. Given by data: it cannot be derived from input amounts --
    #: those are given by labour, not composition (D-146).
    mass: dict[str, float] = Field(default_factory=dict)
    labor_hours: dict[str, float] = Field(default_factory=dict)
    recipes: tuple[Recipe, ...] = ()

    def resolve(self, name: str) -> str:
        """Synonym -> canonical name. "Iron" and "Iron ingot" are one."""
        return self.synonyms.get(name, name)

    def recipe(self, name: str) -> Recipe:
        canonical = self.resolve(name)
        found = self._by_name.get(canonical)
        if found is None:
            raise ConstantError(f"нет рецепта {name!r} в build/recipes.json")
        return found

    def is_raw(self, name: str) -> bool:
        return self.resolve(name) in set(self.raw)

    def labor_of(self, name: str) -> float:
        """Labour hours in a unit -- the basis of the reference price and duty valuation."""
        canonical = self.resolve(name)
        if canonical not in self.labor_hours:
            raise ConstantError(f"нет трудоёмкости {name!r} в build/recipes.json")
        return self.labor_hours[canonical]

    def tools_of_class(self, tool_class: str) -> tuple[str, ...]:
        return self.tool_classes.get(tool_class, ())

    def mass_of(self, name: str, *, default: float = 0.0) -> float:
        """Unit mass of an item, kg (D-146).

        A name unknown to the catalog has no mass -- and that is not a zero
        "just in case" but a visible hole: anything can be carried through an
        item without mass. `default` is returned, and telemetry shows how many
        such items there are in the world.
        """
        return self.mass.get(self.resolve(name), default)

    def counted(self, name: str) -> bool:
        """Whether the thing is counted in pieces rather than measured (D-212).

        The sign belongs to the content and lives in the vault as one list of
        the measured: a name absent from it is a piece. That way a new thing is
        a piece by default, and a forgotten line is visible at once -- sand
        counted in pieces is noticed, sand weighed is not.
        """
        return self.resolve(name) not in self._measured

    def slot_of(self, name: str) -> str | None:
        """Which slot the thing is worn in. Empty -- not gear."""
        found = self._by_name.get(self.resolve(name))
        return found.slot if found is not None else None

    def is_ingredient(self, name: str) -> bool:
        """Whether it goes into the pot: edibility is data, not a guess by name.

        A product is either an edible recipe (`food: true`) or a name from the
        `edible` list in vault data. Suitability for a specific role is content
        too, but it does not exist yet: a product suits any role (16-cooking).
        """
        canonical = self.resolve(name)
        if canonical in self.edible:
            return True
        found = self._by_name.get(canonical)
        return found is not None and found.food

    _by_name: dict[str, Recipe] = PrivateAttr(default_factory=dict)
    _measured: set[str] = PrivateAttr(default_factory=set)

    def model_post_init(self, _: Any) -> None:
        self._by_name.update({recipe.name: recipe for recipe in self.recipes})
        self._measured.update(self.bulk)


class PlantRequirements(Strict):
    temp: dict[str, float]
    water: float
    fertility: float
    light: float


class PlantTraits(Strict):
    hardiness: float
    disease_risk: float
    density_risk: float
    spoilage_k: float


class Plant(Strict):
    id: str
    name: str
    gives: str
    #: What is sown with. Seeds are an item separate from the product: they are
    #: bought, stolen and lost with death, while agrotech is not (D-057).
    seed: str
    byproduct: str | None = None
    cycle_days: float
    yield_per_m2: float
    yield_per_cycle: float
    requires: PlantRequirements
    traits: PlantTraits
    restores_fertility: float = 0
    generosity: float
    generosity_cap: float
    used_in_recipes: int = 0


class PlantCatalog(Strict):
    plants: tuple[Plant, ...] = ()

    def by_id(self, plant_id: str) -> Plant:
        for plant in self.plants:
            if plant.id == plant_id:
                return plant
        raise ConstantError(f"нет культуры {plant_id!r} в build/plants.json")


class CharterOption(Strict):
    id: str
    label: str
    note: str | None = None
    default: bool = False
    #: An option with a numeric parameter: "threshold, %", "term, days".
    param: str | None = None
    #: The option is available only if another charter option is chosen.
    requires_option: str | None = None


class CharterQuestion(Strict):
    id: str
    section: str
    question: str
    note: str | None = None
    #: The question makes sense only with certain answers to other questions.
    requires: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    options: tuple[CharterOption, ...] = ()

    @property
    def default_option(self) -> CharterOption | None:
        return next((o for o in self.options if o.default), None)


class CodeLaw(Strict):
    """A parametric law: a value, not text (D-094, D-130)."""

    id: str
    name: str
    unit: str | None = None
    decision: str | None = None
    note: str | None = None
    default: str | None = None


class Sanction(Strict):
    id: str
    name: str
    note: str | None = None
    decision: str | None = None


class LawBook(Strict):
    charter: tuple[CharterQuestion, ...] = ()
    code_laws: tuple[CodeLaw, ...] = ()
    sanctions: tuple[Sanction, ...] = ()

    def charter_defaults(self) -> dict[str, str]:
        """A new city's charter: the city arises working (D-130)."""
        out: dict[str, str] = {}
        for question in self.charter:
            default = question.default_option
            if default is not None:
                out[question.id] = default.id
        return out

    def code_law_defaults(self) -> dict[str, str]:
        return {law.id: law.default for law in self.code_laws if law.default is not None}


class Catalog(Strict):
    """Everything the engine reads from the vault except flat constants."""

    recipes: RecipeBook
    plants: PlantCatalog
    laws: LawBook


class CatalogHolder:
    """The process's catalogs. Loaded at startup and live in memory.

    A separate cell is needed for the same reason as for constants: the engine
    does not go to files on demand, and the catalog is needed by journal jobs
    too, where there is no FastAPI application at all.
    """


    def __init__(self) -> None:
        self._current: Catalog | None = None

    def set(self, catalog: Catalog) -> None:
        self._current = catalog

    def current(self) -> Catalog:
        current = self._current
        if current is None:
            raise ConstantError(
                "каталоги не загружены: движок обязан загрузить их при старте"
            )
        return current

    def is_loaded(self) -> bool:
        return self._current is not None


CATALOG_HOLDER = CatalogHolder()


def current_catalog() -> Catalog:
    return CATALOG_HOLDER.current()


def _read(build_dir: Path, name: str) -> Any:
    path = Path(build_dir) / name
    if not path.exists():
        raise ConstantError(f"не найден {path}: собери вольт `python tools/build.py`")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_catalog(build_dir: Path) -> Catalog:
    return Catalog(
        recipes=RecipeBook.model_validate(_read(build_dir, "recipes.json")),
        plants=PlantCatalog.model_validate(_read(build_dir, "plants.json")),
        laws=LawBook.model_validate(_read(build_dir, "laws.json")),
    )
