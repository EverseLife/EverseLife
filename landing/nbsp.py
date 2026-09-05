# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Tie short words to the word after them, so no line ends on a preposition.

Russian typography does not leave a one- or two-letter word at the end of a
line: "Вселенная, в которой у" hands the reader a word that means nothing
until the next line arrives. CSS cannot say this -- `text-wrap: balance` only
evens the lines out, and it was already on the heading that broke -- so the
space after such a word has to be a non-breaking one in the markup itself.

    python nbsp.py            # tie what is still loose
    python nbsp.py --check    # say whether a run is due (CI, pre-commit)

Only the Russian text is touched. English ends lines on "a" and "of" without
anybody minding, and the same spaces there would be a rule nobody asked for.

The pages are the obvious half; the other half is the copy no page contains --
the lines `site.js` writes into the form and the carousel, and the refusals
`app.py` sends back. `subset_fonts.py` already counts those two files as text
the site can show, and a line of it wraps in a narrow box like any other one.

In a page only the body is touched, never the `<head>`: a search engine counts
a title and a description in characters, and a space that is not a space would
be counted with them. Scripts and styles are skipped for their own reason --
what looks like text in them is code.

A unit is tied to its number the other way round -- "46 ч" is one thing, and
the number is what would be left hanging.

What the rule does not reach: copy inside an attribute (`aria-label`,
`placeholder`, `alt`), and a short word that ends an element, with the space
living after the closing tag. The pages have none of that last kind today.

A run changes the pages' bytes, so `lastmod.py` comes after it.

