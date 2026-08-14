"""Отдельный тест обязан ловить магические числа в коде (01-tech-notes, паттерн 4).

D-065 требует: ни одно балансное число не зашито в код, изменение применяется
без выката версии. Правило легко нарушить случайно — написать `stamina - 5`
вместо `stamina - constants[BODY_DRAIN_RATE].min` и не заметить.

Что проверяется: в модулях правил (`engine/`) не встречается числовых литералов,
кроме нейтральных 0 и 1. Всё остальное обязано приходить из реестра констант.
Единицы представления (`units.py`) под проверку не попадают — их невозможно
«сбалансировать», это способ хранения, а не свойство игры.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RULES_DIRS = ["engine", "game"]

#: 0 и 1 — нейтральны: пустая сумма, один шаг, первый элемент. Всё прочее
#: числом в правилах быть не может.
ALLOWED = {0, 1}

SRC = Path(__file__).resolve().parents[1] / "src"


def rule_modules() -> list[Path]:
    files: list[Path] = []
    for name in RULES_DIRS:
        directory = SRC / name
        if directory.exists():
            files.extend(sorted(directory.rglob("*.py")))
    return files


def literals(tree: ast.AST) -> list[tuple[int, object]]:
    found: list[tuple[int, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if isinstance(node.value, bool) or node.value in ALLOWED:
                continue
            found.append((node.lineno, node.value))
    return found


def test_модули_правил_найдены() -> None:
    assert rule_modules(), "проверка бесполезна, если ей нечего проверять"


@pytest.mark.parametrize("path", rule_modules(), ids=lambda p: p.name)
def test_в_правилах_нет_балансных_чисел(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = literals(tree)
    assert not offenders, (
        f"{path.relative_to(SRC)}: числа в коде правил — "
        + ", ".join(f"строка {line}: {value}" for line, value in offenders)
        + ". Балансное число живёт в data/constants.yaml вольта и приходит "
        "через octoverse.constants (D-065)"
    )
