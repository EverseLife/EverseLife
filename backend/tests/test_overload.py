# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What does not fit in the hands falls underfoot (D-265).

The carry limit stood at every door a thing is taken through and at none it
arrives through by itself: a batch paid out into the master's hands, the
alpha printer printed into them, and a body walked off with a station it
could never have lifted (playtest 2026-09-02). Here the two doors are tried
past the limit, the fall is checked piece by piece and kilogram by kilogram,
the overfull floor is seen to shout, and two arrivals at once are made to
share one pair of hands.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import alpha, craft, gear, jobs, overload, storage, world
from src.models.estate import Building
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float

SACK = "sack"
STEEL = "steel"
ORE = "iron_ore"
INGOT = "iron_ingot"
NAILS = "nails"
FORGE = "forge"


async def _ground(session: AsyncSession):
    """Nobody's land under the open sky: what falls, falls on the ground."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.field.{stamp}", "Поле", area_m2=200)
    identity = await world.create_identity(session, f"Носильщик-{stamp}")
    body = await world.print_body(session, identity, node)
    return node, identity, body


async def _house(session: AsyncSession, area: float = 200):
    """Own plot with a roof: what falls, falls on the floor."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.home.{stamp}", "Дом", area_m2=200)
    node.owner_city_id = uuid.uuid4()
    session.add(Building(node_id=node.id, area_m2=area))
    await session.flush()
    identity = await world.create_identity(session, f"Хозяин-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.grant_node(session, node, identity)
    return node, identity, body


async def _held(session: AsyncSession, body: Body, type_key: str) -> float:
    pocket = await world.body_container(session, body)
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == type_key
        )
    )
    return amount_float(int(total or 0))


async def _lying(session: AsyncSession, node: Node, type_key: str) -> float:
    yard = await world.node_container(session, node)
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == yard.id, Item.type_key == type_key
        )
    )
    return amount_float(int(total or 0))


async def _hold(session: AsyncSession, body: Body, type_key: str, quantity: float) -> Item:
    """Put a thing straight into the hands, past every door: what the load is
    made of is not what this file is about."""
    pocket = await world.body_container(session, body)
    return await world.grant_item(
        session, pocket, type_key, amount=quantity, quality=60, origin="сценарий теста"
    )


async def _charged(session: AsyncSession, body: Body, charge: float = 50) -> Item:
    """A cell with charge in it: without one the frame is a frame, and lifts
    nothing (D-268). Every test below that leans on the exoskeleton's limit
    needs it, or the limit it measures is the bare thirty kilograms."""
    cell = await _hold(session, body, "battery", 1)
    cell.charge = Decimal(str(charge))
    cell.charged_at = datetime.now(UTC)
    await session.flush()
    return cell


async def _told(session: AsyncSession, identity_id: uuid.UUID, kind: EventKind) -> list[Event]:
    rows = await session.execute(
        select(Event).where(Event.kind == kind.value, Event.actor_identity_id == identity_id)
    )
    return list(rows.scalars().all())


async def test_taking_the_frame_off_drops_what_it_was_carrying(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The limit falls with the exoskeleton, and the load falls with it (D-265).

    The hole the rule was written against, from the other side: nothing
    arrives, the limit simply shrinks. Wear the frame, fill the hands to its
    limit, take it off -- and before this the ore stayed in the pocket and
    walked out of the node. "An overloaded body does not exist any more"
    (D-265, D-268) was true only of the doors things came in through.
    """
    node, _, body = await _ground(session)
    base = constants[R.INVENTORY_CARRY_MASS]
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, exo)
    assert await gear.capacity(session, constants, catalog, body) > base, "каркас поднимает"
    #: A hundred kilograms of ore: nothing a bare pair of hands could hold.
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= base + 1e-6, f"после снятия каркаса тело несёт {carries} при пределе {base}"
    assert await _lying(session, node, ORE) > 0, "лишнее легло под ноги"
    assert await _held(session, body, "exoskeleton") == 1, "снятый каркас остаётся в руках"


async def test_the_lighter_frame_drops_what_the_heavier_carried(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One slot, two frames: putting on the weaker one is the same shrinking
    limit, and the previous frame comes off by itself (`gear.equip`)."""
    node, _, body = await _ground(session)
    heavy = await _hold(session, body, "heavy_exoskeleton", 1)
    light = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, heavy)
    await _hold(session, body, ORE, 250 / catalog.recipes.mass_of(ORE))

    await gear.equip(session, constants, catalog, body, light)

    carries = await gear.load_of(session, constants, catalog, body)
    limit = await gear.capacity(session, constants, catalog, body)
    assert carries <= limit + 1e-6, f"тело несёт {carries} при пределе {limit}"
    assert await _lying(session, node, ORE) > 0


