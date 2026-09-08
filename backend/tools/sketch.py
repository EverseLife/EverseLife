# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The relief a planet **would** have with these numbers, as JSON on stdout.

The vault's editor draws a planet while its numbers are being turned, and the
field those numbers make is the engine's (`engine/terrain`, `src/relief`,
D-319, D-321, D-323). Porting it into the editor would give the world two
shapes -- one the tool shows and one the game builds -- and they would part on
the first change to either. So the editor asks the engine instead, and this is
the door: one command, numbers in, the sketch out.

Reads `build/constants.json` and nothing else -- no database, no session, no
world. `--set` puts a value over the build without writing anything anywhere,
which is what makes a preview a preview: the seed can be turned and looked at
before it is saved.

    python -m tools.sketch --planet terra --set terrain.seed=17

`--set` takes the same shapes the vault writes: a number, or a table as JSON
(`--set 'terrain.sea_share={"terra": 0.7}'`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: Run as a file rather than a module (`python tools/sketch.py`) there is no
#: package above, and the engine's imports are absolute from `src`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants.loader import Constants  # noqa: E402 -- the path has to be set first
from src.engine import terrain  # noqa: E402
from src.models.world import Planet  # noqa: E402


def build_path() -> Path:
    """Where the vault's build sits: told, or beside the repository."""
    told = os.environ.get("EVERSELIFE_VAULT_BUILD")
    if told:
        return Path(told)
    return Path(__file__).resolve().parent.parent.parent.parent / "everselife-vault" / "build"


def parse_set(raw: str) -> tuple[str, object]:
    """`key=value`, the value read as JSON and, failing that, as text."""
    if "=" not in raw:
        raise SystemExit(f"--set: нужно «ключ=значение», а не «{raw}»")
    key, _, text = raw.partition("=")
    try:
        return key.strip(), json.loads(text)
    except json.JSONDecodeError:
        return key.strip(), text


def main() -> None:
    parser = argparse.ArgumentParser(description="рельеф планеты по числам вольта")
    parser.add_argument("--planet", default="terra", help="ключ планеты (terra, pyroxis, …)")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="КЛЮЧ=ЗНАЧЕНИЕ",
        help="число поверх сборки, не записывая его: столько раз, сколько нужно",
    )
    parser.add_argument("--build", default=None, help="каталог build/ вольта")
    args = parser.parse_args()

    build = Path(args.build) if args.build else build_path()
    source = build / "constants.json"
    if not source.exists():
        raise SystemExit(f"нет сборки вольта: {source}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    for one in args.set:
        key, value = parse_set(one)
        raw[key] = value

    try:
        planet = Planet(args.planet)
    except ValueError:
        known = ", ".join(one.value for one in Planet)
        raise SystemExit(f"нет такой планеты: {args.planet} (есть {known})") from None

    constants = Constants(raw, source=str(source))
    #: The whole sketch, as `/public/terrain/{planet}` serves it: the grid, the
    #: water, the rivers, the warmth of each row.
    json.dump(terrain.sketch(constants, planet), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
