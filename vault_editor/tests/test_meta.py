"""Editing the `meta` block: how a thing is measured.

`bulk` decides whether a quantity may be fractional (D-212), `units` says what
word to draw beside the number. Both are lists of names living among comments
that explain the choice, so the same rule holds as for a recipe line: touch one
line, leave the rest of the file alone.
"""

from __future__ import annotations

import copy
from difflib import SequenceMatcher
from pathlib import Path

import pytest
import vaultfile as vault
import yaml


def doc_of(path: Path) -> dict:
    return yaml.safe_load(path.read_bytes().decode("utf-8"))


def written(path: Path, lines: list[str], expect: dict) -> dict:
    file = vault.RecipesFile(path)
    vault.save_doc(path, lines, expect, file.mtime, file.newline)
    return doc_of(path)


def changed_lines(before: str, after: str) -> int:
    """Lines added plus lines removed.

    Positional comparison would not do: one inserted line shifts everything
    below it and would count as hundreds.
    """
    old = before.replace("\r\n", "\n").split("\n")
    new = after.replace("\r\n", "\n").split("\n")
    return sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new).get_opcodes()
        if tag != "equal"
    )


# ---------------------------------------------------------------------- bulk


def test_a_thing_becomes_fractional_by_one_line(recipes: Path):
    before = recipes.read_bytes().decode("utf-8")
    file = vault.RecipesFile(recipes)
    name = next(r["name"] for r in file.recipes() if r["name"] not in file.meta()["bulk"])

    expect = copy.deepcopy(file.doc)
    expect["meta"]["bulk"] = [*expect["meta"]["bulk"], name]
    doc = written(recipes, vault.MetaBlock(file, "bulk").toggle(name, True), expect)

    assert name in doc["meta"]["bulk"]
    assert changed_lines(before, recipes.read_bytes().decode("utf-8")) == 1


def test_a_thing_becomes_whole_again(recipes: Path):
    file = vault.RecipesFile(recipes)
    name = file.meta()["bulk"][0]

    expect = copy.deepcopy(file.doc)
    expect["meta"]["bulk"] = [item for item in expect["meta"]["bulk"] if item != name]
    doc = written(recipes, vault.MetaBlock(file, "bulk").toggle(name, False), expect)

    assert name not in doc["meta"]["bulk"]


def test_toggling_what_is_already_so_changes_nothing(recipes: Path):
    file = vault.RecipesFile(recipes)
    name = file.meta()["bulk"][0]
    assert vault.MetaBlock(file, "bulk").toggle(name, True) == file.lines


