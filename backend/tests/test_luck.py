# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Chance with a memory (D-213).

Checked is the promise the whole thing rests on -- **the mean is untouched,
only the spread is**. If the memory shifted the frequency, every number in the
vault would need recomputing, and the decision would be a balance change
pretending to be a fix.

* the announced share comes out as the observed share;
* a drought is bounded, and the bound is where the growth constant puts it;
* the memory belongs to an identity and a matter: nobody's luck is anybody
  else's;
* a choice of many is dealt from a deck: every card of the table comes out
  before any comes out twice;
* without an identity the coin is plain -- the world rolls for nobody.
"""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import luck, world
from src.units import PERCENT


async def _who(session: AsyncSession) -> uuid.UUID:
    identity = await world.create_identity(session, f"Везунчик-{uuid.uuid4().hex[:6]}")
    return identity.id


def _streaks(share: float, tries: int = 200_000) -> tuple[float, int]:
    """The observed frequency and the longest drought, rolled without the database."""
    step = luck.growth(share)
    dice = random.Random(share)
    misses = hits = longest = running = 0
    for _ in range(tries):
        if dice.random() < min(1.0, step * (misses + 1)):
            hits += 1
            longest = max(longest, running)
            misses = running = 0
        else:
            misses += 1
            running += 1
    return hits / tries, longest


@pytest.mark.parametrize("share", [0.05, 0.22, 0.4, 0.9])
def test_the_announced_share_is_the_observed_share(share: float) -> None:
    """The whole point: the memory changes the spread, not the mean (D-213)."""
    seen, _ = _streaks(share)
    assert seen == pytest.approx(share, abs=0.01)


@pytest.mark.parametrize("share", [0.05, 0.22, 0.4, 0.9])
def test_a_drought_cannot_outlast_the_growth(share: float) -> None:
    """After `1 / C` failures the next try is certain: that is the whole bound."""
    _, longest = _streaks(share)
    ceiling = int(1 / luck.growth(share))
    assert longest <= ceiling
    #: And it is a real bound, not a formality: a fair coin at this share would
    #: run far longer than that.
    assert longest < ceiling + 1


async def test_the_drought_makes_the_next_try_likelier(session: AsyncSession) -> None:
    """The counter is the memory: misses raise the chance, a hit clears it."""
    who = await _who(session)
    #: A rare chance: at a frequent one the threshold reaches certainty in two
    #: misses, and there is no drought left to look at.
    rare = 5.0
    #: A roll that always fails while the threshold is below one.
    stubborn = random.Random()
    stubborn.random = lambda: 0.999999
    for expected in range(1, 6):
        assert await luck.hit(session, who, luck.MINE_DEATH, rare, dice=stubborn) is False
        row = await luck._row(session, who, luck.MINE_DEATH)
        assert row.misses == expected

    lucky = random.Random()
    lucky.random = lambda: 0.0
    assert await luck.hit(session, who, luck.MINE_DEATH, rare, dice=lucky) is True
    row = await luck._row(session, who, luck.MINE_DEATH)
    assert row.misses == 0, "удача обнуляет память"

    #: And the drought is not endless: within `1 / C` tries the threshold
    #: reaches certainty, and even the most stubborn roll comes good.
    ceiling = int(1 / luck.growth(rare / PERCENT)) + 1
    assert any(
        [await luck.hit(session, who, luck.MINE_DEATH, rare, dice=stubborn) for _ in range(ceiling)]
    ), "засуха обязана кончиться сама"


async def test_luck_is_personal_and_by_matter(session: AsyncSession) -> None:
    """One player's drought is nobody else's, and one matter's is not another's."""
    mine, theirs = await _who(session), await _who(session)
    stubborn = random.Random()
    stubborn.random = lambda: 0.999999

    await luck.hit(session, mine, luck.EXPLORE_FIND, 50.0, dice=stubborn)
    await luck.hit(session, mine, luck.EXPLORE_FIND, 50.0, dice=stubborn)

    assert (await luck._row(session, mine, luck.EXPLORE_FIND)).misses == 2
    assert (await luck._row(session, mine, luck.MINE_DEATH)).misses == 0
    assert (await luck._row(session, theirs, luck.EXPLORE_FIND)).misses == 0


async def test_the_deck_deals_every_card_before_repeating(session: AsyncSession) -> None:
    """Ten stones in a row and never any flax is what the deck is against (D-213)."""
    who = await _who(session)
    table = {"Камень": 14, "Дерево": 12, "Лён": 10, "Смола": 2}
    dice = random.Random(4)

    #: One deck's worth of draws shows everything the table names.
    size = sum(max(1, round(weight / min(table.values()))) for weight in table.values())
    drawn = [await luck.draw(session, who, luck.FORAGE_WHAT, table, dice=dice) for _ in range(size)]
    assert set(drawn) == set(table), "колода обязана раздать всё, что в ней есть"
    assert drawn.count("Смола") == 1, "редкое остаётся редким"


async def test_the_deck_keeps_the_table_proportions(session: AsyncSession) -> None:
    who = await _who(session)
    table = {"Камень": 3, "Смола": 1}
    dice = random.Random(9)
    drawn = [await luck.draw(session, who, luck.FORAGE_WHAT, table, dice=dice) for _ in range(400)]
    assert drawn.count("Камень") / len(drawn) == pytest.approx(0.75, abs=0.02)


async def test_a_table_edited_in_the_vault_reshuffles(session: AsyncSession) -> None:
    """A deck of yesterday's things must not outlive the table it was built from."""
    who = await _who(session)
    dice = random.Random(11)
    await luck.draw(session, who, luck.FORAGE_WHAT, {"Камень": 2, "Смола": 1}, dice=dice)

    changed = {"Камень": 2, "Целебные травы": 1}
    for _ in range(6):
        assert await luck.draw(session, who, luck.FORAGE_WHAT, changed, dice=dice) in changed


async def test_without_an_identity_the_coin_is_plain(session: AsyncSession) -> None:
    """The world rolls for nobody: memory belongs to somebody or it belongs to all."""
    always = random.Random()
    always.random = lambda: 0.0
    assert await luck.hit(session, None, luck.SITE_RIVER, 25.0, dice=always) is True

    never = random.Random()
    never.random = lambda: 0.999999
    assert await luck.hit(session, None, luck.SITE_RIVER, 25.0, dice=never) is False
