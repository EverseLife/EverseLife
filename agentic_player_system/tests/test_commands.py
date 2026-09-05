# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The command reference, extracted from the session's own source: one short
clause per command, its arguments, and the marks that tell coins from prices
and an identifier from a name."""

from __future__ import annotations

import pytest
from brain_kit import SESSION_SOURCE

from aps import commands


def test_reference_is_extracted_from_the_session_source() -> None:
    reference = commands.load(SESSION_SOURCE)
    assert "city.found" in reference and "ship.found" in reference
    assert reference["ship.found"]["keys"] == ["name"]
    #: A handler taking a context reads its arguments differently; the
    #: reference must not go quietly empty as the game migrates to `Ctx`.
    from aps.commands import extract

    ctx_style = extract(
        '''
@command("thing.take")
async def _take(ctx: Ctx) -> dict:
    """Take a thing."""
    what = ctx.arg("thing")
    much = ctx.message["amount"]
    where = ctx.message.get("into")
    return {"took": [what, much, where]}
'''
    )
    assert ctx_style["thing.take"]["keys"] == ["thing", "amount", "into"]
    #: A handler that hands the whole request to a parser names no key in its
    #: own body. Believing that, an agent called `craft.plan` bare and got
    #: `KeyError('output')` all day (agents' finding, 2026-08-23).
    by_helper = extract(
        '''
def _craft_request(message):
    return message["output"], float(message.get("units", 1))


@command("craft.plan")
async def _plan(state, db, message) -> dict:
    """Forecast."""
    output, units = _craft_request(message)
    return {"plan": [output, units]}
'''
    )
    assert by_helper["craft.plan"]["keys"] == ["output", "units"]
    #: A parser that hands the request on in its turn: the batch's shape was
    #: split out of `_craft_request` into `_craft_shape`, and every key but
    #: `units` fell out of the reference the same day (CI, 2026-09-04).
    chained = extract(
        '''
def _craft_shape(message):
    return goods_key(message["output"]), _optional_uuid(message.get("tool"))


def _craft_request(message):
    return _craft_shape(message), float(message.get("units", 1))


@command("craft.start")
async def _start(state, db, message) -> dict:
    """Start a batch."""
    shape, units = _craft_request(message)
    return {"batch": [shape, units]}
'''
    )
    assert chained["craft.start"]["keys"] == ["units", "output", "tool"]
    assert chained["craft.start"]["ids"] == ["tool"]
    #: A parser that calls itself, a pair that call each other, and a name the
    #: map has never heard of: a chain is followed, not fallen into.
    circular = extract(
        '''
def _one(message):
    return _two(message), message["first"]


def _two(message):
    return _one(message), message["second"]


@command("thing.take")
async def _take(state, db, message) -> dict:
    """Take a thing."""
    return {"took": [_one(message), _gone(message)]}
'''
    )
    assert circular["thing.take"]["keys"] == ["first", "second"]
    #: The parser may live in a neighbouring module, or in the engine.
    borrowed = extract(
        '''
@command("account.update")
async def _update(state, db, message) -> dict:
    """Change the profile."""
    return accounts.check_profile(message)
''',
        {"check_profile": {"keys": ["surname", "age", "about"], "ids": []}},
    )
    assert borrowed["account.update"]["keys"] == ["surname", "age", "about"]

    #: And the real thing: what the agents tripped over must be named now.
    assert "output" in reference["craft.plan"]["keys"]
    assert "city" in reference["city.found"]["keys"] or reference["city.found"]["keys"] == ["name"]
    assert "plot" in reference["farm.sow"]["keys"]
    assert "spaceport" in reference["ship.found"]["doc"]
    assert "- city.found(name):" in commands.brief(reference)
    #: Every command an agent may run is in the reference, and none without a
    #: doc: the model reads the reference, not the code. The `hidden` ones are
    #: the other half of the same invariant -- a command declared out of the
    #: reference (the alpha's widget, D-229) must actually be out of it, or
    #: hiding it was decoration.
    import subprocess

    backend = SESSION_SOURCE.parents[2]
    interpreter = backend / ".venv" / "Scripts" / "python.exe"
    if not interpreter.exists():
        interpreter = backend / ".venv" / "bin" / "python"
    if not interpreter.exists():
        pytest.skip("нет venv бэкенда: реестр команд не с чем сверить")
    listed = subprocess.run(
        [
            str(interpreter),
            "-c",
            (
                "import src.api.session; from src.api.registry import COMMANDS; "
                "print(sum(not c.hidden for c in COMMANDS.values())); "
                "print(' '.join(n for n, c in COMMANDS.items() if c.hidden))"
            ),
        ],
        cwd=backend,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr[-500:]
    open_count, hidden_names = (listed.stdout.splitlines() + [""])[:2]
    assert len(reference) - 2 == int(open_count), "справочник не совпадает с реестром"
    assert [name for name in hidden_names.split() if name in reference] == [], (
        "скрытая команда всё-таки уехала агентам в промпт"
    )


def test_the_reference_is_one_short_clause_per_command() -> None:
    """The reference rides in every prompt: no vault numbers, no second sentence."""
    reference = commands.load(SESSION_SOURCE)
    lines = commands.brief(reference).splitlines()
    assert len(lines) == len(reference) - len(commands.BUILTIN)
    assert all(len(line) < 130 for line in lines), max(lines, key=len)
    assert not [line for line in lines if "D-" in line or line.rstrip().endswith(":")]
    assert any(l.startswith("- city.found(name): Found a city where you stand") for l in lines)


def test_headline_keeps_the_half_that_tells_commands_apart() -> None:
    """The colon introduces the substance in these docstrings; the vault number does not."""
    assert commands.headline("Buy: a limit order from a present body (D-101).") == (
        "Buy: a limit order from a present body"
    )
    assert commands.headline("Take a loan. Money comes from the reserve.") == "Take a loan"
    assert commands.headline("The most important screen (04-notifications)") == (
        "The most important screen"
    )
    long = commands.headline("Do " + "very " * 40 + "much")
    assert len(long) <= commands.HEADLINE_LIMIT + 1 and long.endswith("…")
    #: A docstring is prose wrapped to the width of the source: `ship.dock`
    #: broke `(D-289, wave 3)` over two lines, and the half of the pointer
    #: left on the first one rode into every prompt of every turn.
    wrapped = commands.headline(
        "Give this hull's consent to dock with the hull it holds on to (D-289,\n"
        "    wave 3). With the other commander's consent already given the two\n"
        "    are joined connector to connector."
    )
    assert wrapped == "Give this hull's consent to dock with the hull it holds on to"
    #: The wrap is read back, the blank line is not: a second paragraph is a
    #: second thought and stays out of the reference.
    assert commands.headline("Name a ship\n\n    Costs nothing.") == "Name a ship"


def test_identifier_arguments_are_marked_in_the_reference() -> None:
    """«Плавильная печь» where an id is wanted answers `badly formed hexadecimal
    UUID string`, which names no argument (11 refusals in the journal)."""
    reference = commands.load(SESSION_SOURCE)
    assert commands.argument_list(reference["market.reserve"]) == "order:id,amount"
    #: Through a helper: `craft.start` parses `tool` in `_craft_request`.
    assert "tool:id" in commands.argument_list(reference["craft.start"])
    #: A name is a name: goods are named, not identified.
    assert "goods:id" not in commands.argument_list(reference["market.buy"])
    assert "«:id»" in commands.help_text(reference, "market.reserve")


def test_sums_in_coins_are_marked_apart_from_prices() -> None:
    """A price is in ten-thousandths, a bank sum is in coins: mixing them up is
    the class of refusal the money line was added to kill, in reverse."""
    reference = commands.load(SESSION_SOURCE)
    assert "amount:coins" in commands.argument_list(reference["finance.transfer"])
    assert "amount:coins" in commands.argument_list(reference["bank.borrow"])
    #: A market price is not in coins and must not be marked.
    assert "price:coins" not in commands.argument_list(reference["market.buy"])
    assert "«:coins»" in commands.help_text(reference, "bank.borrow")


def test_the_coin_arguments_named_by_hand_still_exist() -> None:
    """The list is here because the conversion happens in `engine/bank.py`,
    across a call the parser does not follow -- so the game must be able to
    break it loudly."""
    reference = commands.load(SESSION_SOURCE)
    for command, keys in commands.COIN_ARGUMENTS.items():
        assert command in reference, command
        for key in keys:
            assert key in reference[command]["keys"], (command, key)


def test_an_identifier_passed_by_value_is_marked_too() -> None:
    """`_own_item(db, body, message["item"])`: the helper names the position,
    the call site names the key. Without it `storage.put` and `storage.take`
    disagreed about the same argument."""
    reference = commands.load(SESSION_SOURCE)
    assert "item:id" in commands.argument_list(reference["storage.put"])
    assert "item:id" in commands.argument_list(reference["storage.take"])
    assert "item:id" in commands.argument_list(reference["ground.drop"])
