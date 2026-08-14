"""Вычисление формул вольта.

Часть величин задана в `build/constants.json` не числом, а выражением:

    quality.durability_factor = "base_life * (0.5 + quality / 80)"

Соблазн велик — прочитать выражение глазами и переписать его кодом. Так делать
нельзя: числа `0.5` и `80` тогда переезжают в движок, и правка баланса начинает
требовать выката версии, чего D-065 прямо запрещает. Поэтому выражение
**вычисляется**, а движок отвечает только за то, какие имена в него подставить.

Вычисляется не всё подряд. Разрешены арифметика, скобки и подстановка имён —
ничего больше: ни вызовов, ни обращений к атрибутам, ни индексов. Формула вида
`sum(... for n in 1..floors)` честно отвергается как невычислимая, потому что
она и есть описание алгоритма, который движок обязан написать сам.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from src.constants.spec import ConstantError

#: Операции, которые может содержать формула баланса. Список закрыт намеренно.
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
    """Формула описывает алгоритм, а не выражение. Её пишет движок."""


def evaluate(text: str, **names: float) -> float:
    """Посчитать формулу вольта, подставив названные величины."""
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
