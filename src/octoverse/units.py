"""Единицы представления — не балансные числа.

Здесь живут только величины, определяющие, **как мы храним и считаем**, а не
**сколько чего стоит в игре**. Любое число, влияющее на баланс, обязано лежать
в `build/constants.json` (D-065) и приходить через `octoverse.constants`.

Именно поэтому модуль вынесен из-под проверки на магические числа
(`tests/test_no_magic_numbers.py`): числа отсюда невозможно «сбалансировать».
"""

from __future__ import annotations

from decimal import Decimal

#: Деньги хранятся целыми минорными единицами: 1 ТК = 10 000 единиц.
#: Целые числа исключают ошибки округления в двойной записи — ни одна проводка
#: не может «потерять копейку» (см. 01-tech-notes, паттерн 2).
MONEY_SCALE = 10_000

#: Процентная шкала. Константы вида `craft.waste_share` заданы в процентах.
PERCENT = 100.0

#: Шкала качества и состояния предметов: 0…100 (20-systems/15-quality).
#: Границы шкалы — представление, а не баланс: балансные значения внутри неё
#: (пороги ступеней, потери при ремонте) лежат в `quality.*`.
SCALE_MIN = 0.0
SCALE_MAX = 100.0

#: Количества сырья хранятся целыми тысячными единицы — руда бывает дробной,
#: а плавающая точка в остатках жилы недопустима.
AMOUNT_SCALE = 1_000


def money(value: Decimal | int | float | str) -> int:
    """ТК → минорные единицы. Округление банковское, к ближайшему чётному."""
    return int((Decimal(str(value)) * MONEY_SCALE).to_integral_value())


def money_str(minor: int) -> str:
    """Минорные единицы → строка для показа игроку."""
    return f"{Decimal(minor) / MONEY_SCALE:.4f}".rstrip("0").rstrip(".")


def amount(value: Decimal | int | float | str) -> int:
    """Штуки/килограммы → внутренние целые единицы."""
    return int((Decimal(str(value)) * AMOUNT_SCALE).to_integral_value())


def amount_float(internal: int) -> float:
    return internal / AMOUNT_SCALE
