# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The licence header in every source file: check it, or put it there.

    python tools/spdx.py            # check -- what CI and the pre-commit hook run
    python tools/spdx.py --apply    # stamp whatever is missing one
    python tools/spdx.py --list     # what is covered, and what is not

Two lines at the top of a file, in that file's comment syntax:

    SPDX-License-Identifier: AGPL-3.0-only
    Copyright (C) <year> Nurlan Urazkulov

The header is what makes a single file say its own licence -- a copy that
travels out of the repository takes the answer with it, and `LICENSE` in the
root does not travel. Dual licensing needs that: a commercial licence is only
sellable while the provenance of every file is beyond argument.

Stamping is idempotent, and it never touches the first line where something
must stand first: a shebang, an HTML doctype, a BOM. A file already carrying
somebody else's copyright is left alone entirely -- rewriting a foreign notice
is the one thing worse than missing our own.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

SPDX = "SPDX-License-Identifier: AGPL-3.0-only"
HOLDER = "Nurlan Urazkulov"
#: The year is the file's, not today's: a check that demanded the current year
#: would turn every January into a diff across the whole repository.
COPYRIGHT = re.compile(rf"Copyright \(C\) \d{{4}} {re.escape(HOLDER)}")

#: What carries a header: everything written here by hand. Patterns are
#: `git ls-files` patterns, so nothing untracked and nothing ignored is seen.
COVERED = (
    "backend/src/*.py",
    "backend/tests/*.py",
    "backend/tools/*.py",
    "deploy/*.py",
    "agentic_player_system/aps/*.py",
    "agentic_player_system/tests/*.py",
    "agentic_player_system/aps/static/index.html",
    "landing/*.py",
    "tools/*.py",
    "frontend/src/*.tsx",
    "frontend/*.ts",
    "frontend/*.mjs",
    "frontend/src/*.css",
    "frontend/index.html",
    "landing/index.html",
    ".githooks/pre-commit",
)

#: What does not, and why. Generated files would lose the header at the next
#: generation; lock files, assets and third-party licence texts are not ours to
#: stamp; configuration is not source. Add to this list rather than arguing
#: with the check.
EXCLUDED = (
    "backend/migrations/",  # Alembic writes these, including env.py by template
    "node_modules/",
    "vault/",  # built from the game-design vault, not kept here
)

#: How each language spells a comment. The header goes in as whole lines.
STYLES: dict[str, list[str]] = {
    ".py": ["# {spdx}", "# {copy}"],
    ".ts": ["// {spdx}", "// {copy}"],
    ".tsx": ["// {spdx}", "// {copy}"],
    ".mjs": ["// {spdx}", "// {copy}"],
    ".css": ["/* {spdx}", "   {copy} */"],
    ".html": ["<!--", "{spdx}", "{copy}", "-->"],
    "": ["# {spdx}", "# {copy}"],  # .githooks/pre-commit -- sh
}

BOM = "﻿"
#: How far down a file the header may be looked for. Deeper than this it is not
#: a header any more, and a file quoting the string in its body cannot pass.
HEAD_LINES = 15


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
    )
    return Path(out.stdout.strip())


def covered_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", *COVERED], capture_output=True, text=True, check=True, cwd=root
    )
    files = [line for line in out.stdout.splitlines() if line]
    return sorted({f for f in files if not f.startswith(EXCLUDED)})


def split_keepends(text: str) -> list[str]:
    """Split on newlines only -- `str.splitlines` also breaks on form feeds."""

    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def state(path: Path) -> tuple[str, str]:
    """`("ours" | "missing" | "foreign", head)` for one file."""

    text = path.read_bytes().decode("utf-8").removeprefix(BOM)
    head = "\n".join(text.splitlines()[:HEAD_LINES])
    if SPDX in head and COPYRIGHT.search(head):
        return "ours", head
    if "Copyright" in head and HOLDER not in head:
        return "foreign", head
    return "missing", head


def stamp(path: Path, year: int) -> None:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    bom = ""
    if text.startswith(BOM):
        bom, text = BOM, text[1:]

    #: The header takes the line ending the file already uses, or the file ends
    #: up with two kinds at once and the diff is about line endings.
    crlf = raw.count(b"\r\n")
    newline = "\r\n" if crlf > raw.count(b"\n") - crlf else "\n"

    lines = split_keepends(text)
    #: What must stay on the first line: a shebang, or an HTML doctype.
    at = 0
    if lines and (lines[0].startswith("#!") or lines[0].lstrip().lower().startswith("<!doctype")):
        at = 1

    header = [
        line.format(spdx=SPDX, copy=f"Copyright (C) {year} {HOLDER}") + newline
        for line in STYLES[path.suffix]
    ]
    #: One blank line between the header and what follows, unless one is there.
    if at < len(lines) and lines[at].strip():
        header.append(newline)

    path.write_bytes((bom + "".join(lines[:at] + header + lines[at:])).encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="stamp what is missing a header")
    parser.add_argument("--list", action="store_true", help="print what is covered")
    args = parser.parse_args()

    root = repo_root()
    files = covered_files(root)

    if args.list:
        for name in files:
            print(name)
        print(f"\n{len(files)} files covered; excluded: {', '.join(EXCLUDED)}", file=sys.stderr)
        return 0

    missing, foreign = [], []
    for name in files:
        verdict, _ = state(root / name)
        if verdict == "missing":
            missing.append(name)
        elif verdict == "foreign":
            foreign.append(name)

    for name in foreign:
        print(f"spdx: {name} carries somebody else's copyright -- left alone")

    if not missing:
        print(f"spdx: header in place, {len(files)} files")
        return 0

    if args.apply:
        year = datetime.date.today().year
        for name in missing:
            stamp(root / name, year)
        print(f"spdx: header added to {len(missing)} files")
        return 0

    print(f"\nspdx: no licence header in {len(missing)} files:", file=sys.stderr)
    for name in missing:
        print(f"  {name}", file=sys.stderr)
    print("\nspdx: fix with `python tools/spdx.py --apply`", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
