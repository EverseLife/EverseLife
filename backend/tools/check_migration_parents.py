# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""No two migrations may claim the same parent -- across branches, not just here.

Two revisions with one `down_revision` are two alembic heads waiting to happen.
Nothing notices while they sit on separate branches: each upgrades cleanly on
its own, `alembic heads` shows one head in either worktree, and the collision
appears only when the second one is merged -- at which point `alembic upgrade
head` refuses to choose and main is red for everybody.

It happened four times on 2026-09-02 alone. Three were caught by somebody
reading a neighbour's `down_revision` by hand; the fourth was caught after the
merge. Hence this: the reading, done by a script.

**Why this is not a GitHub CI check.** The branches never leave the machine --
`git branch -r` here knows `origin/main` and nothing else, because the work
lives in local worktrees and is merged locally. A runner that fetches the
repository sees one branch and has nothing to compare. So the check belongs
where the branches are: `.githooks/pre-commit`, and this script by hand. In CI
it is not useless but nearly so -- it degrades to "main does not contradict
itself", which the single-head test in `tests/test_migrations.py` already says.

What is scanned: every local branch, plus the working tree -- an uncommitted
migration collides exactly as well as a committed one, and on 2026-09-02 the
one that collided was still untracked.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

VERSIONS = "backend/migrations/versions"
REVISION = re.compile(r"^revision(?::[^=]*)?\s*=\s*[\"']([^\"']+)", re.M)
DOWN = re.compile(r"^down_revision(?::[^=]*)?\s*=\s*[\"']([^\"']+)", re.M)


def _git(*args: str) -> str:
    """Git output, or empty on failure: a missing branch is not an error here."""
    done = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return done.stdout if done.returncode == 0 else ""


def _parse(text: str) -> tuple[str, str] | None:
    """(revision, down_revision) of a migration, or None for a root or a stub."""
    revision = REVISION.search(text or "")
    down = DOWN.search(text or "")
    if revision is None or down is None:
        return None
    return revision.group(1), down.group(1)


def _branches() -> list[str]:
    lines = _git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    return [line.strip() for line in lines if line.strip()]


def _from_branch(branch: str) -> dict[str, str]:
    """revision -> down_revision for migrations this branch has and main has not."""
    listing = _git("diff", "--name-only", f"main...{branch}", "--", VERSIONS).splitlines()
    found: dict[str, str] = {}
    for path in (line.strip() for line in listing if line.strip().endswith(".py")):
        parsed = _parse(_git("show", f"{branch}:{path}"))
        if parsed is not None:
            found[parsed[0]] = parsed[1]
    return found


def _from_worktree(root: Path) -> dict[str, str]:
    """The same for the files on disk, committed or not.

    An untracked migration is invisible to every `git` listing above and
    collides all the same -- that is precisely the one that got through.
    """
    found: dict[str, str] = {}
    directory = root / VERSIONS
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.py")):
        parsed = _parse(path.read_text(encoding="utf-8", errors="replace"))
        if parsed is not None:
            found[parsed[0]] = parsed[1]
    return found


def collisions(root: Path) -> dict[str, dict[str, list[str]]]:
    """parent -> {revision -> where it was seen}, for parents claimed twice."""
    claims: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for revision, parent in _from_worktree(root).items():
        claims[parent][revision].append("рабочее дерево")
    for branch in _branches():
        for revision, parent in _from_branch(branch).items():
            claims[parent][revision].append(branch)
    return {
        parent: dict(by_revision) for parent, by_revision in claims.items() if len(by_revision) > 1
    }


def main() -> int:
    root = Path(_git("rev-parse", "--show-toplevel").strip() or ".")
    found = collisions(root)
    if not found:
        return 0
    print("миграции: у двух ревизий один родитель -- это две головы после мержа", file=sys.stderr)
    for parent, by_revision in sorted(found.items()):
        print(f"\n  родитель {parent}:", file=sys.stderr)
        for revision, where in sorted(by_revision.items()):
            print(f"    {revision} -- {', '.join(sorted(set(where)))}", file=sys.stderr)
    print(
        "\nкто вливается вторым, тот перецепляет свою миграцию на голову, "
        "которая окажется в main: правится одна строка `down_revision`",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
