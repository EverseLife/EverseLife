"""Типизированные объявления балансных констант.

Правило D-065: ни одно балансное число не зашито в код. Практически это значит,
что движок не пишет `constants["mine.roof_start"]` где попало, а **объявляет**
нужную константу здесь один раз — с ключом и ожидаемой формой.

Что это даёт:

* константы, которой нет в `build/constants.json`, ломают старт, а не бой;
* константа неожиданной формы (число вместо диапазона) ломает старт тоже;
* список всех величин, от которых зависит движок, существует в одном месте —
  и по нему проверяется, что в коде не осталось чисел мимо реестра.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConstantError(Exception):
    """Константа отсутствует или имеет не ту форму."""


@dataclass(frozen=True, slots=True)
class Range:
    min: float
    max: float

    @property
    def mid(self) -> float:
        """Середина шкалы: «обычное» значение, от которого считают отклонение."""
        return (self.min + self.max) / 2

    def clamp(self, value: float) -> float:
        return max(self.min, min(self.max, value))

    def contains(self, value: float) -> bool:
        return self.min <= value <= self.max


@dataclass(frozen=True, slots=True)
class Tier:
    frm: float
    to: float
    name: str


@dataclass(frozen=True, slots=True)
class Formula:
    """Формула из вольта.

    Простое выражение движок **вычисляет** (`value`), а не переписывает кодом:
    иначе числа из формулы переезжают в движок и правка баланса начинает
    требовать выката версии (D-065).

    Формула, описывающая алгоритм — с суммированием по этажам, ветвлением,
    случайностью, — вычислена быть не может, и её движок реализует кодом. Строка
    тогда фиксирует, какую именно: расхождение — вопрос к человеку, а не повод
    молча посчитать иначе (07-implementation-map).
    """

    text: str

    def value(self, **names: float) -> float:
        """Посчитать формулу, подставив названные величины."""
        from src.constants.formula import evaluate

        return evaluate(self.text, **names)


@dataclass(frozen=True, slots=True)
class Spec:
    """Объявление одной константы."""

    key: str

    def read(self, raw: Any) -> Any:  # pragma: no cover - переопределяется
        raise NotImplementedError

    def _fail(self, raw: Any, expected: str) -> ConstantError:
        return ConstantError(f"{self.key}: ожидалось {expected}, получено {raw!r}")


@dataclass(frozen=True, slots=True)
class Num(Spec):
    def read(self, raw: Any) -> float:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise self._fail(raw, "число")
        return float(raw)


@dataclass(frozen=True, slots=True)
class Flag(Spec):
    def read(self, raw: Any) -> bool:
        if not isinstance(raw, bool):
            raise self._fail(raw, "true/false")
        return raw


@dataclass(frozen=True, slots=True)
class Text(Spec):
    def read(self, raw: Any) -> str:
        if not isinstance(raw, str):
            raise self._fail(raw, "строка")
        return raw


@dataclass(frozen=True, slots=True)
class Span(Spec):
    """`{"min": …, "max": …}`."""

    def read(self, raw: Any) -> Range:
        if not isinstance(raw, dict) or "min" not in raw or "max" not in raw:
            raise self._fail(raw, "{min, max}")
        lo, hi = raw["min"], raw["max"]
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            raise self._fail(raw, "{min, max} с числами")
        if lo > hi:
            raise self._fail(raw, "{min, max} с min <= max")
        return Range(float(lo), float(hi))


@dataclass(frozen=True, slots=True)
class Table(Spec):
    """Карта `имя → число`: модификаторы, веса ролей, полосы признаков."""

    def read(self, raw: Any) -> dict[str, float]:
        if not isinstance(raw, dict):
            raise self._fail(raw, "карта имя → число")
        out: dict[str, float] = {}
        for name, value in raw.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise self._fail(raw, f"число в ключе {name!r}")
            out[str(name)] = float(value)
        return out


@dataclass(frozen=True, slots=True)
class Tiers(Spec):
    """Список ступеней `{from, to, name}` — витрина качества."""

    def read(self, raw: Any) -> tuple[Tier, ...]:
        if not isinstance(raw, list) or not raw:
            raise self._fail(raw, "непустой список ступеней")
        tiers = []
        for item in raw:
            if not isinstance(item, dict) or not {"from", "to", "name"} <= item.keys():
                raise self._fail(raw, "ступени {from, to, name}")
            tiers.append(Tier(float(item["from"]), float(item["to"]), str(item["name"])))
        return tuple(tiers)


@dataclass(frozen=True, slots=True)
class FormulaRef(Spec):
    """`{"formula": "…"}` — движок обязан реализовать её кодом."""

    def read(self, raw: Any) -> Formula:
        if not isinstance(raw, dict) or "formula" not in raw:
            raise self._fail(raw, "{formula: …}")
        return Formula(str(raw["formula"]))
