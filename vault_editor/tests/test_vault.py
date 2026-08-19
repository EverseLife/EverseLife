"""What the editor promises about the file it writes.

Nothing here names a recipe: the vault is a living document, and a test tied to
«Дикий лён» starts failing the day it is renamed to «Лён». Each test finds its
own example in the file and asserts the rule.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
import vaultfile as vault
import yaml


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def lines_of(path: Path) -> list[str]:
    return read(path).replace("\r\n", "\n").split("\n")


def diff_count(before: list[str], after: list[str]) -> int:
    return sum(1 for a, b in zip(before, after, strict=False) if a != b) + abs(
        len(before) - len(after)
    )


# ----------------------------------------------------------------- rendering


def test_every_recipe_line_renders_back_identically(recipes: Path):
    """The writer speaks the file's own dialect.

    If this ever fails, saving an untouched recipe would rewrite its line, and
    every edit would carry noise into the diff.
    """
    file = vault.RecipesFile(recipes)
    for recipe in file.recipes():
        data = {k: v for k, v in recipe.items() if k not in ("level", "section")}
        entry = file.entries[recipe["name"]]
        rendered = vault.render_entry(data, entry.indent)
        assert rendered == "\n".join(file.lines[entry.start : entry.end + 1])


@pytest.mark.parametrize(
    "value",
    ["Простое", "С запятой, внутри", "двоеточие: и пробел", "1.5", "true", "", "[скобки]"],
)
def test_scalars_survive_the_round_trip(value: str):
    line = vault.render_entry({"name": value}, 0)
    assert yaml.safe_load(line[2:])["name"] == value


def test_numbers_are_written_without_an_exponent():
    line = vault.render_entry({"name": "X", "mass": 0.001, "store": 300}, 0)
    assert "0.001" in line and "300" in line and "e-" not in line
    back = yaml.safe_load(line[2:])
    assert back["mass"] == 0.001
    assert back["store"] == 300


# -------------------------------------------------------------------- edits


def test_replace_touches_one_line_and_keeps_line_endings(recipes: Path):
    before = lines_of(recipes)
    was_crlf = b"\r\n" in recipes.read_bytes()

    file = vault.RecipesFile(recipes)
    data = {k: v for k, v in file.recipes()[0].items() if k not in ("level", "section")}
    data["note"] = "правка"
    lines = file.replace(data["name"], data)
    vault.save(recipes, lines, {"name": data["name"], "data": data}, file.mtime, file.newline)

    after = lines_of(recipes)
    assert len(before) == len(after)
    assert diff_count(before, after) == 1
    assert (b"\r\n" in recipes.read_bytes()) is was_crlf


def test_insert_lands_right_after_the_last_recipe_of_its_level(recipes: Path):
    file = vault.RecipesFile(recipes)
    level = next(lvl for lvl in file.levels() if lvl["plain"])
    neighbours = [name for name, group in file.groups.items() if group == (level["id"], None)]
    last = max((file.entries[name] for name in neighbours), key=lambda entry: entry.end)

    data = {"name": "Пробник", "kind": "material", "inputs": [file.meta()["raw"][0]],
            "station": "Руками"}
    lines = file.insert(data, level["id"], None)
    vault.save(recipes, lines, {"name": data["name"], "data": data}, file.mtime, file.newline)

    again = vault.RecipesFile(recipes)
    assert again.groups["Пробник"] == (level["id"], None)
    assert again.entries["Пробник"].start == last.end + 1
    assert again.entries["Пробник"].indent == last.indent


def test_insert_into_a_level_of_sections_is_refused(recipes: Path):
    file = vault.RecipesFile(recipes)
    level = next((lvl for lvl in file.levels() if lvl["sections"] and not lvl["plain"]), None)
    if level is None:
        pytest.skip("в файле нет уровня, состоящего только из разделов")

    data = {"name": "Пробник", "kind": "material", "inputs": [file.meta()["raw"][0]],
            "station": "Руками"}
    with pytest.raises(vault.VaultError, match="разделах"):
        file.insert(data, level["id"], None)

    section = level["sections"][0]["id"]
    lines = file.insert(data, level["id"], section)
    vault.save(recipes, lines, {"name": "Пробник", "data": data}, file.mtime, file.newline)
    assert vault.RecipesFile(recipes).groups["Пробник"] == (level["id"], section)


def test_cut_removes_the_line_and_optionally_its_comment(recipes: Path):
    file = vault.RecipesFile(recipes)
    # a recipe the file explains with a comment right above it
    named = next(name for name in file.entries if file.comment_above(name) and name in file.groups)
    comment = file.comment_above(named)

    lines = file.cut(named)
    vault.save(recipes, lines, {"name": named, "data": None}, file.mtime, file.newline)
    kept = vault.RecipesFile(recipes)
    assert named not in kept.entries
    assert "\n".join(comment) in read(recipes).replace("\r\n", "\n")

    file = vault.RecipesFile(recipes)
    other = next(name for name in file.entries if file.comment_above(name) and name in file.groups)
    other_comment = file.comment_above(other)
    lines = file.cut(other, with_comment=True)
    vault.save(recipes, lines, {"name": other, "data": None}, file.mtime, file.newline)
    assert "\n".join(other_comment) not in read(recipes).replace("\r\n", "\n")


def test_a_stale_file_is_not_overwritten(recipes: Path):
    file = vault.RecipesFile(recipes)
    data = {k: v for k, v in file.recipes()[0].items() if k not in ("level", "section")}
    recipes.write_bytes(recipes.read_bytes() + "\n# кто-то другой правил файл\n".encode())
    with pytest.raises(vault.VaultError, match="изменился на диске"):
        vault.save(recipes, file.replace(data["name"], data), {"name": data["name"], "data": data},
                   file.mtime, file.newline)


def test_save_refuses_when_the_document_did_not_change_as_asked(recipes: Path):
    file = vault.RecipesFile(recipes)
    data = {"name": "Небылица", "kind": "material", "inputs": [file.meta()["raw"][0]],
            "station": "Руками"}
    with pytest.raises(vault.VaultError, match="не появился"):
        vault.save(recipes, file.lines, {"name": data["name"], "data": data}, file.mtime)


# ------------------------------------------------------------------- rename


def much_used(file: vault.RecipesFile) -> str:
    """The recipe other recipes lean on most: renaming it touches the most lines."""
    recipes = {r["name"] for r in file.recipes()}
    uses = Counter(i for r in file.recipes() for i in r["inputs"] if i in recipes)
    if not uses:
        pytest.skip("ни один рецепт не входит в другой")
    return uses.most_common(1)[0][0]


def test_rename_updates_every_mention(recipes: Path):
    file = vault.RecipesFile(recipes)
    old = much_used(file)

    text = vault.rename_everywhere(file.text, old, "Черенок")
    flat = yaml.safe_dump(yaml.safe_load(text), allow_unicode=True)
    assert "Черенок" in flat
    assert not re.search(rf"(?<![\w-]){re.escape(old)}(?![\w-])", flat)


def test_rename_leaves_comments_untouched(recipes: Path):
    """Comments explain the design in prose, and prose is not data.

    A name inside a sentence may stand in a different case or a different word;
    the sweep stays out of comment lines entirely.
    """
    file = vault.RecipesFile(recipes)
    text = vault.rename_everywhere(file.text, much_used(file), "Черенок")

    def comments(source: str) -> list[str]:
        return [line for line in source.split("\n") if line.lstrip().startswith("#")]

    assert comments(text) == comments(file.text)


def test_rename_to_a_name_needing_quotes_is_refused(recipes: Path):
    file = vault.RecipesFile(recipes)
    with pytest.raises(vault.VaultError, match="кавычки"):
        vault.rename_everywhere(file.text, much_used(file), "Черенок, длинный")


# --------------------------------------------------------------------- undo


def test_undo_walks_back_one_edit(recipes: Path):
    original = read(recipes)
    file = vault.RecipesFile(recipes)
    data = {k: v for k, v in file.recipes()[0].items() if k not in ("level", "section")}
    data["note"] = "правка"
    vault.save(recipes, file.replace(data["name"], data), {"name": data["name"], "data": data},
               file.mtime, file.newline)
    assert read(recipes) != original
    vault.undo(recipes)
    assert read(recipes) == original
