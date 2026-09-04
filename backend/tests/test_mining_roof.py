# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The roof of a working, and the sign that lies about it (D-143, D-188).

The half of "Roof" that is about the one hidden number: where it lives, who
shares it, what a support does to it, and what the player is told instead of
it. The rest of the mechanic -- the shift, the collapse and its price, the
vein running out, the neighbours' yield -- is in `test_mining.py`; what two
transactions at once do to this same number is in `test_races_roof.py`.

Checked here is not "the function returns a number" but what the mechanic was
written for:

* roof stability **does not leak** out, neither as a number nor as a derivative;
* the sign lies, and one working tells one sign -- to everyone in it, however
  many times they ask, until the roof itself moves;
* the roof belongs to the working and outlives the session that shook it, so
  leaving does not reset the risk and a neighbour's swings are felt live;
* support is finite: the ceiling makes the session finite however much timber
  is spent, and above that ceiling a support is refused rather than wasted.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import fields
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mining_kit import _face, _tool
from src.constants import Constants
from src.constants import registry as R
from src.engine import mining, world
from src.engine.mining import SessionState, _base
from src.models.identity import BodyState
from src.models.inventory import Item
from src.models.world import Vein
from src.units import ROUND_ROOF, amount_float


def test_roof_does_not_leak_out() -> None:
    """The player is shown neither stability nor anything it could be derived from."""
    visible_ = {field.name for field in fields(mining.Sight)}
    assert "roof" not in visible_
    assert visible_ == {"sign", "mined", "swings", "timbers", "stamina", "pace", "state"}


def test_sign_lies_both_ways(constants: Constants) -> None:
    """Without noise the bands are invertible into arithmetic, and the hidden number is gone."""
    bands = constants[R.MINE_SIGN_BANDS]
    border = bands["сыплется пыль"]
    observations = {mining.sign_of(constants, border, random.Random(seed)) for seed in range(200)}
    assert len(observations) > 1, "на границе полосы признак обязан быть неоднозначным"


async def test_sign_cannot_be_reasked(session: AsyncSession, constants: Constants) -> None:
    """Otherwise the average of readings yields the hidden number to any precision.

    The roof changes only from a swing and a support -- so the sign must change
    only with them, not on every look (D-143).
    """
    _, vein, body = await _face(session)
    sess = await mining.start(session, constants, body, vein)

    first = await mining.sight(session, constants, sess)
    repeated = {(await mining.sight(session, constants, sess)).sign for _ in range(20)}
    assert repeated == {first.sign}, "признак обязан быть одним и тем же до удара"

    after_hit = await mining.swing(session, constants, sess)
    more = {(await mining.sight(session, constants, sess)).sign for _ in range(5)}
    assert more == {after_hit.sign}


async def test_one_working_tells_one_sign(session: AsyncSession, constants: Constants) -> None:
    """The lie belongs to the face, not to whoever is looking at it (D-143, D-188).

    Seeded by the session, one roof would have a fresh independent lie for
    every way of looking at it, and each is another sample of the same hidden
    number -- the average by another road. Two of those roads cost nothing:
    walk out and walk back in, and stand a second body at the same face, which
    is the ordinary case (D-099). Neither may buy a second opinion.
    """
    node, vein, body = await _face(session, richness=60)
    first = await mining.start(session, constants, body, vein)
    await mining.swing(session, constants, first)
    await mining.leave(session, constants, first)

    again = await mining.start(session, constants, body, vein)
    stamp = uuid.uuid4().hex[:6]
    neighbour = await world.create_identity(session, f"Сосед-{stamp}")
    neighbour_body = await world.print_body(session, neighbour, node)
    await _tool(session, neighbour_body)
    theirs = await mining.start(session, constants, neighbour_body, vein)

    #: Swept across the scale rather than asked once: a band is wide, so two
    #: different lies about one roof land in the same word more often than
    #: not, and a single reading would go green on a face that hands out a
    #: fresh lie to everyone who walks into it. Twenty roofs do not.
    for point in range(5, 100, 5):
        _base.remember_roof(vein, float(point))
        heard = {(await mining.sight(session, constants, face)).sign for face in (again, theirs)}
        assert len(heard) == 1, f"на своде {point} забой сказал соседям разное: {heard}"


