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
    python lastmod.py --accept   # take the new content under the old date

`--accept` is for the edit a human judged insignificant: a reworded label, a
typo. Search engines only trust `lastmod` while it marks the last *meaningful*
change -- a date that jumps on every touch teaches them to ignore the field --
so "the content moved, the date did not" must be expressible, or the check
below fights the very accuracy it exists to keep. A page with no stamp at all
is still dated today: there is no old date to keep.

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


def main(check: bool, accept: bool = False) -> int:
    stamps = load()
    today = datetime.now(UTC).date().isoformat()
    stale: list[str] = []
    kept: list[str] = []
    fresh: dict[str, dict[str, str]] = {}

    for path, file in PAGES.items():
        now = digest(file)
        was = stamps.get(path)
        if was and was.get("hash") == now:
            fresh[path] = was
            continue
        if accept and was and was.get("date"):
            #: The human called this edit insignificant: the hash follows the
            #: bytes, the date stays where the last meaningful change put it.
            kept.append(path)
            fresh[path] = {"hash": now, "date": was["date"]}
            continue
        stale.append(path)
        fresh[path] = {"hash": now, "date": today}

    if not stale and not kept:
        print(f"lastmod: {len(fresh)} pages, all stamps current")
        return 0
    if check:
        print("lastmod: stamps are behind the pages: " + ", ".join(stale), file=sys.stderr)
        print(
            "lastmod: fix with `python landing/lastmod.py`, or, for an edit too"
            " small to re-date, `python landing/lastmod.py --accept`",
            file=sys.stderr,
        )
        return 1

    STAMPS.write_text(
        json.dumps(fresh, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if stale:
        print(f"lastmod: stamped {today} on " + ", ".join(stale))
    if kept:
        print("lastmod: accepted new content under the old date on " + ", ".join(kept))
    return 0


if __name__ == "__main__":
    sys.exit(
        main(check="--check" in sys.argv[1:], accept="--accept" in sys.argv[1:])
    )
