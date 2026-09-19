# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a standing machine and the plot under it.

One of the race files (see `test_races.py` for the family's method): here the
contested rows are a machine, the fuel beside it, its vein and the node it
stands on, and the question is not a lost write but the **order** they are
taken in. The rig tick holds every rig of the world in one transaction; a
carter settles one rig through the same pass (`rig.empty_hopper`); a house
falling takes the plot and buries what stood and lay indoors; the doors that
stand a machine up and take it down take the plot and the thing; the fire of
Pyroxis takes a field's veins and then everything lying in it. Two of them
taking the same pair the other way round is a deadlock, and the database
kills one: a whole world's rig pass, a day's collapse, an eruption replayed by
the worker's retry. And a pass planned on a free read must not write to a
machine that burnt before the lock, nor burn coal that was carried off.

Each race is built to meet on the crossing every time, not when a pass happens
to outrun a pause: one side stops holding the row the other needs and goes on
only once the other is seen waiting on it (`conftest._until_blocked_by`).
The tick against the hands on a rig's coal, hopper and machine -- the
taking-down door and the place door among them -- is `test_races_rig.py`'s.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from pyroxis_kit import _surface
from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import estate, plates, rig, station, stock, wear, world
from src.engine.rig import run as rig_run
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.models.job import Job, JobKind
from src.models.rig import Rig
from src.models.world import Node, Vein
from src.units import amount_float

ORE = "iron_ore"


def _low() -> uuid.UUID:
    """An id below every random one: the first row an `ORDER BY id` lock takes."""
    return uuid.UUID(bytes=b"\x00" + os.urandom(15))


def _mid() -> uuid.UUID:
    """An id between `_low` and `_high`."""
    return uuid.UUID(bytes=b"\x80" + os.urandom(15))


def _high() -> uuid.UUID:
    """An id above every random one: the last row an `ORDER BY id` lock takes."""
    return uuid.UUID(bytes=b"\xff" + os.urandom(15))


def _failures(outcome: list) -> list[BaseException]:
    return [one for one in outcome if isinstance(one, BaseException)]


async def _falling_house(session: AsyncSession, constants, node: Node) -> None:
    """A house on the node one day short of nothing: the day's step brings it down."""
    house = Building(node_id=node.id, area_m2=40)
    session.add(house)
    await session.flush()
    house.condition = Decimal(str(estate.decay_per_day(constants, house.kind)))


async def _pit(session: AsyncSession) -> tuple[Node, Vein]:
    node = await world.create_node(
        session, f"terra.pit.{uuid.uuid4().hex[:6]}", "Забой", area_m2=200
    )
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    return node, vein


async def _rig_on(session: AsyncSession, node: Node, vein: Vein, *, coal: float = 1000):
    """A rig standing on the vein, fuelled from the node's yard, and whoever stood it:
    the rig's row, the machine, the coal and the body."""
    yard = await world.node_container(session, node)
    fuel = await world.grant_item(session, yard, "coal", amount=coal, quality=55, origin="тест")
    identity = await world.create_identity(session, f"Промышленник-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, "drilling_rig", quality=70, origin="тест")
    installation = await rig.place(session, body, machine, vein)
    return installation, machine, fuel, body


def _held_after(
    monkeypatch: pytest.MonkeyPatch,
    owner: object,
    name: str,
    factory: async_sessionmaker[AsyncSession],
    tasks: dict[str, asyncio.Future],
    other: str,
) -> tuple[asyncio.Event, list[bool]]:
    """Stop the first caller of `owner.name` right after it returns, holding
    what it took, until the task `other` is seen waiting on it.

    The event says the caller got there, for the other side to start on; the
    list gets whether the other side waited at all (`False`: it walked
    through and finished, which is the answer on code with no lock between).
    """
    reached = asyncio.Event()
    waited: list[bool] = []
    original = getattr(owner, name)

    async def held(db, *args, **kwargs):
        result = await original(db, *args, **kwargs)
        if not reached.is_set():
            reached.set()
            waited.append(await _until_blocked_by(factory, db, unless=tasks[other]))
        return result

    monkeypatch.setattr(owner, name, held)
    return reached, waited


async def _fall(factory: async_sessionmaker[AsyncSession], after: asyncio.Event) -> int:
    """The day's step for the houses, once `after` is set. Returns how many fell."""
    await asyncio.wait_for(after.wait(), timeout=30)
    async with factory() as db, db.begin():
        _, fallen = await estate.decay(db, current())
        return fallen


async def _tick(factory: async_sessionmaker[AsyncSession], moment: datetime) -> float:
    async with factory() as db, db.begin():
        return await rig.tick_rigs(db, current(), now=moment)


async def _erupt(factory: async_sessionmaker[AsyncSession], field: Node) -> None:
    """The eruption as `plates.warned` queues it, shaking this field."""
    job = Job(
        kind=JobKind.PLATES_ERUPT.value,
        run_at=datetime.now(UTC),
        payload={"nodes": [str(field.id)]},
    )
    async with factory() as db, db.begin():
        await plates.erupted(db, job)


async def _empty(
    factory: async_sessionmaker[AsyncSession], body: Body, installation: Rig, moment: datetime
) -> float | str:
    """The carter at the hopper. A refusal comes back as its key, and rolls the
    transaction back as a command's does."""
    try:
        async with factory() as db, db.begin():
            own_body = await db.get(Body, body.id)
            own = await db.get(Rig, installation.id)
            #: The machine in the session's memory too, as it is for whoever
            #: looked at it earlier in the command: the identity map is weak,
            #: and a copy nobody holds would vanish on its own.
            _looked = await db.get(Item, installation.item_id)
            return await rig.empty_hopper(db, current(), own_body, own, now=moment)
    except rig.NoRig as refusal:
        return refusal.key


def _after_the_plan(
    monkeypatch: pytest.MonkeyPatch, yard_id: uuid.UUID
) -> tuple[asyncio.Event, asyncio.Event]:
    """Stop a pass of a rig in `yard_id` once it has planned -- the machine
    judged standing and the coal counted, both off free reads, nothing locked
    past the rig's row and nothing written -- until the second event is set.
    The first says the pass got there."""
    planned = asyncio.Event()
    resume = asyncio.Event()
    count = rig_run._coal_available

    async def counted_and_held(db, container_id):
        coal = await count(db, container_id)
        if container_id == yard_id and not planned.is_set():
            planned.set()
            await asyncio.wait_for(resume.wait(), timeout=30)
        return coal

    #: In the room the pass counts it from: `advance` reads the name off its
    #: own module, and one set on the door alone would never stop the pass.
    monkeypatch.setattr(rig_run, "_coal_available", counted_and_held)
    return planned, resume


async def test_a_house_falling_on_a_rig_mid_pass_does_not_cross_the_tick(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collapse takes the plot, then buries the machine; the tick writes the
    machine, then writes its rig row a second time -- and a row written twice
    in one transaction re-checks its foreign keys, which holds the node `FOR
    KEY SHARE`. A plot held `FOR UPDATE` refuses that lock, so the tick waited
    on the plot while the collapse waited on the machine. Both steps run in
    the first stage of the tick, side by side. The plot is held against its
    other spenders, not against a key it never changes: `FOR NO KEY UPDATE`
    lets the tick's re-check through."""
    node, vein = await _pit(session)
    await _falling_house(session, constants, node)
    installation, machine, fuel, _ = await _rig_on(session, node, vein)
    #: The coal lies in the rain and is spared by the fall (D-244): the one row
    #: the two steps meet on is the machine, which goes down with the roof
    #: whatever its mark says.
    fuel.outdoors = True
    await session.commit()
    moment = installation.counted_at + timedelta(hours=4)
    tasks: dict[str, asyncio.Future] = {}
    #: The machine's row is the tick's from here, and the second write of the
    #: rig row is still ahead -- the moment the collapse is let in.
    worn, waited = _held_after(monkeypatch, wear, "spend", factory, tasks, "fall")

    tasks["tick"] = asyncio.ensure_future(_tick(factory, moment))
    tasks["fall"] = asyncio.ensure_future(_fall(factory, worn))
    outcome = await asyncio.gather(tasks["tick"], tasks["fall"], return_exceptions=True)

    assert not _failures(outcome), outcome
    assert waited == [True], "обрушение не встало за машиной, которую держит тик"
    mined, fallen = outcome
    assert mined > 0 and fallen == 1, "тик прошёл, дом упал"
    async with factory() as db:
        assert await db.get(Item, machine.id) is None, "машина ушла под крышу после тика"
        row = await db.get(Rig, installation.id)
        assert row is not None and row.counted_at == moment, "проход тика записан целиком"


@pytest.mark.parametrize("door", ["take", "place"])
async def test_a_machine_stood_or_taken_down_as_its_house_falls(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
    door: str,
) -> None:
    """Both doors spend the floor, so both take the node; and both take the
    thing. The collapse takes the node and then deletes the thing. A door that
    held the thing and then waited on the node crossed the collapse holding the
    node and waiting on the thing -- so the node comes first in the doors too,
    the collapse's own order."""
    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.shop.{stamp}", "Мастерская", area_m2=200)
    await _falling_house(session, constants, node)
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    #: Standing, to be taken down; lying on the floor, to be stood up (D-278).
    bench = await world.grant_item(
        session, yard, "workbench", quality=60, origin="тест", installed=door == "take"
    )
    await session.commit()
    tasks: dict[str, asyncio.Future] = {}
    #: Past the first lock the door takes, whichever it is, and before the
    #: last: the collapse is let in here.
    judged, waited = _held_after(monkeypatch, station, "may_build", factory, tasks, "fall")

    async def through_the_door() -> None:
        async with factory() as db, db.begin():
            own_body = await db.get(Body, body.id)
            own = await db.get(Item, bench.id)
            walk = station.take if door == "take" else station.place
            await walk(db, current_catalog(), own_body, own)

    tasks["door"] = asyncio.ensure_future(through_the_door())
    tasks["fall"] = asyncio.ensure_future(_fall(factory, judged))
    outcome = await asyncio.gather(tasks["door"], tasks["fall"], return_exceptions=True)

    assert not _failures(outcome), outcome
    assert waited == [True], "обрушение не встало за дверью"
    assert outcome[1] == 1, "дом упал"
    async with factory() as db:
        assert await db.get(Item, bench.id) is None, "верстак ушёл под крышу после двери"


async def test_a_rig_burnt_while_its_hopper_is_emptied_is_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emptying the hopper settles the rig through the pass and goes on with the
    machine after it. The fire burns it between the plan and the lock: a write
    to its row would throw, and the session must not go on answering for the
    machine out of memory either -- the carter would be handed the hopper of a
    machine that is ash. The row ends with the machine, and the carter hears
    the world's word for it (D-314). The tick takes every machine before it
    reads one (`rig._hold_the_world`), and the place door takes its machine
    before the pass (`test_races_rig.py`), so a fire comes to those after."""
    _, fields = await _surface(session, count=1)
    field = fields[0]
    vein = await world.create_vein(session, field, ORE, richness=60, remaining=100_000)
    installation, _, _, body = await _rig_on(session, field, vein)
    #: Ore from the passes before: what the carter must not be handed.
    installation.hopper = Decimal(5)
    pocket = (await world.body_container(session, body)).id
    planned, burnt = _after_the_plan(monkeypatch, (await world.node_container(session, field)).id)
    await session.commit()
    moment = installation.counted_at + timedelta(hours=4)

    async def erupt() -> None:
        await asyncio.wait_for(planned.wait(), timeout=30)
        try:
            await _erupt(factory, field)
        finally:
            burnt.set()

    outcome = await asyncio.gather(
        _empty(factory, body, installation, moment), erupt(), return_exceptions=True
    )

    assert outcome == ["rig-machine-gone", None], outcome
    async with factory() as db:
        handed = await db.scalar(
            select(Item.id).where(Item.container_id == pocket, Item.type_key == ORE)
        )
        assert handed is None, "руда сгоревшей машины не попала в руки"


async def test_a_carter_and_a_falling_house_take_the_machine_and_its_coal_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The carter's pass took the coal to burn it and the machine only to wear
    it; the fall takes what lay and stood under its roof in id order. With the
    machine's id below the coal's, the fall held the machine and waited on the
    coal while the pass held the coal and waited on the machine. A pass takes
    the two in one statement, in id order (`rig._held`)."""
    node, vein = await _pit(session)
    await _falling_house(session, constants, node)
    installation, machine, fuel, body = await _rig_on(session, node, vein)
    #: The coal lies under the roof, where a thing put down in a house lies
    #: (D-244), so the fall takes it along with the machine.
    assert fuel.outdoors is False
    machine.id, fuel.id = _low(), _high()
    installation.item_id = machine.id
    await session.commit()
    moment = installation.counted_at + timedelta(hours=4)
    tasks: dict[str, asyncio.Future] = {}
    #: The coal is burning and the machine is not worn yet: the fall is let in.
    burning, waited = _held_after(monkeypatch, stock, "consume", factory, tasks, "fall")

    tasks["empty"] = asyncio.ensure_future(_empty(factory, body, installation, moment))
    tasks["fall"] = asyncio.ensure_future(_fall(factory, burning))
    outcome = await asyncio.gather(tasks["empty"], tasks["fall"], return_exceptions=True)

    assert not _failures(outcome), outcome
    assert waited == [True], "обрушение не встало за строками, которые держит проход"
    taken, fallen = outcome
    assert isinstance(taken, float) and taken > 0 and fallen == 1, outcome
    async with factory() as db:
        assert await db.get(Item, machine.id) is None, "машина ушла под крышу после разгрузки"
        assert await db.get(Item, fuel.id) is None, "и уголь"


async def test_a_falling_house_holds_what_it_buries_under_a_carters_pass(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other way round: the fall goes first. It deleted what it buried one
    row at a time, in whatever order the heap gave them -- the coal first, say
    -- while a pass takes the machine and the coal by id, and the two crossed.
    The fall takes everything it buries by id before it deletes a thing
    (`estate.upkeep._bury`, then `world.destroy`), so the pass arriving now
    waits for it holding nothing the fall wants, and finds the machine gone."""
    node, vein = await _pit(session)
    await _falling_house(session, constants, node)
    installation, machine, fuel, body = await _rig_on(session, node, vein)
    machine.id, fuel.id = _low(), _high()
    installation.item_id = machine.id
    await session.commit()
    moment = installation.counted_at + timedelta(hours=4)

    planned, buried = _after_the_plan(monkeypatch, fuel.container_id)
    waited: list[bool] = []
    tasks: dict[str, asyncio.Future] = {}
    destroy = world.destroy

    async def held_then_destroyed(db, things):
        #: What goes down is the fall's by now, and nothing is deleted yet:
        #: the carter's pass is let on here.
        buried.set()
        waited.append(await _until_blocked_by(factory, db, unless=tasks["empty"]))
        return await destroy(db, things)

    monkeypatch.setattr(world, "destroy", held_then_destroyed)

    async def fall() -> int:
        await asyncio.wait_for(planned.wait(), timeout=30)
        async with factory() as db, db.begin():
            _, fallen = await estate.decay(db, current())
            return fallen

    tasks["empty"] = asyncio.ensure_future(_empty(factory, body, installation, moment))
    tasks["fall"] = asyncio.ensure_future(fall())
    outcome = await asyncio.gather(tasks["empty"], tasks["fall"], return_exceptions=True)

    assert not _failures(outcome), outcome
    assert waited == [True], "разгрузка не встала за строками, которые держит обрушение"
    assert outcome == ["rig-machine-gone", 1], outcome
    async with factory() as db:
        assert await db.get(Item, machine.id) is None, "машина ушла под крышу"


async def test_two_rigs_in_one_house_and_its_fall_take_the_machines_and_coal_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick holds every rig of the world at once, and each pass took its
    machine and its coal in one statement -- one rig at a time. Two rigs in one
    house share the coal: the first pass took the coal and its machine, the
    fall took the second machine (the lowest id) and waited on the coal, and
    the second pass came to its machine. The tick takes the machines and the
    fuel of every rig in one statement before the first pass
    (`rig._hold_the_world`)."""
    node, vein = await _pit(session)
    await _falling_house(session, constants, node)
    first, first_machine, _, _ = await _rig_on(session, node, vein)
    second, second_machine, _, _ = await _rig_on(session, node, vein)
    #: The first rig's pass goes first, and its machine is numbered last; the
    #: second's machine is numbered below the coal.
    first.id, second.id = _low(), _high()
    first_machine.id, second_machine.id = _high(), _low()
    first.item_id, second.item_id = first_machine.id, second_machine.id
    yard = await world.node_container(session, node)
    for stack in (
        await session.execute(
            select(Item).where(Item.container_id == yard.id, Item.type_key == "coal")
        )
    ).scalars():
        stack.id = _mid()
    await session.commit()
    moment = max(first.counted_at, second.counted_at) + timedelta(hours=4)
    tasks: dict[str, asyncio.Future] = {}
    #: The first pass has burnt; the second is ahead.
    burning, waited = _held_after(monkeypatch, stock, "consume", factory, tasks, "fall")

    tasks["tick"] = asyncio.ensure_future(_tick(factory, moment))
    tasks["fall"] = asyncio.ensure_future(_fall(factory, burning))
    outcome = await asyncio.gather(tasks["tick"], tasks["fall"], return_exceptions=True)

    assert not _failures(outcome), outcome
    assert waited == [True], "обрушение не встало за строками, которые держит тик"
    async with factory() as db:
        for one in (first, second):
            row = await db.get(Rig, one.id)
            assert row is not None and row.counted_at == moment, "обе буровые отработали проход"
        left = (await db.execute(select(Item).where(Item.type_key == "drilling_rig"))).all()
        assert left == [], "обе машины ушли под крышу после тика"


async def test_the_tick_and_the_fire_take_a_fields_veins_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fire takes a field's veins in id order before anything else; the
    tick took each rig's vein as it came to the rig, in the order of the rigs.
    Two rigs on two veins of one field, numbered the other way round, and the
    fire held the one vein the tick wanted next while waiting on the one it had
    already. The tick takes every rig's vein in one statement, by id, before
    the first pass (`rig._hold_the_world`)."""
    _, fields = await _surface(session, count=2)
    field = fields[0]
    first = await world.create_vein(session, field, ORE, richness=60, remaining=100_000)
    second = await world.create_vein(session, field, ORE, richness=60, remaining=100_000)
    #: The rig numbered first stands on the vein numbered last.
    first.id, second.id = _high(), _low()
    await session.flush()
    early, _, _, _ = await _rig_on(session, field, first)
    late, _, _, _ = await _rig_on(session, field, second)
    early.id, late.id = _low(), _high()
    await session.commit()
    moment = max(early.counted_at, late.counted_at) + timedelta(hours=4)
    tasks: dict[str, asyncio.Future] = {}
    #: One rig's pass is done with its vein still held, the other's is ahead.
    burning, waited = _held_after(monkeypatch, stock, "consume", factory, tasks, "fire")

    async def erupt() -> None:
        await asyncio.wait_for(burning.wait(), timeout=30)
        await _erupt(factory, field)

    tasks["tick"] = asyncio.ensure_future(_tick(factory, moment))
    tasks["fire"] = asyncio.ensure_future(erupt())
    outcome = await asyncio.gather(tasks["tick"], tasks["fire"], return_exceptions=True)

    assert not _failures(outcome), outcome
    assert waited == [True], "огонь не встал за жилой, которую держит тик"
    async with factory() as db:
        for one in (early, late):
            row = await db.get(Rig, one.id)
            assert row is not None and row.counted_at == moment, "обе буровые отработали проход"
        left = (await db.execute(select(Item).where(Item.type_key == "drilling_rig"))).all()
        assert left == [], "огонь сжёг обе машины после тика"


async def test_a_pass_burns_only_the_coal_it_holds(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pass plans its hours on a free count of the coal. Carried off between
    that count and the lock, the coal was not there to burn, and the pass
    raised the ore of every hour it had planned: `stock.consume` takes what
    there is and says nothing. The hours are capped again by the coal the pass
    holds (`rig.advance`), as the ore is by the vein under its lock."""
    constants = current()
    fuel_per_hour = constants[R.RIG_FUEL_PER_HOUR]
    output_per_hour = constants[R.RIG_OUTPUT_PER_HOUR]
    node, vein = await _pit(session)
    #: Coal for two hours of a four-hour pass, and three quarters of it carried off.
    installation, _, fuel, body = await _rig_on(session, node, vein, coal=2 * fuel_per_hour)
    carried = 1.5 * fuel_per_hour
    pocket = await world.body_container(session, body)
    await session.commit()
    moment = installation.counted_at + timedelta(hours=4)
    planned, carried_off = _after_the_plan(monkeypatch, fuel.container_id)

    async def settle() -> float:
        async with factory() as db, db.begin():
            #: The prologue of `rig.empty_hopper`: the rig's own row, then the pass.
            own = await db.get(Rig, installation.id)
            await db.refresh(own, with_for_update=True)
            return await rig.advance(db, current(), own, now=moment)

    async def carry() -> None:
        await asyncio.wait_for(planned.wait(), timeout=30)
        try:
            async with factory() as db, db.begin():
                own = await db.get(Item, fuel.id)
                target = await db.get(Container, pocket.id)
                await world.move_stack(db, own, target, carried)
        finally:
            carried_off.set()

    outcome = await asyncio.gather(settle(), carry(), return_exceptions=True)

    assert not _failures(outcome), outcome
    async with factory() as db:
        row = await db.get(Rig, installation.id)
        left = await db.scalar(
            select(Item.amount).where(
                Item.container_id == fuel.container_id, Item.type_key == "coal"
            )
        )
        burnt = 2 * fuel_per_hour - carried - (amount_float(left) if left else 0.0)
        paid_for = float(row.hopper) * fuel_per_hour / output_per_hour
        assert float(row.hopper) > 0, "проход намыл то, что оплатил"
        #: A thousandth of coal may be owed to the next pass (`fuel_remainder`).
        assert paid_for <= burnt + 0.002, (
            f"в бункере руда на {paid_for:.3f} угля, сожжено {burnt:.3f}"
        )
