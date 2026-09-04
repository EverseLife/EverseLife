# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

""" "Roof" (D-143): the shift, its price and the ground under it.

Checked is not "the function returns a number" but what the mechanic was written for:

* the stake grows during the session, and a collapse costs everything mined;
* the body pays for the second cave-in with itself, and a fresh one starts over;
* a vein is finite and grows poorer as it is worked;
* neighbours change the yield in opposite directions on rich and poor veins;
* a face is not opened by a body that cannot swing, walk to it, or hold a pick.

The one hidden number -- the roof, who shares it, what a support does to it
and what the player is told instead of it -- fills its own file,
`test_mining_roof.py`. What two transactions at once do to it is in
`test_races_roof.py`, and to the ore in `test_races_mining.py`.
"""

from __future__ import annotations

import random
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mining_kit import ORE, _face, _spend_the_grace, _to_the_collapse, _tool
from src.constants import Constants
from src.constants import registry as R
from src.engine import mining, world
from src.engine.mining import Pace, SessionState
from src.models.identity import BodyState, Identity, Wound
from src.models.inventory import Item
from src.models.world import Vein
from src.units import PERCENT, ROUND_ROOF, amount_float, step


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


# --- the shift ---------------------------------------------------------------


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


async def test_fast_pace_yields_more_and_risks_more(
    session: AsyncSession, constants: Constants
) -> None:
    """One multiplier for yield, roof sag and stamina spend (D-091)."""
    _, vein_a, body_a = await _face(session)
    steady = await mining.start(session, constants, body_a, vein_a, pace=Pace.STEADY)
    calm = await mining.swing(session, constants, steady)
    sag_steady = mining.starting_roof(constants, vein_a) - mining.roof_of(constants, vein_a)

    _, vein_b, body_b = await _face(session)
    fast = await mining.start(session, constants, body_b, vein_b, pace=Pace.FAST)
    greedy = await mining.swing(session, constants, fast)
    sag_fast = mining.starting_roof(constants, vein_b) - mining.roof_of(constants, vein_b)

    k = constants[R.MINE_PACE_K]
    assert greedy.mined == pytest.approx(calm.mined * k, rel=0.01)
    #: The sag is measured off the row, which keeps the roof on its own grid
    #: (`ROUND_ROOF`), and the starting roof it is measured from is a real
    #: number since D-302 -- so the two ends are a hundredth apart at worst.
    assert sag_fast == pytest.approx(sag_steady * k, abs=2 * float(step(ROUND_ROOF)))
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

    _, vein, body = await _face(session)
    sess = await mining.start(session, constants, body, vein)

    body.stamina = Decimal("0")
    await session.flush()
    with pytest.raises(mining.NoStrength):
        await mining.swing(session, constants, sess)


async def test_session_does_not_open_with_zero_stamina(
    session: AsyncSession, constants: Constants
) -> None:

    _, vein, body = await _face(session)
    body.stamina = Decimal("0")
    await session.flush()
    with pytest.raises(mining.NoStrength):
        await mining.start(session, constants, body, vein)
