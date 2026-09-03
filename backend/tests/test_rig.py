# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Drilling rig: capital instead of labour (D-115).

Checked is what the rig was introduced this way for:

* it works without the player and **does not sleep** -- that is its whole strength;
* and loses to a human in everything else: lower output, quality bounded by
  `rig.quality_cap`, eats the vein twice as fast;
* three obligations keep it dependent on people: fuel, hopper and maintenance.
  Any one violated -- and the machine stands.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import rig, world
from src.models.inventory import Item
from src.models.rig import Rig
from src.units import ROUND_REMAINDER, amount_float


async def _face(session: AsyncSession, *, coal: float = 100, richness: float = 60):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=200)
    vein = await world.create_vein(session, node, "iron_ore", richness=richness, remaining=100_000)
    yard = await world.node_container(session, node)
    if coal > 0:
        await world.grant_item(session, yard, "coal", amount=coal, quality=55, origin="тест")
    identity = await world.create_identity(session, f"Промышленник-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="тест")
    installation = await rig.place(session, body, machine, vein)
    return node, vein, body, installation, machine


def _terra_day(constants: Constants) -> timedelta:
    return timedelta(hours=constants[R.TIME_DAY_TERRA])


def _via(installation, hours: float) -> datetime:
    return installation.counted_at + timedelta(hours=hours)


# --- works without the player ------------------------------------------------


async def test_mines_over_time_and_burns_coal(session: AsyncSession, constants: Constants) -> None:
    """The machine does not sleep: the hopper fills while the owner is busy elsewhere."""
    node, _, _, installation, machine = await _face(session)
    coal_before = 100.0

    mined = await rig.advance(session, constants, installation, now=_via(installation, 4))
    #: Output is set by the vault and does not depend on condition: a worn
    #: machine digs not less but worse -- that shows in quality on emptying.
    assert mined == pytest.approx(constants[R.RIG_OUTPUT_PER_HOUR] * 4)
    assert float(installation.hopper) == pytest.approx(mined)

    yard = await world.node_container(session, node)
    left = await rig._coal_available(session, yard.id)  # noqa: SLF001
    assert left == pytest.approx(coal_before - constants[R.RIG_FUEL_PER_HOUR] * 4, rel=0.01)


async def test_machine_loses_to_human_in_output(
    session: AsyncSession, constants: Constants
) -> None:
    """Craft is the way to get good ore, the rig -- a lot of average."""
    assert constants[R.RIG_OUTPUT_PER_HOUR] < constants[R.MINING_IRON_PER_HOUR]


# --- three obligations -------------------------------------------------------


async def test_rig_idle_without_coal(session: AsyncSession, constants: Constants) -> None:
    """Fuel ran out -- it stopped. Hence a standing contract with a coal hauler."""
    _, _, _, installation, _ = await _face(session, coal=0)
    mined = await rig.advance(session, constants, installation, now=_via(installation, 5))
    assert mined == 0
    assert float(installation.hopper) == 0


async def test_coal_lasts_exactly_its_hours(session: AsyncSession, constants: Constants) -> None:
    """An hour and a half of fuel -- an hour and a half of work, not five."""
    hours = 1.5
    coal = constants[R.RIG_FUEL_PER_HOUR] * hours
    _, _, _, installation, _ = await _face(session, coal=coal)

    mined = await rig.advance(session, constants, installation, now=_via(installation, 5))
    assert mined == pytest.approx(constants[R.RIG_OUTPUT_PER_HOUR] * hours, rel=0.01)


async def test_full_bunker_stops_machine(session: AsyncSession, constants: Constants) -> None:
    """Coming is mandatory: without a carter the enterprise does not work."""
    _, _, _, installation, _ = await _face(session, coal=100_000)
    volume = rig.hopper_capacity(constants)

    #: Deliberately more than the hopper holds.
    hours = constants[R.RIG_HOPPER_CAPACITY] * 3
    await rig.advance(session, constants, installation, now=_via(installation, hours))
    assert float(installation.hopper) == pytest.approx(volume, rel=0.02)

    #: And it grows no further, however long one waits.
    more = await rig.advance(session, constants, installation, now=_via(installation, hours))
    assert more == 0


async def test_ore_kept_over_short_passes_is_paid_for_in_coal(
    session: AsyncSession, constants: Constants
) -> None:
    """Coal is spent by the same measure as the ore, not by the clock.

    Fuel is written off in thousandths as well, so a pass too short to burn
    one burns nothing. That was harmless only while the ore was lost to the
    same rounding -- self-punishing. Keeping the ore and still charging coal
    by elapsed time would make `rig.empty`, which settles the rig and is
    not throttled, a way of raising ore for nothing.
    """
    node, _, _, installation, _ = await _face(session, coal=100)
    yard = await world.node_container(session, node)
    started = installation.counted_at
    coal_before = await rig._coal_available(session, yard.id)  # noqa: SLF001

    #: Half a second at a time: under what a thousandth of coal takes.
    for tick in range(1, 61):
        when = started + timedelta(seconds=tick / 2)
        await rig.advance(session, constants, installation, now=when)
        await session.flush()
        await session.refresh(installation, ["hopper", "hopper_remainder", "fuel_remainder"])

    burnt = coal_before - await rig._coal_available(session, yard.id)  # noqa: SLF001
    #: Ore did come out, and it was paid for: the coal spent is the fuel rate
    #: over the output rate, times the ore banked -- with the coal not yet
    #: thick enough to burn counted in, which is the difference between this
    #: test and a tolerance wide enough to hide the whole sliver.
    #:
    #: The tolerance left is for the sliver's own floor: each pass drops what
    #: falls past the ninth decimal, so the gap grows with the number of passes
    #: and not with the sum. Sixty passes lose about a ten-millionth of a unit;
    #: `abs` rather than `rel`, because that is how the error actually behaves.
    assert float(installation.hopper) > 0
    per_ore = constants[R.RIG_FUEL_PER_HOUR] / constants[R.RIG_OUTPUT_PER_HOUR]
    owed = burnt + float(installation.fuel_remainder)
    assert owed == pytest.approx(float(installation.hopper) * per_ore, abs=1e-6)


async def test_the_last_of_a_vein_is_banked_at_what_the_ground_gave(
    session: AsyncSession, constants: Constants
) -> None:
    """A vein ending on a half-thousandth neither invents ore nor loses it.

    `rig.depletion_multiplier` is two and a pickaxe leaves an odd remainder,
    so what the ground can still give the rig is half a thousandth -- off the
    grid the hopper is written on. Passed to the column as it came it rounded
    to the nearest, which is up as often as down: up, and the hopper held ore
    the ground never gave; down, and the last of the vein went nowhere while
    the pass still reported it mined.

    Both remainders here separate the two roundings, in opposite directions:
    half to even carries `0.0035` up to `0.004` and `0.0015` up to `0.002`,
    while the floor keeps `0.003` and `0.001`. A remainder of five would not --
    on `0.0025` the two agree -- and would leave the case looking covered.
    """
    _, vein, _, installation, _ = await _face(session)
    #: A vein all but worked out, and a sliver already waiting from the pass
    #: before -- together they ask for more than is left in the ground.
    for remaining in (7, 3):
        vein.remaining = remaining
        installation.hopper = Decimal(0)
        installation.hopper_remainder = Decimal("0.000900000")
        installation.counted_at = installation.counted_at - timedelta(hours=1)
        await session.flush()
        in_the_ground = vein.remaining

        mined = await rig.advance(session, constants, installation)
        await session.flush()
        await session.refresh(installation, ["hopper", "hopper_remainder"])
        await session.refresh(vein, ["remaining"])

        #: The one invariant: what left the ground is what the hopper holds,
        #: times the vault's depletion. Not a thousandth more, not one less.
        taken = amount_float(in_the_ground - vein.remaining)
        assert taken == pytest.approx(
            float(installation.hopper) * constants[R.RIG_DEPLETION_MULTIPLIER]
        )
        #: And the pass reports the number the row took, not the one it asked for.
        assert mined == pytest.approx(float(installation.hopper))
        assert 0 <= float(installation.hopper_remainder) < 0.001


def test_the_rig_slivers_are_kept_at_the_scale_they_are_written_with() -> None:
    """`ROUND_REMAINDER` and the two rig columns are one number in three places."""
    assert Rig.__table__.c.hopper_remainder.type.scale == ROUND_REMAINDER
    assert Rig.__table__.c.fuel_remainder.type.scale == ROUND_REMAINDER


async def test_a_short_pass_does_not_empty_the_vein_for_ore_nobody_gets(
    session: AsyncSession, constants: Constants
) -> None:
    """The vein gives up exactly what the hopper is credited with.

    The hopper keeps thousandths and the rig is settled by elapsed time, so a
    short pass raises less than one. The sum used to go straight to the column,
    which rounded it away -- while the vein had already been emptied for that
    same ore, and by `rig.depletion_multiplier` as much again. The ore left the
    world and reached nobody.
    """
    _, vein, _, installation, _ = await _face(session)
    started = installation.counted_at
    await session.refresh(vein)
    in_the_ground = vein.remaining

    #: Half a second at a time, well under what a thousandth of ore takes.
    for tick in range(1, 41):
        when = started + timedelta(seconds=tick / 2)
        await rig.advance(session, constants, installation, now=when)
        await session.flush()
        await session.refresh(installation, ["hopper", "hopper_remainder"])
        await session.refresh(vein, ["remaining"])

    taken = amount_float(in_the_ground - vein.remaining)
    #: Exactly: what left the ground is the depletion the vault sets times what
    #: the hopper actually holds. Not what was asked for -- the sliver still
    #: waiting on the rig has not been dug for, and that is the whole point.
    assert taken == pytest.approx(
        float(installation.hopper) * constants[R.RIG_DEPLETION_MULTIPLIER]
    )
    #: And the machine really did work, or the test proves nothing.
    assert float(installation.hopper) > 0
    #: And the sliver stays under the thousandth the hopper cannot show.
    assert 0 <= float(installation.hopper_remainder) < 0.001


async def test_a_rig_settled_often_wears_as_much_as_one_settled_once(
    session: AsyncSession, constants: Constants
) -> None:
    """`rig.wear_per_day` is paid however often the rig is brought up to date.

    Condition is kept to a hundredth, and at six a day a stretch under a
    couple of minutes cannot be written to it -- less still for a good machine,
    which wears slower. Such wear used to be dropped outright, and
    `empty_hopper` settles the rig too, so an owner tapping the button kept a
    machine that never wore out at all. The sliver waits on the thing itself
    now (`Item.wear_remainder`), not on any clock: the rig's stamp measures the
    mining as well, and holding it back mines the same ore twice.
    """
    from src.engine import wear

    _, _, _, often, machine_a = await _face(session)
    _, _, _, once, machine_b = await _face(session)
    started = often.counted_at
    once.counted_at = started
    for thing in (machine_a, machine_b):
        thing.condition = Decimal("100")
    await session.flush()

    #: Through the row every time: the whole defect lives in the round trip,
    #: where `Numeric(6, 2)` rounds the write away.
    steps, every = 60, timedelta(seconds=30)
    for tick in range(1, steps + 1):
        await rig.advance(session, constants, often, now=started + every * tick)
        await session.refresh(machine_a, ["condition"])
    await rig.advance(session, constants, once, now=started + every * steps)
    await session.refresh(machine_b, ["condition"])

    #: The ore is the trap this fix fell into once: carrying the sliver on
    #: `counted_at` made the next pass re-mine the same stretch. The busy rig
    #: must have dug exactly what the quiet one dug.
    #: Hopper and sliver together, because the sliver is where the ore that
    #: does not fit the thousandth waits -- the busy rig banks a little less
    #: and owes the difference. Sixty sums against one, so to a millionth
    #: rather than to the digit: the double count this guards against was a
    #: whole percent and more.
    raised_often = float(often.hopper) + float(often.hopper_remainder)
    raised_once = float(once.hopper) + float(once.hopper_remainder)
    assert raised_often == pytest.approx(raised_once)

    term = wear.life_factor(constants, float(machine_a.quality))
    worn = constants[R.RIG_WEAR_PER_DAY] / term * (steps * every) / _terra_day(constants)
    #: The whole point: the busy rig wore exactly as much as the quiet one.
    assert Decimal(machine_a.condition) == Decimal(machine_b.condition)
    #: And neither wore more than the half hour earned, nor fell more than the
    #: one step behind it that the column cannot yet show -- that part is not
    #: lost, it waits on the thing.
    assert 100 - float(machine_b.condition) <= worn
    assert 100 - float(machine_b.condition) > worn - 0.01


async def test_rig_wears_and_abandoned_falls_apart(
    session: AsyncSession, constants: Constants
) -> None:
    """`rig.wear_per_day` goes by time, not by what is mined.

    A good machine wears slower -- by the same common rule as a pickaxe and an
    anvil (D-129): no second formula is created for the rig.
    """
    from src.engine import wear

    _, _, _, installation, machine = await _face(session)
    before = float(machine.condition)
    day = constants[R.TIME_DAY_TERRA]
    await rig.advance(session, constants, installation, now=_via(installation, day))

    term = wear.life_factor(constants, float(machine.quality))
    assert float(machine.condition) == pytest.approx(
        before - constants[R.RIG_WEAR_PER_DAY] / term, abs=0.01
    )


# --- emptying and quality ----------------------------------------------------


async def test_bunker_emptied_on_foot_and_quality_under_ceiling(
    session: AsyncSession, constants: Constants
) -> None:
    """The machine works by its setting: above `rig.quality_cap` it does not give."""
    _, vein, body, installation, _ = await _face(session, richness=80)
    #: An hour of work, not three: the hopper is emptied by hand, and hands are
    #: not bottomless (D-146). A full hopper is work for a carter, not for pockets.
    await rig.advance(session, constants, installation, now=_via(installation, 1))

    taken = await rig.empty_hopper(session, constants, body, installation)
    assert taken > 0
    assert float(installation.hopper) == 0

    pocket = await world.body_container(session, body)
    from sqlalchemy import select

    ore_ = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == vein.resource)
            )
        )
        .scalars()
        .all()
    )
    assert ore_, "бункер переехал в карман"
    quality = float(ore_[0].quality)
    assert quality == pytest.approx(constants[R.RIG_QUALITY_CAP])
    assert quality < 80, "богатая жила машине не помогает — она ровна по настройке"
    assert amount_float(ore_[0].amount) == pytest.approx(taken, rel=0.01)