def test_the_roof_column_is_as_wide_as_the_engine_rounds() -> None:
    """`ROUND_ROOF` is the width of `Vein.roof`, and `units.py` promises a test.

    The engine puts the roof on that grid before writing it (`remember_roof`)
    and seeds the sign's lie off the same scale, so a column narrowed under
    them would round finer than the row can hold and the lie would be redrawn
    by a trip through the database -- silently, since nothing else objects.
    """
    assert Vein.__table__.c.roof.type.scale == ROUND_ROOF


def test_rich_vein_gives_less_stability(constants: Constants) -> None:
    """Richness is paid for with risk."""
    poor = mining.starting_roof(constants, 10)
    rich = mining.starting_roof(constants, 90)
    assert rich < poor
    #: But not below the support ceiling -- otherwise the session would end before starting.
    assert rich >= constants[R.MINE_ROOF_TIMBER_CAP]


def test_hour_of_mining_equals_session_without_support(constants: Constants) -> None:
    """`mining.iron_per_hour` per hour, sixteen swings -- a short session."""
    hits = constants[R.MINE_ROOF_START] / constants[R.MINE_ROOF_PER_SWING]
    assert mining.swing_hours(constants) * hits == pytest.approx(1.0)


# --- session flow ------------------------------------------------------------


async def test_support_holds_roof_but_not_forever(
    session: AsyncSession, constants: Constants
) -> None:
    """The `mine.roof_timber_cap` ceiling is the main knob: the session is finite.

    Timber is not a way to dig for ever: however much of it is spent, the
    working comes back to the ceiling and no higher, and the next support has
    nothing left to hold. The roof is shaken below that ceiling first, because
    a working nobody has touched starts **above** it -- shoring one refuses
    (`RoofHolds`), which is the same statement said at the other end.
    """
    _, vein, body = await _face(session, richness=10)
    container = await world.body_container(session, body)
    await world.grant_item(session, container, "shaft_support", amount=50, origin="сценарий теста")
    await _tool(session, body)

    cap = constants[R.MINE_ROOF_TIMBER_CAP]
    sess = await mining.start(session, constants, body, vein)
    while mining.roof_of(constants, vein) >= cap:
        await mining.swing(session, constants, sess)

    await mining.timber(session, constants, sess)
    assert mining.roof_of(constants, vein) == pytest.approx(cap)
    with pytest.raises(mining.RoofHolds):
        await mining.timber(session, constants, sess)


async def test_roof_survives_leaving(session: AsyncSession, constants: Constants) -> None:
    """Leaving the pit no longer resets the risk (D-188).

    That was the hole: dig down to "it creaks", press "leave", come back --
    and the roof was whole again, so support was never needed.
    """
    _, vein, body = await _face(session, richness=60)
    await _tool(session, body)

    first = await mining.start(session, constants, body, vein)
    for _ in range(5):
        await mining.swing(session, constants, first)
    shaken = mining.roof_of(constants, vein)
    await mining.leave(session, constants, first)

    await mining.start(session, constants, body, vein)
    assert mining.roof_of(constants, vein) == pytest.approx(shaken), "свод забоя обнулился уходом"


async def test_support_stays_after_the_shift(session: AsyncSession, constants: Constants) -> None:
    """A support is an investment in the working, not a consumable of one visit."""
    _, vein, body = await _face(session, richness=60)
    container = await world.body_container(session, body)
    await world.grant_item(session, container, "shaft_support", amount=5, origin="сценарий теста")
    await _tool(session, body)

    first = await mining.start(session, constants, body, vein)
    #: Down past the ceiling before shoring: a support raises the roof to it
    #: and refuses above it (`RoofHolds`), so a fresh working has nothing for
    #: one to do.
    while mining.roof_of(constants, vein) >= constants[R.MINE_ROOF_TIMBER_CAP]:
        await mining.swing(session, constants, first)
    await mining.timber(session, constants, first)
    shored = mining.roof_of(constants, vein)
    await mining.leave(session, constants, first)

    await mining.start(session, constants, body, vein)
    assert mining.roof_of(constants, vein) == pytest.approx(shored)


