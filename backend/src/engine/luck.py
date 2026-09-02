# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Chance with a memory (D-213).

A fair coin is not a fair deal. At a 22% chance of a find, twelve empty runs in
a row come up once in eighteen tries: rare enough to feel like a bug, common
enough to be somebody's whole evening. And it cuts the other way in the tests --
a check that "twelve runs found at least one vein" failed by luck about as
often, which taught us to distrust the suite rather than the world.

Competitive games have solved this for years, and the crit chance is the famous
case: **the chance grows with every failure and resets on success**, with the
growth chosen so that the mean frequency stays exactly the announced one. The
player is told 22% and gets 22% over a long enough run -- without the twelve
empty evenings and without four crits in a row.

## The constant

For an announced probability `p` there is one `C` such that a chance of
`C, 2C, 3C, ...` on the first, second, third try after a success gives a mean
frequency of exactly `p`. There is no closed form; `_growth` finds it by
bisection over the mean, which is a short and exact sum:

    E[tries] = sum over n of n * P(the n-th try is the first success)
    mean frequency = 1 / E[tries]

The answer depends on nothing but `p`, so it is cached.

## What this is *not* for

**Magnitudes stay plain rolls.** Quality, spread, duration, temperature, the
richness of a vein -- these answer "how much", not "did it happen", and they
have neither droughts nor streaks: the vault already bounds them. Only a
yes-or-no chance and a choice of many go through here.

## The deck

A choice of many is not a coin, and its unfairness is different: ten stones in
a row and never any flax. So a choice is dealt from a **deck** built by the
table's own weights -- draw without replacement, and when the deck runs out,
build it again. The proportions are the table's as near as whole cards allow
-- and the odds announced to the player are the deck's (`shares`), not the
table's; the drought is bounded by the deck.

## Why it survives a retry