Exit code is 1 when `--check` finds a page with a loose short word, so a hook
can stop the commit and say what to run.
"""

import re
import sys
from pathlib import Path

from app import DEFAULT_LANG, PAGE_LANG, PAGES

ROOT = Path(__file__).parent

NBSP = "\u00a0"

#: Cyrillic by code point rather than by letter, the way the rest of the
#: repository writes it -- see `backend/tools/check_copy.py`.
CYRILLIC = "\u0400-\u04ff"

#: A short word: one or two Cyrillic letters standing on their own. The rule is
#: about length, not about a list of prepositions -- "в", "у", "и", "но" and
#: "не" read the same way at the end of a line, and a hand-kept list would be
#: missing one of them by the next paragraph anybody writes. The lookbehinds
#: keep the tail of a longer word out of it: neither "его" nor the "то" of
#: "чем-то" is a short word.
SHORT = rf"(?<![^\W\d_])(?<!-)(?<![\d}}]\s)([{CYRILLIC}]{{1,2}})"

#: The space to tie is whatever whitespace follows -- a newline in the source
#: is a space on the page, so a short word left at the end of a source line is
#: loose too, and tying it pulls the next line up. What follows may be any word
#: at all: "в Discord" hangs exactly like "в городе", and a lookahead that asked
#: for Cyrillic would leave the site's own Latin nouns as the one place where
#: the rule quietly does not hold.
LOOSE = re.compile(SHORT + r"[ \t\n\r]+(?=[«(\"„]?[^\s<])")

#: The same word with a tag after it instead of a word: "и <b>дом</b>" breaks
#: after "и" like any other space, and the text to tie to is simply in the next
#: piece. Only inline tags -- a paragraph or a list item is a new line anyway,
#: and tying a space to the end of one would leave it hanging in the markup.
TAIL = re.compile(SHORT + r"[ \t\n\r]+$")

#: A short word right after a number is not a preposition but a unit, and it
#: belongs to the number in front of it: "46 ч" breaks between the two only in
#: a text nobody proof-read. The lookbehind above keeps such a word out of the
#: rule above, so that "${h} ч ${m}" is not tied the wrong way round.
UNIT = re.compile(rf"([\d}}])[ \t\n\r]+([{CYRILLIC}]{{1,3}})(?![{CYRILLIC}])")
INLINE = ("a", "b", "code", "em", "i", "mark", "span", "strong", "sub", "sup")

#: Inside these, text is not text. Everything else in the body is.
CODE = ("script", "style")

#: The copy that lives outside the pages: the lines the script writes in and
#: the refusals the service sends back. `subset_fonts.py` reads the same two
#: files for the same reason.
COPY = ("site.js", "app.py")

#: A quoted string, in any of the three quotes the two languages use. A literal
#: with Cyrillic in it is copy; a literal without is code, and code is left
#: alone -- that is the whole test, and it needs no list of variable names.
LITERAL = re.compile(r"(['\"`])((?:[^\\\n]|\\.)*?)\1")
SPEAKS = re.compile(f"[{CYRILLIC}]")


def _opens_inline(piece: str) -> bool:
    tag = re.match(r"<([a-zA-Z][a-zA-Z0-9]*)", piece)
    return bool(tag) and tag.group(1).lower() in INLINE


def tie(html: str) -> str:
    """The page with every loose short word tied to the word after it.

    Idempotent: a space already tied is no longer an ordinary one, so a second
    run finds nothing. That is what lets `--check` be a comparison.

    One case is left alone: a short word that ends an element, with the space
    living after the closing tag. The page has none, and the markup for it
    would have to put a non-breaking space where a reader of the file cannot
    see what it belongs to.
    """
    head, end, body = html.partition("</head>")
    if not end:
        raise SystemExit("nbsp: a page without a head is not one of ours")
    #: Splitting on `<...>` trusts that no attribute value carries a `>` of its
    #: own. None does today. If one ever did, the split would hand a piece of
    #: markup over as text and this would write a space into it, so the run
    #: stops instead of guessing where the text is.
    if set("<>") & set(re.sub(r"<[^>]*>", "", body)):
        raise SystemExit("nbsp: a stray `<` or `>` in the markup -- refusing to guess what is text")
    inside = ""
    pieces = re.split(r"(<[^>]*>)", body)
    written = []
    for at, piece in enumerate(pieces):
        if piece.startswith("<"):
            tag = re.match(r"</?([a-zA-Z][a-zA-Z0-9]*)", piece)
            if tag and tag.group(1).lower() in CODE:
                inside = "" if piece.startswith("</") else tag.group(1).lower()
            written.append(piece)
            continue
        if inside:
            written.append(piece)
            continue
        text = LOOSE.sub(rf"\1{NBSP}", UNIT.sub(rf"\1{NBSP}\2", piece))
        if at + 1 < len(pieces) and _opens_inline(pieces[at + 1]):
            text = TAIL.sub(rf"\1{NBSP}", text)
        written.append(text)
    return head + end + "".join(written)


def tie_copy(source: str) -> str:
    """A copy file with every loose short word inside its Russian strings tied.

    A line that ends on a short word ends on it for a reason: the script has
    something to put after it -- the sign-up answer stops at "мы в " and a link
    follows. So the tail is tied here as it is in the markup, and the reader
    never sees the seam.
    """

    def one(found: "re.Match[str]") -> str:
        quote, said = found.group(1), found.group(2)
        if not SPEAKS.search(said):
            return found.group(0)
        said = LOOSE.sub(rf"\1{NBSP}", UNIT.sub(rf"\1{NBSP}\2", said))
        return quote + TAIL.sub(rf"\1{NBSP}", said) + quote

    return LITERAL.sub(one, source)


def main(check: bool) -> int:
    work = [(path, file, tie) for path, file in PAGES.items() if PAGE_LANG[path] == DEFAULT_LANG]
    work += [(name, ROOT / name, tie_copy) for name in COPY]

    loose = []
    for name, file, rule in work:
        was = file.read_text(encoding="utf-8")
        now = rule(was)
        if now == was:
            continue
        loose.append(name)
        if not check:
            file.write_text(now, encoding="utf-8", newline="\n")

    if not loose:
        print("nbsp: nothing hangs at the end of a line")
        return 0
    if check:
        print("nbsp: short words left loose on " + ", ".join(loose), file=sys.stderr)
        print("nbsp: fix with `python landing/nbsp.py`", file=sys.stderr)
        return 1
    print("nbsp: tied short words on " + ", ".join(loose))
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv[1:]))
