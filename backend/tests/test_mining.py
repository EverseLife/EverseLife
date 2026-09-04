# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

""" "Roof" (D-143).

Checked is not "the function returns a number" but what the mechanic was written for:

* roof stability **does not leak** out, neither as a number nor as a derivative;
* the sign lies, and the roof cannot be reconstructed from one observation;
* the stake grows during the session, and a collapse costs everything mined;
* support is finite: the ceiling makes the session finite however much timber is spent;
* neighbours change the yield in opposite directions on rich and poor veins.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import fields
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import mining, world
from src.engine.mining import Pace, SessionState
from src.models.identity import BodyState, Identity, Wound
from src.models.inventory import Item
from src.models.world import Vein
from src.units import PERCENT, amount_float

ORE = "iron_ore"


async def _face(
    session: AsyncSession,
    *,
    richness: float = 60,
    remaining: float = 100_000,
    tooled: bool = True,
):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.mine.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, ORE, richness=richness, remaining=remaining)
    identity = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, identity, node)
    if tooled:
        #: The vault requires a pickaxe (`Добыча requires: [Кирка, Жила]`),
        #: and since D-215 the engine checks it at the face.
        pocket = await world.body_container(session, body)
        await world.grant_item(
            session, pocket, "stone_pickaxe", quality=50, origin="сценарий теста"
        )
    return node, vein, body


async def _tool(session: AsyncSession, body):
    container = await world.body_container(session, body)
    return await world.grant_item(
        session, container, "iron_pickaxe", quality=50, origin="сценарий теста"
    )


async def _spend_the_grace(
    session: AsyncSession,
    constants: Constants,
    body,
    vein,
    rng: random.Random,
) -> None:
    """Drop the roof as many times as the vault spares this body (D-294)."""
    for _ in range(int(constants[R.MINE_COLLAPSES_SURVIVED])):
        sess = await mining.start(session, constants, body, vein)
        await _to_the_collapse(session, constants, sess, rng)


async def _to_the_collapse(
    session: AsyncSession,
    constants: Constants,
    sess,
    rng: random.Random,
) -> None:
    """Swing until the roof comes down: a face without timber is finite by design."""
    for _ in range(100):
        sight = await mining.swing(session, constants, sess, rng=rng)
        if sight.state is SessionState.COLLAPSED:
            return
    raise AssertionError("свод так и не обрушился")


async def test_mining_requires_a_pickaxe(session: AsyncSession, constants: Constants) -> None:
    """`Добыча requires: [Кирка, Жила]` was in the vault from the start; the
    engine finally checks it (D-215): no tool of the class -- no session."""
    _, vein, body = await _face(session, tooled=False)
    #: Asserted by the key and the tool class it names, not by the sentence:
    #: the wording belongs to the locale now (D-251 wave III).
    with pytest.raises(mining.NoTool) as refused:
        await mining.start(session, constants, body, vein)
    assert refused.value.key == "mining-no-tool"
    assert refused.value.params["tool_class"] == "pickaxe"


async def test_pick_refuses_a_liquid_vein(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """A liquid vein is not worked by hand (D-252): oil is pumped by the rig,
    and the pick has nothing to grip. Refused at the door, before any effect."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.oil.{stamp}", "Поле", area_m2=100)
    vein = await world.create_vein(session, node, "crude_oil", richness=55, remaining=40_000)
    identity = await world.create_identity(session, f"Бурильщик-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="сценарий теста")

    with pytest.raises(mining.VeinLiquid) as refused:
        await mining.start(session, constants, body, vein, catalog=catalog)
    assert refused.value.key == "mining-vein-liquid"
    assert refused.value.params["goods"] == "crude_oil"


# --- hidden state ------------------------------------------------------------


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


async def test_mined_accumulates_and_goes_to_inventory(
    session: AsyncSession, constants: Constants
) -> None:
    _, vein, body = await _face(session)
    sess = await mining.start(session, constants, body, vein)

    for _ in range(3):
        kind = await mining.swing(session, constants, sess)
    assert kind.mined > 0
    assert kind.swings == 3

    yield_ = await mining.leave(session, constants, sess)
    await session.commit()

    inventory = await world.body_container(session, body)
    in_inventory = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == inventory.id, Item.type_key == ORE
        )
    )
    assert in_inventory > 0
    assert yield_ == pytest.approx(kind.mined)


