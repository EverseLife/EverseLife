# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Every language says the same things, with the same values in them (D-251 V).

Translating a message file is not translating a document: the sentence carries
machinery, and the machinery has to survive the crossing. Three things can be
lost silently, and none of them raises anything at render time --

* a **message**. Fluent falls back to nothing: the key itself reaches the eye.
* an **argument**. `{ $left }` dropped from the English line means the deadline
  is simply not said, and the sentence still reads perfectly well.
* a **function**. `NAME($goods)` written as `{ $goods }` prints `iron_ore` in
  the middle of the sentence -- exactly the defect wave IV spent itself on.

A fourth thing is not lost but broken in place: a substituted **name under a
preposition** (D-258). The name arrives in the nominative and nothing can
decline it, so «Перелить в { $target }» renders «Перелить в канистра». A
number under a preposition is fine («из { $cap }»), and quotes are the legal
escape («из «{ NAME($goods) }»» -- the text then ends with the quote, not the
preposition). Checked per language, for the languages that decline.

So the check is parity against the default language, per message: the same
ids, the same `$names`, the same functions. Wording is nobody's business here.

Variant **keys** are checked too, and only where they are identifiers that the
code matches on -- `[true]`, `[election]`, `[plowing]`. Those are values of an
enum and belong to the code, not to the language. A numeric select (`[0]`,
`[one]`, `*[other]`) is the language's own business: Russian counts in three
forms and English in two, and demanding parity there would be demanding a
mistranslation.

Both halves of the game are checked by this one script, and on purpose. The
engine's words live next door in `backend/locales`; the window's own live in
`frontend/src/locales`, in the very same format, and `--tree` points the check
at them. A second copy of this rule written in JavaScript would drift from this
one within a wave -- a convention written twice is a convention that drifts --
so the client's files are read by the code that already knows the rule, and
the script lives here because the Fluent parser does.

    python tools/check_locales.py
    python tools/check_locales.py --tree ../frontend/src/locales
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from fluent.syntax import FluentParser
from fluent.syntax import ast as ftl

#: Whose words are checked unless `--tree` says otherwise: the engine's own.
LOCALES = Path(__file__).resolve().parent.parent / "locales"

#: A variant key that is a plural category or a number is the language's to
#: choose. Anything else is an identifier the code selects on.
PLURAL = {"zero", "one", "two", "few", "many", "other"}

#: The prepositions of every language that declines (D-258): a substitution
#: right after one of these is a case the string demands and nobody forms.
#: A new cased language adds its list here; a language absent here is not
#: checked -- English survives any preposition.
PREPOSITIONS = {
    "ru": [
        "в",
        "во",
        "на",
        "из",
        "изо",
        "к",
        "ко",
        "с",
        "со",
        "от",
        "до",
        "у",
        "о",
        "об",
        "обо",
        "за",
        "под",
        "над",
        "надо",
        "при",
        "про",
        "без",
        "безо",
        "через",
        "для",
        "по",
        "перед",
        "передо",
        "среди",
        "между",
        "вокруг",
        "после",
        "кроме",
        "ради",
        "сквозь",
        "вместо",
    ],
}

#: Arguments that carry a number or a date, which any preposition may govern
#: («из { $cap }», «в мире с { $since }»). The check cannot tell a name from
#: a number by syntax, so the counted ones are named; a new counted argument
#: under a preposition is added here, a *name* never is -- for a name the
#: string itself is rebuilt (D-258): a colon label, quotes, or a « · » detail.
COUNTED = {
    "advised",
    "all",
    "amount",
    "area",
    "cap",
    "capacity",
    "days",
    "floors",
    "frm",
    "ground",
    "hours",
    "left",
    "limit",
    "max",
    "min",
    "minutes",
    "need",
    "needed",
    "needs",
    "norm",
    "of",
    "pages",
    "per",
    "permitted",
    "price",
    "principal",
    "rate",
    "since",
    "slots",
    "span",
    "support",
    "term",
    "times",
    "to",
    "total",
    "until",
    "yes",
}


def _walk(node: object):
    stack = [node]
    while stack:
        one = stack.pop()
        yield one
        for value in vars(one).values() if hasattr(one, "__dict__") else ():
            if isinstance(value, ftl.BaseNode):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, ftl.BaseNode))


def shape(entry: ftl.Message) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """What a message interpolates, calls and selects on -- ignoring its words."""
    names: set[str] = set()
    functions: set[str] = set()
    keys: set[str] = set()
    for node in _walk(entry):
        if isinstance(node, ftl.VariableReference):
            names.add(node.id.name)
        elif isinstance(node, ftl.FunctionReference):
            functions.add(node.id.name)
        elif (
            isinstance(node, ftl.Variant)
            and isinstance(node.key, ftl.Identifier)
            and node.key.name not in PLURAL
        ):
            keys.add(node.key.name)
    return frozenset(names), frozenset(functions), frozenset(keys)


