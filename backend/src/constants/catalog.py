# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

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

from src.constants.renames import RenameTable
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


class Material(Strict):
    """A thing not made by any recipe: world raw material or an operation product
    (D-215). One registry row is all a new material needs to exist."""

    name: str
    #: Stable key (D-251): the thing's future identity in code, DB and wire.
    #: Optional until wave II so that pre-D-251 vault snapshots still load.
    id: str | None = None
    #: Thing class ("Ископаемое", ...). Engine behaviour binds to classes,
    #: never to names.
    thing_class: str | None = Field(default=None, alias="class")
    mass: float = 0.0
    bulk: bool = False
    edible: bool = False
    #: Units per hour of labour; also the vein weight for minerals.
    rate: float | None = None
    #: {finds, handful, place} -- present when the thing lies on the surface
    #: (D-210). `place` is a node-property word (D-254), the rest are numbers.
    forage: dict[str, float | str] | None = None
    #: Energy per unit when burned. Present -- the thing is a fuel (D-215).
    fuel: float | None = None
    #: A relic of the Forerunners (D-232): found, never made. It is not taken
    #: down, not taken apart and not picked up off the ground -- ever. The mark
    #: lives here rather than in code so that the next relic is a line in the
    #: vault, like everything else.
    relic: bool = False

    @property
    def type_key(self) -> str:
        """The D-251 identity, as on Recipe.type_key."""
        return self.id or self.name


class Recipe(Strict):
    name: str
    #: Stable key (D-251); optional until wave II, see Material.id.
    id: str | None = None
    level: int
    section: str | None = None
    kind: ItemKind
    #: Thing class (D-215): "Кирка", "Кровать", "Тачка"... Behaviour binds
    #: to the class, so a second bed is data, not code.
    thing_class: str | None = Field(default=None, alias="class")
    key: bool = False
    #: Built in place (D-268): a station that never fits in the hands -- its
    #: batch stands it on the floor where it was made, and nobody takes it up.
    built: bool = False
    #: Runs on electricity (D-269): a manual batch at it draws
    #: `craft.powered_energy_per_hour` from the grid, or from the cells beside it.
    powered: bool = False
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
    #: What the storage admits (D-230): `жидкость` -- a vessel, liquids only.
    #: Empty -- anything but liquids: a liquid exists in a vessel and nowhere else.
    holds: str | None = None
    inputs: tuple[str, ...] = ()
    amounts: dict[str, float] = Field(default_factory=dict)
    manual_amounts: bool = False
    #: Labour is not repeated here: `RecipeBook.labor_hours` holds it for every
    #: name at once -- raw material and operation products included -- and
    #: `labor_of()` is the one way to ask. A copy on the recipe was read by nobody.
    station: str | None = None

    @property
    def type_key(self) -> str:
        """The D-251 identity of the thing -- what `item.type_key`, the wire
        and the journal store. `name` stays the Russian display word; identity
        positions ask for this. Not `key`: that field is the ladder-milestone
        flag. The fallback keeps hand-built books in unit tests usable."""
        return self.id or self.name

    @property
    def is_assembly(self) -> bool:
        """An assembly is neither a mix nor a dish: quality is determined by inputs alone
        (D-092)."""
        return not self.mix and not self.roles


