# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The game's words, by language (D-251 wave III).

Two languages are equal (D-249), so no text may be written into the code that
produces it: the engine raises a **key** with arguments, and the sentence is
assembled here, in the language of whoever is listening.

    engine:   raise NoGoods(key="goods-not-enough", goods="iron_ore", short=3)
    api:      answer = {"refused": i18n.render(key, args, locale=state.locale),
                        "code": key, "args": args}

The format is Fluent (FTL): Russian plurals and genders are its own business
rather than a helper of ours, and the same file format feeds the client's
`@fluent/bundle`. A message names a thing by its D-251 id and the bundle turns
it into a word: `NAME($goods)` -- so the id travels the wire and the reader
sees «Железная руда».

Loading is like the constants': whole or not at all, once at start, into one
process-wide cell. A locale that fails to parse must fail the boot, not the
first refusal of the evening.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from fluent.runtime import FluentBundle, FluentResource
from fluent.syntax.ast import Junk, Message, Term

log = logging.getLogger(__name__)

#: The language a session speaks unless it says otherwise, and the one every
#: other language falls back to. Russian is not "the original" -- D-249 makes
#: the languages equal -- it is simply the one that is complete today.
DEFAULT_LOCALE = "ru"

#: What a session may ask for. A language appears here when its files do.
LOCALES: tuple[str, ...] = ("ru", "en")


class MissingMessage(Exception):
    """A key no locale has. A bug in the caller, never a refusal to a player."""


def spoken(locale: str | None) -> str | None:
    """The language we serve for this spelling, or None if we serve none.

    `ru-RU`, `RU` and `ru` are one language; `kl` is not one of ours. The two
    answers are told apart because the callers want different things: reading
    falls back, choosing refuses.
    """
    if not locale:
        return None
    short = str(locale).replace("_", "-").split("-")[0].lower()
    return short if short in LOCALES else None


def event_key(kind: str) -> str:
    """The message that tells about an event of this kind (wave IV).

    A dot is not allowed in a Fluent message name, and an event's kind is full
    of them (`craft.finished`), so the dots become dashes and nothing else
    changes -- an underscore is a legal identifier character and stays. The
    rule is a function rather than a convention because the client applies the
    very same one, and a convention written twice is a convention that drifts.
    """
    return "event-" + kind.replace(".", "-")


def normalize(locale: str | None) -> str:
    """Any spelling of a language to the one we serve, or the default.

    Deliberately forgiving: a stored `ru-RU`, a stale client sending `RU`, or
    a language we do not have yet must not break a session -- it reads the
    world in the default language instead. Whoever is *choosing* a language
    asks `spoken()` and refuses on None.
    """
    return spoken(locale) or DEFAULT_LOCALE


class Words:
    """The parsed messages of every language, ready to render."""

    def __init__(self, bundles: dict[str, FluentBundle], sources: dict[str, str]) -> None:
        self._bundles = bundles
        #: The FTL as written: the client parses the same text with its own
        #: bundle, so one file feeds both ends and cannot drift between them.
        self._sources = sources

    def has(self, key: str, *, locale: str = DEFAULT_LOCALE) -> bool:
        bundle = self._bundles.get(normalize(locale))
        return bundle is not None and bundle.has_message(key)

    def render(self, key: str, args: Mapping[str, Any] | None = None, *, locale: str) -> str:
        """The sentence for this key in this language.

        Falls back to the default language, and then -- loudly, in the log --
        to the key itself: a player seeing `goods-not-enough` is a visible bug
        report, while a crash inside a refusal would hide the original refusal.
        """
        for tried in (normalize(locale), DEFAULT_LOCALE):
            bundle = self._bundles.get(tried)
            if bundle is None or not bundle.has_message(key):
                continue
            message = bundle.get_message(key)
            if message.value is None:  # pragma: no cover -- an attributes-only message
                continue
            text, errors = bundle.format_pattern(message.value, dict(args or {}))
            for error in errors:
                log.warning("message %r in %r: %s", key, tried, error)
            return text
        log.error("no message %r in any locale", key)
        return key

    def keys(self, locale: str = DEFAULT_LOCALE) -> set[str]:
        """Every key the language knows. The completeness test reads this."""
        bundle = self._bundles.get(normalize(locale))
        return set(bundle._messages) if bundle is not None else set()  # noqa: SLF001

    def source(self, locale: str) -> str:
        """The FTL text as written, for the client's own bundle."""
        return self._sources.get(normalize(locale), "")