async def test_collapse_costs_whole_session(session: AsyncSession, constants: Constants) -> None:
    """The stake grows during the session -- that is all the tension (D-143)."""
    _, vein, body = await _face(session)
    pickaxe = await _tool(session, body)
    sess = await mining.start(session, constants, body, vein, tool_item_id=pickaxe.id)

    #: We dig to the end without shoring. The roof must give out.
    kind = None
    for _ in range(100):
        kind = await mining.swing(session, constants, sess, rng=random.Random(0))
        if kind.state is SessionState.COLLAPSED:
            break
    await session.commit()

    assert kind is not None and kind.state is SessionState.COLLAPSED
    assert kind.mined == 0, "обрушилось — потеряно всё добытое за сессию"

    #: A collapse hits the tool too: ordinary wear plus `mine.collapse_wear`,
    #: divided by the service life of this quality -- a good pickaxe holds longer
    #: (`engine.wear`).
    from src.engine import wear

    hit = constants[R.WEAR_TOOL_PER_SESSION] + constants[R.MINE_COLLAPSE_WEAR]
    expected_value = hit / wear.life_factor(constants, 50)
    await session.refresh(pickaxe)
    assert float(pickaxe.condition) == pytest.approx(100 - expected_value, abs=0.01)


async def test_collapse_sometimes_wounds(session: AsyncSession, constants: Constants) -> None:
    """The sparing cave-in still rolls for a wound (D-096, D-143)."""
    _, vein, body = await _face(session)
    sess = await mining.start(session, constants, body, vein)

    #: The chance keeps a memory (D-213), so the threshold of a first roll is
    #: the growth constant of the announced chance -- and the test asks the
    #: engine for it rather than knowing the number itself.
    from src.engine.luck import growth

    hurting = growth(constants[R.MINE_COLLAPSE_WOUND_CHANCE] / PERCENT)
    unlucky = random.Random()
    unlucky.random = lambda: hurting / 2
    await _to_the_collapse(session, constants, sess, unlucky)
    await session.commit()

    wounds = await session.scalar(
        select(func.count()).select_from(Wound).where(Wound.body_id == body.id)
    )
    assert wounds == 1
    assert body.state is BodyState.ALIVE, "рана — не смерть"


async def test_the_cave_ins_a_body_lives_through_are_the_vault_s_count(
    session: AsyncSession, constants: Constants
) -> None:
    """The rock does not roll dice for a life any more (D-294).

    Whatever the dice say, a body lives through `mine.collapses_survived`
    cave-ins and dies in the next one. The number is asked of the vault, not
    known here: a playtest that gives a miner two warnings must not need this
    file changed (D-065).
    """
    spared = int(constants[R.MINE_COLLAPSES_SURVIVED])
    _, vein, body = await _face(session)

    unfortunate = random.Random()
    unfortunate.random = lambda: 0.0  # the unluckiest roll: below every threshold
    for lived in range(1, spared + 1):
        sess = await mining.start(session, constants, body, vein)
        await _to_the_collapse(session, constants, sess, unfortunate)
        assert body.state is BodyState.ALIVE, f"обвал {lived} обязан щадить"
        assert body.cave_ins == lived

    last = await mining.start(session, constants, body, vein)
    await _to_the_collapse(session, constants, last, unfortunate)
    assert body.state is BodyState.DEAD, "следующий за форой убивает"


