"""The ladder as the editor reads it, and the refusals it hands back.

Nothing here names a recipe. The vault is a living document -- «Дикий лён»
became «Лён» while this tool was being written -- so every test picks its own
example out of the file and asserts the rule, not the example.
"""

from __future__ import annotations

from pathlib import Path

import ladder as model
import pytest
import vaultfile as vault


@pytest.fixture
def ladder(recipes: Path, source: Path) -> model.Ladder:
    derived, _ = model.load_derived(source.parent.parent)
    return model.Ladder(vault.RecipesFile(recipes), derived)


@pytest.fixture
def sample(ladder: model.Ladder) -> dict:
    """Some recipe with more than one input, standing on a real station."""
    for recipe in ladder.recipes.values():
        station = ladder.canon(recipe.get("station") or "")
        if len(recipe["inputs"]) > 1 and station in ladder.recipes:
            return recipe
    pytest.skip("в файле не нашлось рецепта с двумя входами на настоящей станции")


# -------------------------------------------------------------------- ladder


def test_everything_is_reachable_from_bare_raw_material(ladder: model.Ladder):
    """A thing without a rung is a thing the graph cannot place.

    It would mean the ladder does not pass from raw material -- and the picture
    would quietly lie about it instead of saying so.
    """
    assert [node["name"] for node in ladder.nodes() if node["depth"] is None] == []


def test_raw_material_stands_at_the_bottom(ladder: model.Ladder):
    depth = ladder.depths()
    assert {depth[name] for name in ladder.raw} == {0}


def test_a_thing_stands_deeper_than_everything_it_needs(ladder: model.Ladder):
    """The column really means «this many rounds of work away from raw».

    Every input and the station itself must sit strictly to the left, otherwise
    an arrow on the graph would point backwards and the layout would be a lie.
    """
    depth = ladder.depths()
    for name, recipe in ladder.recipes.items():
        for item in recipe["inputs"]:
            nearest = min(depth[o] for o in ladder.options(item) if o in depth)
            assert nearest < depth[name], f"«{name}» не глубже своего входа «{item}»"
        station = ladder.canon(recipe.get("station") or "Руками")
        if station in depth:
            assert depth[station] < depth[name], f"«{name}» не глубже своей станции"


def test_an_edge_goes_from_every_input_to_its_recipe(ladder: model.Ladder, sample: dict):
    edges = {(edge["from"], edge["to"], edge["rel"]) for edge in ladder.edges()}
    for item in sample["inputs"]:
        assert (ladder.canon(item), sample["name"], "input") in edges
    # a station is a different kind of tie and must not pass for an ingredient
    station = ladder.canon(sample["station"])
    assert (station, sample["name"], "station") in edges
    assert (station, sample["name"], "input") not in edges


def test_a_synonym_points_at_the_thing_it_names(ladder: model.Ladder):
    """«Печь» is spoken shorthand for «Плавильная печь» -- one node, not two."""
    names = {node["name"] for node in ladder.nodes()}
    for spoken, real in ladder.synonyms.items():
        assert spoken not in names
        assert real in names


def test_raw_cost_expands_to_raw_material_only(ladder: model.Ladder, sample: dict):
    cost = ladder.raw_cost(sample["name"])
    assert cost["totals"]
    assert set(cost["totals"]) <= set(ladder.raw)
    assert cost["cycles"] == []
    assert cost["unknown"] == []
    assert cost["mass"] > 0


def test_a_tool_class_has_no_cost(ladder: model.Ladder):
    """«Кирка» is a requirement, not a thing: summing it would invent a pick."""
    for klass in ladder.tool_classes:
        if klass in ladder.recipes:
            continue
        assert ladder.raw_cost(klass)["totals"] == {}


# ------------------------------------------------------------------ stations


def test_every_station_says_where_it_is_assembled(ladder: model.Ladder):
    stations = {item["name"]: item for item in ladder.stations()}
    assert stations, "в вольте не нашлось ни одной рабочей станции"
    for item in stations.values():
        if item["virtual"]:
            assert item["parent"] is None
            continue
        assert item["parent"], f"«{item['name']}» не говорит, на чём собирается"
        assert item["parent"] in stations, f"«{item['name']}» собирается неизвестно на чём"


def test_a_station_lists_what_is_made_on_it(ladder: model.Ladder):
    stations = {item["name"]: item for item in ladder.stations()}
    for name, recipe in ladder.recipes.items():
        station = ladder.canon(recipe.get("station") or "Руками")
        assert name in stations[station]["makes"]
    # and every recipe is counted exactly once
    counted = sum(len(item["makes"]) for item in stations.values())
    assert counted == len(ladder.recipes)