#: The message functions that turn an id into a word, and the kind of id each
#: one takes. One per namespace on purpose: ids collide between them -- `stone`
#: is «Камень» among goods and «каменный» among building kinds -- so a single
#: merged lookup would render one of them wrong without ever saying so. Which
#: domains stand behind each name is the caller's business (`NAME_DOMAINS`).
NAME_FUNCTIONS: tuple[str, ...] = ("NAME", "KIND", "PLANET", "TIER", "SLOT", "CULTURE")

#: The plural of the two that are handed lists -- what a machine can be made
#: of, what a house may be built from. Fluent takes no list argument (it
#: refuses anything but a string or a number), so the ids arrive joined and
#: are split here. Written as a function rather than joined in Python because
#: the separator is the language's: an English list is not punctuated the way
#: a Russian one is, and that decision belongs in the locale, not the engine.
LIST_FUNCTIONS: dict[str, str] = {"NAMES": "NAME", "KINDS": "KIND"}

#: What separates the ids on the way in, and the words on the way out.
LIST_IN = ","
LIST_OUT = ", "


def load_words(locales_dir: Path, names: Callable[..., str] | None = None) -> Words:
    """Read `locales/<lang>/*.ftl` into a bundle per language.

    `names(id, locale, function)` turns a content id into a word of that
    language -- it is what `NAME()` and its siblings call. Injected rather than
    imported so that this package stays the bottom layer it looks like: it
    knows how to say things, not what the world contains.
    """
    bundles: dict[str, FluentBundle] = {}
    sources: dict[str, str] = {}
    for locale in LOCALES:
        folder = Path(locales_dir) / locale
        files = sorted(folder.glob("*.ftl"))
        if not files:
            raise MissingMessage(f"нет файлов языка {locale!r} в {folder}")

        def word_for(function: str, _locale: str = locale) -> Callable[[Any], str]:
            def said(value: Any) -> str:
                key = str(value)
                return names(key, _locale, function) if names is not None else key

            return said

        def words_for(function: str, _locale: str = locale) -> Callable[[Any], str]:
            one = word_for(function, _locale)

            def said(value: Any) -> str:
                ids = [part.strip() for part in str(value).split(LIST_IN) if part.strip()]
                return LIST_OUT.join(one(entry) for entry in ids)

            return said

        bundle = FluentBundle(
            [locale],
            #: No isolating marks: they are invisible control characters, and
            #: the answers of this game are read as plain text -- in a terminal,
            #: in a chat, in a test's assertion.
            use_isolating=False,
            functions={
                **{name: word_for(name) for name in NAME_FUNCTIONS},
                **{many: words_for(one) for many, one in LIST_FUNCTIONS.items()},
            },
        )
        text = "\n".join(path.read_text(encoding="utf-8") for path in files)
        resource = FluentResource(text)
        #: A line Fluent could not parse becomes `Junk` and is dropped in
        #: silence -- so a typo would cost one message, discovered by a player.
        #: Read the parse instead: a broken locale fails the boot.
        broken = [entry for entry in resource.body if isinstance(entry, Junk)]
        if broken:
            spoiled = "; ".join(entry.content.strip().splitlines()[0] for entry in broken)
            raise MissingMessage(f"{locale}: не разобрано в {folder}: {spoiled}")
        #: `add_resource` keeps the first definition of an id and drops the
        #: rest without a word (fluent.runtime's own TODO): a message redeclared
        #: in a second file would shadow the first, unseen by the completeness
        #: tests, which read ids and not definitions. Fail the boot instead.
        #: Terms count too, spelled with their dash: they live in a namespace
        #: of their own, so a term and a message may share a name legally.
        counted = Counter(
            ("-" if isinstance(entry, Term) else "") + entry.id.name
            for entry in resource.body
            if isinstance(entry, (Message, Term))
        )
        doubled = sorted(name for name, times in counted.items() if times > 1)
        if doubled:
            raise MissingMessage(f"{locale}: message defined twice: {', '.join(doubled)}")
        bundle.add_resource(resource)
        bundles[locale] = bundle
        sources[locale] = text
    return Words(bundles, sources)