async def test_the_second_cave_in_kills_and_leaves_everything_lying(
    session: AsyncSession, constants: Constants
) -> None:
    """The environment is the only source of death in the alpha (D-111, D-294).

    The second cave-in takes the body, and what it carried stays lying on the
    floor of the node **whole**: the rock comes down on the body and its load in
    one place, so `death.salvage_ratio` -- the share of an ordinary death -- does
    not apply here.
    """
    node, vein, body = await _face(session)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "coal", amount=100, quality=50, origin="сценарий теста")

    luckless = random.Random()
    luckless.random = lambda: 0.0
    await _spend_the_grace(session, constants, body, vein, luckless)
    assert body.state is BodyState.ALIVE

    hurt = select(func.count()).select_from(Wound).where(Wound.body_id == body.id)
    #: The sparing one wounded, with this roll it always does. What matters is
    #: that the deadly one adds nothing: the dead do not work slower.
    wounded_once = await session.scalar(hurt)

    last = await mining.start(session, constants, body, vein)
    await _to_the_collapse(session, constants, last, luckless)
    await session.commit()

    assert body.state is BodyState.DEAD and body.died_at is not None
    assert body.cave_ins == int(constants[R.MINE_COLLAPSES_SURVIVED]) + 1
    assert await session.scalar(hurt) == wounded_once, "мёртвому раны не пишут"

    yard = await world.node_container(session, node)
    left = (
        (
            await session.execute(
                select(Item).where(Item.container_id == yard.id, Item.type_key == "coal")
            )
        )
        .scalars()
        .all()
    )
    carried = [amount_float(thing.amount) for thing in left]
    assert carried == [100.0], "погибшего хоронит, а не рассыпает"
    assert float(left[0].condition) == 100, "порода накрыла груз, а не потрепала его"
    #: Without this the test would pass on an ordinary death too.
    assert constants[R.DEATH_SALVAGE_RATIO] < PERCENT


async def test_a_printed_body_meets_the_roof_with_its_grace_back(
    session: AsyncSession, constants: Constants
) -> None:
    """The count is on the body, not on the identity (D-012, D-294).

    Death is the thing that forgives it: no timer anywhere has to.
    """
    node, vein, body = await _face(session)
    luckless = random.Random()
    luckless.random = lambda: 0.0
    await _spend_the_grace(session, constants, body, vein, luckless)
    doomed = await mining.start(session, constants, body, vein)
    await _to_the_collapse(session, constants, doomed, luckless)
    assert body.state is BodyState.DEAD

    printed = await world.print_body(session, await session.get(Identity, body.identity_id), node)
    pocket = await world.body_container(session, printed)
    await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="сценарий теста")
    assert printed.cave_ins == 0

    sess = await mining.start(session, constants, printed, vein)
    await _to_the_collapse(session, constants, sess, luckless)
    assert printed.state is BodyState.ALIVE, "у нового тела фора своя"


async def test_banking_the_haul_no_longer_buys_a_whole_roof(
    session: AsyncSession, constants: Constants
) -> None:
    """The hole a player found and D-294 closes.

    Leaving banks the haul at any moment, so the stake of a collapse shrinks to
    the swings taken after the last exit -- and a collapse hands back a whole
    roof (D-188). Dig, walk out, walk in, drop the roof, repeat: support was
    never needed. The ore still reaches the pocket, but the second dropped roof
    now costs the body, and the banked ore goes with it onto the floor of the mine.
    """
    node, vein, body = await _face(session)
    luckless = random.Random()
    luckless.random = lambda: 0.0
    pocket = await world.body_container(session, body)

    banked = 0.0
    for _ in range(int(constants[R.MINE_COLLAPSES_SURVIVED]) + 1):
        sess = await mining.start(session, constants, body, vein)
        #: Bank after every swing, which is exactly what the loop does.
        await mining.swing(session, constants, sess, rng=luckless)
        banked += await mining.leave(session, constants, sess)
        again = await mining.start(session, constants, body, vein)
        await _to_the_collapse(session, constants, again, luckless)
    assert banked > 0, "руда действительно доходила до кармана"

    assert body.state is BodyState.DEAD, "два обвала стоят тела"
    mined = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(Item.container_id == pocket.id)
    )
    assert amount_float(int(mined or 0)) == 0, "карман достался узлу"
    yard = await world.node_container(session, node)
    dropped = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == yard.id, Item.type_key == ORE
        )
    )
    assert amount_float(int(dropped or 0)) == pytest.approx(banked), "накопанное лежит в забое"


