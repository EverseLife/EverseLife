# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Keep `lastmod.json`: the date each page's content last really changed.

The sitemap used to date pages by file mtime, and mtime is the deploy's own
timestamp: a checkout writes every file anew, so a one-line CSS fix announced
that all four pages had changed. A search engine that catches a site lying
about `lastmod` stops believing the field altogether, which is worse than not
sending it.

So the date is kept beside the pages instead, and moves only when the bytes of
a page move. The file travels into the image with the pages themselves --
there is no git inside the container to ask.

    python lastmod.py            # restamp what changed
    python lastmod.py --check    # say whether a restamp is due (CI, pre-commit)

Exit code is 1 when `--check` finds a page whose content no longer matches its
stamp, so a hook can stop the commit and say what to run.
"""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app import PAGES

STAMPS = Path(__file__).parent / "lastmod.json"


def digest(file: Path) -> str:
    """A page's content, as sixteen hexadecimal characters.

    Half of sha256 is far more than enough to notice an edit; the file stays
    readable, and nobody has to scroll past a wall of hashes to see the dates.
    """
    return hashlib.sha256(file.read_bytes()).hexdigest()[:16]


def load() -> dict[str, dict[str, str]]:
    if not STAMPS.is_file():
        return {}
    try:
        return json.loads(STAMPS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        #: A broken file is not a reason to lose the dates silently: say so and
        #: let the run rebuild it from scratch.
        print(f"{STAMPS.name} is not valid JSON -- restamping everything", file=sys.stderr)
        return {}


def main(check: bool) -> int:
    stamps = load()
    today = datetime.now(UTC).date().isoformat()
    stale: list[str] = []
    fresh: dict[str, dict[str, str]] = {}

    for path, file in PAGES.items():
        now = digest(file)
        was = stamps.get(path)
        if was and was.get("hash") == now:
            fresh[path] = was
            continue
        stale.append(path)
        fresh[path] = {"hash": now, "date": today}

    if not stale:
        print(f"lastmod: {len(fresh)} pages, all stamps current")
        return 0
    if check:
        print("lastmod: stamps are behind the pages: " + ", ".join(stale), file=sys.stderr)
        print("lastmod: fix with `python landing/lastmod.py`", file=sys.stderr)
        return 1

    STAMPS.write_text(
        json.dumps(fresh, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"lastmod: stamped {today} on " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv[1:]))
