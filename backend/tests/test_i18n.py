# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The words of the game, and the promise that every key has some (D-251 III).

A refusal is a key now, and the sentence is assembled at the edge. Two things
must hold for that to be an improvement rather than a way to lose messages:
every key a raise site names must exist in every language, and a key that does
not must be visible rather than fatal.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src import i18n
from src.engine.errors import Refusal, Says

SRC = Path(__file__).resolve().parent.parent / "src"


#: What names a message besides `raise X(key=...)`. A refusal is not always
#: raised where it is built (`farm.py` builds one into a variable), the socket
#: has a helper of its own (`_said`), and an invention's note is a message that
#: is returned rather than raised. Each of those was invisible to the first
#: version of this scan, which is exactly how a typo would reach a player.
KEY_ARGUMENTS = ("key", "note_key")

#: The field a payload names its own line with. Not every message is a refusal:
#: a line of the attention list is a message the client draws, and it travels
#: as `{"say": "attention-case", "args": {...}}`. Without this the five of them
#: would look like messages nobody asks for -- and a typo in one would reach a
#: player as `attention-case` on the screen.
#:
#: Read from `i18n` rather than spelled again: a rate decision stores its
#: reasons in the very same two fields (wave IV), and a convention written
#: twice is a convention that drifts.
SAY_FIELD = i18n.SAID_KEY
ARGS_FIELD = i18n.SAID_ARGS

#: Calls whose first argument is a message key. `render` is the blunt one: it
#: is how anything said at the edge in a fixed language names its message --
#: the chronicle, and the one line the server says into a room. Matched by the
#: bare name, so both `render(...)` and `i18n.render(...)` are seen.
NAMED_BY = {"_said", "Says", "render"}


