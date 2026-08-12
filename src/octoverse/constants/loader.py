"""Снимок балансных констант и его горячая замена.

D-065 требует двух вещей сразу: числа не зашиты в код и меняются **без выката
версии**. Отсюда конструкция:

* `Constants` — неизменяемый снимок. Он либо собрался целиком, либо не собрался
  вовсе: частично валидных констант не бывает;
* `ConstantsHolder` — единственная изменяемая ячейка на процесс. Замена снимка
  атомарна, читатель всегда видит согласованный набор;
* поверх файла ложатся правки админ-панели (`overrides`), и каждая обязана быть
  записана в журнал изменений — это делает слой хранения, не этот модуль.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from octoverse.constants.spec import ConstantError, Spec

T = TypeVar("T")


class Constants:
    """Согласованный снимок `build/constants.json` плюс правки поверх него."""

    __slots__ = ("_raw", "_cache", "_digest", "_source")

    def __init__(self, raw: Mapping[str, Any], source: str = "?") -> None:
        self._raw: dict[str, Any] = dict(raw)
        self._cache: dict[str, Any] = {}
        self._source = source
        payload = json.dumps(self._raw, sort_keys=True, ensure_ascii=False).encode()
        self._digest = hashlib.sha256(payload).hexdigest()[:16]

    @property
    def digest(self) -> str:
        """Отпечаток набора. Пишется в события — по нему видно, на каких
        числах игрался эпизод, когда числа потом поменяли."""
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
        """Новый снимок с правками поверх. Исходный не меняется."""
        unknown = set(overrides) - set(self._raw)
        if unknown:
            raise ConstantError(
                "правка ссылается на несуществующие константы: " + ", ".join(sorted(unknown))
            )
        return Constants({**self._raw, **overrides}, source=f"{self._source}+overrides")

    def validate(self, specs: Iterable[Spec]) -> None:
        """Проверить объявленные константы разом.

        Сообщает **все** проблемы сразу: чинить набор по одной ошибке за
        перезапуск — это тот случай, когда старт должен падать один раз.
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
    """Текущий снимок процесса. Замена атомарна."""

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