def test_a_thing_already_fractional_keeps_its_place(recipes: Path):
    """Position in the list is part of the file.

    Re-saving something already listed must not move it to the end: the write is
    verified by comparing whole documents, and a moved line is a changed document.
    """
    file = vault.RecipesFile(recipes)
    listed = file.meta()["bulk"]
    name = listed[len(listed) // 2]
    lines = vault.MetaBlock(file, "bulk").toggle(name, True)
    assert yaml.safe_load("\n".join(lines))["meta"]["bulk"] == listed


def test_an_emptied_list_stays_a_list(tmp_path: Path):
    """`bulk:` with nothing under it reads as null, not as zero things."""
    path = tmp_path / "recipes.yaml"
    path.write_text("meta:\n  bulk:\n    - Камень\nlevels: []\n", encoding="utf-8")
    file = vault.RecipesFile(path)
    lines = vault.MetaBlock(file, "bulk").toggle("Камень", False)
    assert yaml.safe_load("\n".join(lines))["meta"]["bulk"] == []


# --------------------------------------------------------------------- units


def test_a_unit_is_written_and_taken_back(recipes: Path):
    file = vault.RecipesFile(recipes)
    name = file.recipes()[0]["name"]

    expect = copy.deepcopy(file.doc)
    expect["meta"]["units"] = {**(expect["meta"].get("units") or {}), name: "м"}
    doc = written(recipes, vault.MetaBlock(file, "units").put(name, "м"), expect)
    assert doc["meta"]["units"][name] == "м"

    file = vault.RecipesFile(recipes)
    expect = copy.deepcopy(file.doc)
    expect["meta"]["units"] = {}
    doc = written(recipes, vault.MetaBlock(file, "units").put(name, None), expect)
    assert doc["meta"]["units"] == {}


def test_a_unit_is_replaced_not_doubled(recipes: Path):
    file = vault.RecipesFile(recipes)
    name = file.recipes()[0]["name"]
    lines = vault.MetaBlock(file, "units").put(name, "м")

    file = vault.RecipesFile(recipes, text="\n".join(lines))
    lines = vault.MetaBlock(file, "units").put(name, "л")
    doc = yaml.safe_load("\n".join(lines))
    assert doc["meta"]["units"] == {name: "л"}
    # counted inside the block only: `meta.mass` names the same things
    block = vault.MetaBlock(vault.RecipesFile(recipes, text="\n".join(lines)), "units")
    assert sum(1 for line in lines[block.start : block.end + 1] if name in line) == 1


def test_a_unit_with_a_colon_is_quoted_not_broken(recipes: Path):
    file = vault.RecipesFile(recipes)
    name = file.recipes()[0]["name"]
    lines = vault.MetaBlock(file, "units").put(name, "шт: пара")
    assert yaml.safe_load("\n".join(lines))["meta"]["units"][name] == "шт: пара"


# ---------------------------------------------------------------------- mass


def test_the_mass_of_raw_material_is_writable(recipes: Path):
    """Raw mass is the floor of the whole system: everything made is capped by it."""
    file = vault.RecipesFile(recipes)
    name = file.meta()["raw"][0]

    expect = copy.deepcopy(file.doc)
    expect["meta"]["mass"] = {**(expect["meta"].get("mass") or {}), name: 2.5}
    doc = written(recipes, vault.MetaBlock(file, "mass").put(name, 2.5), expect)
    assert doc["meta"]["mass"][name] == 2.5


def test_a_weightless_thing_keeps_its_zero(recipes: Path):
    """Energy weighs nothing, and that is a statement, not a missing number."""
    file = vault.RecipesFile(recipes)
    name = file.meta()["raw"][0]
    lines = vault.MetaBlock(file, "mass").put(name, 0)
    assert yaml.safe_load("\n".join(lines))["meta"]["mass"][name] == 0


def test_a_mass_written_and_then_dropped(recipes: Path):
    file = vault.RecipesFile(recipes)
    name = file.meta()["raw"][0]
    lines = vault.MetaBlock(file, "mass").put(name, None)
    assert name not in yaml.safe_load("\n".join(lines))["meta"]["mass"]


def test_a_recipe_may_be_measured_but_not_weighed_here(recipes: Path, source: Path):
    """Unit and fractionality belong to any thing; mass belongs to the recipe line.

    A second weight in `meta` would quietly win over the one written beside the
    recipe, so the refusal fires on the mass alone -- not on the recipe.
    """
    import ladder as model
    import server

    derived, _ = model.load_derived(source.parent.parent)
    ladder = model.Ladder(vault.RecipesFile(recipes), derived)
    name = next(iter(ladder.recipes))

    assert server._mass_for(name, None, ladder) is None
    with pytest.raises(vault.VaultError, match="рецепт"):
        server._mass_for(name, 3, ladder)

    raw = ladder.raw[0]
    assert server._mass_for(raw, 2.5, ladder) == 2.5
    assert server._mass_for(raw, 0, ladder) == 0
    with pytest.raises(vault.VaultError, match="не меньше нуля"):
        server._mass_for(raw, -1, ladder)


# ------------------------------------------------------------------ guarding


def test_a_wrong_expectation_stops_the_write(recipes: Path):
    """The whole document is compared, so a slip in line arithmetic cannot pass."""
    file = vault.RecipesFile(recipes)
    name = file.recipes()[0]["name"]
    lines = vault.MetaBlock(file, "units").put(name, "м")
    with pytest.raises(vault.VaultError, match="не так, как задумано"):
        vault.save_doc(recipes, lines, file.doc, file.mtime, file.newline)


def test_the_rest_of_the_file_is_left_alone(recipes: Path):
    """Comments around the block explain the design; a unit must not disturb them."""
    before = recipes.read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
    file = vault.RecipesFile(recipes)
    name = file.recipes()[0]["name"]
    lines = vault.MetaBlock(file, "units").put(name, "м")
    comments = [line for line in lines if line.lstrip().startswith("#")]
    assert comments == [line for line in before if line.lstrip().startswith("#")]