class Operation(Strict):
    """An operation without a recipe: what everyone can do (20-systems/03-crafting)."""

    name: str
    #: Stable key (D-251); optional until wave II, see Material.id.
    id: str | None = None
    requires: tuple[str, ...] = ()
    gives: tuple[str, ...] = ()
    #: The class the gives list was declared with, if any (D-215). The list
    #: itself arrives expanded by the vault build.
    gives_class: str | None = None
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
    #: Thing classes (D-215): class -> members. The one way behaviour groups
    #: things; `tool_classes` below is the tools-only view kept for the client.
    classes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: Stable class keys (D-251): class name -> id. Members in `classes` stay
    #: names until wave II.
    class_ids: dict[str, str] = Field(default_factory=dict)
    tool_classes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: Everything not made by a recipe, one row per thing (D-215).
    materials: tuple[Material, ...] = ()
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
    #: Liquids (D-230): they exist only inside a vessel (`Recipe.holds`). One
    #: list, like `bulk` -- not a guess by the label class "Жидкость".
    liquid: tuple[str, ...] = ()
    #: What to draw next to a quantity: "5 шт", "3 м". Display only -- whether a
    #: quantity may be fractional is decided by `bulk`, not by the word (D-212).
    #: The engine never reads it; it travels so the client need not invent it.
    units: dict[str, str] = Field(default_factory=dict)
    #: Unit mass, kg. Ready-made by the vault: raw mass is authored in the
    #: material registry, and an item's mass is what went into it (D-228). The
    #: engine never recounts it -- amounts here are given by labour, not by
    #: composition (D-146), and the vault is where that is reconciled.
    mass: dict[str, float] = Field(default_factory=dict)
    labor_hours: dict[str, float] = Field(default_factory=dict)
    #: Own processing time per unit, hours (D-215). Before, the engine and the
    #: vault editor both reconstructed it by subtracting input labour -- two
    #: copies of one formula.
    step_hours: dict[str, float] = Field(default_factory=dict)
    recipes: tuple[Recipe, ...] = ()

    def resolve(self, name: str) -> str:
        """Synonym -> canonical name. "Iron" and "Iron ingot" are one."""
        return self.synonyms.get(name, name)

    def recipe(self, name: str) -> Recipe:
        canonical = self.resolve(name)
        found = self._by_name.get(canonical)
        if found is None:
            raise ConstantError(f"no recipe {name!r} in build/recipes.json")
        return found

    def names(self) -> frozenset[str]:
        """Every name a thing can exist under: a recipe output or a material.

        The two lists are the whole of what the world holds -- a thing is
        either made by a recipe or exists without one (D-215) -- so whoever
        asks "is there such a thing at all" asks here rather than walking both.
        """
        return frozenset(self._names)

    def exists(self, name: str) -> bool:
        """Whether the world knows such a thing. A synonym counts: it is a name."""
        return self.resolve(name) in self._names

    def is_raw(self, name: str) -> bool:
        return self.resolve(name) in set(self.raw)

    def labor_of(self, name: str) -> float:
        """Labour hours in a unit -- the basis of the reference price and duty valuation."""
        canonical = self.resolve(name)
        if canonical not in self.labor_hours:
            raise ConstantError(f"no labour hours for {name!r} in build/recipes.json")
        return self.labor_hours[canonical]

    def tools_of_class(self, tool_class: str) -> tuple[str, ...]:
        return self.tool_classes.get(tool_class, ())

    def of_class(self, thing_class: str) -> tuple[str, ...]:
        """Members of a thing class (D-215). Unknown class -- empty, and the
        caller decides whether that is a refusal or just 'no such thing here'."""
        return self.classes.get(thing_class, ())

    def made_of_class(self, thing_class: str) -> tuple[str, ...]:
        """Members of the class that this world can actually make (D-232).

        A relic is a member like any other -- behaviour binds to the class, and
        a Forerunner plant heats exactly like a built one -- but nobody makes
        it. Whoever needs a thing to **put somewhere** asks for this list; the
        first name of `of_class` would sooner or later be a relic, and the
        capital would find itself with the Forerunners' yard on its pier.
        """
        return tuple(name for name in self.of_class(thing_class) if not self.is_relic(name))

    def class_of(self, name: str) -> str | None:
        """The class of a thing, or None. Behaviour code asks this instead of
        comparing names: a second bed must work without a code change."""
        return self._class_by_name.get(self.resolve(name))

    def is_of_class(self, name: str, thing_class: str) -> bool:
        return self.resolve(name) in set(self.classes.get(thing_class, ()))

    def fuels(self) -> dict[str, float]:
        """Energy per unit for every burnable material (D-215), keyed by id."""
        return {(m.id or m.name): m.fuel for m in self.materials if m.fuel}

    def is_relic(self, name: str) -> bool:
        """Whether this thing is a relic of the Forerunners (D-232).

        Asked by everything that would move a thing out of a place -- and asked
        once per item of a node scene, so it reads a set rather than walking
        every material in the world.
        """
        return self.resolve(name) in self._relics

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

    def is_liquid(self, name: str) -> bool:
        """Whether the thing is a liquid (D-230): never loose, always in a vessel."""
        return self.resolve(name) in self._liquids

    def holds_of(self, name: str) -> str | None:
        """What the thing admits as a storage: `жидкость` for a vessel, None otherwise."""
        found = self._by_name.get(self.resolve(name))
        return found.holds if found is not None else None

    def slot_of(self, name: str) -> str | None:
        """Which slot the thing is worn in. Empty -- not gear."""
        found = self._by_name.get(self.resolve(name))
        return found.slot if found is not None else None

    def built(self, name: str) -> bool:
        """Whether the station is built in place and never carried (D-268)."""
        found = self._by_name.get(self.resolve(name))
        return bool(found is not None and found.built)

    def powered(self, name: str) -> bool:
        """Whether the station runs on electricity and stands still without it (D-269)."""
        found = self._by_name.get(self.resolve(name))
        return bool(found is not None and found.powered)

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
    #: Every name in the world, recipes and materials alike: asked of every
    #: order placed in a book, so a set rather than two walks.
    _names: set[str] = PrivateAttr(default_factory=set)
    _measured: set[str] = PrivateAttr(default_factory=set)
    _liquids: set[str] = PrivateAttr(default_factory=set)
    _class_by_name: dict[str, str] = PrivateAttr(default_factory=dict)
    #: What the Forerunners left (D-232). A set, because every item of every
    #: node scene is asked about it.
    _relics: set[str] = PrivateAttr(default_factory=set)

    def model_post_init(self, _: Any) -> None:
        #: Keyed by the D-251 id: after load-time normalization every internal
        #: key is an id. `or name` keeps hand-built books in unit tests usable.
        self._by_name.update({(recipe.id or recipe.name): recipe for recipe in self.recipes})
        self._names.update((recipe.id or recipe.name) for recipe in self.recipes)
        self._names.update((material.id or material.name) for material in self.materials)
        self._measured.update(self.bulk)
        self._liquids.update(self.liquid)
        self._relics.update(
            (material.id or material.name) for material in self.materials if material.relic
        )
        for thing_class, members in self.classes.items():
            for member in members:
                self._class_by_name[member] = thing_class


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
    #: The wild ancestor's display name (D-260): a distinct cultivar, so a
    #: distinct name. Optional until the vault emits it.
    wild_name: str | None = None
    #: Stable key of the wild ancestor (D-251), resolved at load from the
    #: vault's own rename table (`spelt_wild`): the engine never derives it.
    wild_id: str | None = None
    gives: str
    #: What is sown with. Seeds are an item separate from the product: they are
    #: bought, stolen and lost with death, while agrotech is not (D-057).
    seed: str
    #: Stable key of the seed item (D-251), derived by the vault build from the
    #: plant id (`spelt` -> `spelt_seeds`). Optional until the vault emits it.
    seed_id: str | None = None
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
        raise ConstantError(f"no crop {plant_id!r} in build/plants.json")

    def by_seed(self, type_key: str) -> Plant | None:
        """The crop these seeds sow, or None if the goods are not a seed at all.

        Asked by whatever meets a stack before knowing what it is -- a find on
        the ground (D-254) -- so a miss is an answer, not an error.
        """
        for plant in self.plants:
            if plant.seed == type_key:
                return plant
        return None


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


