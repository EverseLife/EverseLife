"""Проверки слоя констант.

Смысл этих тестов не в том, что загрузчик умеет читать JSON, а в том, что
**отсутствующая или испорченная константа ломает старт**, а не бой (D-065).
"""

from __future__ import annotations

import json

import pytest

from src.constants import Constants, load_constants
from src.constants import registry as R
from src.constants.spec import ConstantError, Num, Span


def test_все_объявленные_константы_есть_в_вольте(constants: Constants) -> None:
    constants.validate(R.declared())


def test_нет_константы_ломает_проверку() -> None:
    snapshot = Constants({"mine.roof_start": 100}, source="тест")
    with pytest.raises(ConstantError) as exc:
        snapshot.validate([Num("mine.roof_start"), Num("нет.такого"), Num("тоже.нет")])
    #: Все проблемы разом — чинить набор по одной за перезапуск невыносимо.
    assert "нет.такого" in str(exc.value)
    assert "тоже.нет" in str(exc.value)


def test_неверная_форма_ломает_проверку() -> None:
    snapshot = Constants({"body.drain_rate": 5}, source="тест")
    with pytest.raises(ConstantError, match="ожидалось"):
        snapshot[Span("body.drain_rate")]


def test_диапазон_с_min_больше_max_отвергается() -> None:
    snapshot = Constants({"x": {"min": 10, "max": 1}}, source="тест")
    with pytest.raises(ConstantError):
        snapshot[Span("x")]


def test_правка_несуществующего_ключа_отвергается(constants: Constants) -> None:
    """Правка меняет значение, а не вводит новую величину: новая заводится в вольте."""
    with pytest.raises(ConstantError, match="несуществующие"):
        constants.with_overrides({"выдуманная.константа": 1})


def test_правка_даёт_новый_снимок_и_новый_отпечаток(constants: Constants) -> None:
    before = constants[R.MINING_IRON_PER_HOUR]
    patched = constants.with_overrides({"mining.iron_per_hour": before + 10})

    assert patched[R.MINING_IRON_PER_HOUR] == before + 10
    #: Исходный снимок неизменен — читатель никогда не видит полуправку.
    assert constants[R.MINING_IRON_PER_HOUR] == before
    #: Отпечаток другой: по нему в журнале видно, на каких числах шёл эпизод.
    assert patched.digest != constants.digest


def test_полосы_признаков_свода_покрывают_шкалу(constants: Constants) -> None:
    """«свод сухой» → «сыплется пыль» → «свод потрескивает» → «трещит» (D-143)."""
    bands = constants[R.MINE_SIGN_BANDS]
    assert min(bands.values()) == 0, "нижняя полоса обязана доходить до нуля"
    assert len(set(bands.values())) == len(bands), "полосы не должны совпадать"


def test_отпечаток_не_зависит_от_порядка_ключей(tmp_path) -> None:
    raw = {"a": 1, "b": {"min": 0, "max": 2}}
    first = Constants(raw, source="тест")
    second = Constants(dict(reversed(list(raw.items()))), source="тест")
    assert first.digest == second.digest


def test_понятная_ошибка_если_вольт_не_собран(tmp_path) -> None:
    with pytest.raises(ConstantError, match="build.py"):
        load_constants(tmp_path)


def test_загрузка_читает_файл_целиком(tmp_path) -> None:
    path = tmp_path / "constants.json"
    path.write_text(json.dumps({"time.tick": 1}), encoding="utf-8")
    assert load_constants(tmp_path)[Num("time.tick")] == 1
