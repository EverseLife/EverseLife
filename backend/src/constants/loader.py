# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A snapshot of balance constants and its hot replacement.

D-065 demands two things at once: numbers are not hard-coded and they change
**without a release**. Hence the construction:

* `Constants` -- an immutable snapshot. It either assembled whole or not at
  all: there are no partially valid constants;
* `ConstantsHolder` -- the only mutable cell per process. Snapshot replacement
  is atomic, a reader always sees a consistent set;
* on top of the file lie admin-panel edits (`overrides`), and each must be
  written to the change journal -- the storage layer does that, not this module.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from src.constants.renames import RenameTable
from src.constants.spec import ConstantError, Spec

T = TypeVar("T")


def _translate_keys(value: Any, table: Mapping[str, str]) -> Any:
    """Dict keys through the D-251 rename table, recursively. Values stay."""
    if isinstance(value, dict):
        return {table.get(k, k): _translate_keys(v, table) for k, v in value.items()}
    if isinstance(value, list):
        return [_translate_keys(v, table) for v in value]
    return value


def rename_key_table(renames: RenameTable) -> dict[str, str]:
    """One merged key-translation map for constants sub-dicts.

    `harvest.rates` keys by goods, `transport.speed_k` by classes,
    `wear.environment_k` by planets, `build.types` by building kinds -- the
    domains are disjoint in their Russian spellings, and that disjointness is
    checked here rather than assumed: one word mapping two ways would translate
    silently and differently depending on merge order.
    """
    merged: dict[str, str] = {}
    for domain in (
        renames.goods,
        renames.classes,
        renames.planets,
        renames.building_kinds,
        renames.virtual_stations,
    ):
        for name, entry_id in domain.items():
            if merged.get(name, entry_id) != entry_id:
                raise ConstantError(
                    f"the word {name!r} translates to two keys: {merged[name]!r} and {entry_id!r}"
                )
            merged[name] = entry_id
    return merged


def _mark(renames: RenameTable, word: str) -> str:
    """A node-property word as its id, or a refusal to start.

    Loudly, because the quiet version is invisible: an untranslated mark would
    match no node ever, and the thing bound to it would simply stop existing in
    the world -- a typo in the vault reading as a design decision.
    """
    found = renames.node_properties.get(word)
    if found is None:
        raise ConstantError(f"forage.place: unknown node property {word!r}")
    return found


def normalize_constants(raw: Mapping[str, Any], renames: RenameTable) -> dict[str, Any]:
    """constants.json with Russian sub-dict keys translated to D-251 ids.

    The vault still emits name-keyed tables; this is the constants side of the
    same load-time seam as the catalog's. Special case: `quality.tiers` names
    a tier in a VALUE field, and market rows store that word -- so it turns
    into the tier id here.
    """
    table = rename_key_table(renames)
    #: Three tables key by LOWERCASED vault words rather than the names
    #: themselves: where a chat leaks ("кузница", "библиотека") and what a
    #: vehicle class carries and how fast ("тачка", "повозка"). The lowercase
    #: forms are admitted for them alone; a word with no class yet ("судно")
    #: stays as written and becomes reachable when its class arrives.
    lowered = {name.lower(): entry_id for name, entry_id in table.items()}
    lowercase_keyed = (
        "chat.leak_location_modifier",
        "transport.speed_k",
        "transport.capacity",
    )
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key in lowercase_keyed and isinstance(value, dict):
            out[key] = {lowered.get(k, table.get(k, k)): v for k, v in value.items()}
            continue
        if key == "forage.place" and isinstance(value, dict):
            #: The one table whose VALUE is a place mark rather than a number
            #: (D-254). Its keys are goods and go through the merged table
            #: like any other; its values live in their own domain, and
            #: translating them with the goods map would leave them Russian.
            out[key] = {table.get(name, name): _mark(renames, mark) for name, mark in value.items()}
            continue
        if key == "quality.tiers" and isinstance(value, list):
            value = [
                {**tier, "name": renames.tiers.get(tier.get("name"), tier.get("name"))}
                if isinstance(tier, dict)
                else tier
                for tier in value
            ]
        out[key] = _translate_keys(value, table)
    return out


class Constants:
    """A consistent snapshot of `build/constants.json` plus edits on top of it."""

    __slots__ = ("_raw", "_cache", "_digest", "_source")

    def __init__(self, raw: Mapping[str, Any], source: str = "?") -> None:
        self._raw: dict[str, Any] = dict(raw)
        self._cache: dict[str, Any] = {}
        self._source = source
        payload = json.dumps(self._raw, sort_keys=True, ensure_ascii=False).encode()
        self._digest = hashlib.sha256(payload).hexdigest()[:16]

    @property
    def digest(self) -> str:
        """The set's fingerprint. Written into events -- by it one sees which
        numbers an episode was played on once the numbers were later changed."""
        return self._digest

    @property
    def source(self) -> str:
        return self._source

    def __getitem__(self, spec: Spec) -> Any:
        cached = self._cache.get(spec.key)
        if cached is not None:
            return cached
        if spec.key not in self._raw:
            raise ConstantError(f"{spec.key}: not in the constant set ({self._source})")
        value = spec.read(self._raw[spec.key])
        self._cache[spec.key] = value
        return value

    def get(self, spec: Spec) -> Any:
        return self[spec]

    def has(self, key: str) -> bool:
        return key in self._raw

    def raw(self) -> Mapping[str, Any]:
        return self._raw

    def with_overrides(self, overrides: Mapping[str, Any]) -> Constants:
        """A new snapshot with edits on top. The original does not change."""
        unknown = set(overrides) - set(self._raw)
        if unknown:
            raise ConstantError(
                "the edit references constants that do not exist: " + ", ".join(sorted(unknown))
            )
        return Constants({**self._raw, **overrides}, source=f"{self._source}+overrides")

    def validate(self, specs: Iterable[Spec]) -> None:
        """Check the declared constants at once.

        Reports **all** problems at once: fixing the set one error per restart
        is the case where startup should fail once.
        """
        problems: list[str] = []
        for spec in specs:
            try:
                self[spec]
            except ConstantError as exc:
                problems.append(str(exc))
        if problems:
            raise ConstantError(
                f"the constant set is unusable ({self._source}):\n  " + "\n  ".join(problems)
            )


def load_constants(build_dir: Path, renames: RenameTable) -> Constants:
    path = Path(build_dir) / "constants.json"
    if not path.exists():
        raise ConstantError(
            f"{path} not found. The engine reads only build/ of the vault; "
            "build it with `python tools/build.py` in the game-design vault"
        )
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ConstantError(f"{path}: expected a flat map of key -> value")
    return Constants(normalize_constants(raw, renames), source=str(path))


class ConstantsHolder:
    """The process's current snapshot. Replacement is atomic."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Constants | None = None

    def set(self, constants: Constants) -> None:
        with self._lock:
            self._current = constants

    def current(self) -> Constants:
        current = self._current
        if current is None:
            raise ConstantError(
                "constants are not loaded: the engine must load them at startup, not on demand"
            )
        return current

    def is_loaded(self) -> bool:
        return self._current is not None


HOLDER = ConstantsHolder()


def current() -> Constants:
    return HOLDER.current()
