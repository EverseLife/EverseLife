# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Typed declarations of balance constants.

Rule D-065: not one balance number is hard-coded. In practice this means the
engine does not write `constants["mine.roof_start"]` anywhere it likes but
**declares** the needed constant here once -- with a key and an expected shape.

What this gives:

* a constant missing from `build/constants.json` breaks startup, not gameplay;
* a constant of an unexpected shape (a number instead of a range) breaks startup too;
* the list of all quantities the engine depends on exists in one place -- and
  it is checked against that no numbers past the registry remain in code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConstantError(Exception):
    """The constant is missing or has the wrong shape."""


@dataclass(frozen=True, slots=True)
class Range:
    min: float
    max: float

    @property
    def mid(self) -> float:
        """The middle of the scale: the "ordinary" value deviation is counted from."""
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
    """A formula from the vault.

    A simple expression the engine **evaluates** (`value`) rather than
    rewrites in code: otherwise numbers from the formula move into the engine
    and a balance edit starts requiring a release (D-065).

    A formula describing an algorithm -- with summation over levels,
    branching, randomness -- cannot be evaluated, and the engine implements it
    in code. The string then records which one exactly: a discrepancy is a
    question for a human, not a reason to silently compute otherwise
    (07-implementation-map).
    """

    text: str

    def value(self, **names: float) -> float:
        """Evaluate the formula, substituting the named quantities."""
        #: Lazy: `formula` reads these specs, the cycle closes otherwise.
        from src.constants.formula import evaluate  # noqa: PLC0415

        return evaluate(self.text, **names)


@dataclass(frozen=True, slots=True)
class Spec:
    """Declaration of one constant."""

    key: str

    def read(self, raw: Any) -> Any:  # pragma: no cover - overridden
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
    """`{"min": ..., "max": ...}`."""

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
    """A map `name -> number`: modifiers, role weights, sign bands."""

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
class Book(Spec):
    """A map `name -> (part -> number)`: a named composition, not one number.

    A building type is exactly that (D-218): `build.types` gives every type its
    own bill of materials, and a single multiplier over one shared recipe could
    never say what actually goes into the wall.
    """

    def read(self, raw: Any) -> dict[str, dict[str, float]]:
        if not isinstance(raw, dict) or not raw:
            raise self._fail(raw, "непустая карта имя → состав")
        out: dict[str, dict[str, float]] = {}
        for name, body in raw.items():
            if not isinstance(body, dict) or not body:
                raise self._fail(raw, f"состав в ключе {name!r}")
            parts: dict[str, float] = {}
            for part, value in body.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise self._fail(raw, f"число в {name!r} → {part!r}")
                parts[str(part)] = float(value)
            out[str(name)] = parts
        return out


@dataclass(frozen=True, slots=True)
class Tiers(Spec):
    """A list of tiers `{from, to, name}` -- the quality shop window."""

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
    """`{"formula": "..."}` -- the engine must implement it in code."""

    def read(self, raw: Any) -> Formula:
        if not isinstance(raw, dict) or "formula" not in raw:
            raise self._fail(raw, "{formula: …}")
        return Formula(str(raw["formula"]))
