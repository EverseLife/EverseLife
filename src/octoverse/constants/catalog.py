"""Каталоги мира: рецепты, культуры, законы.

Всё это движок читает из `build/*.json` и никогда не хранит у себя. Модели ниже
— это разбор готового, а не вторая спецификация: если поля разошлись с вольтом,
прав вольт (07-implementation-map).
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from octoverse.constants.spec import ConstantError


class ItemKind(StrEnum):
    """Поведение предмета в движке (D-090)."""

    STATION = "station"
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
    inputs: tuple[str, ...] = ()
    amounts: dict[str, float] = Field(default_factory=dict)
    manual_amounts: bool = False
    labor_hours: float
    station: str | None = None

    @property
    def is_assembly(self) -> bool:
        """Сборка — не смесь и не блюдо: качество определяют одни входы (D-092)."""
        return not self.mix and not self.roles


class Operation(Strict):
    """Операция без рецепта: то, что умеет каждый (20-systems/03-crafting)."""

    name: str
    requires: tuple[str, ...] = ()
    gives: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    amounts: dict[str, dict[str, float]] = Field(default_factory=dict)
    hours_per_unit: dict[str, float] = Field(default_factory=dict)
    yields: dict[str, float] = Field(default_factory=dict, alias="yield")
    manual_amounts: dict[str, bool] = Field(default_factory=dict)


class RecipeBook(Strict):
    synonyms: dict[str, str] = Field(default_factory=dict)
    tool_classes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    operations: tuple[Operation, ...] = ()
    raw: tuple[str, ...] = ()
    labor_hours: dict[str, float] = Field(default_factory=dict)
    recipes: tuple[Recipe, ...] = ()

    def resolve(self, name: str) -> str:
        """Синоним → каноническое имя. «Железо» и «Слиток железа» — одно."""
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
        """Часы труда в единице — основа цены-ориентира и оценки пошлин."""
        canonical = self.resolve(name)
        if canonical not in self.labor_hours:
            raise ConstantError(f"нет трудоёмкости {name!r} в build/recipes.json")
        return self.labor_hours[canonical]

    def tools_of_class(self, tool_class: str) -> tuple[str, ...]:
        return self.tool_classes.get(tool_class, ())

    _by_name: dict[str, Recipe] = PrivateAttr(default_factory=dict)

    def model_post_init(self, _: Any) -> None:
        self._by_name.update({recipe.name: recipe for recipe in self.recipes})


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
    #: Вариант с числовым параметром: «порог, %», «срок, суток».
    param: str | None = None
    #: Вариант доступен только если выбран другой вариант устава.
    requires_option: str | None = None


class CharterQuestion(Strict):
    id: str
    section: str
    question: str
    note: str | None = None
    #: Вопрос имеет смысл только при определённых ответах на другие вопросы.
    requires: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    options: tuple[CharterOption, ...] = ()

    @property
    def default_option(self) -> CharterOption | None:
        return next((o for o in self.options if o.default), None)


class CodeLaw(Strict):
    """Параметрический закон: значение, а не текст (D-094, D-130)."""

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
        """Устав нового города: город возникает работающим (D-130)."""
        out: dict[str, str] = {}
        for question in self.charter:
            default = question.default_option
            if default is not None:
                out[question.id] = default.id
        return out

    def code_law_defaults(self) -> dict[str, str]:
        return {law.id: law.default for law in self.code_laws if law.default is not None}


class Catalog(Strict):
    """Всё, что движок читает из вольта, кроме плоских констант."""

    recipes: RecipeBook
    plants: PlantCatalog
    laws: LawBook


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