def _keys_named(path: Path) -> set[str]:
    """Every message key this module names, however it names it.

    Read from the parse rather than by a pattern: an example in a docstring is
    not a call site, and the first version of this test failed on one.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if not (isinstance(key, ast.Constant) and key.value == SAY_FIELD):
                    continue
                #: Every string under `"say"`, not only a bare one: a payload
                #: that picks between two lines (`"a" if law else "b"`) names
                #: both, and both owe a message.
                found |= {
                    part.value
                    for part in ast.walk(value)
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                }
            continue
        if not isinstance(node, ast.Call):
            continue
        #: `_said("session-...", state)`, `Says("doing-...", {...})` and
        #: `i18n.render("chat-hands-over", ...)` -- the key is the first
        #: argument. Written as the plain name so that both `render(...)` and
        #: `i18n.render(...)` are seen: the last one was added by a line the
        #: server says into a room, and it was invisible until it was.
        if isinstance(node.func, ast.Name | ast.Attribute) and NAMED_BY.intersection(
            {getattr(node.func, "id", None), getattr(node.func, "attr", None)}
        ):
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
        for word in node.keywords:
            if word.arg in KEY_ARGUMENTS and isinstance(word.value, ast.Constant):
                found.add(str(word.value.value))
    return found


def _derived_keys() -> set[str]:
    """Keys nothing spells out: built from a value at the point of use.

    Five families: an occupation's one-word title is `doing-<kind>`
    (`occupation.Doing.title`), a founding role's word is `city-role-<role>`,
    a digest line is `event-<kind>` (`i18n.event_key`), and a statement line
    names both its ground and the side it faces. Each is listed from the
    code's own enum or tuple rather than from a copy kept here, so a kind, a
    role, an event or a posting ground added without its word fails this suite
    rather than showing a player `doing-whatever`.
    """
    from src.api.commands.world import TOLD, TOLD_OF_THE_PLACE
    from src.engine import explore, occupation
    from src.engine.city import founding
    from src.herald import chronicle
    from src.models.ledger import AccountKind, PostingReason

    return (
        {f"doing-{kind}" for kind in occupation.KINDS}
        | {f"city-role-{role}" for role in founding.FOUNDATION_ROLES}
        #: Every event the digest is allowed to tell about owes a line. The
        #: list is the server's own allowlist, so the two cannot drift: an
        #: event added to the digest without a line fails here rather than
        #: showing the player `plates.erupted`.
        | {i18n.event_key(kind) for kind in TOLD | TOLD_OF_THE_PLACE}
        #: The statement reads by these two: on what ground the money moved,
        #: and who stood on the other side. Both are enums the client is
        #: handed raw, and a member without a word is shown as its own code --
        #: which is how `works_fund` reached the screen before this wave.
        | {f"ledger-ground-{reason.value}" for reason in PostingReason}
        | {f"ledger-side-{kind.value}" for kind in AccountKind}
        #: What the chronicle puts where a name should be when the row it
        #: pointed at is gone. Named through a constant rather than spelled at
        #: the call site, so the scan cannot see them -- read from the module's
        #: own constants, which is what makes a rename follow.
        | {chronicle.UNKNOWN, chronicle.NOWHERE}
        #: What a search may look for. The word used to be a Russian noun in a
        #: map beside the goal; now the goal names a message, and a goal added
        #: without one would leave the refusal naming a key.
        | {f"explore-goal-{goal}" for goal in explore.GOALS}
    )


def ast_walk(node: object):
    """Every Fluent node under this one. `ast.walk` for the other syntax tree."""
    stack = [node]
    while stack:
        one = stack.pop()
        yield one
        for value in vars(one).values() if hasattr(one, "__dict__") else ():
            if hasattr(value, "__dict__") and not isinstance(value, str):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(item for item in value if hasattr(item, "__dict__"))


def _variables_of(pattern: object) -> set[str]:
    """The `$name`s a Fluent pattern interpolates, at any depth."""
    from fluent.syntax import ast as ftl

    seen: set[str] = set()
    stack = [pattern]
    while stack:
        node = stack.pop()
        if isinstance(node, ftl.VariableReference):
            seen.add(node.id.name)
        for value in vars(node).values() if hasattr(node, "__dict__") else ():
            if isinstance(value, ftl.BaseNode):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, ftl.BaseNode))
    return seen


def test_every_raised_key_exists_in_every_language(words: i18n.Words) -> None:
    """The check that makes the conversion safe to do module by module.

    A typo in a key is not a crash and not a wrong sentence -- it is the key
    itself shown to the player, which nobody notices until they do. Here it is
    a failing test the moment the module is converted.
    """
    raised: dict[str, str] = {key: "ключ выводится из значения" for key in _derived_keys()}
    for path in sorted(SRC.rglob("*.py")):
        for key in _keys_named(path):
            raised.setdefault(key, path.relative_to(SRC).as_posix())
    assert raised, "ни одного отказа с ключом: регулярное выражение отстало от кода"

    for locale in i18n.LOCALES:
        known = words.keys(locale)
        missing = sorted(f"{key} ({where})" for key, where in raised.items() if key not in known)
        assert not missing, f"нет сообщений для языка {locale}: " + ", ".join(missing)


def test_no_message_is_written_for_nobody(words: i18n.Words) -> None:
    """A key nothing names is either a typo at the call site or dead weight.

    The other half of the completeness check: the first test catches a call
    site whose key has no message, this one a message whose key has no call
    site -- and a misspelled `key=` produces both at once.
    """
    named: set[str] = set(_derived_keys())
    for path in sorted(SRC.rglob("*.py")):
        named |= _keys_named(path)
    orphans = sorted(words.keys(i18n.DEFAULT_LOCALE) - named)
    assert not orphans, "сообщения, которых никто не называет: " + ", ".join(orphans)


def test_every_message_gets_the_arguments_it_interpolates(words: i18n.Words) -> None:
    """A message may not ask for a `$name` its call site never passes.

    Fluent does not fail on a missing argument -- it prints the variable's own
    name into the sentence -- so this cannot be caught by rendering, only by
    reading both sides. Variants of a `select` are excluded on purpose: only
    one branch is ever taken, and the others may legitimately want more.

    **Every site apart, not all of them together.** The sets used to be
    merged per key, and a key raised from three places passed the check while
    two of them passed nothing: `craft-batch-too-big` gained a `$most` at one
    site, and the mint and the invention went on printing the word "most" into
    the player's refusal. One site short is the whole bug.
    """
    from fluent.syntax import FluentParser
    from fluent.syntax import ast as ftl

    passes: dict[str, list[set[str]]] = {}
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            #: A payload that names its own line: `{"say": ..., "args": {...}}`.
            #: The two fields sit side by side in one dict, so both are read
            #: from it -- a line of the attention list is a message like any
            #: other and owes the same coverage.
            if isinstance(node, ast.Dict):
                fields = {
                    key.value: value
                    for key, value in zip(node.keys, node.values, strict=True)
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                if SAY_FIELD not in fields:
                    continue
                given = {
                    name.value
                    for args in ast.walk(fields.get(ARGS_FIELD, ast.Constant(None)))
                    if isinstance(args, ast.Dict)
                    for name in args.keys
                    if isinstance(name, ast.Constant) and isinstance(name.value, str)
                }
                for part in ast.walk(fields[SAY_FIELD]):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        passes.setdefault(part.value, []).append(given)
                continue
            if not isinstance(node, ast.Call):
                continue
            #: A message named positionally: `Says("time-left", {...})`,
            #: `i18n.render("chat-hands-over", {...})`. The key is the first
            #: argument and the parameters the second, and until this was read
            #: forty-one keys were named by nobody the check could see -- every
            #: `chronicle-*` among them, which are the most argument-heavy
            #: messages in the repository.
            if NAMED_BY.intersection(
                {getattr(node.func, "id", None), getattr(node.func, "attr", None)}
            ):
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    #: Two shapes at once, because the three calls differ:
                    #: `Says("k", {...})` and `render("k", {...})` carry their
                    #: parameters in the second positional argument, while
                    #: `_said("k", state, field=...)` carries them as keywords.
                    #: Reading only the first shape marked every `_said` key as
                    #: passing nothing, which read as a failure rather than as
                    #: the blind spot it was.
                    passes.setdefault(first.value, []).append(
                        {
                            name.value
                            for said in node.args[1:2]
                            for part in ast.walk(said)
                            if isinstance(part, ast.Dict)
                            for name in part.keys
                            if isinstance(name, ast.Constant) and isinstance(name.value, str)
                        }
                        | {
                            word.arg
                            for word in node.keywords
                            if word.arg and word.arg not in KEY_ARGUMENTS
                        }
                    )
            key = next(
                (
                    str(word.value.value)
                    for word in node.keywords
                    if word.arg in KEY_ARGUMENTS and isinstance(word.value, ast.Constant)
                ),
                None,
            )
            if key is None:
                continue
            given = {word.arg for word in node.keywords if word.arg not in KEY_ARGUMENTS}
            #: A quoted message arrives as `inner={"what": [...]}`: the dict's
            #: own keys are the arguments the outer message will find (wave IV).
            #: Walked rather than matched, because a site may assemble it --
            #: `{"what": …} | ({} if … else {"left": …})` is one raise site
            #: with two dicts in it.
            for word in node.keywords:
                if word.arg != "inner":
                    continue
                for inner in ast.walk(word.value):
                    if isinstance(inner, ast.Dict):
                        given |= {
                            name.value
                            for name in inner.keys
                            if isinstance(name, ast.Constant) and isinstance(name.value, str)
                        }
            passes.setdefault(key, []).append(given)

    resource = FluentParser().parse(words.source(i18n.DEFAULT_LOCALE))
    short: list[str] = []
    for entry in resource.body:
        if not isinstance(entry, ftl.Message) or entry.value is None:
            continue
        key = entry.id.name
        if key not in passes:
            continue
        #: The plain text of the pattern, without the branches of a select.
        wanted = {
            name
            for element in entry.value.elements
            if isinstance(element, ftl.Placeable)
            and not isinstance(element.expression, ftl.SelectExpression)
            for name in _variables_of(element)
        }
        selectors = {
            name
            for element in entry.value.elements
            if isinstance(element, ftl.Placeable)
            and isinstance(element.expression, ftl.SelectExpression)
            for name in _variables_of(element.expression.selector)
        }
        for given in passes[key]:
            missing = (wanted | selectors) - given
            if missing:
                short.append(f"{key}: не передано {sorted(missing)}")
    assert not short, "; ".join(short)


def test_languages_say_the_same_things(words: i18n.Words) -> None:
    """No language may know a key another does not (D-249: they are equal)."""
    by_locale = {locale: words.keys(locale) for locale in i18n.LOCALES}
    complete = set.union(*by_locale.values())
    for locale, known in by_locale.items():
        assert not (complete - known), f"{locale} не знает: {sorted(complete - known)}"


def test_a_name_is_said_in_words_not_in_keys(words: i18n.Words) -> None:
    """`NAME($id)` is the whole point: the id travels, the word is read."""
    said = words.render("storage-relic", {"goods": "iron_ore"}, locale="ru")
    assert "Железная руда" in said
    assert "iron_ore" not in said


def test_numbers_are_written_the_way_the_language_writes_them(words: i18n.Words) -> None:
    """A decimal comma is not decoration: it is what Russian does with 12.3."""
    said = words.render(
        "storage-chest-full", {"chest": "chest", "free": 12.34, "mass": 3.5}, locale="ru"
    )
    assert "12,3" in said and "3,5" in said


def test_a_choice_inside_a_message_belongs_to_the_message(words: i18n.Words) -> None:
    """Which of two words to use is grammar, and grammar lives in the locale."""
    vessel = words.render(
        "storage-mismatch", {"goods": "water", "chest": "canister", "why": "vessel"}, locale="ru"
    )
    chest = words.render(
        "storage-mismatch", {"goods": "water", "chest": "chest", "why": "chest"}, locale="ru"
    )
    assert "тара берёт только жидкость" in vessel
    assert "жидкость держат в таре" in chest


def test_a_list_shown_as_a_list_travels_as_one(words: i18n.Words) -> None:
    """`clauses` hands over the facts; `join` is for a phrase (wave IV).

    The two differ in exactly the way that matters: a clause carries the
    punctuation of its own language -- Russian writes 12,3 with the comma
    `join` strings a phrase together by -- so a joined list cannot be taken
    apart again. Whoever draws a list asks for the list.
    """
    said = [
        Says("bank-why-limit-base", {"money": "900"}),
        Says("bank-why-limit-turnover", {"money": "1 200", "days": 7}),
    ]
    apart = i18n.clauses(said, locale="ru")
    assert len(apart) == 2, "оговорка на строку"
    assert i18n.join(said, locale="ru") == i18n.LIST_OUT.join(apart), "фраза — из тех же слов"
    #: The number brings a comma of its own into the clause, which is the
    #: whole reason the list is not shipped as one string.
    assert (
        "12,3"
        in i18n.clauses(
            [Says("storage-chest-full", {"chest": "chest", "free": 12.34, "mass": 3.5})],
            locale="ru",
        )[0]
    )


def test_a_stored_message_keeps_its_key_and_is_said_on_the_way_out(words: i18n.Words) -> None:
    """What a column or an event payload keeps: keys, never a sentence.

    An archive row outlives the language it was written in and the wording it
    was written with. Stored as keys, one row is said to a Russian and to an
    Englishman alike, and an edit in the locale reaches the history too.
    """
    said = [
        Says("bank-why-limit-no-overdue"),
        Says("bank-why-limit-trust", {"trust": 80}),
    ]
    rows = i18n.written(said)
    assert rows[0] == {"say": "bank-why-limit-no-overdue", "args": {}}
    assert rows[1]["args"] == {"trust": 80}
    assert i18n.retold(rows, locale="ru") == i18n.clauses(said, locale="ru")
    #: The way it comes back out of JSONB: a plain list of plain dicts.
    assert i18n.retold(json.loads(json.dumps(rows)), locale="ru") == i18n.clauses(said, locale="ru")
    #: Nothing to say is not something to say: an absent column and an empty
    #: one both mean the row has no stated reasons.
    assert i18n.retold(None, locale="ru") == []
    assert i18n.retold([], locale="ru") == []
    assert i18n.retold([{"args": {}}], locale="ru") == [], "строка без ключа — не сообщение"


def test_an_unknown_key_is_shown_rather_than_thrown(words: i18n.Words) -> None:
    """A missing message must not hide the refusal it was meant to carry."""
    assert words.render("no-such-message-anywhere", locale="ru") == "no-such-message-anywhere"


def test_an_unknown_language_reads_the_default_one(words: i18n.Words) -> None:
    assert i18n.normalize("kl") == i18n.DEFAULT_LOCALE
    assert i18n.normalize(None) == i18n.DEFAULT_LOCALE
    #: A stored `ru-RU` or a shouting client are the same language.
    assert i18n.normalize("ru-RU") == "ru"
    assert i18n.normalize("RU") == "ru"
    assert words.render("storage-nothing-to-put", locale="kl") == "класть нечего"


def test_a_broken_locale_stops_the_boot(tmp_path: Path) -> None:
    """A line Fluent cannot parse is dropped in silence -- unless we look."""
    folder = tmp_path / i18n.DEFAULT_LOCALE
    folder.mkdir(parents=True)
    (folder / "broken.ftl").write_text("good = так\nэто не сообщение\n", encoding="utf-8")
    with pytest.raises(i18n.MissingMessage, match="не разобрано"):
        i18n.load_words(tmp_path)


def test_a_message_defined_twice_stops_the_boot(tmp_path: Path) -> None:
    """`add_resource` keeps the first definition and drops the second silently.

    The completeness tests read ids, not definitions, so a message redeclared
    in a second file would shadow the first without a single signal anywhere --
    not at load, not in the parity check, not at render.
    """
    folder = tmp_path / i18n.DEFAULT_LOCALE
    folder.mkdir(parents=True)
    (folder / "a.ftl").write_text("greeting = привет\n", encoding="utf-8")
    (folder / "b.ftl").write_text("greeting = здравствуйте\n", encoding="utf-8")
    with pytest.raises(i18n.MissingMessage, match="defined twice: greeting"):
        i18n.load_words(tmp_path)


def test_every_member_of_an_enum_gets_its_own_variant(words: i18n.Words) -> None:
    """A message that selects on an enum owes every member a branch of its own.

    `_derived_keys` guards families of **keys** -- `doing-<kind>`,
    `ledger-ground-<reason>` -- and nothing guarded families of **variants**.
    A member added without a branch falls into the catch-all and is simply
    read as the wrong thing: a new `VoteKind` would be announced as «закон»,
    quietly and forever. That is the same defect as `works_fund` reaching the
    statement as its own code, which this wave was fixing.

    The branches are read out of the FTL rather than inferred from what the
    renders look like. The first version of this test compared renders and
    passed with a branch deliberately removed: the member had fallen into a
    catch-all named `*[unknown]`, which is not a member, so its text collided
    with nothing. Comparing output cannot see a hole whose default happens to
    say something nobody else says; the parse can.
    """
    from fluent.syntax import FluentParser
    from fluent.syntax import ast as ftl

    from src.models.farm import PlotState
    from src.models.market import OrderState, ReservationState
    from src.models.mining import SessionState
    from src.models.vote import VoteKind

    bound = (
        ("attention-vote-kind", "kind", VoteKind),
        ("chronicle-vote-closed", "kind", VoteKind),
        ("farm-not-fallow", "state", PlotState),
        #: Four more that used to print the enum's own value at the player --
        #: «заявка уже cancelled», «сессия закрыта: collapsed». English read
        #: acceptably by accident, which is exactly what would have hidden it.
        ("market-order-not-active", "state", OrderState),
        ("market-order-already", "state", OrderState),
        ("market-reservation-not-held", "state", ReservationState),
        ("mining-session-closed", "state", SessionState),
    )
    resource = FluentParser().parse(words.source(i18n.DEFAULT_LOCALE))
    messages = {
        entry.id.name: entry
        for entry in resource.body
        if isinstance(entry, ftl.Message) and entry.value is not None
    }
    for key, field, enum in bound:
        assert key in messages, f"{key}: сообщения нет"
        branches: set[str] = set()
        for node in ast_walk(messages[key]):
            selects = isinstance(node, ftl.SelectExpression) and _variables_of(node.selector) == {
                field
            }
            if selects:
                branches |= {
                    variant.key.name
                    for variant in node.variants
                    if isinstance(variant.key, ftl.Identifier)
                }
        assert branches, f"{key}: нет выбора по ${field}"
        missing = sorted(member.value for member in enum if member.value not in branches)
        assert not missing, f"{key}: у членов перечисления нет своей ветки: {', '.join(missing)}"


def test_a_refusal_carries_its_key_and_numbers() -> None:
    """The engine names the reason; nobody down there knows the language."""
    refusal = Refusal(key="storage-chest-full", chest="chest", free=1.0, mass=2.0)
    assert refusal.key == "storage-chest-full"
    assert refusal.params == {"chest": "chest", "free": 1.0, "mass": 2.0}


def test_an_unconverted_refusal_still_speaks() -> None:
    """Six hundred sites convert module by module: the rest must keep working."""
    refusal = Refusal("это старая строка")
    assert refusal.key is None
    assert str(refusal) == "это старая строка"


def test_a_converted_refusal_still_has_a_str_for_the_operator() -> None:
    """`jobs.py` writes `str(exc)` into `job.last_error` and into the log.

    A converted site carries no text, and `str()` of it used to be empty:
    the operator read `"CraftError: "` where a reason had been. The key and
    the numbers are the diagnostic; the player-facing sentence is the edge's
    business, not this one's.
    """
    assert str(Refusal(key="craft-job-without-batch", job="42")) == (
        "craft-job-without-batch (job='42')"
    )
    assert str(Refusal(key="death-job-dangling")) == "death-job-dangling"
    #: A legacy sentence, when there is one, still wins: it is the reason.
    assert str(Refusal("своя строка", key="some-key")) == "своя строка"


@pytest.fixture
def words(loaded: object) -> i18n.Words:
    """The real locale files -- this suite is about them, not about a fixture.

    Takes `loaded` for the boot: `NAME()` resolves through the rename table,
    so the words are only whole once the vault is.
    """
    return i18n.current()