async def test_what_is_worn_never_falls(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The shedding strips nobody: a pack on the back is on the body, not in
    the hands, and taking one thing off must not silently take another."""
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    pack = await _hold(session, body, "sturdy_backpack", 1)
    await gear.equip(session, constants, catalog, body, exo)
    await gear.equip(session, constants, catalog, body, pack)
    await _hold(session, body, ORE, 200 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    worn = await gear.equipped(session, body)
    assert "back" in worn and worn["back"].id == pack.id, "рюкзак остался на теле"
    assert await _lying(session, node, "sturdy_backpack") == 0


async def test_dressing_that_lowers_nothing_drops_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Only the act that lowered the limit sheds (D-306).

    A suit is put on over a load already past the limit -- the world puts one
    there past every door, and until the "before and after" test the dressing
    answered for an overload it had not caused: the cylinder in the hands hit
    the ground and the wearer suffocated in a suit they had just donned.
    """
    node, _, body = await _ground(session)
    suit = await _hold(session, body, "heatproof_suit", 1)
    #: Past the limit before a finger is lifted: nothing here arrived by a door.
    await _hold(session, body, ORE, 60 / catalog.recipes.mass_of(ORE))
    before = await gear.load_of(session, constants, catalog, body)
    assert before > await gear.capacity(session, constants, catalog, body)

    await gear.equip(session, constants, catalog, body, suit)

    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(before)
    assert await _lying(session, node, ORE) == 0, "надетое ничего не опустило -- ничего и не упало"


async def test_a_drained_frame_drops_what_it_was_carrying(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No charge, no lift, and no load above the bare hands either (D-306).

    The frame is powered by a cell in the hands, so taking it off is only one
    of the ways the lift goes. The tick is the sweep that answers all of them:
    it finds the wearer without a charge, whatever the road to it.
    """
    node, _, body = await _ground(session)
    base = constants[R.INVENTORY_CARRY_MASS]
    exo = await _hold(session, body, "exoskeleton", 1)
    cell = await _charged(session, body, 0.5)
    await gear.equip(session, constants, catalog, body, exo)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    assert await gear.load_of(session, constants, catalog, body) > base

    moment = datetime.now(UTC)
    await gear.wear_exoskeletons(session, constants, catalog, hours=100, now=moment)

    assert float(cell.charge) == 0, "тик выпил ячейку до дна"
    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= base + 1e-6, f"без заряда тело несёт {carries} при пределе {base}"
    assert await _lying(session, node, ORE) > 0


async def test_a_charged_frame_drops_nothing_at_the_tick(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """While the cell holds, the frame lifts and the tick is a drink, not a fall."""
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body, 500)
    await gear.equip(session, constants, catalog, body, exo)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    before = await gear.load_of(session, constants, catalog, body)

    await gear.wear_exoskeletons(session, constants, catalog, hours=1, now=datetime.now(UTC))

    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(before)
    assert await _lying(session, node, ORE) == 0


async def test_the_cell_put_down_is_the_same_lost_lift(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A cell laid on the ground lowers the limit exactly as a drained one does,
    and the sweep does not ask by which road the charge left the hands (D-306).

    Without this the fix would have been a fix of one click: wear, load, put
    the cell down, walk off with the load and pick the cell up later.
    """
    node, _, body = await _ground(session)
    base = constants[R.INVENTORY_CARRY_MASS]
    exo = await _hold(session, body, "exoskeleton", 1)
    cell = await _charged(session, body, 500)
    await gear.equip(session, constants, catalog, body, exo)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    #: Out of the hands and onto the ground, by the ordinary door.
    await storage.drop(session, constants, catalog, body, cell)
    await gear.wear_exoskeletons(session, constants, catalog, hours=1, now=datetime.now(UTC))

    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= base + 1e-6, f"аккумулятор на земле -- тело несёт {carries}"
    assert await _lying(session, node, ORE) > 0


async def test_a_full_vessel_falls_by_what_it_holds(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A vessel weighs its fill, and so it must fall by its fill (D-230).

    The falls used to read the tare alone: a plastic canister of two and a half
    kilograms holding forty sorted as the lightest thing in the hands and, once
    it did fall, was subtracted from the excess as two and a half. The ore went
    first and kept going -- the hands were emptied of everything and the load
    was still over.
    """
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, exo)
    can = await _hold(session, body, "plastic_canister", 1)
    inside = await storage.inside(session, can)
    await world.grant_item(
        session, inside, "water", amount=40 / catalog.recipes.mass_of("water"), origin="тест"
    )
    await _hold(session, body, ORE, 20 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    #: The heaviest thing in the hands was the canister, fill and all.
    assert await _held(session, body, "plastic_canister") == 0, "полная канистра ушла первой"
    assert await _lying(session, node, "plastic_canister") == 1
    #: And the ore was not emptied out after it: the excess was already paid.
    assert await _held(session, body, ORE) > 0, "руду сняли ровно на избыток"
    carries = await gear.load_of(session, constants, catalog, body)
    assert carries <= constants[R.INVENTORY_CARRY_MASS] + 1e-6


async def test_gear_heavier_than_bare_hands_takes_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What is worn is a floor the shedding cannot go under (D-306).

    A heavy frame and a suit weigh more than the bare limit together, so a
    wearer whose cell runs dry is over it with empty hands. Emptying the pocket
    would take the food, the tool and the air cylinder and leave the body over
    anyway -- every tick, for ever, and on Pyroxis that is a death by suffocation
    for a state the numbers should not allow. Nothing is taken; the log shouts.
    """
    node, _, body = await _ground(session)
    frame = await _hold(session, body, "heavy_exoskeleton", 1)
    suit = await _hold(session, body, "heatproof_suit", 1)
    await gear.equip(session, constants, catalog, body, frame)
    await gear.equip(session, constants, catalog, body, suit)
    tank = await _hold(session, body, "oxygen_tank", 1)
    worn = catalog.recipes.mass_of("heavy_exoskeleton") + catalog.recipes.mass_of("heatproof_suit")
    assert worn > constants[R.INVENTORY_CARRY_MASS], "снаряжение тяжелее голых рук"

    #: No cell at all: the frame lifts nothing, and the limit is the bare hands.
    await gear.wear_exoskeletons(session, constants, catalog, hours=1, now=datetime.now(UTC))

    assert await _held(session, body, "oxygen_tank") == 1, "баллон остался в руках"
    assert await _lying(session, node, "oxygen_tank") == 0
    assert tank.container_id == (await world.body_container(session, body)).id


async def test_the_frame_just_taken_off_stays_in_the_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What was just taken off does not fall (D-306).

    `unequip` deletes the worn record before it settles, so without a word the
    frame is no longer "worn" and becomes the heaviest thing in the hands --
    and lands on the ground, out of reach of the very hands that could not
    lift what it was lifting.
    """
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, exo)
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    assert await _held(session, body, "exoskeleton") == 1, "снятый каркас остался в руках"
    assert await _lying(session, node, "exoskeleton") == 0
    assert await _lying(session, node, ORE) > 0, "упала руда, а не каркас"


async def test_undressing_answers_for_its_own_excess_and_no_more(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The overload a door found is not the door's to answer (D-306).

    The world puts things into the hands past every door -- the harvest does
    it to this day -- so a body can already be over the limit when a pack comes
    off. Taking the pack off adds its own few kilograms and takes those; the
    fifty somebody else's door let in stay where they were.
    """
    node, _, body = await _ground(session)
    pack = await _hold(session, body, "sturdy_backpack", 1)
    await gear.equip(session, constants, catalog, body, pack)
    await _hold(session, body, ORE, 80 / catalog.recipes.mass_of(ORE))
    before = await gear.load_of(session, constants, catalog, body)
    over = before - await gear.capacity(session, constants, catalog, body)
    assert over > 0, "тело уже за пределом, и не этой дверью"

    await gear.unequip(session, constants, catalog, body, "back")

    #: The pack stopped lightening the load, and that -- and only that -- fell:
    #: the felt load is where it was, and the fifty it found stayed in the hands.
    left = await gear.load_of(session, constants, catalog, body)
    assert left == pytest.approx(before, abs=0.5), f"упало лишнее: было {before}, стало {left}"
    fell = await _lying(session, node, ORE) * catalog.recipes.mass_of(ORE)
    assert fell < 15, f"с рудой ушло {fell} кг вместо облегчения рюкзака"
    assert await _held(session, body, ORE) > 0, "руки не вычищены"


async def test_the_air_a_body_breathes_never_falls(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Where there is no air, the cylinder is not what falls (D-233, D-306).

    The suit is worn and safe; the air is a bottle in the hands, and a heavy
    one. Dropped for the sake of the carry limit it is a death sentence carried
    out while the player is offline -- the tick that took it would suffocate
    them on the next pass. Nothing but air goes into a breathing cylinder, so
    the exception carries no ore.
    """
    from src.engine import oxygen
    from src.models.world import Layer, Planet

    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session,
        f"pyroxis.sky.{stamp}",
        "Пироксис",
        area_m2=1,
        planet=Planet.PYROXIS,
        layer=Layer.SPACE,
        properties={oxygen.AIRLESS: True},
    )
    node = await world.create_node(
        session,
        f"pyroxis.field.{stamp}",
        "Плато",
        area_m2=400,
        planet=Planet.PYROXIS,
        layer=Layer.PLANET,
        parent=sphere,
    )
    identity = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, identity, node)

    suit = await _hold(session, body, "heatproof_suit", 1)
    await gear.equip(session, constants, catalog, body, suit)
    exo = await _hold(session, body, "exoskeleton", 1)
    await _charged(session, body)
    await gear.equip(session, constants, catalog, body, exo)
    bottle = await _hold(session, body, "oxygen_tank", 1)
    inside = await storage.inside(session, bottle)
    await world.grant_item(session, inside, "oxygen", amount=10, origin="тест")
    await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))

    await gear.unequip(session, constants, catalog, body, "frame")

    assert await _held(session, body, "oxygen_tank") == 1, "баллон остался в руках"
    assert await _lying(session, node, "oxygen_tank") == 0
    assert await _lying(session, node, ORE) > 0, "упала руда"


async def test_a_print_past_the_limit_falls_in_whole_pieces(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Sixty ingots printed into empty hands: what is over the limit lies on the ground."""
    node, identity, body = await _ground(session)
    await alpha.spawn(session, constants, catalog, body, type_key=STEEL, amount=60)

    limit = await gear.capacity(session, constants, catalog, body)
    assert await gear.load_of(session, constants, catalog, body) <= limit + 1e-6
    kept, fell = await _held(session, body, STEEL), await _lying(session, node, STEEL)
    assert kept + fell == 60, "материя не пропала"
    assert kept == int(kept) and fell == int(fell), "штучное падает целыми штуками"
    assert fell > 0
    #: One more ingot would not have fit: the hands are as full as they may be.
    #: Weighed as the load would be, not as it is plus a kilogram -- readings of
    #: a load do not add up once a pack bends them (`gear.carried_mass`).
    unit = gear.mass_of(catalog, STEEL, 1)
    worn = await gear.equipped(session, body)
    mass = await gear.carried_mass(session, catalog, body) + unit
    assert gear.packed(constants, catalog, worn, mass) > limit

    said = await _told(session, identity.id, EventKind.ITEM_FELL)
    assert len(said) == 1 and said[0].payload["roofed"] is False
    assert said[0].payload["amount"] == pytest.approx(fell)


async def test_a_measured_thing_falls_by_the_excess(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _ground(session)
    await alpha.spawn(session, constants, catalog, body, type_key=ORE, amount=300)
    limit = await gear.capacity(session, constants, catalog, body)
    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(limit, abs=1e-3)
    total = await _held(session, body, ORE) + await _lying(session, node, ORE)
    assert total == pytest.approx(300)


async def test_what_falls_is_matter_under_a_roomy_pack(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What falls is measured in matter, not in what the body feels.

    A pack roomier than the whole limit bends the two apart: inside its
    capacity a kilogram put down lightens the body by `factor` of itself, so
    dropping the felt excess would leave the hands over the limit still. No
    pack in the vault reaches there -- every `capacity * factor` is under
    `inventory.carry_mass`, and `test_gear` keeps a tripwire on it -- so the
    pack here is doctored on purpose: the door has to be right for the pack
    that gets written, not only for the five that are.
    """
    node, _, body = await _ground(session)
    roomy = Constants(
        {**constants.raw(), "inventory.pack": {SACK: {"capacity": 100, "factor": 0.5}}},
        source="тест",
    )
    pocket = await world.body_container(session, body)
    sack = await world.grant_item(session, pocket, SACK, amount=1, quality=60, origin="тест")
    await gear.equip(session, roomy, catalog, body, sack)
    #: Seventy kilograms of steel: the body feels thirty-five of them, five over
    #: the limit -- and five kilograms put down would leave it feeling 32.5.
    steel = await world.grant_item(session, pocket, STEEL, amount=70, quality=60, origin="тест")

    fell = await overload.settle_load(session, roomy, catalog, body, [steel])

    limit = await gear.capacity(session, roomy, catalog, body)
    assert fell > 0
    assert await gear.load_of(session, roomy, catalog, body) <= limit + 1e-6, (
        "упал прочувствованный излишек вместо материи"
    )
    assert await _held(session, body, STEEL) + await _lying(session, node, STEEL) == pytest.approx(
        70
    ), "материя не пропала"


async def test_the_yield_of_a_batch_falls_at_the_bench(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The master stands at the forge with full hands: the nails fall on the floor.

    The hands are over the limit before the batch -- ingots granted past any
    door, as a print or a yield would be -- and what the rule drops is the
    thing that arrives, never what was carried before it: the nails lie on
    the floor whole, the ingots stay in the hands.
    """
    async with factory() as session, session.begin():
        node, identity, body = await _house(session)
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, FORGE, quality=60, origin="тест")
        await world.learn(session, identity, NAILS)
        pocket = await world.body_container(session, body)
        await world.grant_item(session, pocket, INGOT, amount=60, quality=60, origin="тест")
        batch = await craft.start(session, constants, catalog, body, NAILS, 20)
        ready, identity_id, node_id = batch.ready_at, identity.id, node.id

    assert await jobs.run_one(factory, now=ready) is not None

    async with factory() as session:
        body = (
            await session.execute(select(Body).where(Body.identity_id == identity_id))
        ).scalar_one()
        node = await session.get(Node, node_id)
        assert node is not None
        assert await _lying(session, node, NAILS) == 20, "гвозди легли на пол целиком"
        assert await _held(session, body, NAILS) == 0
        assert await _held(session, body, INGOT) > 0, "несённое до того осталось в руках"
        said = await _told(session, identity_id, EventKind.ITEM_FELL)
        assert said and said[0].payload["roofed"] is True


async def test_an_overfull_floor_is_journaled_and_shouted(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ten-metre room holds two hundred kilos of cargo: three hundred fall anyway."""
    node, identity, body = await _house(session, area=10)
    budget = 10 * constants[R.BUILD_FLOOR_PER_M2]
    count = int(budget / gear.mass_of(catalog, STEEL, 1)) + 40
    with caplog.at_level(logging.ERROR, logger="src.engine.overload"):
        await alpha.spawn(session, constants, catalog, body, type_key=STEEL, amount=count)

    assert await _lying(session, node, STEEL) > 0, "упало, хотя места не было"
    over = await _told(session, identity.id, EventKind.STORAGE_OVERFULL)
    assert len(over) == 1 and over[0].payload["roofed"] is True
    assert any("floor overfull" in line.message for line in caplog.records)


async def test_two_prints_at_once_share_one_pair_of_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the row lock both arrivals read empty hands and both keep a load."""
    #: On the path the arrival takes: `settle_load` reads the matter in the
    #: hands, not the load through the pack, and a delay hung on a function
    #: nobody calls widens no window at all.
    _slow(monkeypatch, gear, "carried_mass")
    node, _, body = await _ground(session)
    body_id, node_id = body.id, node.id
    await session.commit()

    async def print_steel() -> None:
        async with factory() as db, db.begin():
            who = await db.get(Body, body_id)
            assert who is not None
            await alpha.spawn(db, constants, catalog, who, type_key=STEEL, amount=30)

    await asyncio.gather(print_steel(), print_steel())

    async with factory() as db:
        who = await db.get(Body, body_id)
        assert who is not None
        limit = await gear.capacity(db, constants, catalog, who)
        assert await gear.load_of(db, constants, catalog, who) <= limit + 1e-6, (
            "две печати нашли одно место"
        )
        field = await db.get(Node, node_id)
        assert field is not None
        assert await _held(db, who, STEEL) + await _lying(db, field, STEEL) == 60


async def test_the_tick_and_a_hand_over_one_heap_lose_nothing(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep moves a living player's stacks, so it races their own hands.

    The tick sheds a drained wearer while the player puts the same heap down
    themselves. Whichever wins, matter is conserved and the hands end within
    the limit: `world.move_stack` locks and rereads each stack, so neither
    side moves what the other already took.
    """
    _slow(monkeypatch, gear, "load_of")
    node, _, body = await _ground(session)
    exo = await _hold(session, body, "exoskeleton", 1)
    await gear.equip(session, constants, catalog, body, exo)
    #: No cell at all: the frame is a frame, and the sweep will find it so.
    ore = await _hold(session, body, ORE, 100 / catalog.recipes.mass_of(ORE))
    body_id, node_id, ore_id = body.id, node.id, ore.id
    whole = await _held(session, body, ORE)
    await session.commit()

    async def sweep() -> None:
        async with factory() as db, db.begin():
            await gear.wear_exoskeletons(db, constants, catalog, hours=1, now=datetime.now(UTC))

    async def by_hand() -> None:
        async with factory() as db, db.begin():
            who = await db.get(Body, body_id)
            stack = await db.get(Item, ore_id)
            assert who is not None and stack is not None
            await storage.drop(db, constants, catalog, who, stack)

    outcomes = await asyncio.gather(sweep(), by_hand(), return_exceptions=True)
    raised = [one for one in outcomes if isinstance(one, Exception)]
    assert not raised, raised

    async with factory() as db:
        who = await db.get(Body, body_id)
        field = await db.get(Node, node_id)
        assert who is not None and field is not None
        assert await _held(db, who, ORE) + await _lying(db, field, ORE) == pytest.approx(whole), (
            "руда не удвоилась и не пропала"
        )
        limit = await gear.capacity(db, constants, catalog, who)
        assert await gear.load_of(db, constants, catalog, who) <= limit + 1e-6