async def test_broken_machine_gives_worse_ore(session: AsyncSession, constants: Constants) -> None:
    """Maintenance is mandatory: a worn one does not break suddenly, it works worse."""
    _, _, body, installation, machine = await _face(session, richness=80)
    from decimal import Decimal

    machine.condition = Decimal("20")
    await session.flush()
    await rig.advance(session, constants, installation, now=_via(installation, 1))
    await rig.empty_hopper(session, constants, body, installation)

    from sqlalchemy import select

    pocket = await world.body_container(session, body)
    ore_ = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == "iron_ore")
            )
        )
        .scalars()
        .all()
    )
    quality = float(ore_[0].quality)
    assert quality < constants[R.RIG_QUALITY_CAP], "потолок опустился с износом"


async def test_foreign_bunker_not_emptied(session: AsyncSession, constants: Constants) -> None:
    """Emptying is by contract with the owner, not by showing up (D-116)."""
    node, _, _, installation, _ = await _face(session)
    foreign_id = await world.create_identity(session, f"Чужой-{uuid.uuid4().hex[:6]}")
    foreign_body = await world.print_body(session, foreign_id, node)

    with pytest.raises(rig.NotYours):
        await rig.empty_hopper(session, constants, foreign_body, installation)


async def test_eats_vein_twice_as_fast(session: AsyncSession, constants: Constants) -> None:
    """Capital speeds up the world's depletion -- and that is a reason for a dispute at the vein
    (D-101)."""
    _, vein, _, installation, _ = await _face(session)
    before = vein.remaining

    mined = await rig.advance(session, constants, installation, now=_via(installation, 4))
    went = amount_float(before - vein.remaining)
    assert went == pytest.approx(mined * constants[R.RIG_DEPLETION_MULTIPLIER], rel=0.01)