class CodeLawOption(Strict):
    """One choice of a law that is a choice: a key, and the word for it.

    The key is what the city stores and the engine compares; the word is the
    vault's, and the second language comes by `<law>.<option>` in the names
    table. Before this the value was free text matched by substring -- «гражд»
    in it meant citizens -- so the law could not be set in another language at
    all, and a typo set it to "everyone" in silence.
    """

    id: str
    label: str


class CodeLaw(Strict):
    """A parametric law: a value, not text (D-094, D-130)."""

    id: str
    name: str
    unit: str | None = None
    decision: str | None = None
    note: str | None = None
    default: str | None = None
    #: Empty for a law that is a number or free text; a list for one that is a
    #: choice. The window draws a picker exactly when this is not empty.
    options: tuple[CodeLawOption, ...] = ()

    def has(self, option: str) -> bool:
        """Whether this key is one of the law's own choices."""
        return any(one.id == option for one in self.options)


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
            raise ConstantError("catalogs are not loaded: the engine must load them at startup")
        return current

    def is_loaded(self) -> bool:
        return self._current is not None


CATALOG_HOLDER = CatalogHolder()


def current_catalog() -> Catalog:
    return CATALOG_HOLDER.current()


def _read(build_dir: Path, name: str) -> Any:
    path = Path(build_dir) / name
    if not path.exists():
        raise ConstantError(f"{path} not found: build the vault with `python tools/build.py`")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _domain_id(table: dict[str, str], kind: str):
    """A translator for one rename domain: Russian name -> id, id passes.

    Anything else is a ConstantError: a name that resolves to nothing is a
    vault/engine mismatch, and letting it through would push the mismatch
    into the database as a phantom key.
    """
    ids = set(table.values())

    def translate(name: str | None) -> str | None:
        if name is None:
            return None
        found = table.get(name)
        if found:
            return found
        if name in ids:
            return name
        raise ConstantError(f"no stable key ({kind}) for the name {name!r}")

    return translate


