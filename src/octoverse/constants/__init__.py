"""Балансные константы и каталоги — единственный источник чисел (D-065).

Движок не хранит собственных числовых значений: всё приходит из `build/` вольта
гейм-дизайна. Здесь — загрузка, типизация и горячая замена.
"""

from __future__ import annotations

from pathlib import Path

from octoverse.constants.catalog import Catalog, load_catalog
from octoverse.constants.loader import HOLDER, Constants, current, load_constants
from octoverse.constants.registry import declared
from octoverse.constants.spec import ConstantError, Formula, Range, Tier

__all__ = [
    "HOLDER",
    "Catalog",
    "ConstantError",
    "Constants",
    "Formula",
    "Range",
    "Tier",
    "bootstrap",
    "current",
    "load_catalog",
    "load_constants",
]


def bootstrap(build_dir: Path, overrides: dict | None = None) -> tuple[Constants, Catalog]:
    """Загрузить и проверить всё, от чего зависит движок.

    Вызывается при старте процесса. Константы, которой нет, обязана падать
    здесь — а не в бою (01-tech-notes, паттерн 4).
    """
    constants = load_constants(build_dir)
    if overrides:
        constants = constants.with_overrides(overrides)
    constants.validate(declared())
    catalog = load_catalog(build_dir)
    HOLDER.set(constants)
    return constants, catalog