# --- the liquid vein (D-252) -------------------------------------------------


async def _oil_face(session: AsyncSession, *, coal: float = 100):
    """Same enterprise, but the vein is oil: pumped, never picked."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.oil.{stamp}", "Поле", area_m2=200)
    vein = await world.create_vein(session, node, "crude_oil", richness=55, remaining=100_000)
    yard = await world.node_container(session, node)
    if coal > 0:
        await world.grant_item(session, yard, "coal", amount=coal, quality=55, origin="тест")
    identity = await world.create_identity(session, f"Нефтяник-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="тест")
    installation = await rig.place(session, body, machine, vein)
    return node, vein, body, pocket, installation


async def test_oil_pours_into_the_vessel_and_the_rest_waits_in_the_hopper(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """A liquid hopper is poured, not handed over (D-252): into the vessels
    within reach, and what fits nowhere stays in the hopper -- the well does
    not spill for a forgotten canister."""
    from sqlalchemy import select

    from src.engine import storage

    _, _, body, pocket, installation = await _oil_face(session)
    canister = await world.grant_item(session, pocket, "canister", quality=60, origin="тест")

    moment = _via(installation, 8)
    taken = await rig.empty_hopper(session, constants, body, installation, now=moment)

    pumped = constants[R.RIG_OUTPUT_PER_HOUR] * 8
    #: The canister takes 20 kg of oil at 0.17 kg a unit -- less than the pump gave.
    capacity = storage.capacity(catalog, "canister") / catalog.recipes.mass_of("crude_oil")
    assert taken == pytest.approx(capacity, rel=0.01)
    assert float(installation.hopper) == pytest.approx(pumped - taken, rel=0.01)

    inside = await storage.inside(session, canister)
    stacks = (
        (await session.execute(select(Item).where(Item.container_id == inside.id))).scalars().all()
    )
    assert len(stacks) == 1 and stacks[0].type_key == "crude_oil"
    assert amount_float(stacks[0].amount) == pytest.approx(taken, rel=0.01)
    #: Quality under the machine's ceiling, as with ore (D-115).
    assert float(stacks[0].quality) == constants[R.RIG_QUALITY_CAP]

    #: Nothing lies loose: a liquid outside a vessel does not exist (D-230).
    loose = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == "crude_oil")
            )
        )
        .scalars()
        .all()
    )
    assert loose == []


async def test_oil_with_no_vessel_is_refused(session: AsyncSession, constants: Constants) -> None:
    """Coming to an oil well without a canister is a wasted trip, and the
    engine says so instead of silently doing nothing (D-252)."""
    _, _, body, _, installation = await _oil_face(session)

    with pytest.raises(rig.NoRoom) as refused:
        await rig.empty_hopper(session, constants, body, installation, now=_via(installation, 4))
    assert refused.value.key == "rig-liquid-no-room"
    assert refused.value.params["goods"] == "crude_oil"
