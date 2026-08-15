"""A separate test must catch magic numbers in code (01-tech-notes, pattern 4).

D-065 demands: not one balance number is hard-coded, a change applies without
a release. The rule is easy to break by accident -- write `stamina - 5`
instead of `stamina - constants[BODY_DRAIN_RATE].min` and not notice.

What is checked: no numeric literals occur in the rule modules (`engine/`)
except the neutral 0 and 1. Everything else must come from the constants
registry. Representation units (`units.py`) are not checked -- they cannot be
"balanced", they are a way of storing, not a property of the game.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RULES_DIRS = ["engine", "game"]

#: 0 and 1 are neutral: an empty sum, one step, the first element. Nothing
#: else can be a number in the rules.

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


def test_rule_modules_found() -> None:
    assert rule_modules(), "проверка бесполезна, если ей нечего проверять"


@pytest.mark.parametrize("path", rule_modules(), ids=lambda p: p.name)
def test_no_balance_numbers_in_rules(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = literals(tree)
    assert not offenders, (
        f"{path.relative_to(SRC)}: числа в коде правил — "
        + ", ".join(f"строка {line}: {value}" for line, value in offenders)
        + ". Балансное число живёт в data/constants.yaml вольта и приходит "
        "через octoverse.constants (D-065)"
    )
