"""Rebuild the game-design vault and refresh the snapshot in `vault/`.

    python deploy/sync-vault.py
    python deploy/sync-vault.py --vault D:\\path\\to\\octoverse-game-design
    python deploy/sync-vault.py --no-build

Numbers are edited only in the vault (D-065). Here -- carrying the build output
over, so that the server image and CI see the same values as the developer.

This is the only implementation of that step, and it is deliberately in Python:
the same one runs from a Windows shell, from CI and from inside the recipe
editor's container. A second copy for another shell would have to be kept in
step with this one by hand, and one day it would not be.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# What the engine reads. Nothing else from `build/` travels.
SNAPSHOT = ("constants.json", "laws.json", "plants.json", "recipes.json")


def default_vault() -> Path:
    """Where the vault lies: told by the environment, or next to this repository."""
    told = os.environ.get("OCTOVERSE_VAULT")
    return Path(told).expanduser() if told else REPO.parent / "octoverse-game-design"


def build(vault: Path) -> int:
    """Run the vault's own build. Its words go straight through, untranslated."""
    tool = vault / "tools" / "build.py"
    if not tool.exists():
        print(f"warning: {tool} not found -- taking the ready build/", file=sys.stderr)
        return 0
    # Flushed: the child writes to the same stream, and into a pipe -- the editor
    # console -- an unflushed line would surface after the output it announces.
    print(f"building the vault: {tool}", flush=True)
    done = subprocess.run(  # noqa: S603
        [sys.executable, "tools/build.py"],
        cwd=vault,
        # Windows would hand the child a cp1251 pipe and mangle the Russian output.
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        check=False,
    )
    return done.returncode


def copy_snapshot(vault: Path, repo: Path = REPO) -> list[str]:
    """Carry the four files over. Half a snapshot is worse than none.

    A partial copy would leave the engine reading today's recipes against
    yesterday's constants -- so a missing file stops the whole step.
    """
    source = vault / "build"
    missing = [name for name in SNAPSHOT if not (source / name).exists()]
    if missing:
        raise FileNotFoundError(f"the build is incomplete, missing: {', '.join(missing)}")
    target = repo / "vault"
    target.mkdir(parents=True, exist_ok=True)
    for name in SNAPSHOT:
        shutil.copyfile(source / name, target / name)
    return list(SNAPSHOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", default=None, help="where the vault lies")
    parser.add_argument("--no-build", action="store_true", help="take the ready build/")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser() if args.vault else default_vault()
    if not (vault / "data").is_dir():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 1
    vault = vault.resolve()

    if not args.no_build and (code := build(vault)) != 0:
        print("the build failed -- the snapshot is left as it was", file=sys.stderr)
        return code

    try:
        copied = copy_snapshot(vault)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1

    for name in copied:
        print(f"  {name}")
    print()
    print(f"snapshot refreshed: {REPO / 'vault'}")
    print("next: git add vault && git commit && git push -- the numbers reach the server by deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
