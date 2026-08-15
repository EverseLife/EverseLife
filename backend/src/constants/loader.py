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

from src.constants.spec import ConstantError, Spec

T = TypeVar("T")


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
            raise ConstantError(f"{spec.key}: нет в наборе констант ({self._source})")
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
                "правка ссылается на несуществующие константы: " + ", ".join(sorted(unknown))
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
                f"набор констант непригоден ({self._source}):\n  " + "\n  ".join(problems)
            )


def load_constants(build_dir: Path) -> Constants:
    path = Path(build_dir) / "constants.json"
    if not path.exists():
        raise ConstantError(
            f"не найден {path}. Движок читает только build/ вольта; "
            f"собери его командой `python tools/build.py` в вольте гейм-дизайна"
        )
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ConstantError(f"{path}: ожидалась плоская карта ключ → значение")
    return Constants(raw, source=str(path))


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
                "константы не загружены: движок обязан загрузить их при старте, "
                "а не по требованию"
            )
        return current

    def is_loaded(self) -> bool:
        return self._current is not None


HOLDER = ConstantsHolder()


def current() -> Constants:
    return HOLDER.current()
