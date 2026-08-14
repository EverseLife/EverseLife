"""Балансные константы и каталоги — единственный источник чисел (D-065).

Движок не хранит собственных числовых значений: всё приходит из `build/` вольта
гейм-дизайна. Здесь — загрузка, типизация и горячая замена.
"""

from __future__ import annotations

from pathlib import Path

from src.constants.catalog import (
    CATALOG_HOLDER,
    Catalog,
    current_catalog,
    load_catalog,
)
from src.constants.loader import HOLDER, Constants, current, load_constants
from src.constants.registry import declared
from src.constants.spec import ConstantError, Formula, Range, Tier

__all__ = [
    "CATALOG_HOLDER",
    "HOLDER",
    "Catalog",
    "ConstantError",
    "Constants",
    "Formula",
    "Range",
    "Tier",
    "bootstrap",
    "current",
    "current_catalog",
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
    CATALOG_HOLDER.set(catalog)
    return constants, catalog
