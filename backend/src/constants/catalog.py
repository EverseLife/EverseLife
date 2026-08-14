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

from src.constants.spec import ConstantError


class ItemKind(StrEnum):
    """Поведение предмета в движке (D-090)."""

    STATION = "station"
    #: Мебель обустраивает здание, но станком не является: на ней не работают.
    #: Кровать — гибернация, стеллаж — хранение.
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
    #: Съедобность и «горячее» — из данных, а не из догадок движка (D-119).
    food: bool = False
    hot: bool = False
    #: В какой слот надевается: `спина`, `тело`, `каркас`. У не-снаряжения
    #: пусто — надеть кирку нельзя (D-146).
    slot: str | None = None
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
    #: Свойство узла, где операция возможна (D-177): «Рубка дерева» → `лес`.
    #: Пусто — операция не привязана к месту.
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
    #: Что годится в котёл сверх съедобных рецептов: сырьё и полуфабрикаты.
    edible: tuple[str, ...] = ()
    #: Слоты снаряжения: в каждый надевается одна вещь (D-146).
    gear_slots: tuple[str, ...] = ()
    #: Масса единицы, кг. Задана данными: вывести её из количеств входов
    #: нельзя — те заданы трудом, а не составом (D-146).
    mass: dict[str, float] = Field(default_factory=dict)
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

    def mass_of(self, name: str, *, default: float = 0.0) -> float:
        """Масса единицы предмета, кг (D-146).

        Незнакомое каталогу имя массы не имеет — и это не ноль «на всякий
        случай», а видимая дыра: через предмет без массы пронесут что угодно.
        Возвращается `default`, а сколько таких предметов в мире, показывает
        телеметрия.
        """
        return self.mass.get(self.resolve(name), default)

    def slot_of(self, name: str) -> str | None:
        """В какой слот надевается вещь. Пусто — не снаряжение."""
        found = self._by_name.get(self.resolve(name))
        return found.slot if found is not None else None

    def is_ingredient(self, name: str) -> bool:
        """Годится ли в котёл: съедобность — данные, а не догадка по имени.

        Продукт — это либо съедобный рецепт (`food: true`), либо имя из списка
        `edible` в данных вольта. Годность конкретной роли — тоже содержание,
        но его пока нет: продукт годится в любую роль (16-cooking).
        """
        canonical = self.resolve(name)
        if canonical in self.edible:
            return True
        found = self._by_name.get(canonical)
        return found is not None and found.food

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
    #: Чем сеют. Семена — предмет, отдельный от продукта: их покупают, крадут
    #: и теряют со смертью, а агротехнику — нет (D-057).
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


class CatalogHolder:
    """Каталоги процесса. Загружаются при старте и живут в памяти.

    Отдельная ячейка нужна по той же причине, что и у констант: движок не
    ходит в файлы по требованию, а каталог нужен и заданиям журнала, где
    приложения FastAPI нет вовсе.
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
