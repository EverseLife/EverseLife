# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a refusal carries back to the model: the arguments it did not pass, the
way it need not name, and the wire's `code` the advice reads instead of the
words (D-251)."""

from __future__ import annotations

from typing import Any

import pytest
from brain_kit import SESSION_SOURCE, FakeGame, _arguments, _call

from aps import brain, commands, llm
from aps.game import Refused
from aps.store import Store


async def test_a_refusal_of_an_argumentless_call_carries_the_argument_list(
    monkeypatch: pytest.MonkeyPatch, store: Store, agent: dict[str, Any]
) -> None:
    """`act(cmd="market.buy")` with no args: the model gets the keys, not another turn."""
    seen: list[str] = []

    replies = iter(
        [
            llm.Reply(
                content="",
                tool_calls=[_call("act", cmd="market.buy")],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            llm.Reply(
                content="",
                tool_calls=[_call("finish", thought="поняла")],
                prompt_tokens=1,
                completion_tokens=1,
            ),
        ]
    )

    async def fake_chat(_p: Any, messages: list[dict[str, Any]], *_a: Any, **_k: Any) -> llm.Reply:
        seen.extend(str(m.get("content")) for m in messages if m.get("role") == "tool")
        return next(replies)

    monkeypatch.setattr(llm, "chat", fake_chat)
    game = FakeGame(
        {
            "look": {"money": 0},
            "market.buy": Refused(
                "команде не хватает поля «goods»", "session-field-missing", {"field": "goods"}
            ),
        }
    )
    await brain.run_turn(
        agent=agent,
        game=game,  # type: ignore[arg-type]
        store=store,
        provider=llm.Provider("u", "k", "m"),
        reference=commands.load(SESSION_SOURCE),
    )
    hint = "".join(seen)
    assert "ОТКАЗ: команде не хватает поля «goods»" in hint
    #: Проверяется, что подсказка **называет аргументы**, а не что список
    #: начинается с определённого. Порядок и состав приходят из реестра команд
    #: сервера: D-241 добавил `min_quality`, и равенство сломалось на фиче,
    #: которая ничего в поведении подсказки не изменила.
    assert "goods" in _arguments(hint, "market.buy")


async def test_a_missing_field_refusal_carries_the_arguments_even_with_args(
    monkeypatch: pytest.MonkeyPatch, store: Store, agent: dict[str, Any]
) -> None:
    """Half the arguments given is the commonest miss, not none of them."""
    seen: list[str] = []

    replies = iter(
        [
            llm.Reply(
                content="",
                tool_calls=[_call("act", cmd="market.buy", args={"goods": "Соль"})],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            llm.Reply(
                content="",
                tool_calls=[_call("finish", thought="поняла")],
                prompt_tokens=1,
                completion_tokens=1,
            ),
        ]
    )

    async def fake_chat(_p: Any, messages: list[dict[str, Any]], *_a: Any, **_k: Any) -> llm.Reply:
        seen.extend(str(m.get("content")) for m in messages if m.get("role") == "tool")
        return next(replies)

    monkeypatch.setattr(llm, "chat", fake_chat)
    game = FakeGame(
        {
            "look": {"money": 0},
            "market.buy": Refused(
                "команде не хватает поля «amount»", "session-field-missing", {"field": "amount"}
            ),
        }
    )
    await brain.run_turn(
        agent=agent,
        game=game,  # type: ignore[arg-type]
        store=store,
        provider=llm.Provider("u", "k", "m"),
        reference=commands.load(SESSION_SOURCE),
    )
    named = _arguments("".join(seen), "market.buy")
    assert {"goods", "tier", "price", "amount"} <= named, named


async def test_a_name_where_an_id_was_wanted_gets_the_arguments_back(
    monkeypatch: pytest.MonkeyPatch, store: Store, agent: dict[str, Any]
) -> None:
    """`badly formed hexadecimal UUID string` names no argument, and the model
    tries the next name it can read -- three station names in a row."""
    seen: list[str] = []
    replies = iter(
        [
            llm.Reply(
                content="",
                tool_calls=[
                    _call("act", cmd="craft.start", args={"output": "Слиток", "tool": "Кузница"})
                ],
                prompt_tokens=1,
                completion_tokens=1,
            ),
            llm.Reply(
                content="",
                tool_calls=[_call("finish", thought="поняла")],
                prompt_tokens=1,
                completion_tokens=1,
            ),
        ]
    )

    async def fake_chat(_p: Any, messages: list[dict[str, Any]], *_a: Any, **_k: Any) -> llm.Reply:
        seen.extend(str(m.get("content")) for m in messages if m.get("role") == "tool")
        return next(replies)

    monkeypatch.setattr(llm, "chat", fake_chat)
    game = FakeGame(
        {
            "look": {"money": 0},
            "craft.start": Refused(
                "команда не понята: badly formed hexadecimal UUID string",
                "session-not-understood",
                {"why": "badly formed hexadecimal UUID string"},
            ),
        }
    )
    await brain.run_turn(
        agent=agent,
        game=game,  # type: ignore[arg-type]
        store=store,
        provider=llm.Provider("u", "k", "m"),
        reference=commands.load(SESSION_SOURCE),
    )
    assert "tool:id" in "".join(seen)


def test_a_refusal_about_the_way_says_the_way_is_optional() -> None:
    """Only while the server has no ways to name (`craft-unknown-way` with an
    empty `ways`): otherwise the model guesses the next English word for it."""
    reference = commands.load(SESSION_SOURCE)
    bare = brain._advice(
        reference,
        "craft.start",
        {"output": "iron_ingot", "way": "forge"},
        Refused(
            "«Слиток» не делается способом «forge»",
            "craft-unknown-way",
            {"goods": "iron_ingot", "way": "forge", "known": "false", "ways": ""},
        ),
    )
    assert "без way игра берёт основной" in bare
    #: The server that names the ways needs no help, and the advice retires.
    named = brain._advice(
        reference,
        "craft.start",
        {"output": "iron_ingot", "way": "forge"},
        Refused(
            "«Слиток» не делается способом «forge»; способы: iron_smelting",
            "craft-unknown-way",
            {"goods": "iron_ingot", "way": "forge", "known": "true", "ways": "iron_smelting"},
        ),
    )
    assert named == ""


def test_advice_reads_the_code_and_never_the_words() -> None:
    """The sentence changes with the locale and with every edit of a message
    file (D-251): a refusal whose *words* carry the old marks but whose code
    is something else -- or nothing, an unconverted site -- gets no hint."""
    reference = commands.load(SESSION_SOURCE)
    for said in (
        Refused("команде не хватает поля «amount»"),
        Refused("не делается способом «forge»", "storage-no-room", {}),
    ):
        assert brain._advice(reference, "market.buy", {"goods": "salt"}, said) == "", said.code


def test_a_refusal_keeps_its_words_beside_the_code() -> None:
    """`Refused` stores the wire's arguments under `params`, never under the
    exception's own `args`: assigning a dict there coerces it to a tuple of
    its keys, and `str()` of the refusal becomes the first key -- the model
    would read «ОТКАЗ: field». Caught live while this was being written."""
    said = Refused("команде не хватает поля «goods»", "session-field-missing", {"field": "goods"})
    assert str(said) == "команде не хватает поля «goods»"
    assert said.code == "session-field-missing"
    assert said.params == {"field": "goods"}
    #: The wire may drop `args` entirely (`_without_nulls`) and an unconverted
    #: site sends no code at all: both read as "nothing", not as a crash.
    bare = Refused("нет столько")
    assert (bare.code, bare.params) == (None, {})