The counter moves in the same transaction as what it decided. A job that fails
rolls the counter back with its effects, and the retry -- reading the same
counter, with the same seed -- decides the same thing again.
"""

from __future__ import annotations

import random
import uuid
from functools import lru_cache
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.luck import Luck
from src.runtime import LUCK_CACHE, LUCK_EPSILON, LUCK_GRAIN, LUCK_LONGEST, LUCK_STEPS
from src.units import PERCENT

#: What the memory is about. ASCII ids: they are keys of a table, not words of
#: the interface, and renaming one would lose everybody's counter.
EXPLORE_FIND = "explore.find"
EXPLORE_VEIN = "explore.vein"
EXPLORE_SPECIES = "explore.species"
#: Which room of a Forerunner city is opened, and what lies in it (D-232).
RUINS_ROOM = "ruins.room"
RUINS_FIND = "ruins.find"
SITE_RIVER = "site.river"
SITE_WOODS = "site.woods"
SITE_STONES = "site.stones"
SITE_MEADOW = "site.meadow"
DEATH_KEEP = "death.keep"
CHAT_LEAK = "chat.leak"
MINE_DEATH = "mine.death"
MINE_WOUND = "mine.wound"
FORAGE_WHAT = "forage.what"
BREED_NOVEL = "breed.novel"

#: How exactly the constant is solved -- precision, cache, how far the sum
#: looks -- lives in `runtime`, next to the other numbers about the machine
#: rather than the game. The announced chance itself comes from the vault, as
#: every game number does (D-065).


def _mean(growth: float) -> float:
    """The mean frequency of successes for a chance growing by `growth` a try."""
    left = 1.0
    expected = 0.0
    for step in range(1, LUCK_LONGEST + 1):
        odds = min(1.0, growth * step)
        expected += step * left * odds
        left *= 1 - odds
        if left <= LUCK_EPSILON:
            break
    return 1 / expected if expected else 0.0


@lru_cache(maxsize=LUCK_CACHE)
def _solve(share: float) -> float:
    """Bisection over `_mean`, which grows with `C`: exact to the epsilon."""
    low, high = 0.0, 1.0
    for _ in range(LUCK_STEPS):
        middle = fmean((low, high))
        if _mean(middle) < share:
            low = middle
        else:
            high = middle
    return fmean((low, high))


def growth(share: float) -> float:
    """The constant `C` for an announced probability: the first try's chance.

    The share is rounded to a hundredth of a percent before the search. Some
    chances float -- a leak grows with the crowd, a run's odds fall with every
    find -- and solving anew for each of them would cost milliseconds on a hot
    path for a difference far below anything the vault states.
    """
    if share <= 0:
        return 0.0
    if share >= 1:
        return 1.0
    return _solve(round(share, LUCK_GRAIN))


async def _row(session: AsyncSession, identity_id: uuid.UUID, matter: str) -> Luck:
    """This identity's memory of this matter, made on first use.

    The insert is an upsert: a job and a command of the same player can arrive
    at the same matter at once, and a unique-key collision would turn one
    player's second action into an error about a counter they never asked for.
    """
    stmt = select(Luck).where(Luck.identity_id == identity_id, Luck.matter == matter)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row
    await session.execute(
        insert(Luck)
        .values(id=uuid.uuid4(), identity_id=identity_id, matter=matter, misses=0, deck={})
        .on_conflict_do_nothing(index_elements=["identity_id", "matter"])
    )
    await session.flush()
    return (await session.execute(stmt)).scalar_one()


async def hit(
    session: AsyncSession,
    identity_id: uuid.UUID | None,
    matter: str,
    percent: float,
    *,
    dice: random.Random,
) -> bool:
    """Did it happen? The announced chance, in percent, with a memory (D-213).

    Without an identity -- the world rolling for nobody in particular -- the
    coin is plain: memory belongs to somebody, and a counter shared by everyone
    would make one player's luck another's drought.
    """
    share = max(0.0, min(1.0, percent / PERCENT))
    if identity_id is None or share <= 0 or share >= 1:
        return dice.random() < share

    row = await _row(session, identity_id, matter)
    step = growth(share)
    lucky = dice.random() < min(1.0, step * (row.misses + 1))
    row.misses = 0 if lucky else row.misses + 1
    await session.flush()
    return lucky


async def draw(
    session: AsyncSession,
    identity_id: uuid.UUID | None,
    matter: str,
    weights: dict[str, float],
    *,
    dice: random.Random,
) -> str:
    """Which of them: dealt from a deck built by the weights (D-213).

    The deck holds each thing as many times as its weight says, rounded to
    whole cards with at least one each; a draw takes a card out, and an empty
    deck is built again. So the proportions are the table's as near as whole
    cards allow -- `shares` says how near -- and the drought is the deck's
    length: never "ten stones and no flax".
    """
    alive = {name: weight for name, weight in weights.items() if weight > 0}
    if not alive:
        raise ValueError("нечего тянуть: все веса нулевые")
    names = sorted(alive)
    if identity_id is None:
        return dice.choices(names, weights=[alive[name] for name in names])[0]

    row = await _row(session, identity_id, matter)
    left = {name: int(count) for name, count in (row.deck or {}).items() if int(count) > 0}
    #: A table edited in the vault must not keep dealing yesterday's cards.
    if not left or set(left) - set(alive):
        left = _fresh(alive)

    pick = dice.choices(sorted(left), weights=[left[name] for name in sorted(left)])[0]
    left[pick] -= 1
    row.deck = {name: count for name, count in left.items() if count > 0}
    await session.flush()
    return pick


def _fresh(weights: dict[str, float]) -> dict[str, int]:
    """A new deck: each thing as many cards as its share of the smallest weight.

    Scaled by the smallest weight rather than by a fixed size, so the deck is
    the shortest one that keeps the table's proportions to a whole card -- a
    longer one would only make the drought longer for no gain.
    """
    least = min(weights.values())
    return {name: max(1, round(weight / least)) for name, weight in weights.items()}


def shares(weights: dict[str, float]) -> dict[str, float]:
    """What the deck actually deals, as shares of one: the odds to announce.

    A deck holds whole cards, so a table of fine weights -- wild seed at a
    sixth of a find an hour against fourteen of stone -- is dealt a little
    off its own proportions: two and a half cards' worth of a thing is dealt
    as two or three, never as two and a half. The player is promised what
    they get, not what the vault wrote (D-213), so a window that names odds
    names these, not the table's shares. Only the positive weights: a thing
    of no weight is not in the deck and has no share.
    """
    alive = {name: weight for name, weight in weights.items() if weight > 0}
    if not alive:
        return {}
    deck = _fresh(alive)
    total = sum(deck.values())
    return {name: cards / total for name, cards in deck.items()}
