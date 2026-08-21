# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Evaluation of vault formulas.

Some quantities are given in `build/constants.json` not as a number but as an
expression:

    quality.durability_factor = "base_life * (0.5 + quality / 80)"

The temptation is great -- read the expression by eye and rewrite it in code.
That is not allowed: the numbers `0.5` and `80` would then move into the
engine, and a balance edit would start requiring a release, which D-065
directly forbids. So the expression is **evaluated**, and the engine is
responsible only for which names to substitute into it.

Not everything is evaluated. Arithmetic, parentheses and name substitution are
allowed -- nothing else: no calls, no attribute access, no indexing. A formula
like `sum(... for n in 1..floors)` is honestly rejected as unevaluable,
because it is a description of an algorithm the engine must write itself.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from src.constants.spec import ConstantError

#: Operations a balance formula may contain. The list is closed on purpose.
BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class NotComputable(ConstantError):
    """The formula describes an algorithm, not an expression. The engine writes it."""


def evaluate(text: str, **names: float) -> float:
    """Evaluate a vault formula, substituting the named quantities."""
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as broken:
        raise NotComputable(f"формула {text!r} не разбирается: {broken}") from broken
    return _walk(tree.body, text, names)


def _walk(node: ast.AST, text: str, names: dict[str, float]) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise NotComputable(
                f"формула {text!r} требует величину {node.id!r}, а её не передали"
            )
        return float(names[node.id])
    if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
        return BINARY[type(node.op)](
            _walk(node.left, text, names), _walk(node.right, text, names)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
        return UNARY[type(node.op)](_walk(node.operand, text, names))
    raise NotComputable(
        f"формула {text!r} содержит {type(node).__name__}: это алгоритм, "
        "и движок обязан написать его сам"
    )
