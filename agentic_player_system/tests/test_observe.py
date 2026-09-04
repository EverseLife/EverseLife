# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The observation the agent reads: a digest of the `look`, what changed since
the last turn, and its own standing affairs beside it (D-226)."""

from __future__ import annotations

import json

from aps import names, observe


def test_observation_is_a_digest_with_changes_and_the_whole_look_every_few_turns() -> None:
    #: The wire speaks ids (D-251); the digest gives them back their Russian
    #: names as «Имя [id]», the id staying quotable in commands.
    names.install({"goods": {"bioprinter": "Биопринтер", "bread": "Хлеб", "pickaxe": "Кирка"}})
    first = {
        "look": {
            "identity": "Марта",
            "money": "120",
            "body": {"stamina": 90.0, "sleeping_since": None},
            "node": {"name": "Ядро", "key": "terra.capital.core", "owner_city": "Столица"},
            "carry": {"load": 3.0, "capacity": 30.0},
            "bench": [{"goods": "bioprinter", "busy": False}],
            "exits": [{"key": "terra.capital.market", "name": "Рынок", "seconds": 5}],
            "city": {
                "name": "Столица",
                "node": "terra.capital",
                "citizen": False,
                "admission": "open",
            },
            "inventory": [{"goods": "bread", "amount": 2}],
            "doings": [],
            "travel": None,
            "clock": {"now": "1"},
        }
    }
    second = json.loads(json.dumps(first))
    second["look"]["money"] = "95"
    second["look"]["inventory"].append({"goods": "pickaxe", "amount": 1})
    second["look"]["clock"]["now"] = "2"

    text, mode = observe.observation(None, first, full=False, packed="{}")
    assert mode == "full" and "Полный look" in text
    text, mode = observe.observation(first, second, full=False, packed="x" * 5000)
    assert mode == "delta"
    assert "деньги 95" in text and "Сумка (2)" in text
    #: Worn gear left `inventory` for `carry.equipped` (D-305): without a line
    #: of its own the agent would carry an exoskeleton it never knew it had.
    assert "Надето:" not in text, "нечего надевать — нечего и говорить"
    #: The shape of `look` after D-226: stations are the things standing here,
    #: citizenship lives in `city`, and the ways out are named in the digest.
    assert "Станции здесь: Биопринтер [bioprinter]" in text
    assert "Выходы: Рынок [terra.capital.market] 5с" in text
    assert "ты не гражданин" in text and "несёшь 3/30 кг" in text
    assert "money: 120 → 95" in text and "появилось Кирка [pickaxe]×1" in text
    assert "clock" not in text
    text, mode = observe.observation(first, second, full=True, packed="{}")
    assert mode == "full"
    #: A diff no shorter than the whole thing is pointless: show the whole thing.
    text, mode = observe.observation(first, second, full=False, packed="{}")
    assert mode == "full"


def test_digest_names_what_is_worn() -> None:
    """Gear stands apart from the sack on the wire since D-305, and the digest
    keeps it: an agent that cannot see its pack cannot take it off or mend it."""
    names.install({"goods": {"exoskeleton": "Экзоскелет", "bread": "Хлеб"}})
    look = {
        "look": {
            "identity": "Марта",
            "money": "120",
            "body": {"stamina": 90.0, "sleeping_since": None},
            "node": {"name": "Ядро", "key": "terra.capital.core"},
            "carry": {
                "load": 13.0,
                "capacity": 130.0,
                "equipped": {"frame": {"id": "1", "goods": "exoskeleton", "amount": 1}},
            },
            "inventory": [{"goods": "bread", "amount": 2}],
            "doings": [],
            "travel": None,
            "clock": {"now": "1"},
        }
    }
    text, _ = observe.observation(None, look, full=True, packed="{}")
    assert "Надето: Экзоскелет [exoskeleton]" in text
    assert "Сумка (1)" in text, "надетое не считается за содержимое сумки"


def test_digest_says_whose_the_ground_is() -> None:
    """Wild land is nobody's and needs no title (D-198): an agent that did not
    know it hunted for a way to own a node instead of building on one."""
    wild = {
        "look": {
            "identity": "Марта",
            "money": "10",
            "body": {"stamina": 90.0},
            "node": {"name": "Поляна", "key": "terra.wild.1"},
            "floor": {"mine": True},
        }
    }
    assert "Участок ничей: строить и ставить оборудование здесь можно." in observe.digest(wild)

    someones = json.loads(json.dumps(wild))
    someones["look"]["node"]["owner"] = "Пётр"
    someones["look"]["floor"]["mine"] = False
    text = observe.digest(someones)
    assert "владелец Пётр" in text and "Участок ничей" not in text

    own = json.loads(json.dumps(wild))
    own["look"]["node"]["owner"] = "Марта"
    text = observe.digest(own)
    assert "Участок твой" in text


def test_digest_says_the_ground_is_about_to_move() -> None:
    """The window before an eruption is the whole licence for the burning
    (D-197, P6), and an agent that has to dig the hour out of the raw `look`
    never digs -- it would stand in a field and lose everything it carried."""
    quiet = {
        "look": {
            "identity": "Марта",
            "money": "10",
            "body": {"stamina": 90.0},
            "node": {"name": "Чёрное поле", "key": "pyroxis.anvil.field.01"},
        }
    }
    assert "ЗЕМЛЯ ТРОНЕТСЯ" not in observe.digest(quiet)

    warned = json.loads(json.dumps(quiet))
    warned["look"]["node"]["shaking_at"] = "2026-09-01T12:00:00+00:00"
    text = observe.digest(warned)
    assert "ЗЕМЛЯ ТРОНЕТСЯ здесь в 2026-09-01T12:00:00+00:00" in text
    #: And what to do about it, or the warning is a decoration.
    assert "сгорит" in text and "улететь" in text


def test_events_heard_between_turns_open_the_observation() -> None:
    #: Things named by wire id in an event get the same «Имя [id]» as the
    #: digest; a key the table does not know (a node key) stays raw.
    names.install({"goods": {"pickaxe": "Кирка"}})
    told = observe.happened(
        [
            {"event": "knowledge.learned", "seq": 5, "touches": ["knowledge"], "key": "pickaxe"},
            {
                "event": "travel.arrived",
                "seq": 6,
                "touches": ["body", "node"],
                "who": "Тэрн",
                "node": {"key": "terra.mine", "name": "Забой"},
            },
        ]
    )
    assert told.splitlines() == [
        "- knowledge.learned · key: Кирка [pickaxe]",
        '- travel.arrived · кто: Тэрн · node: {"key": "terra.mine", "name": "Забой"}',
    ]
    assert observe.happened([]) == ""


def test_the_purse_is_shown_in_both_units() -> None:
    """`look` gives coins, every price is in ten-thousandths: 56 of 194 refusals."""
    assert observe.money("25") == "деньги 25 монет (в ценах команд это 250000)"
    assert observe.money("0") == "деньги 0 монет (в ценах команд это 0)"
    assert observe.money("0.5") == "деньги 0.5 монет (в ценах команд это 5000)"
    #: Nonsense from the server must not take the digest down with it.
    assert observe.money(None) == "деньги None"


def test_standing_affairs_are_named_or_declared_empty() -> None:
    """An agent that does not see its own orders posts them again and waits for
    a delivery it never ordered -- both in the journal."""
    names.install(
        {
            "goods": {
                "iron_ore": "Железная руда",
                "mine_support": "Шахтная крепь",
                "iron_part": "Железная деталь",
            }
        }
    )
    own = {
        "orders": {
            "orders": [
                {"id": "o-1", "side": "buy", "goods": "iron_ore", "price": 30000, "left": 5.0}
            ],
            "reservations": [
                {
                    "id": "r-1",
                    "goods": "mine_support",
                    "amount": 5.0,
                    "node": "Рынок",
                    "expires_at": "2026-09-02T03:52:32+00:00",
                }
            ],
            "batches": [{"output": "iron_part", "units": 1, "ready_at": "2026-08-28T12:00:00"}],
        }
    }
    said = observe.standing(own)
    assert "покупка «Железная руда [iron_ore]» ×5 по 30000 [o-1]" in said
    assert "Шахтная крепь [mine_support]" in said and "[r-1]" in said
    assert "Железная деталь [iron_part]" in said
    assert observe.standing({"orders": {"orders": [], "reservations": [], "batches": []}}) == (
        "Ни заявок, ни броней, ни партий, ни товара в терминале — ждать нечего."
    )


def test_the_terminal_shelf_says_what_is_free_to_sell() -> None:
    """`market.sell` refuses on the free amount; the shelf minus own sell orders
    in this node is the only way to know it before being refused."""
    names.install(
        {
            "goods": {"iron_ore": "Железная руда", "salt": "Соль"},
            "tiers": {"good": "хорошее", "common": "обычное"},
        }
    )
    look = {
        "look": {
            "node": {"key": "terra.capital.market", "name": "Рынок"},
            "stall": [
                {"goods": "iron_ore", "tier": "good", "amount": 5.0},
                {"goods": "salt", "tier": "common", "amount": 2.0},
            ],
        }
    }
    own = {
        "orders": {
            "orders": [
                {
                    "id": "o1",
                    "side": "sell",
                    "goods": "iron_ore",
                    "tier": "good",
                    "price": 30000,
                    "left": 3.0,
                    "node_key": "terra.capital.market",
                },
                #: The same goods committed in another node must not be
                #: subtracted from the shelf standing here.
                {
                    "id": "o2",
                    "side": "sell",
                    "goods": "salt",
                    "tier": "common",
                    "price": 100,
                    "left": 2.0,
                    "node_key": "terra.other",
                },
            ],
            "reservations": [],
            "batches": [],
        }
    }
    said = observe.standing(own, look)
    assert "«Железная руда [iron_ore]» (хорошее [good]) ×5, свободно 2" in said
    assert "(обычное [common]) ×2;" in said or "(обычное [common]) ×2." in said
    assert "свободно 2; «Соль" not in said.replace("×5, свободно 2", "")


def test_a_batch_says_why_it_is_not_moving() -> None:
    """«away» means walk back to the machine, not wait -- the difference is the turn."""
    names.install({"goods": {"nails": "Гвозди"}})
    frozen = {
        "orders": {
            "orders": [],
            "reservations": [],
            "batches": [{"output": "nails", "units": 200, "waiting": "away", "node": "Кузница"}],
        }
    }
    said = observe.standing(frozen)
    assert "Гвозди [nails] ×200" in said and "тебя нет у станка в Кузница" in said


def test_long_lists_say_how_many_were_left_out() -> None:
    """Silently cut orders are orders the agent posts a second time."""
    many = [
        {"id": f"o{i}", "side": "sell", "goods": "salt", "price": 10, "left": 1}
        for i in range(observe.STANDING_ROWS + 3)
    ]
    said = observe.standing({"orders": {"orders": many, "reservations": [], "batches": []}})
    assert "…и ещё 3" in said


def test_a_server_that_does_not_place_orders_makes_the_shelf_cautious() -> None:
    """Against a server without the node on an order, every sell order counts
    against the shelf: a shelf that looks all free sent the agent into
    `market.take` eighteen times in ten minutes."""
    look = {
        "look": {
            "node": {"key": "terra.capital.market"},
            "stall": [{"goods": "iron_ore", "tier": "good", "amount": 5.0}],
        }
    }
    old = {
        "orders": {
            "orders": [
                {
                    "id": "o1",
                    "side": "sell",
                    "goods": "iron_ore",
                    "tier": "good",
                    "price": 30000,
                    "left": 5.0,
                }
            ],
            "reservations": [],
            "batches": [],
        }
    }
    said = observe.standing(old, look)
    assert "свободно не больше 0" in said


def test_the_digest_translates_node_features_and_the_climate() -> None:
    """Node features and `frost.climate` come as ids since D-251; the digest
    keeps talking to the model in Russian."""
    names.install({"node_properties": {"stones": "камни", "meadow": "луг"}})
    seen = {
        "look": {
            "identity": "Марта",
            "money": "10",
            "body": {"stamina": 90.0},
            "node": {"name": "Поляна", "key": "terra.wild.1", "features": ["stones", "meadow"]},
            "frost": {"climate": "frost", "hours": 0, "max": 12, "per_hour": 0, "at": "x"},
        }
    }
    text = observe.digest(seen)
    assert "есть: камни [stones], луг [meadow]" in text
    assert "ЗАМЁРЗ (здесь мороз)" in text
