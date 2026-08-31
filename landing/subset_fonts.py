# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Cut the typefaces down to the letters this site can actually show.

    python landing/subset_fonts.py            # regenerate fonts/ from fonts/src/
    python landing/subset_fonts.py --check    # what a page needs, does a font have it?

The fonts arrived already cut to Latin and Cyrillic, and half of even that is
dead weight: the landing puts 167 distinct characters on screen and the files
carry 305 to 332. They are also the bulk of the page -- 131 KB of the 171 KB
the front page fetches -- which matters because a good part of the audience
reads this over a throttled link, where every kilobyte is time spent looking
at a blank column.

`fonts/src/` is the source and is never served; `fonts/` is what the browser
gets. Keeping the source in the repository is the whole reason this is safe:
a subset is a lossy operation, and without it a sentence that one day needs a
character nobody thought of could only be fixed by finding the right release
of the right typeface again -- an instruction that rots the moment it is
written down.

What is kept is not only what the pages say today. Every letter of both
alphabets goes in whether or not it is currently used, along with the
punctuation prose grows on its own, so that ordinary writing cannot quietly
lose a glyph. `--check` covers the rest: it reads the pages, the strings
site.js writes in and the refusals the server sends back, and says which
character no longer has a shape. That check belongs in the pre-commit hook
beside `lastmod.py --check`, for the same reason -- both go stale when the
text changes, and neither failure is visible by looking at the page you edited.

Needs `fonttools[woff]`: `pip install fonttools brotli`. Nothing at runtime
imports this, and the landing image does not carry it.
"""

from __future__ import annotations

import argparse
import re
import string
import sys
import unicodedata
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).parent
SOURCE = ROOT / "fonts" / "src"
SERVED = ROOT / "fonts"

#: Where the site's words live. The pages are the obvious half; the other half
#: is text no page contains -- the carousel labels and form answers site.js
#: writes in, and the refusals app.py sends back.
WORD_FILES = ("site.js", "app.py", "site.css")

#: Both alphabets whole, not just the letters in today's copy: a subset that
#: tracked the text exactly would break on the next sentence somebody writes,
#: and it would break silently, one letter falling back to another typeface.
ALWAYS = (
    set(string.printable[:95])
    | {chr(c) for c in range(0x0410, 0x0450)}
    | set("Ёё")
    #: The punctuation Russian prose grows on its own: dashes, the quotes an
    #: editor substitutes, the ellipsis, the no-break space, and the few signs
    #: a page about a game with an economy reaches for.
    | set("—–‘’“”«»… ")
    | set("−×→←·•′″°№₽")
)


def spoken() -> set[str]:
    """Every character the landing can put in front of a reader."""
    said = []
    for page in sorted(ROOT.glob("*.html")) + sorted((ROOT / "en").glob("*.html")):
        text = page.read_text(encoding="utf-8")
        #: Comments and scripts are not shown; their text would drag in the
        #: whole of whatever a developer once wrote in a note.
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
        text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
        said.append(re.sub(r"<[^>]+>", " ", text))
    for name in WORD_FILES:
        said.append((ROOT / name).read_text(encoding="utf-8"))
    return {c for c in "".join(said) if ord(c) > 31}


def sources() -> list[Path]:
    found = sorted(SOURCE.glob("*.woff2"))
    if not found:
        raise SystemExit(f"no source fonts in {SOURCE}")
    return found


def cut(keep: set[str]) -> int:
    text = "".join(sorted(keep))
    before = after = 0
    for src in sources():
        dst = SERVED / src.name
        subset.main(
            [
                str(src),
                f"--text={text}",
                #: Kerning and the rest travel with the glyphs: dropping them
                #: saves little and shows up as loose spacing in headlines.
                "--layout-features=*",
                "--flavor=woff2",
                f"--output-file={dst}",
            ]
        )
        before += src.stat().st_size
        after += dst.stat().st_size
        print(f"fonts: {src.name:24} {src.stat().st_size:>7} -> {dst.stat().st_size:>7}")
    print(f"fonts: {len(keep)} characters, {before} -> {after} bytes ({before - after} saved)")
    return 0


def check() -> int:
    need = spoken()
    missing: dict[str, set[str]] = {}
    for src in sources():
        served = SERVED / src.name
        if not served.is_file():
            print(f"fonts: {served.name} is not built -- run `python landing/subset_fonts.py`")
            return 1
        have = set(TTFont(str(served), lazy=True).getBestCmap())
        gone = {c for c in need if ord(c) not in have}
        #: Only what the source could have given: a character missing from the
        #: original typeface is not this script's doing, and saying so would
        #: make the check unfixable.
        could = set(TTFont(str(src), lazy=True).getBestCmap())
        gone = {c for c in gone if ord(c) in could}
        if gone:
            missing[served.name] = gone

    if not missing:
        print(f"fonts: {len(sources())} faces, every one of {len(need)} characters has a shape")
        return 0
    for name, gone in sorted(missing.items()):
        #: By code point and name, never the character itself: a Windows
        #: console on a legacy code page cannot encode what it is being told
        #: about, and the report would die on the one line that matters.
        shown = ", ".join(f"U+{ord(c):04X} {unicodedata.name(c, 'unnamed')}" for c in sorted(gone))
        print(f"fonts: {name} cannot show {shown}")
    print("fonts: the text moved past the subset -- run `python landing/subset_fonts.py`")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not rebuild; report characters the served fonts cannot show",
    )
    args = parser.parse_args()
    return check() if args.check else cut(spoken() | ALWAYS)


if __name__ == "__main__":
    sys.exit(main())
