# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Fast check of one edited file, run by the Claude Code PostToolUse hook.

Reads the hook's JSON from stdin, finds the edited file and runs what fits:

* `backend/**/*.py` -- `ruff check` on the file and `lint-imports` on the
  backend (layers `api -> engine -> models -> constants`);
* `frontend/src/**/*.ts|tsx` -- `tsc --noEmit` on the app project.

Problems go to stdout as a `systemMessage`, so the agent sees them at the
moment of the edit, not in CI. Exit code is always 0: the hook informs, it
does not block -- blocking on a half-written file would only get in the way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
LINT_IMPORTS = BACKEND / ".venv" / "Scripts" / "lint-imports.exe"


def _run(args: list[str], cwd: Path) -> str:
    try:
        done = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
            shell=False,
            #: import-linter prints emoji; a cp1251 console would crash on them.
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as trouble:
        return f"{args[0]}: {trouble}"
    if done.returncode == 0:
        return ""
    return (done.stdout + done.stderr).strip()


def check(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        relative = path.resolve().relative_to(ROOT)
    except ValueError:
        return findings
    parts = relative.parts
    if parts[0] == "backend" and path.suffix == ".py" and ".venv" not in parts:
        if PYTHON.exists():
            out = _run([str(PYTHON), "-m", "ruff", "check", str(path)], BACKEND)
            if out:
                findings.append(f"ruff:\n{out}")
        if LINT_IMPORTS.exists() and parts[1] == "src":
            out = _run([str(LINT_IMPORTS)], BACKEND)
            if out and "BROKEN" in out:
                findings.append(f"lint-imports:\n{out}")
    elif parts[0] == "frontend" and path.suffix in (".ts", ".tsx") and "node_modules" not in parts:
        out = _run(["cmd", "/c", "npx", "tsc", "--noEmit", "-p", "tsconfig.app.json"], FRONTEND)
        if out:
            findings.append(f"tsc:\n{out}")
    return findings


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    raw = (payload.get("tool_input") or {}).get("file_path") or (
        payload.get("tool_response") or {}
    ).get("filePath")
    if not raw:
        return
    findings = check(Path(raw))
    if findings:
        print(json.dumps({"systemMessage": "quickcheck:\n" + "\n\n".join(findings)[:4000]}))


if __name__ == "__main__":
    main()