def test_a_station_that_is_also_a_part_says_so(ladder: model.Ladder):
    """The press is a station and an input of the coin station at once."""
    stations = {item["name"]: item for item in ladder.stations()}
    for name, item in stations.items():
        users = [
            other["name"]
            for other in ladder.recipes.values()
            if any(ladder.canon(i) == name for i in other["inputs"])
        ]
        assert item["inputs_to"] == sorted(users)


def test_virtual_stations_are_there_but_are_not_recipes(ladder: model.Ladder):
    stations = {item["name"]: item for item in ladder.stations()}
    for name in vault.VIRTUAL_STATIONS:
        assert name in stations, f"«{name}» пропала: половина лестницы начинается на ней"
        assert stations[name]["editable"] is False
        assert stations[name]["depth"] == 0


# ---------------------------------------------------------------- refusals


def base(sample: dict, **rest) -> dict:
    return {
        "name": "Пробник",
        "kind": "material",
        "inputs": list(sample["inputs"]),
        "station": sample["station"],
        **rest,
    }


def test_a_recipe_that_makes_sense_passes(ladder: model.Ladder, sample: dict):
    model.validate(base(sample), ladder)


def test_a_name_already_taken_is_refused(ladder: model.Ladder, sample: dict):
    with pytest.raises(vault.VaultError, match="уже есть"):
        model.validate(base(sample, name=sample["name"]), ladder)


def test_a_name_taken_by_raw_material_is_refused(ladder: model.Ladder, sample: dict):
    with pytest.raises(vault.VaultError, match="сырьё"):
        model.validate(base(sample, name=ladder.raw[0]), ladder)


def test_an_input_nobody_makes_is_refused(ladder: model.Ladder, sample: dict):
    assert "Мифрил" not in ladder.known_names()
    with pytest.raises(vault.VaultError, match="не рецепт"):
        model.validate(base(sample, inputs=[*sample["inputs"], "Мифрил"]), ladder)


def test_a_station_nobody_makes_is_refused(ladder: model.Ladder, sample: dict):
    assert "Алтарь" not in ladder.known_names()
    with pytest.raises(vault.VaultError, match="станцию"):
        model.validate(base(sample, station="Алтарь"), ladder)


def test_quantities_for_only_some_inputs_are_refused(ladder: model.Ladder, sample: dict):
    """Partial `amounts` do not add to the derived ones -- they replace them.

    The input left without a number drops out of the composition entirely, and
    nothing downstream would say so.
    """
    inputs = sample["inputs"]
    with pytest.raises(vault.VaultError, match="не для всех входов"):
        model.validate(base(sample, amounts={inputs[0]: 2}), ladder)
    model.validate(base(sample, amounts=dict.fromkeys(inputs, 2)), ladder)


def test_a_twin_composition_on_the_same_station_is_refused(ladder: model.Ladder):
    """D-209: invention knows a recipe by its composition, so twins are illegal."""
    for name, recipe in ladder.recipes.items():
        amounts = (ladder.derived_recipes.get(name) or {}).get("amounts")
        if not amounts:
            continue
        twin = {
            "name": "Пробник",
            "kind": "material",
            "inputs": list(amounts),
            "amounts": dict(amounts),
            "station": recipe["station"],
        }
        with pytest.raises(vault.VaultError, match="D-209"):
            model.validate(twin, ladder)
        return
    pytest.skip("сборка не считала количеств — сравнивать нечего")


def test_an_empty_recipe_is_refused(ladder: model.Ladder, sample: dict):
    with pytest.raises(vault.VaultError, match="без входов"):
        model.validate(base(sample, inputs=[]), ladder)


def test_a_slot_that_does_not_exist_is_refused(ladder: model.Ladder, sample: dict):
    with pytest.raises(vault.VaultError, match="слота"):
        model.validate(base(sample, kind="gear", slot="голова"), ladder)


def test_references_see_through_a_spoken_name(ladder: model.Ladder):
    """An operation requires «Печь»; the recipe is «Плавильная печь».

    Reported literally, such a station reads as used nowhere -- and the delete
    dialog would offer to cut it without a word of warning.
    """
    for op in ladder.operations:
        for need in [*(op.get("consumes") or []), *op["requires"]]:
            found = model.references(ladder.canon(need), ladder)["operations"]
            assert op["name"] in found, f"«{need}» не признался, что нужен операции"


def test_references_find_every_mention(ladder: model.Ladder, sample: dict):
    for item in sample["inputs"]:
        assert sample["name"] in model.references(ladder.canon(item), ladder)["inputs"]
    assert sample["name"] in model.references(ladder.canon(sample["station"]), ladder)["stations"]
    for klass, members in ladder.tool_classes.items():
        assert klass in model.references(members[0], ladder)["classes"]