async def test_support_holds_roof_but_not_forever(
    session: AsyncSession, constants: Constants
) -> None:
    """The `mine.roof_timber_cap` ceiling is the main knob: the session is finite."""
    _, vein, body = await _face(session, richness=10)
    container = await world.body_container(session, body)
    await world.grant_item(session, container, "shaft_support", amount=50, origin="сценарий теста")

    sess = await mining.start(session, constants, body, vein)
    for _ in range(10):
        await mining.timber(session, constants, sess)

    assert mining.roof_of(constants, vein) == pytest.approx(constants[R.MINE_ROOF_TIMBER_CAP])


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

    Written unrounded, it would come back from the column rounded to two
    decimals, seed a different lie and give a different sign for a face nobody
    has touched -- a second reading with new information in it, which is the
    averaging D-143 forbids. `remember_roof` rounds where it writes, and the
    seed is formatted to the same scale.
    """
    _, vein, body = await _face(session, richness=37)
    sess = await mining.start(session, constants, body, vein)
    struck = await mining.swing(session, constants, sess)

    #: Back out of the column, where `Numeric(6, 2)` has had its say: the same
    #: roof must come back, and with it the same lie.
    await session.refresh(vein)
    assert (await mining.sight(session, constants, sess)).sign == struck.sign


async def test_cannot_shore_without_support(session: AsyncSession, constants: Constants) -> None:
    """Support costs timber and rope -- that is the whole point of the choice."""
    _, vein, body = await _face(session)
    sess = await mining.start(session, constants, body, vein)
    with pytest.raises(mining.NoTimber):
        await mining.timber(session, constants, sess)


async def test_fast_pace_yields_more_and_risks_more(
    session: AsyncSession, constants: Constants
) -> None:
    """One multiplier for yield, roof sag and stamina spend (D-091)."""
    _, vein_a, body_a = await _face(session)
    steady = await mining.start(session, constants, body_a, vein_a, pace=Pace.STEADY)
    calm = await mining.swing(session, constants, steady)
    sag_steady = mining.starting_roof(constants, float(vein_a.richness)) - mining.roof_of(
        constants, vein_a
    )

    _, vein_b, body_b = await _face(session)
    fast = await mining.start(session, constants, body_b, vein_b, pace=Pace.FAST)
    greedy = await mining.swing(session, constants, fast)
    sag_fast = mining.starting_roof(constants, float(vein_b.richness)) - mining.roof_of(
        constants, vein_b
    )

    k = constants[R.MINE_PACE_K]
    assert greedy.mined == pytest.approx(calm.mined * k, rel=0.01)
    assert sag_fast == pytest.approx(sag_steady * k)
    spent_steady = constants[R.BODY_STAMINA_MAX] - calm.stamina
    spent_fast = constants[R.BODY_STAMINA_MAX] - greedy.stamina
    assert spent_fast == pytest.approx(spent_steady * k)


# --- vein and neighbours -----------------------------------------------------


async def test_vein_is_finite(session: AsyncSession, constants: Constants) -> None:
    """A worked-out vein disappears. That is irrevocable (pillar P2)."""
    _, vein, body = await _face(session, remaining=1)
    sess = await mining.start(session, constants, body, vein)
    await mining.swing(session, constants, sess)
    await session.commit()

    assert vein.remaining == 0
    assert vein.depleted_at is not None
    with pytest.raises(mining.VeinDepleted):
        await mining.swing(session, constants, sess)


async def test_vein_depletes_in_tiers(session: AsyncSession, constants: Constants) -> None:
    _, vein, body = await _face(session, richness=90, remaining=1_000_000)
    before = float(vein.richness)

    #: We move the extraction counter right past the tier -- digging that long by hand.
    from src.units import amount

    vein.extracted = amount(constants[R.VEIN_DEPLETION_STEP]) - amount(1)
    sess = await mining.start(session, constants, body, vein)
    await mining.swing(session, constants, sess)
    await session.commit()

    assert float(vein.richness) == pytest.approx(before - constants[R.VEIN_RICHNESS_DECAY])


async def test_neighbour_hinders_on_rich_vein(session: AsyncSession, constants: Constants) -> None:
    """Such a vein is fought over, guarded and licensed (D-099)."""
    rich = constants[R.MINING_RICH_THRESHOLD] + 20
    _, vein, first = await _face(session, richness=rich)
    self_ = await mining.crowd_factor(constants, session, vein)

    await mining.start(session, constants, first, vein)

    neighbour = await world.create_identity(session, "Сосед")
    neighbour_body = await world.print_body(session, neighbour, await _node(session, vein))
    await _tool(session, neighbour_body)
    await mining.start(session, constants, neighbour_body, vein)

    together = await mining.crowd_factor(constants, session, vein)
    assert together < self_


async def test_neighbour_helps_on_poor_vein(session: AsyncSession, constants: Constants) -> None:
    """A poor vein feeds a crew. The best entry into the social game (D-099)."""
    poor = constants[R.MINING_RICH_THRESHOLD] - 20
    _, vein, first = await _face(session, richness=poor)
    await mining.start(session, constants, first, vein)

    second = await world.create_identity(session, "Артельщик")
    body = await world.print_body(session, second, await _node(session, vein))
    await _tool(session, body)
    await mining.start(session, constants, body, vein)

    assert await mining.crowd_factor(constants, session, vein) > 1.0


async def test_cannot_dig_two_faces_at_once(session: AsyncSession, constants: Constants) -> None:
    _, vein, body = await _face(session)
    await mining.start(session, constants, body, vein)
    with pytest.raises(mining.SessionClosed):
        await mining.start(session, constants, body, vein)


async def test_must_walk_to_vein(session: AsyncSession, constants: Constants) -> None:
    """Information travels over the Net, matter requires presence (D-044)."""
    _, vein, _ = await _face(session)
    other = await world.create_node(session, "terra.far", "Далеко", area_m2=100)
    identity = await world.create_identity(session, "Гость")
    body = await world.print_body(session, identity, other)

    with pytest.raises(mining.NotHere):
        await mining.start(session, constants, body, vein)


async def _node(session: AsyncSession, vein: Vein):
    from src.models.world import Node

    node = await session.get(Node, vein.node_id)
    assert node is not None
    return node


# --- stamina (the zero constraint) -------------------------------------------


async def test_no_digging_with_zero_stamina(session: AsyncSession, constants: Constants) -> None:
    """A body at zero does not mine: a swing costs strength, and without it the vein is closed."""
    from decimal import Decimal

    _, vein, body = await _face(session)
    sess = await mining.start(session, constants, body, vein)

    body.stamina = Decimal("0")
    await session.flush()
    with pytest.raises(mining.NoStrength):
        await mining.swing(session, constants, sess)


async def test_session_does_not_open_with_zero_stamina(
    session: AsyncSession, constants: Constants
) -> None:
    from decimal import Decimal

    _, vein, body = await _face(session)
    body.stamina = Decimal("0")
    await session.flush()
    with pytest.raises(mining.NoStrength):
        await mining.start(session, constants, body, vein)
