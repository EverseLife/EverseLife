# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The names table (D-251): the wire speaks ids, the model reads «Имя [id]»,
and an id the server does not know stays raw."""

from __future__ import annotations

from typing import Any

from aps import names, prompt


def test_an_id_gets_its_russian_name_and_an_unknown_one_stays_raw() -> None:
    """D-251, wave II: the wire speaks ids, the model reads «Имя [id]» and
    quotes the id -- the same convention the digest uses for node keys."""
    assert names.label("goods", "iron_ore") == "iron_ore"
    names.install(
        {
            "goods": {"iron_ore": "Железная руда"},
            "virtual_stations": {"coin_station": "Монетная станция"},
        }
    )
    assert names.label("goods", "iron_ore") == "Железная руда [iron_ore]"
    #: A station standing in a place is a thing: the goods domain covers both.
    assert names.label("goods", "coin_station") == "Монетная станция [coin_station]"
    assert names.label("goods", "mystery_thing") == "mystery_thing"
    assert names.label("tiers", "good") == "good"
    #: And the system prompt teaches the convention.
    assert "из квадратных скобок" in prompt.SYSTEM


async def test_renames_are_fetched_once_and_a_failure_is_retried() -> None:
    class Flaky:
        calls = 0
        broken = True

        async def public(self, path: str) -> Any:
            assert path == "renames"
            self.calls += 1
            if self.broken:
                raise RuntimeError("404")
            return {"names_ru": {"goods": {"salt": "Соль"}}}

    game = Flaky()
    #: A server without the endpoint leaves ids raw and does not cache the
    #: failure: the next turn asks again.
    await names.ensure(game)
    assert names.label("goods", "salt") == "salt"
    game.broken = False
    await names.ensure(game)
    assert game.calls == 2
    assert names.label("goods", "salt") == "Соль [salt]"
    #: Loaded is loaded: the table is per process, not per turn.
    await names.ensure(game)
    assert game.calls == 2