async def test_collapse_starts_the_working_over(
    session: AsyncSession, constants: Constants
) -> None:
    """The rubble is cleared: a collapsed vein is not locked forever (P2, D-188)."""
    _, vein, body = await _face(session, richness=60)
    await _tool(session, body)

    sess = await mining.start(session, constants, body, vein)
    #: One swing from nought, and the roof is the vein's (D-188).
    vein.roof = Decimal("1")
    await session.flush()
    #: A collapse rolls for death and then for a wound (`_collapse`), and a dead
    #: body cannot open a working. Those rolls go through `luck.hit`, which
    #: draws `random()`; the sign of the roof draws `uniform()`. Both are pinned
    #: to the top of the scale, so this test is about the rubble, not about luck.
    unharmed = random.Random()
    unharmed.uniform = lambda a, b: b  # noqa: ARG005 -- always the upper bound
    unharmed.random = lambda: 1.0  # never below any chance
    await mining.swing(session, constants, sess, rng=unharmed)
    assert sess.state is SessionState.COLLAPSED
    assert body.state is BodyState.ALIVE

    await session.refresh(vein)
    assert vein.roof is None, "после обвала забой начинается заново"
    await mining.start(session, constants, body, vein)
    assert mining.roof_of(constants, vein) == pytest.approx(
        mining.starting_roof(constants, float(vein.richness))
    )


async def test_shaken_working_is_shared(session: AsyncSession, constants: Constants) -> None:
    """The roof is common to everyone digging the vein (D-099, D-188)."""
    node, vein, first_body = await _face(session, richness=60)
    await _tool(session, first_body)
    first = await mining.start(session, constants, first_body, vein)
    for _ in range(4):
        await mining.swing(session, constants, first)
    shaken = mining.roof_of(constants, vein)
    await mining.leave(session, constants, first)

    stamp = uuid.uuid4().hex[:6]
    neighbour = await world.create_identity(session, f"Сосед-{stamp}")
    neighbour_body = await world.print_body(session, neighbour, node)
    await _tool(session, neighbour_body)
    second = await mining.start(session, constants, neighbour_body, vein)
    #: Asserted through a swing rather than through the opening: a session
    #: carries no roof of its own to compare, and what matters is that the
    #: neighbour's own sag starts from where the first miner stopped.
    await mining.swing(session, constants, second)
    sagged = shaken - constants[R.MINE_ROOF_PER_SWING]
    assert mining.roof_of(constants, vein) == pytest.approx(sagged), "сосед пришёл в целый забой"


async def test_a_neighbours_swings_shake_an_open_face(
    session: AsyncSession, constants: Constants
) -> None:
    """Shaken by one -- dangerous for all, while both are still in it (D-099, D-188).

    The roof used to be copied onto the session at `start`, and every swing
    worked from that copy: two bodies at one vein overwrote each other's sag,
    so a neighbour who came in at a hundred put back ninety-four over somebody
    else's forty and neither was told the truth. Here the working is left with
    one swing in it, both faces are open, and the **first** swing spends it --
    the second must bring the roof down, though it is the only one that body
    has struck.
    """
    node, vein, first_body = await _face(session, richness=60)
    await _tool(session, first_body)
    stamp = uuid.uuid4().hex[:6]
    neighbour = await world.create_identity(session, f"Сосед-{stamp}")
    neighbour_body = await world.print_body(session, neighbour, node)
    await _tool(session, neighbour_body)

    #: One swing left in the working, and both open it in that state: the sag
    #: of a single swing takes it to a hair above nought, and the next past it.
    per_swing = constants[R.MINE_ROOF_PER_SWING]
    vein.roof = Decimal(str(per_swing + 0.5))
    await session.flush()
    mine = await mining.start(session, constants, first_body, vein)
    theirs = await mining.start(session, constants, neighbour_body, vein)

    spent = await mining.swing(session, constants, mine)
    assert spent.state is SessionState.ACTIVE, "первый удар ещё не роняет свод"
    assert mining.roof_of(constants, vein) == pytest.approx(0.5)

    buried = await mining.swing(session, constants, theirs)
    assert buried.state is SessionState.COLLAPSED, "свод соседа не дрогнул от чужих ударов"
    assert theirs.swings == 1, "обвалило с одного собственного удара — так и задумано"
    await session.refresh(neighbour_body)
    assert neighbour_body.cave_ins == 1

    #: And the rubble is cleared for everyone at the face -- the miner still
    #: standing in it goes on under a working as good as new (OQ-122).
    assert vein.roof is None
    assert mine.state is SessionState.ACTIVE