def messages(locale: str, only: str | None = None, tree: Path = LOCALES) -> dict[str, ftl.Message]:
    """Every message of one language, by id, read from all of its files.

    `only` narrows to a single file, which is how a translation in progress is
    checked: the whole-language comparison is meaningless until every file
    exists, and waiting until then to find a dropped argument is waiting too
    long.
    """
    found: dict[str, ftl.Message] = {}
    for path in sorted((tree / locale).glob(only or "*.ftl")):
        for entry in FluentParser().parse(path.read_text(encoding="utf-8")).body:
            if isinstance(entry, ftl.Junk):
                raise SystemExit(f"{path.name}: не разобрано -- {entry.content.strip()[:80]}")
            if isinstance(entry, ftl.Message) and entry.value is not None:
                found[entry.id.name] = entry
    return found


def compare(default: str, locale: str, only: str | None = None, tree: Path = LOCALES) -> list[str]:
    ours, theirs = messages(default, only, tree), messages(locale, only, tree)
    problems = [f"{locale}: нет сообщения «{key}»" for key in sorted(set(ours) - set(theirs))]
    problems += [f"{locale}: лишнее сообщение «{key}»" for key in sorted(set(theirs) - set(ours))]
    for key in sorted(set(ours) & set(theirs)):
        for what, mine, yours in zip(
            ("аргумент", "функция", "ветка"), shape(ours[key]), shape(theirs[key]), strict=True
        ):
            for lost in sorted(mine - yours):
                problems.append(f"{locale}: «{key}» потерял {what} {lost}")
            odd = "лишнюю" if what == "ветка" else "лишний"
            for gained in sorted(yours - mine):
                problems.append(f"{locale}: «{key}» получил {odd} {what} {gained}")
    return problems


def declined(locale: str, only: str | None = None, tree: Path = LOCALES) -> list[str]:
    """Substituted names put under a preposition (D-258) -- per one language.

    Structural, not a grep: the parser already splits a pattern into text and
    placeables, so the check looks for a text element that *ends* with a
    preposition right before a placeable -- variants of a select included.
    The quoted escape passes by construction: «из «{ NAME($goods) }»» ends
    with the quote, not the preposition.
    """
    prepositions = PREPOSITIONS.get(locale)
    if not prepositions:
        return []
    tail = re.compile(r"(?:^|[\s(«\"—–-])(?:" + "|".join(prepositions) + r")\s+$", re.IGNORECASE)
    problems: list[str] = []
    for path in sorted((tree / locale).glob(only or "*.ftl")):
        for entry in FluentParser().parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(entry, ftl.Message):
                continue
            for pattern in (node for node in _walk(entry) if isinstance(node, ftl.Pattern)):
                for text, place in zip(pattern.elements, pattern.elements[1:], strict=False):
                    if not (
                        isinstance(text, ftl.TextElement)
                        and isinstance(place, ftl.Placeable)
                        and tail.search(text.value)
                    ):
                        continue
                    what = place.expression
                    if isinstance(what, ftl.VariableReference) and what.id.name in COUNTED:
                        continue
                    if isinstance(what, ftl.FunctionReference) and what.id.name == "NUMBER":
                        continue
                    said = text.value.split()[-1]
                    problems.append(
                        f"{locale}: «{entry.id.name}» ({path.name}) ставит подстановку "
                        f"под предлог «{said}» -- имя не склоняется (D-258)"
                    )
    return problems


def main() -> int:
    #: `check_locales.py en city.ftl` -- одна пара файлов, пока перевод идёт.
    parser = argparse.ArgumentParser(description="сверить языки между собой")
    parser.add_argument("locale", nargs="?", help="сверить только этот язык")
    parser.add_argument("only", nargs="?", help="сверить только этот файл (city.ftl)")
    parser.add_argument(
        "--tree",
        type=Path,
        default=LOCALES,
        help="папка с языками (по умолчанию backend/locales)",
    )
    args = parser.parse_args()
    tree: Path = args.tree
    asked, only = args.locale, args.only
    #: An empty or missing tree is not "the languages agree": a check that
    #: silently checked nothing is a check nobody notices for years.
    languages = sorted(p.name for p in tree.iterdir() if p.is_dir()) if tree.is_dir() else []
    if not languages:
        print(f"нет языков в {tree}", file=sys.stderr)
        return 1
    default, *rest = languages
    #: Русский — язык вольта и точка отсчёта, где бы он ни оказался в алфавите.
    if "ru" in languages:
        rest = [name for name in languages if name != "ru"]
        default = "ru"
    if not rest:
        print(f"локали: один язык ({default}), сверять не с чем")
        return 0

    if asked:
        rest = [asked]
    problems: list[str] = []
    for locale in rest:
        problems += compare(default, locale, only, tree)
    #: The default language answers for its own cases too: the declension
    #: rule is per language, not a parity with anything.
    for locale in {default, *rest}:
        problems += declined(locale, only, tree)
    if problems:
        print(f"локали разошлись: {len(problems)} расхождени(й)\n", file=sys.stderr)
        for line in problems[:60]:
            print(f"  {line}", file=sys.stderr)
        if len(problems) > 60:
            print(f"  ... и ещё {len(problems) - 60}", file=sys.stderr)
        return 1

    said = len(messages(default, only, tree))
    where = f" в {only}" if only else ""
    whose = "" if tree == LOCALES else f" в {tree}"
    print(
        f"локали сходятся{whose}: {said} сообщений{where} "
        f"в каждом из ({default}, {', '.join(rest)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
