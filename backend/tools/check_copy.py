# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""No new Russian written into the engine's own source (D-251).

The twin of `frontend/scripts/check-copy.mjs`, and it works the other way
round because the two halves are at different stages. The client is **clean**:
every sentence it shows lives in `src/locales/*.ftl`, and the lint there fails
on any Cyrillic outside them. The engine is not clean yet -- 288 string
literals in 64 modules -- and a lint that fails on all of them could only be
turned off.

So this one is a **ratchet**: every module that still writes Russian is listed
below with the number of literals it has, and the check fails when a number
moves. A module not listed may have none at all. That stops the class in the
hundred modules that are already clean, and makes the rest a number that only
goes down.

What is counted: a Cyrillic string **literal** that is not a docstring.
Comments and docstrings are not counted and never will be -- a Russian comment
is fine here, and often better than an English one.

What is still in the list, by kind, so the number can be read:

* **boot-time errors** for whoever runs the server (`constants/*`, `db/ddl`,
  `jobs`, `loader`, `spec`, `formula`). Developer-facing, and CLAUDE.md wants
  them in English -- a separate sweep, not this one;
* **names the world generates for itself** (`seed_*`, `explore/site`, `ruins`,
  `ship/building`, `farm`): data rather than copy, and a design decision is
  written down for them in the vault plan;
* **keys of the ledger's `memo`** and of a few event payloads: an audit record
  nothing renders, and the ledger is append-only, so they cannot be migrated;
* **telemetry metric names** (`деньги.двойная-запись`): admin-facing
  identifiers, and identifiers are supposed to be ASCII.

Add a module here only with its reason in the list above. A number that went
down is also a failure: lower it, and the ratchet has turned.

    python tools/check_copy.py
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

#: A Cyrillic letter -- the shape a sentence written in place has.
CYRILLIC = re.compile(r"[\u0400-\u04FF]")

#: Module -> how many Russian string literals it still holds. Nothing else may
#: hold any. Taken on 2026-08-31, at the end of D-251 wave IV.
KNOWN: dict[str, int] = {
    "src/api/app.py": 1,
    "src/api/commands/city.py": 1,
    "src/api/commands/transport.py": 1,
    "src/api/commands/views.py": 1,
    "src/constants/catalog.py": 14,
    "src/constants/formula.py": 8,
    "src/constants/loader.py": 10,
    "src/constants/renames.py": 5,
    "src/constants/spec.py": 13,
    "src/db/ddl.py": 4,
    "src/engine/account.py": 15,
    "src/engine/bank.py": 5,
    "src/engine/battery.py": 2,
    "src/engine/chat.py": 2,
    "src/engine/city/_base.py": 1,
    "src/engine/city/grant.py": 2,
    "src/engine/city/land.py": 2,
    "src/engine/city/citizen.py": 4,
    "src/engine/city/law.py": 4,
    "src/engine/city/succession.py": 3,
    "src/engine/coin.py": 1,
    "src/engine/craft/_internal.py": 1,
    "src/engine/craft/batch.py": 1,
    "src/engine/craft/queue.py": 2,
    "src/engine/customs.py": 5,
    "src/engine/death.py": 4,
    "src/engine/energy.py": 3,
    "src/engine/estate/building.py": 1,
    "src/engine/estate/deed.py": 1,
    "src/engine/estate/price.py": 3,
    "src/engine/explore/run.py": 1,
    "src/engine/explore/site.py": 4,
    "src/engine/farm.py": 3,
    "src/engine/finance.py": 1,
    "src/engine/food.py": 1,
    "src/engine/forage.py": 2,
    "src/engine/frost.py": 2,
    "src/engine/jobs.py": 4,
    "src/engine/justice.py": 3,
    "src/engine/luck.py": 1,
    "src/engine/market.py": 6,
    "src/engine/mining.py": 3,
    "src/engine/oxygen.py": 2,
    "src/engine/plates.py": 1,
    "src/engine/rig.py": 1,
    "src/engine/ruins.py": 8,
    "src/engine/ship/_base.py": 1,
    "src/engine/ship/building.py": 3,
    "src/engine/storage.py": 1,
    "src/engine/transport.py": 2,
    "src/engine/utility.py": 3,
    "src/engine/vote.py": 18,
    "src/engine/wear.py": 3,
    "src/engine/works.py": 9,
    "src/engine/works_city.py": 8,
    "src/engine/world/things.py": 2,
    "src/herald/webhook.py": 2,
    "src/i18n/__init__.py": 4,
    "src/seed.py": 8,
    "src/seed_catchup.py": 7,
    "src/seed_parts.py": 18,
    "src/seed_surfaces.py": 2,
    "src/seed_world.py": 10,
    "src/telemetry/metrics.py": 23,
}


def _docstrings(tree: ast.AST) -> set[int]:
    """The id() of every string node that is a docstring rather than a value."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            first = node.body[0] if node.body else None
            said = (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            )
            if said:
                found.add(id(first.value))  # type: ignore[union-attr]
    return found


def written(path: Path) -> int:
    """How many Russian string literals this module holds."""
    source = path.read_text(encoding="utf-8")
    if not CYRILLIC.search(source):
        return 0
    tree = ast.parse(source)
    skip = _docstrings(tree)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and CYRILLIC.search(node.value)
    )


def main() -> int:
    counted = {
        path.relative_to(SRC.parent).as_posix(): written(path) for path in sorted(SRC.rglob("*.py"))
    }
    grew = sorted(
        (name, KNOWN.get(name, 0), found)
        for name, found in counted.items()
        if found > KNOWN.get(name, 0)
    )
    shrank = sorted(
        (name, allowed, counted.get(name, 0))
        for name, allowed in KNOWN.items()
        if counted.get(name, 0) < allowed
    )

    if grew:
        print(f"copy check failed: {len(grew)} module(s) write more Russian than before\n")
        for name, allowed, found in grew:
            print(f"  {name}: {found}, was {allowed}")
        print(
            "\nA sentence written in the code that produces it cannot be read in a"
            "\nsecond language (D-251). Name a message and let `src/i18n` say it:"
            '\n    raise NoGoods(key="goods-not-enough", goods="iron_ore", short=3)'
            "\nIf the string is genuinely not copy -- a boot error, a generated name,"
            "\nan audit key -- say which in the list at the top of this file and raise"
            "\nthe number there, the way `tools/spdx.py` is argued with."
        )
        return 1

    if shrank:
        print(f"copy check: {len(shrank)} module(s) got better -- turn the ratchet\n")
        for name, allowed, found in shrank:
            print(f"  {name}: {found}, allowed {allowed}")
        print("\nLower the numbers in KNOWN (or drop the line at zero) so they cannot come back.")
        return 1

    total = sum(counted.values())
    print(f"copy fine: no new Russian in the engine; {total} literals left in {len(KNOWN)} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