class WordsHolder:
    """The process's messages. Set once at boot, like the constants'."""

    def __init__(self) -> None:
        self._current: Words | None = None

    def set(self, words: Words) -> None:
        self._current = words

    def current(self) -> Words:
        current = self._current
        if current is None:
            raise MissingMessage("словари не загружены: их ставит bootstrap")
        return current

    def is_loaded(self) -> bool:
        return self._current is not None


HOLDER = WordsHolder()


def current() -> Words:
    return HOLDER.current()


def render(key: str, args: Mapping[str, Any] | None = None, *, locale: str) -> str:
    return current().render(key, args, locale=locale)


def clauses(said: Iterable[Any], *, locale: str) -> list[str]:
    """Several messages, each said on its own, in this language (wave IV).

    An explanation is a list of facts rather than a paragraph about them: what
    the credit limit is made of, what moved the rate. Handed over as a list,
    nobody downstream has to take a rendered sentence apart again -- and it
    could not be done safely anyway, because a clause carries the punctuation
    of its own language: Russian writes 0,5 with the comma a list would be
    strung together by.
    """
    words = current()
    return [words.render(one.key, one.params, locale=locale) for one in said]


def join(said: Iterable[Any], *, locale: str) -> str:
    """Several messages as one phrase, in this language (wave IV).

    What a refusal quotes is often a list -- the buildings a city still lacks,
    the reasons a house may not come down -- and how a list is strung together
    is the language's business. `LIST_OUT` is the same separator `NAMES()`
    uses, so a quoted list and a list of names read alike.

    For a phrase. A list the reader is shown *as* a list goes over as one
    (`clauses`): joining it here only to split it there is how a decimal comma
    becomes a bullet point.
    """
    return LIST_OUT.join(clauses(said, locale=locale))


#: How a message travels inside a stored payload: the key under `say`, its
#: arguments under `args`. The same two fields the attention list already uses
#: (`api/commands/world.py`), so a message written into a column and a message
#: drawn by the client are one shape rather than two conventions.
SAID_KEY = "say"
SAID_ARGS = "args"


def written(said: Iterable[Any]) -> list[dict[str, Any]]:
    """Messages as payload rows, to be stored or sent.

    What is kept is the key and its numbers, never the sentence. A row written
    today is read back long after by somebody who need not read the language it
    was written in -- and by then the wording may have been edited in the
    locale, which is where wording is allowed to change.
    """
    return [{SAID_KEY: one.key, SAID_ARGS: dict(one.params)} for one in said]


def retold(rows: Iterable[Mapping[str, Any]] | None, *, locale: str) -> list[str]:
    """Stored rows read back as clauses, in the language of whoever is reading.

    The other half of `written`: the archive keeps keys, the edge says them.
    A row without a key is skipped rather than shown -- an old payload is not
    a reason to print `None` at somebody.
    """
    words = current()
    return [
        words.render(str(row[SAID_KEY]), row.get(SAID_ARGS) or {}, locale=locale)
        for row in rows or ()
        if row.get(SAID_KEY)
    ]
