"""Decision codes belong in comments, never in what the player reads.

`D-143`, `D-116`, `D-055` are how the vault and the engine talk to each other,
and comments here keep them deliberately -- a rule without its reason rots.
But a refusal is not a comment: it is a sentence the player reads at the moment
something did not work, and a code in it promises a document they will never be
given. "чужая установка: вывоз — по договору с хозяином (D-116)" tells a person
nothing that "чужая установка: вывоз — по договору с хозяином" does not.

It matters more than it used to. Refusals now appear next to the control that
was refused rather than in one strip at the bottom of the screen, so they are
read far more often.

What is checked: no decision code appears in a string handed to an exception --
that is, in the engine's own wording of a refusal. Docstrings, comments and
identifiers are untouched, and codes in them stay exactly where they are.

The client has the same guard for its own copy: `frontend/scripts/check-copy.mjs`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

#: A vault decision: `D-` and three digits.
CODE = re.compile(r"\bD-\d{3}\b")


def sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def spoken(tree: ast.AST) -> list[tuple[int, str]]:
    """Strings the engine says out loud: everything raised as an exception.

    Both shapes occur: a literal argument, and an f-string assembled from
    parts. A joined string is checked piece by piece -- the code, if there is
    one, sits in a literal piece.
    """
    said: list[tuple[int, str]] = []

    def collect(node: ast.AST, line: int) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            said.append((line, node.value))
        elif isinstance(node, ast.JoinedStr):
            for piece in node.values:
                collect(piece, line)
        elif isinstance(node, ast.FormattedValue):
            #: The substituted value is a number or a name, not our copy.
            return

    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        call = node.exc
        if isinstance(call, ast.Call):
            for argument in call.args:
                collect(argument, node.lineno)

    return said


def test_no_decision_codes_in_refusals() -> None:
    guilty: list[str] = []
    for path in sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, text in spoken(tree):
            for hit in CODE.findall(text):
                shown = path.relative_to(SRC.parent).as_posix()
                guilty.append(f"{shown}:{line}: {hit} в отказе — {text.strip()[:70]}")

    assert not guilty, (
        "коды решений вольта попали в текст, который читает игрок:\n  "
        + "\n  ".join(guilty)
        + "\n\nВ комментариях они нужны и остаются. В отказе — обещают документ,"
        "\nкоторого игроку никто не покажет."
    )


def test_the_guard_would_notice() -> None:
    """The check is worth nothing if it cannot fail: prove it sees a code."""
    tree = ast.parse('raise Refused("чужая установка: по договору (D-116)")')
    assert any(CODE.search(text) for _, text in spoken(tree))
