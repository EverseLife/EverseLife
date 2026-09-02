# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The D-251 key table: Russian vault names -> stable English ids.

`build/renames.json` is produced by the vault build and travels with the
snapshot. Wave II uses it in exactly one place on the engine side: catalog and
constants NORMALIZATION at load. Past that point the engine speaks ids only --
`iron_ore`, never "Железная руда" -- so nothing downstream translates anything.

The inverse maps (`names_ru`) are served raw to the client via /public: until
the locale layer of wave III, they are how a player still reads Russian names
over an id-speaking wire.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.constants.spec import ConstantError


class RenameTable(BaseModel):
    """One domain map each: Russian name -> id. Frozen after load."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goods: dict[str, str] = Field(default_factory=dict)
    classes: dict[str, str] = Field(default_factory=dict)
    operations: dict[str, str] = Field(default_factory=dict)
    slots: dict[str, str] = Field(default_factory=dict)
    tiers: dict[str, str] = Field(default_factory=dict)
    building_kinds: dict[str, str] = Field(default_factory=dict)
    node_properties: dict[str, str] = Field(default_factory=dict)
    planets: dict[str, str] = Field(default_factory=dict)
    #: Crop cultures (D-057): «Полба» -> `spelt`. Their own domain -- a culture
    #: and its produce are different things with different names.
    plants: dict[str, str] = Field(default_factory=dict)
    #: Code-laws (D-094): «Налог с продажи» -> `tax_trade`. The wire carries the
    #: id and the reader sees the name -- in Discord, in the digest, in the panel.
    laws: dict[str, str] = Field(default_factory=dict)
    virtual_stations: dict[str, str] = Field(default_factory=dict)
    #: Each thing's name per language: domain -> id -> word. Russian is derived
    #: by inverting the maps above (the vault is written in Russian and the id
    #: is derived from the name); the others arrive as an overlay by id
    #: (`data/locales/<lang>.yaml`).
    names_ru: dict[str, dict[str, str]] = Field(default_factory=dict)
    names_en: dict[str, dict[str, str]] = Field(default_factory=dict)

    def named(self, locale: str) -> dict[str, dict[str, str]]:
        """The names of this language, falling back to the vault's own.

        A language the vault has no overlay for reads in Russian. That is the
        honest degradation and not a hole to be filled later: «Железная руда»
        to an English reader is an untranslated name, while `iron_ore` in the
        middle of an English sentence is a broken sentence. The vault's build
        refuses an incomplete overlay outright (`check_locales`), so the
        fallback is reached only by a language nobody has started.
        """
        return getattr(self, f"names_{locale}", None) or self.names_ru

    def goods_id(self, name: str) -> str:
        """Id of a thing by any of its spellings: id, Russian name, virtual
        station. Unknown -- a ConstantError, not a silent pass-through: a name
        that resolves to nothing is a vault/engine mismatch, and pretending it
        is an id would push the mismatch into the database.
        """
        found = self.goods.get(name) or self.virtual_stations.get(name)
        if found:
            return found
        if name in self._goods_ids:
            return name
        raise ConstantError(f"no stable key for the name {name!r} in renames.json")

    _goods_ids: set[str] = PrivateAttr(default_factory=set)

    def model_post_init(self, _: object) -> None:
        self._goods_ids.update(self.goods.values())
        self._goods_ids.update(self.virtual_stations.values())


class RenamesHolder:
    """The process's rename table. Loaded at startup, next to the catalogs."""

    def __init__(self) -> None:
        self._current: RenameTable | None = None

    def set(self, renames: RenameTable) -> None:
        self._current = renames

    def current(self) -> RenameTable:
        current = self._current
        if current is None:
            raise ConstantError("the stable key table is not loaded: bootstrap installs it")
        return current


RENAMES_HOLDER = RenamesHolder()


def current_renames() -> RenameTable:
    return RENAMES_HOLDER.current()


#: Which domains each message function looks in, in order.
#:
#: `NAME()` searches the thing-space, and those domains genuinely share one:
#: the vault names a class after its best member on purpose («Топор» is both a
#: class and a recipe), so one lookup is right there.
#:
#: The others get a function each because their ids **collide** with the
#: thing-space and mean something else there: `stone` is «Камень» among goods
#: and «каменный» among building kinds. One merged lookup would render one of
#: them wrong, silently, in a sentence nobody re-reads.
NAME_DOMAINS: dict[str, tuple[str, ...]] = {
    "NAME": ("goods", "virtual_stations", "classes", "operations", "node_properties"),
    "KIND": ("building_kinds",),
    "PLANET": ("planets",),
    "TIER": ("tiers",),
    "SLOT": ("slots",),
    #: Not `PLANT`, which reads as `PLANET` at a glance in a message file.
    #: A culture is not its produce: «Полба» is sown, «Зерно» is harvested.
    "CULTURE": ("plants",),
    #: A code-law by its id: `tax_trade` -> «Налог с продажи». Its own domain
    #: because law ids are short and general -- `access`, `salary`, `toll` --
    #: and a table shared with goods would one day answer with the wrong one.
    "LAW": ("laws",),
    #: The value a law that is a choice holds, by the composite key
    #: `<law>.<option>`: `citizens` reads differently under different laws.
    #: A value that is not a choice -- a rate, a sum -- is not in the table and
    #: comes back as itself, which is exactly what a number wants.
    "CHOICE": ("law_options",),
}


def display_name(key: str, locale: str = "ru", domain: str = "NAME") -> str:
    """The word a content id is read as, in this language (D-251).

    This is what a message's `NAME($goods)` -- or `KIND($kind)`, `PLANET($p)`
    -- calls: an id travels the wire, a word reaches the eye. Until the vault
    ships locale overlays (wave V) every language falls back to the Russian
    names the vault is written in: an English reader seeing «Железная руда» is
    an untranslated name, which is honest, while `iron_ore` in a sentence is
    a broken sentence.

    An id with no word is returned as it is -- a missing name must not swallow
    the refusal it was carrying.
    """
    table = RENAMES_HOLDER.current().named(locale)
    for area in NAME_DOMAINS.get(domain, NAME_DOMAINS["NAME"]):
        found = table.get(area, {}).get(key)
        if found:
            return found
    return key


def load_renames(build_dir: Path) -> RenameTable:
    path = Path(build_dir) / "renames.json"
    if not path.exists():
        raise ConstantError(
            f"{path} not found: the engine reads the stable key table from the "
            "snapshot; resync the vault (`python deploy/sync-vault.py`)"
        )
    with path.open(encoding="utf-8") as fh:
        return RenameTable.model_validate(json.load(fh))
