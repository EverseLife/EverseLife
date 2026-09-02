# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""All the migrations on this machine must still make one chain.

Two revisions with one `down_revision` are two alembic heads waiting to happen.
Nothing notices while they sit apart: each upgrades from zero, each shows one
head in its own worktree, and the collision appears when the second one is
merged -- at which point `alembic upgrade head` refuses to choose and main is
red for everybody. It happened four times on 2026-09-02; three were caught by
somebody reading a neighbour's `down_revision` by hand, the fourth after the
merge.

So the question asked here is not "does my branch look right" -- it always
does -- but **"what will main hold when everything lands"**. Every local
branch, every worktree on disk, and main itself go into one heap, and that
heap must have a single head.

Three sources, and each was learned by getting it wrong:

* **every ref, whole** -- not `main...branch`, which shows only the branch's
  own side. Read that way, a migration main gained *after* the branch point is
  invisible from the branch, and the one who merges second is told "clean";
* **every worktree's files** -- not just this one's. An untracked migration
  collides exactly as well as a committed one, and sibling worktrees are where
  they live: on 2026-09-02 two untracked ones sat in different trees;
* **main wins a disagreement.** When a revision is in main with one parent and
  on a branch with another, the branch was re-parented during the merge; main
  is the truth and the stale branch copy is not a collision.

**Why not GitHub CI.** The branches never leave the machine -- `git branch -r`
knows `origin/main` alone, because work lives in local worktrees and is merged
locally. A runner has nothing to compare. The half that CI *can* do is in
`tests/test_migrations.py`: one head in the tree being tested.

**A merge migration is not a collision.** `alembic merge` writes
`down_revision = ("a", "b")` and is the cure for two heads, so both parents are
read and the chain is judged by its head count, not by counting children.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

VERSIONS = "backend/migrations/versions"
REVISION = re.compile(r"^revision(?:\s*:[^=]*)?\s*=\s*[\"']([^\"']+)", re.M)
#: `down_revision` is a string, `None`, or -- for a merge -- a tuple of them.
DOWN = re.compile(r"^down_revision(?:\s*:[^=]*)?\s*=\s*(.+)$", re.M)
QUOTED = re.compile(r"[\"']([^\"']+)[\"']")


class Unusable(Exception):
    """The check cannot answer. Louder than a false all-clear."""


def _git(*args: str, quiet: bool = True) -> str:
    done = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if done.returncode != 0 and not quiet:
        raise Unusable(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout if done.returncode == 0 else ""


def parse(text: str) -> tuple[str, tuple[str, ...]] | None:
    """(revision, parents) of a migration. `None` when it is not one."""
    revision = REVISION.search(text or "")
    if revision is None:
        return None
    down = DOWN.search(text or "")
    parents = tuple(QUOTED.findall(down.group(1))) if down is not None else ()
    return revision.group(1), parents


def _refs() -> list[str]:
    lines = _git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    return [line.strip() for line in lines if line.strip()]


def _blobs(ref: str) -> dict[str, str]:
    """path -> blob sha for the migrations of one ref."""
    found: dict[str, str] = {}
    for line in _git("ls-tree", "-r", ref, "--", VERSIONS).splitlines():
        head, _, path = line.partition("\t")
        parts = head.split()
        if len(parts) == 3 and parts[1] == "blob" and path.endswith(".py"):
            found[path] = parts[2]
    return found


def _read(shas: set[str]) -> dict[str, str]:
    """Every blob in one `cat-file --batch`: a `git show` per file is minutes."""
    if not shas:
        return {}
    listing = sorted(shas)
    done = subprocess.run(
        ["git", "cat-file", "--batch"],
        input="\n".join(listing) + "\n",
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    )
    if done.returncode != 0:
        raise Unusable(f"git cat-file: {done.stderr.strip()}")
    out: dict[str, str] = {}
    rest = done.stdout
    for sha in listing:
        marker = rest.find(sha)
        if marker < 0:
            continue
        line_end = rest.find("\n", marker)
        header = rest[marker:line_end].split()
        size = int(header[2]) if len(header) == 3 and header[2].isdigit() else 0
        body_at = line_end + 1
        out[sha] = rest[body_at : body_at + size]
        rest = rest[body_at + size :]
    return out


def _worktrees() -> list[Path]:
    paths: list[Path] = []
    for line in _git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line[len("worktree ") :].strip()))
    return paths


def gather() -> dict[str, tuple[tuple[str, ...], list[str]]]:
    """revision -> (parents, where it was seen), main's answer preferred."""
    if not _git("rev-parse", "--verify", "--quiet", "refs/heads/main").strip():
        raise Unusable(
            "нет ветки main -- сравнивать не с чем, а молчаливое «чисто» здесь хуже отказа"
        )

    per_ref: dict[str, dict[str, str]] = {ref: _blobs(ref) for ref in _refs()}
    bodies = _read({sha for blobs in per_ref.values() for sha in blobs.values()})

    found: dict[str, tuple[tuple[str, ...], list[str]]] = {}
    from_main: set[str] = set()

    def remember(revision: str, parents: tuple[str, ...], where: str, *, main: bool) -> None:
        if revision in found and (revision in from_main or not main):
            #: Seen already: keep the parent main knows, only add the place.
            kept, seen = found[revision]
            if where not in seen:
                seen.append(where)
            found[revision] = (kept, seen)
            return
        found[revision] = (parents, [where])
        if main:
            from_main.add(revision)

    for ref in sorted(per_ref, key=lambda name: (name != "main", name)):
        for sha in per_ref[ref].values():
            parsed = parse(bodies.get(sha, ""))
            if parsed is not None:
                remember(*parsed, where=ref, main=ref == "main")
    for tree in _worktrees():
        directory = tree / VERSIONS
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            parsed = parse(path.read_text(encoding="utf-8", errors="replace"))
            if parsed is not None:
                remember(*parsed, where=f"{tree.name}/", main=False)
    return found


def heads(found: dict[str, tuple[tuple[str, ...], list[str]]]) -> list[str]:
    claimed = {parent for parents, _ in found.values() for parent in parents}
    return sorted(revision for revision in found if revision not in claimed)


def main() -> int:
    try:
        found = gather()
    except Unusable as why:
        print(f"проверка родителей миграций не работает: {why}", file=sys.stderr)
        return 1
    ends = heads(found)
    if len(ends) <= 1:
        return 0

    print(
        "миграции: когда всё это вольётся в main, у него будет "
        f"{len(ends)} головы -- `alembic upgrade head` откажется выбирать",
        file=sys.stderr,
    )
    for head in ends:
        parents, where = found[head]
        on = ", ".join(parents) or "корень"
        seen = ", ".join(sorted(set(where)))
        print(f"\n  {head} (на {on})\n    где: {seen}", file=sys.stderr)
    print(
        "\nкто вливается вторым, тот перецепляет свою миграцию на голову, "
        "которая к тому времени будет в main: правится одна строка `down_revision`. "
        "Голову берите из `alembic heads` перед самым коммитом, а не по памяти.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