def _renamed_recipes(payload: dict, renames: RenameTable) -> dict:
    """recipes.json with every reference translated to D-251 ids.

    The vault still emits name-keyed structures; this is the single seam where
    Russian names become ids. Past it the whole engine speaks ids -- which is
    why the translation is total and strict rather than best-effort.
    """
    klass = _domain_id(renames.classes, "class")
    prop = _domain_id(renames.node_properties, "node property")
    slot = _domain_id(renames.slots, "slot")
    #: The vault refers to things by synonyms too ("Печь" for the smelting
    #: furnace) -- a goods position resolves the synonym before the id.
    payload_synonyms: dict[str, str] = payload.get("synonyms") or {}

    def goods(name: str) -> str:
        return renames.goods_id(payload_synonyms.get(name, name))

    def requirement(name: str) -> str:
        #: An operation requirement closes with a class or a concrete thing.
        try:
            return klass(name)
        except ConstantError:
            return goods(name)

    def keyed(mapping: dict | None) -> dict:
        return {goods(k): v for k, v in (mapping or {}).items()}

    out = dict(payload)
    #: resolve() keeps working for old spellings: every Russian name -- and
    #: every colloquial synonym -- now lands on the id.
    out["synonyms"] = {
        **{syn: goods(name) for syn, name in (payload.get("synonyms") or {}).items()},
        **renames.goods,
        **renames.virtual_stations,
    }
    out["classes"] = {
        klass(k): [goods(m) for m in members]
        for k, members in (payload.get("classes") or {}).items()
    }
    out["tool_classes"] = {
        klass(k): [goods(m) for m in members]
        for k, members in (payload.get("tool_classes") or {}).items()
    }
    out["materials"] = [{**m, "class": klass(m.get("class"))} for m in payload.get("materials", [])]
    out["operations"] = [
        {
            **op,
            "requires": [requirement(r) for r in op.get("requires", [])],
            "gives": [goods(g) for g in op.get("gives", [])],
            "gives_class": klass(op.get("gives_class")),
            "consumes": [goods(c) for c in op.get("consumes", [])],
            "place": prop(op.get("place")),
            "amounts": {goods(g): keyed(v) for g, v in (op.get("amounts") or {}).items()},
            "hours_per_unit": keyed(op.get("hours_per_unit")),
            "yield": keyed(op.get("yield")),
            "manual_amounts": keyed(op.get("manual_amounts")),
        }
        for op in payload.get("operations", [])
    ]
    for listed in ("raw", "bulk", "edible", "liquid"):
        out[listed] = [goods(x) for x in payload.get(listed, [])]
    out["units"] = keyed(payload.get("units"))
    out["gear_slots"] = [slot(s) for s in payload.get("gear_slots", [])]
    for table in ("mass", "labor_hours", "step_hours"):
        out[table] = keyed(payload.get(table))
    out["recipes"] = [
        {
            **r,
            "class": klass(r.get("class")),
            "slot": slot(r.get("slot")),
            #: `holds: жидкость` marks a vessel (D-230); the word is the same
            #: vocabulary entry as the node property.
            "holds": prop(r.get("holds")),
            "inputs": [goods(i) for i in r.get("inputs", [])],
            "amounts": keyed(r.get("amounts")),
            "station": goods(r.get("station")) if r.get("station") else None,
        }
        for r in payload.get("recipes", [])
    ]
    return out


def _renamed_plants(payload: dict, renames: RenameTable) -> dict:
    """plants.json with item references as ids: gives, seed, byproduct.

    The wild ancestor's key rides along (D-251): the vault pins it in the
    plants domain under the wild display name, and looking it up here keeps
    the engine out of the id-derivation business.
    """
    goods = renames.goods_id
    return {
        "plants": [
            {
                **plant,
                "gives": goods(plant["gives"]),
                "seed": goods(plant["seed"]),
                "byproduct": goods(plant["byproduct"]) if plant.get("byproduct") else None,
                "wild_id": (
                    renames.plants.get(plant["wild_name"]) if plant.get("wild_name") else None
                ),
            }
            for plant in payload.get("plants", [])
        ]
    }


def load_catalog(build_dir: Path, renames: RenameTable) -> Catalog:
    return Catalog(
        recipes=RecipeBook.model_validate(
            _renamed_recipes(_read(build_dir, "recipes.json"), renames)
        ),
        plants=PlantCatalog.model_validate(
            _renamed_plants(_read(build_dir, "plants.json"), renames)
        ),
        laws=LawBook.model_validate(_read(build_dir, "laws.json")),
    )
