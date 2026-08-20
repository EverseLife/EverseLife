"""The recipe ladder as a graph, plus the checks the editor runs before writing.

Two layers meet here and they must not be confused:

  * **authored** -- `data/recipes.yaml`: what a designer wrote. Inputs, station,
    kind, flags. This is what the editor changes.
  * **derived** -- `build/recipes.json`: what the build computed out of it.
    Quantities, labour, mass. Shown, never written (D-133).

When the snapshot is older than the source, the derived layer is marked stale and
the interface says so instead of quietly showing yesterday's numbers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vaultfile import KNOWN_KINDS, STATION_ALIASES, VIRTUAL_STATIONS, RecipesFile, VaultError

# Node types, in the order the interface colours them.
RAW = "raw"
OPERATION = "operation"
RECIPE = "recipe"
CLASS = "class"


def load_derived(vault: Path) -> tuple[dict, bool]:
    """The last build output and whether it lags behind the source."""
    source = vault / "data" / "recipes.yaml"
    built = vault / "build" / "recipes.json"
    if not built.exists():
        return {}, True
    stale = built.stat().st_mtime_ns < source.stat().st_mtime_ns
    return json.loads(built.read_text(encoding="utf-8")), stale


class Ladder:
    """Names, who makes them, what they are made of, and how deep they sit."""

    def __init__(self, file: RecipesFile, derived: dict | None = None):
        self.file = file
        self.derived = derived or {}
        meta = file.meta()
        self.raw: list[str] = list(meta.get("raw") or [])
        self.bulk = set(meta.get("bulk") or [])
        self.edible = set(meta.get("edible") or [])
        self.gear_slots: list[str] = list(meta.get("gear_slots") or [])
        self.synonyms: dict[str, str] = {**STATION_ALIASES, **(meta.get("synonyms") or {})}
        self.tool_classes: dict[str, list[str]] = dict(meta.get("tool_classes") or {})
        self.cut_candidates = set(file.doc.get("cut_candidates") or [])
        self.recipes = {recipe["name"]: recipe for recipe in file.recipes()}
        self.operations = file.operations()
        self.op_outputs = {give: op for op in self.operations for give in op["gives"]}
        self.derived_recipes = {r["name"]: r for r in self.derived.get("recipes", [])}
        self.derived_ops = {op["name"]: op for op in self.derived.get("operations", [])}
        self.mass: dict[str, float] = dict(self.derived.get("mass") or {})
        self.labor: dict[str, float] = dict(self.derived.get("labor_hours") or {})

    # -- naming ------------------------------------------------------------

    def canon(self, name: str) -> str:
        return self.synonyms.get(name, name)

    def options(self, name: str) -> list[str]:
        """What can close a requirement: the thing itself or any of its class."""
        name = self.canon(name)
        return self.tool_classes.get(name, [name])

    def known_names(self) -> set[str]:
        return (
            set(self.recipes)
            | set(self.raw)
            | set(self.op_outputs)
            | set(self.tool_classes)
            | set(VIRTUAL_STATIONS)
            | set(self.synonyms)
        )

    # -- depth -------------------------------------------------------------

    def depths(self) -> dict[str, int]:
        """Rung of the ladder: how many rounds from bare raw material.

        The walk is the one the build check performs -- a thing opens when its
        station and all of its inputs are already open -- so the number on screen
        means the same as the number the check reasons about. Anything that never
        opens gets no depth and the interface shows it as unreachable.
        """
        depth: dict[str, int] = {name: 0 for name in self.raw}
        available = set(self.raw)
        step = 0
        while True:
            step += 1
            # What opens this round is decided by what was open when the round
            # began. Adding to `available` inside the round would let a whole
            # chain open at once and every link of it would land in one column.
            opened: set[str] = set()
            for op in self.operations:
                need = list(op["requires"]) + list(op.get("consumes") or [])
                if not all(any(o in available for o in self.options(q)) for q in need):
                    continue
                opened.update(give for give in op["gives"] if give not in available)
            for name, recipe in self.recipes.items():
                if name in available:
                    continue
                station = self.canon(recipe.get("station") or "Руками")
                if station not in VIRTUAL_STATIONS and not any(
                    o in available for o in self.options(station)
                ):
                    continue
                if all(any(o in available for o in self.options(i)) for i in recipe["inputs"]):
                    opened.add(name)
            if not opened:
                break
            for name in opened:
                depth[name] = step
            available |= opened
        for klass, members in self.tool_classes.items():
            reached = [depth[member] for member in members if member in depth]
            if reached and klass not in depth:
                depth[klass] = min(reached)
        return depth

    # -- graph -------------------------------------------------------------

    def nodes(self) -> list[dict]:
        depth = self.depths()
        out: dict[str, dict] = {}

        def put(name: str, node_type: str, **rest: Any) -> dict:
            node = out.setdefault(name, {"name": name, "type": node_type})
            node.update({k: v for k, v in rest.items() if v is not None})
            node.setdefault("depth", depth.get(name))
            node["depth"] = depth.get(name)
            node["bulk"] = name in self.bulk
            node["edible"] = name in self.edible
            node["mass"] = self.mass.get(name)
            node["labor_hours"] = self.labor.get(name)
            return node

        for name in self.raw:
            put(name, RAW)
        for give, op in self.op_outputs.items():
            node = put(give, OPERATION if give not in self.raw else RAW)
            node.setdefault("operations", [])
            node["operations"] = sorted({*node.get("operations", []), op["name"]})
        for name, recipe in self.recipes.items():
            derived = self.derived_recipes.get(name, {})
            put(
                name,
                RECIPE,
                kind=recipe.get("kind", "material"),
                station=recipe.get("station"),
                level=recipe.get("level"),
                section=recipe.get("section"),
                inputs=list(recipe.get("inputs") or []),
                amounts=derived.get("amounts") or recipe.get("amounts") or {},
                manual_amounts=bool(recipe.get("amounts")),
                # Собственное время изготовления единицы: то, что вывела сборка,
                # минус труд, пришедший со входами. Тем же вычитанием его
                # достаёт движок (src/engine/craft.py), а не считает заново
                step_hours=self._own_hours(name, derived),
                matter=self.matter_of(name),
                manual_hours=recipe.get("hours"),
                is_key=bool(recipe.get("key")),
                mix=bool(recipe.get("mix")),
                roles=bool(recipe.get("roles")),
                food=bool(recipe.get("food")),
                hot=bool(recipe.get("hot")),
                slot=recipe.get("slot"),
                store=recipe.get("store"),
                note=recipe.get("note"),
                cut_candidate=name in self.cut_candidates,
            )
        for klass, members in self.tool_classes.items():
            put(klass, CLASS, members=list(members))
        # A station named by a synonym is the same node as the recipe it means,
        # so nothing else is added here: `canon` already pointed the edges home.
        return sorted(out.values(), key=lambda node: (node.get("depth") is None, node["name"]))

    def matter_of(self, name: str) -> float | None:
        """Сколько вещества вошло в единицу: сумма масс входов с их количествами.

        Потолок массы изделия: материя при переделе не появляется. Массы здесь
        уже конечные — сборка подрезала их тем же правилом уровнем ниже.
        """
        derived = self.derived_recipes.get(name)
        if not derived or not derived.get("amounts"):
            return None
        into = sum(
            float(quantity) * self.mass.get(self.canon(item), 0.0)
            for item, quantity in derived["amounts"].items()
        )
        return round(into, 3) if into > 0 else None

    def _own_hours(self, name: str, derived: dict) -> float | None:
        labour = self.labor.get(name)
        if labour is None:
            return None
        spent = sum(
            float(quantity) * self.labor.get(self.canon(item), 0.0)
            for item, quantity in (derived.get("amounts") or {}).items()
        )
        return round(max(labour - spent, 0.0), 3)

    def edges(self) -> list[dict]:
        out: list[dict] = []
        seen: set[tuple] = set()

        def add(source: str, target: str, relation: str, via: str, amount: float | None = None):
            source = self.canon(source)
            target = self.canon(target)
            marker = (source, target, relation, via)
            if source == target or marker in seen:
                return
            seen.add(marker)
            edge = {"from": source, "to": target, "rel": relation, "via": via}
            if amount is not None:
                edge["amount"] = amount
            out.append(edge)

        for name, recipe in self.recipes.items():
            amounts = (self.derived_recipes.get(name, {}) or {}).get("amounts") or {}
            for item in recipe.get("inputs") or []:
                add(item, name, "input", name, amounts.get(item))
            station = recipe.get("station")
            if station and self.canon(station) not in VIRTUAL_STATIONS:
                add(station, name, "station", name)
        for op in self.operations:
            derived = self.derived_ops.get(op["name"], {})
            for give in op["gives"]:
                amounts = (derived.get("amounts") or {}).get(give) or {}
                for item in op.get("consumes") or []:
                    add(item, give, "input", op["name"], amounts.get(item))
                for tool in op["requires"]:
                    add(tool, give, "tool", op["name"])
        for klass, members in self.tool_classes.items():
            for member in members:
                add(member, klass, "class", klass)
        return out

    # -- stations ----------------------------------------------------------

    def stations(self) -> list[dict]:
        """Every working station and everything it takes part in.

        A station is tied into the ladder in three different ways, and they are
        easy to confuse when reading the file:

          * `parent`     -- the station this one is **assembled on** (D-106: the
            workbench is the only one made by hand, the rest need a workbench or
            something deeper);
          * `makes`      -- what is **made on it**;
          * `inputs_to`  -- where it goes **as a part**: the press is a station,
            and it is also an input of the coin station.

        «Руками» and «Стройка» are here too. They are not recipes -- nobody makes
        hands -- but half the ladder starts on them, and a picture of stations
        without them would begin in mid-air.
        """
        depth = self.depths()
        names = {name for name, r in self.recipes.items() if r.get("kind") == "station"}
        names |= set(VIRTUAL_STATIONS)
        names |= {
            self.canon(recipe["station"])
            for recipe in self.recipes.values()
            if recipe.get("station")
        }

        out = []
        for name in names:
            recipe = self.recipes.get(name)
            virtual = name in VIRTUAL_STATIONS
            out.append(
                {
                    "name": name,
                    "virtual": virtual,
                    "editable": recipe is not None,
                    "kind": (recipe or {}).get("kind"),
                    "level": (recipe or {}).get("level"),
                    "parent": self.canon(recipe["station"]) if recipe else None,
                    "depth": 0 if virtual else depth.get(name),
                    "makes": sorted(
                        other["name"]
                        for other in self.recipes.values()
                        if self.canon(other.get("station") or "") == name
                    ),
                    "inputs_to": sorted(
                        other["name"]
                        for other in self.recipes.values()
                        if any(self.canon(i) == name for i in other["inputs"])
                    ),
                    "operations": sorted(
                        op["name"]
                        for op in self.operations
                        if any(self.canon(need) == name for need in op["requires"])
                    ),
                }
            )
        return sorted(out, key=lambda s: (s["depth"] is None, s["depth"], s["name"]))

    # -- rollup ------------------------------------------------------------

    def raw_cost(self, name: str, quantity: float = 1.0) -> dict:
        """What one unit costs in bare raw material, by the derived quantities.

        The editor's own addition: the numbers are the build's, the summing up is
        not written anywhere in the vault. Useful for seeing at a glance that a
        ship node eats four hundred kilograms of ore.
        """
        totals: dict[str, float] = {}
        cycles: list[str] = []
        unknown: list[str] = []
        # Only a thing has a cost. A tool class is a requirement -- «Кирка» is any
        # of three picks -- and «Руками» is not an object at all; summing either
        # up would invent a thing the world does not have.
        if name not in self.recipes and name not in self.raw and name not in self.op_outputs:
            return {
                "name": name,
                "quantity": quantity,
                "totals": {},
                "mass": None,
                "labor_hours": None,
                "cycles": [],
                "unknown": [],
                "stale": not self.derived_recipes,
            }

        def walk(item: str, need: float, seen: tuple[str, ...]) -> None:
            item = self.canon(item)
            if item in seen:
                cycles.append(" → ".join([*seen, item]))
                return
            if item in self.raw:
                totals[item] = totals.get(item, 0.0) + need
                return
            recipe = self.derived_recipes.get(item)
            if recipe and recipe.get("amounts"):
                for sub, per_unit in recipe["amounts"].items():
                    walk(sub, need * float(per_unit), (*seen, item))
                return
            op = self.op_outputs.get(item)
            if op:
                derived = self.derived_ops.get(op["name"], {}).get("amounts") or {}
                amounts = derived.get(item) or {}
                if not amounts:
                    # A gathering operation takes matter from the world, not from
                    # another item: the walk ends here, and the item is the cost.
                    totals[item] = totals.get(item, 0.0) + need
                    return
                for sub, per_unit in amounts.items():
                    walk(sub, need * float(per_unit), (*seen, item))
                return
            unknown.append(item)
            totals[item] = totals.get(item, 0.0) + need

        walk(name, quantity, ())
        return {
            "name": name,
            "quantity": quantity,
            "totals": dict(sorted(totals.items(), key=lambda kv: -kv[1])),
            "mass": round(
                sum(self.mass.get(item, 0.0) * qty for item, qty in totals.items()),
                3,
            ),
            "labor_hours": self.labor.get(name),
            "cycles": cycles,
            "unknown": sorted(set(unknown)),
            "stale": not self.derived_recipes,
        }

    # -- vocabulary for the interface --------------------------------------

    def vocabulary(self) -> dict:
        used = {r.get("kind", "material") for r in self.recipes.values()}
        kinds = sorted(used | set(KNOWN_KINDS))
        stations = sorted(
            {r["station"] for r in self.recipes.values() if r.get("station")}
            | {name for name, r in self.recipes.items() if r.get("kind") == "station"}
            | set(VIRTUAL_STATIONS)
            | set(STATION_ALIASES)
        )
        return {
            "units": dict(self.file.meta().get("units") or {}),
            # Заданные руками массы сырья и продуктов операций. Отдельно от
            # выведенных: сразу после правки сборка ещё не считала, и поле
            # должно показывать написанное, а не вчерашнее
            "masses": dict(self.file.meta().get("mass") or {}),
            "kinds": kinds,
            "stations": stations,
            "slots": self.gear_slots,
            "levels": self.file.levels(),
            "names": sorted(self.known_names()),
            "raw": sorted(self.raw),
            "tool_classes": self.tool_classes,
            "synonyms": self.synonyms,
            "virtual_stations": list(VIRTUAL_STATIONS),
        }


# ------------------------------------------------------------------ checking


def validate(data: dict, ladder: Ladder, original: str | None = None) -> None:
    """Refuse what the build would refuse anyway, but with a readable reason.

    This is not a second source of truth: `build.py --check` still runs after the
    save. It is here so that an obvious slip -- an input nobody makes, a slot that
    does not exist -- is caught before it touches the file.
    """
    name = (data.get("name") or "").strip()
    if not name:
        raise VaultError("у рецепта должно быть название")
    if name != original and name in ladder.recipes:
        raise VaultError(f"рецепт «{name}» уже есть")
    if name != original and (name in ladder.raw or name in ladder.op_outputs):
        raise VaultError(f"«{name}» уже существует как сырьё или продукт операции")

    kind = data.get("kind")
    if not kind:
        raise VaultError("не выбран тип рецепта")

    inputs = data.get("inputs") or []
    if not inputs:
        raise VaultError("рецепт без входов: из чего он делается?")
    if len(set(inputs)) != len(inputs):
        raise VaultError("один и тот же вход перечислен дважды")
    known = ladder.known_names() | {name}
    for item in inputs:
        if ladder.canon(item) not in known:
            raise VaultError(f"вход «{item}» не рецепт, не сырьё и не продукт операции")

    station = data.get("station")
    if not station:
        raise VaultError("не выбрана рабочая станция")
    if ladder.canon(station) not in known:
        raise VaultError(f"рабочую станцию «{station}» никто не делает")

    amounts = data.get("amounts") or {}
    if amounts and set(amounts) != set(inputs):
        missing = ", ".join(item for item in inputs if item not in amounts)
        raise VaultError(
            f"количества заданы вручную не для всех входов: без числа остался «{missing}». "
            "Ручное `amounts` перекрывает вывод целиком, и вход без числа просто исчезнет "
            "из состава. Задайте все количества или уберите все."
        )
    for item, quantity in amounts.items():
        # Штучное считают штуками (D-212). Игрок не положит полкуска стали, и
        # рецепт, который этого требует, невыполним — а не просто некрасив.
        if ladder.canon(item) not in ladder.bulk and float(quantity) != int(quantity):
            raise VaultError(
                f"«{item}» — штучная вещь, а требуется {quantity}. Задайте целое "
                "количество либо отметьте вещь дробной в блоке «измерение» (D-212)."
            )

    for field in ("amounts", "weights"):
        for item in (data.get(field) or {}):
            if item not in inputs:
                raise VaultError(f"«{field}»: «{item}» не значится среди входов")
        for item, value in (data.get(field) or {}).items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise VaultError(f"«{field}»: у «{item}» количество должно быть числом больше нуля")
    for item in data.get("highlight") or []:
        if item not in inputs:
            raise VaultError(f"выделенный вход «{item}» не значится среди входов")

    heavier = data.get("mass")
    into = ladder.matter_of(original) if original else None
    if heavier is not None and into is not None and float(heavier) > into:
        raise VaultError(
            f"масса {heavier} кг больше того, что вошло в вещь ({into} кг). "
            "Материя при переделе не появляется: сборка всё равно подрежет число "
            "до вошедшего, и в файле оно будет неправдой."
        )

    slot = data.get("slot")
    if slot and slot not in ladder.gear_slots:
        raise VaultError(f"слота «{slot}» нет: есть {', '.join(ladder.gear_slots)}")
    for field in ("mass", "store", "hours"):
        value = data.get(field)
        if value is not None and (not isinstance(value, (int, float)) or value <= 0):
            raise VaultError(f"«{field}» должно быть числом больше нуля")

    twin = _same_composition(data, ladder, original)
    if twin:
        raise VaultError(
            f"на станции «{station}» уже есть рецепт с таким же составом: «{twin}» (D-209). "
            "Изобретение узнаёт рецепт по составу, и различить их будет нечем."
        )


def _same_composition(data: dict, ladder: Ladder, original: str | None) -> str | None:
    """D-209: two recipes on one station may not share a composition.

    Compared by authored quantities where they are set, otherwise by the derived
    ones: that is the pair the build itself compares.
    """

    def signature(recipe: dict, derived: dict | None) -> tuple:
        amounts = recipe.get("amounts") or (derived or {}).get("amounts") or {}
        items = recipe.get("inputs") or []
        return tuple(sorted((item, round(float(amounts.get(item, 0)), 4)) for item in items))

    mine = signature(data, None)
    if not any(quantity for _, quantity in mine):
        # Quantities are not authored here: the build will derive them, and two
        # recipes with the same inputs may still differ. Left to the check.
        return None
    station = ladder.canon(data.get("station") or "")
    for name, recipe in ladder.recipes.items():
        if name == original or ladder.canon(recipe.get("station") or "") != station:
            continue
        if signature(recipe, ladder.derived_recipes.get(name)) == mine:
            return name
    return None


def references(name: str, ladder: Ladder) -> dict[str, list[str]]:
    """Everywhere a name is mentioned -- what a rename or a deletion would break.

    Compared through `canon`, always: the file calls the smelter «Печь» when it
    is required by an operation and «Плавильная печь» when it is made. A literal
    comparison would report such a thing as used nowhere, and the delete dialog
    would say «ни на что не ссылается» about a station five operations need.
    """
    used_by = [
        recipe["name"]
        for recipe in ladder.recipes.values()
        if any(ladder.canon(item) == name for item in recipe.get("inputs") or [])
    ]
    stations = [
        recipe["name"]
        for recipe in ladder.recipes.values()
        if ladder.canon(recipe.get("station") or "") == name
    ]
    operations = [
        op["name"]
        for op in ladder.operations
        if any(
            ladder.canon(item) == name
            for item in [*(op.get("consumes") or []), *op["requires"]]
        )
    ]
    classes = [
        klass
        for klass, members in ladder.tool_classes.items()
        if any(ladder.canon(member) == name for member in members)
    ]
    lists = []
    if name in ladder.bulk:
        lists.append("meta.bulk")
    if name in ladder.edible:
        lists.append("meta.edible")
    if name in ladder.cut_candidates:
        lists.append("cut_candidates")
    if name in ladder.synonyms.values():
        lists.append("meta.synonyms")
    return {
        "inputs": sorted(used_by),
        "stations": sorted(stations),
        "operations": sorted(operations),
        "classes": sorted(classes),
        "lists": lists,
    }