async def test_the_sign_survives_the_database(session: AsyncSession, constants: Constants) -> None:
    """The lie is seeded by the roof, so the roof must be one number everywhere.

    Left unrounded in hand, it would come back from `Numeric(6, 2)` a
    hundredth away, seed a different lie and give a different sign for a face
    nobody has touched -- a second reading with new information in it, which
    is the averaging D-143 forbids. `remember_roof` puts the value on the
    column's grid and answers with what it wrote, so the two cannot part.

    The roof is put off the grid **by hand**, and with the digit that makes
    the two roundings disagree: today's vault numbers keep a roof on tenths
    (`mine.roof_start` 100, `mine.roof_per_swing` 6, richness whole), so a
    swing at an ordinary face would round to itself and prove nothing. Written
    without the grid, 86.625 is 86.62 to Python's format and 86.63 to
    Postgres, and the sign is drawn from a seed that says one or the other.
    """
    _, vein, body = await _face(session, richness=37)
    sess = await mining.start(session, constants, body, vein)

    stored = _base.remember_roof(vein, 86.625)
    said = await mining.sight(session, constants, sess)
    await session.flush()
    session.expire(vein)
    await session.refresh(vein)

    assert float(vein.roof) == stored, f"колонка держит {vein.roof}, вызвавший ушёл с {stored}"
    assert (await mining.sight(session, constants, sess)).sign == said.sign


async def test_cannot_shore_without_support(session: AsyncSession, constants: Constants) -> None:
    """Support costs timber and rope -- that is the whole point of the choice."""
    _, vein, body = await _face(session)
    sess = await mining.start(session, constants, body, vein)
    with pytest.raises(mining.NoTimber):
        await mining.timber(session, constants, sess)


async def test_a_support_never_makes_the_working_worse(
    session: AsyncSession, constants: Constants
) -> None:
    """A support raises the roof; it does not set it (D-188).

    `mine.roof_timber_cap` is the ceiling a support raises to, and every
    untouched working starts above it -- `starting_roof` runs from
    `mine.roof_start` **down** to the cap as the vein gets richer. Shoring one
    used to spend a timber and bring the roof down to the ceiling: a loss the
    miner paid alone while the roof was their own copy, and the artel's
    working once it is shared.
    """
    node, vein, body = await _face(session, richness=60)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "shaft_support", amount=2, origin="сценарий теста")

    sess = await mining.start(session, constants, body, vein)
    whole = mining.roof_of(constants, vein)
    assert whole > constants[R.MINE_ROOF_TIMBER_CAP], "свежий забой обязан быть выше потолка крепи"

    with pytest.raises(mining.RoofHolds):
        await mining.timber(session, constants, sess)
    assert mining.roof_of(constants, vein) == pytest.approx(whole), "крепь опустила свод"
    #: And the refusal is asked **after** the pocket, so an empty one hears
    #: about itself and not about the roof: the answer «свод ≥ потолка» is a
    #: fact about the one number the player is never told (D-143), and a body
    #: carrying no timber must not get it for the price of a button.
    bare = await world.create_identity(session, f"Порожняк-{uuid.uuid4().hex[:6]}")
    bare_body = await world.print_body(session, bare, node)
    await _tool(session, bare_body)
    empty_handed = await mining.start(session, constants, bare_body, vein)
    with pytest.raises(mining.NoTimber):
        await mining.timber(session, constants, empty_handed)
    assert sess.timbers == 0
    left = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == "shaft_support"
        )
    )
    assert amount_float(int(left or 0)) == 2, "отказ съел бревно"
