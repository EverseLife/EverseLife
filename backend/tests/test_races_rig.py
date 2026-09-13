# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a standing machine and the plot under it.

One of the race files (see `test_races.py` for the family's method): here the
contested rows are a machine, the fuel beside it and the node it stands on,
and the question is not a lost write but the **order** they are taken in. The
rig tick holds every rig row of the world in one transaction and writes the
machine, its coal and its vein under them; a house falling takes the plot and
buries what stood indoors; the doors that stand a machine up and take it down
take the plot and the thing; the fire of Pyroxis takes the veins and then
everything lying in a field. Two of them taking the same pair the other way
round is a deadlock, and the database kills one: a whole world's rig pass, a
day's collapse, an eruption replayed by the worker's retry. And a pass planned
on a free read of a machine the fire then burns must not write to it.

Each race is built to meet on the crossing every time, not when a pass happens
to outrun a pause: one side stops holding the row the other needs and goes on
only once the other is seen waiting on it (`automat_kit._until_blocked_by`).
The taking-down door against the tick over a rig's hopper is
`test_races_mining.py`'s.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from pyroxis_kit import _surface
from src.constants import current, current_catalog
from src.engine import estate, plates, rig, station, stock, wear, world
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.rig import Rig
from src.models.world import Node, Vein

ORE = "iron_ore"


def _low() -> uuid.UUID:
    """An id below every random one: the first row an `ORDER BY id` lock takes."""
    return uuid.UUID(bytes=b"\x00" + os.urandom(15))


def _high() -> uuid.UUID:
    """An id above every random one: the last row an `ORDER BY id` lock takes."""
    return uuid.UUID(bytes=b"\xff" + os.urandom(15))


def _failures(outcome: list) -> list[BaseException]:
    return [one for one in outcome if isinstance(one, BaseException)]


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


def _eruption(*fields: Node, at: datetime) -> Job:
    """The eruption job as `plates.warned` queues it, shaking these fields."""
    return Job(
        kind=JobKind.PLATES_ERUPT.value,
        run_at=at,
        payload={"nodes": [str(field.id) for field in fields]},
    )


def _fire_after_the_plan(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    field: Node,
    yard_id: uuid.UUID,
) -> Callable[[], Awaitable[None]]:
    """The fire over `field`, let in while a pass of its rig is planned and not
    yet locked, and run to its commit before the pass goes on.

    The pass has judged the machine standing and counted the coal, both off
    free reads, and written nothing. Returned to be gathered with the pass.
    """
    planned = asyncio.Event()
    burnt = asyncio.Event()
    count = rig._coal_available

    async def counted_and_held(db, container_id):
        coal = await count(db, container_id)
        if container_id == yard_id and not planned.is_set():
            planned.set()
            await asyncio.wait_for(burnt.wait(), timeout=30)
        return coal

    monkeypatch.setattr(rig, "_coal_available", counted_and_held)

    async def erupt() -> None:
        await planned.wait()
        try:
            async with factory() as db, db.begin():
                place = await db.get(Node, field.id)
                await plates.erupted(db, _eruption(place, at=datetime.now(UTC)))
        finally:
            burnt.set()

    return erupt


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
    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=200)
    house = Building(node_id=node.id, area_m2=40)
    session.add(house)
    await session.flush()
    #: One day short of nothing: the day's step brings it down.
    house.condition = Decimal(str(estate.decay_per_day(constants, house.kind)))
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    installation, machine, fuel, _ = await _rig_on(session, node, vein)
    #: The coal lies in the rain and is spared by the fall (D-244): the one row
    #: the two steps meet on is the machine, which goes down with the roof
    #: whatever its mark says.
    fuel.outdoors = True
    await session.commit()
    moment = installation.counted_at + timedelta(hours=4)

    worn = asyncio.Event()
    waited: list[bool] = []
    tasks: dict[str, asyncio.Future] = {}
    spend = wear.spend

    async def worn_and_held(db, *args, **kwargs):
        #: The machine's row is the tick's from here, and the second write of
        #: the rig row is still ahead -- the moment the collapse is let in.
        finished = await spend(db, *args, **kwargs)
        if not worn.is_set():
            worn.set()
            waited.append(await _until_blocked_by(factory, db, unless=tasks["fall"]))
        return finished

    monkeypatch.setattr(wear, "spend", worn_and_held)

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await rig.tick_rigs(db, current(), now=moment)

    async def fall() -> int:
        await worn.wait()
        async with factory() as db, db.begin():
            _, fallen = await estate.decay(db, current())
            return fallen

    tasks["tick"] = asyncio.ensure_future(tick())
    tasks["fall"] = asyncio.ensure_future(fall())
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
    house = Building(node_id=node.id, area_m2=40)
    session.add(house)
    await session.flush()
    house.condition = Decimal(str(estate.decay_per_day(constants, house.kind)))
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    #: Standing, to be taken down; lying on the floor, to be stood up (D-278).
    bench = await world.grant_item(
        session, yard, "workbench", quality=60, origin="тест", installed=door == "take"
    )
    await session.commit()

    judged = asyncio.Event()
    waited: list[bool] = []
    tasks: dict[str, asyncio.Future] = {}
    judge = station.may_build

    async def judged_and_held(db, *args, **kwargs):
        #: Past the first lock the door takes, whichever it is, and before the
        #: last: the collapse is let in here.
        verdict = await judge(db, *args, **kwargs)
        if not judged.is_set():
            judged.set()
            waited.append(await _until_blocked_by(factory, db, unless=tasks["fall"]))
        return verdict

    monkeypatch.setattr(station, "may_build", judged_and_held)

    async def through_the_door() -> None:
        async with factory() as db, db.begin():
            own_body = await db.get(Body, body.id)
            own = await db.get(Item, bench.id)
            walk = station.take if door == "take" else station.place
            await walk(db, current_catalog(), own_body, own)

    async def fall() -> int:
        await judged.wait()
        async with factory() as db, db.begin():
            _, fallen = await estate.decay(db, current())
            return fallen

    tasks["door"] = asyncio.ensure_future(through_the_door())
    tasks["fall"] = asyncio.ensure_future(fall())
    outcome = await asyncio.gather(tasks["door"], tasks["fall"], return_exceptions=True)

    assert not _failures(outcome), outcome
    assert waited == [True], "обрушение не встало за дверью"
    assert outcome[1] == 1, "дом упал"
    async with factory() as db:
        assert await db.get(Item, bench.id) is None, "верстак ушёл под крышу после двери"


async def test_a_rig_burnt_mid_pass_does_not_stop_the_rigs_step(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tick reads the machine free, plans the pass, and writes the machine
    at its end. The fire burns it in between and commits: the write then finds
    no row, and the ORM throws -- out of `tick_rigs`, where every rig of the
    world shares one transaction, so a machine burnt on one field took the pass
    of every other rig with it. The machine is taken under the lock before the
    pass writes anything, and a machine gone by then ends its row the way a
    worn-out one does. A rig in the next field is the control: its pass lands."""
    _, fields = await _surface(session, count=2)
    burning, spared = fields
    rigs: dict[str, Rig] = {}
    machines: dict[str, Item] = {}
    for name, field in (("burning", burning), ("spared", spared)):
        vein = await world.create_vein(session, field, ORE, richness=60, remaining=100_000)
        rigs[name], machines[name], _, _ = await _rig_on(session, field, vein)
    erupt = _fire_after_the_plan(
        monkeypatch, factory, burning, (await world.node_container(session, burning)).id
    )
    await session.commit()
    moment = max(one.counted_at for one in rigs.values()) + timedelta(hours=4)

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await rig.tick_rigs(db, current(), now=moment)

    outcome = await asyncio.gather(tick(), erupt(), return_exceptions=True)

    assert not _failures(outcome), outcome
    async with factory() as db:
        assert await db.get(Item, machines["burning"].id) is None, "огонь сжёг машину"
        assert await db.get(Rig, rigs["burning"].id) is None, "строка сгоревшей буровой ушла с ней"
        control = await db.get(Rig, rigs["spared"].id)
        assert control is not None and control.counted_at == moment, "соседняя отработала проход"
        assert float(control.hopper) > 0, "соседняя намыла руду"


@pytest.mark.parametrize("hand", ["empty", "place"])
async def test_a_rig_burnt_while_it_is_settled_by_hand_is_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    hand: str,
) -> None:
    """Emptying the hopper and standing the rig up again settle it through the
    same pass, and go on with the machine after it. Burnt between the plan and
    the lock, the machine ends its row inside that pass -- and the session must
    not go on answering for it out of memory: the carter would be handed the
    hopper of a machine that is ash, the owner told that cinders stand on the
    vein. Both hear the world's word for it instead (D-011)."""
    _, fields = await _surface(session, count=1)
    field = fields[0]
    vein = await world.create_vein(session, field, ORE, richness=60, remaining=100_000)
    installation, machine, _, body = await _rig_on(session, field, vein)
    #: Ore from the passes before: what the carter must not be handed.
    installation.hopper = Decimal(5)
    pocket = (await world.body_container(session, body)).id
    erupt = _fire_after_the_plan(
        monkeypatch, factory, field, (await world.node_container(session, field)).id
    )
    await session.commit()
    moment = installation.counted_at + timedelta(hours=4)

    async def settle() -> str:
        #: Refused outside the transaction, so it rolls back as a command does.
        try:
            async with factory() as db, db.begin():
                own_body = await db.get(Body, body.id)
                #: The machine in the session's memory, as it is for whoever
                #: looked at it earlier in the command: the identity map is
                #: weak, and a copy nobody holds would vanish on its own.
                own_machine = await db.get(Item, machine.id)
                if hand == "empty":
                    own = await db.get(Rig, installation.id)
                    await rig.empty_hopper(db, current(), own_body, own, now=moment)
                else:
                    own_vein = await db.get(Vein, vein.id)
                    await rig.place(db, own_body, own_machine, own_vein, now=moment)
        except rig.NoRig as refusal:
            return refusal.key
        return "done"

    outcome = await asyncio.gather(settle(), erupt(), return_exceptions=True)

    assert outcome == ["rig-machine-gone", None], outcome
    async with factory() as db:
        handed = await db.scalar(
            select(Item.id).where(Item.container_id == pocket, Item.type_key == ORE)
        )
        assert handed is None, "руда сгоревшей машины не попала в руки"


async def test_the_fire_and_the_tick_take_a_rigs_machine_and_its_coal_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fire takes everything lying in a field in id order; the tick took
    the coal first (to burn it) and the machine after (to wear it). With the
    machine's id below the coal's, the fire held the machine and waited on the
    coal while the tick held the coal and waited on the machine. The tick now
    takes the two in one statement, in the fire's order.

    They meet only when the vein does not keep them apart: the fire takes the
    field's veins before its things, and so does a drilling pass. A vein moved
    out by an earlier eruption (D-197) is drilled on from the old yard today
    -- the field burning holds no vein of the rig, and the two meet on the
    things alone."""
    _, fields = await _surface(session, count=2)
    field, next_door = fields
    vein = await world.create_vein(session, field, ORE, richness=60, remaining=100_000)
    installation, machine, fuel, _ = await _rig_on(session, field, vein)
    machine.id, fuel.id = _low(), _high()
    installation.item_id = machine.id
    vein.node_id = next_door.id
    await session.commit()
    moment = installation.counted_at + timedelta(hours=4)

    burning = asyncio.Event()
    waited: list[bool] = []
    tasks: dict[str, asyncio.Future] = {}
    consume = stock.consume

    async def burnt_and_held(db, *args, **kwargs):
        #: The coal is burning and the machine is not worn yet: the fire is let in.
        taken = await consume(db, *args, **kwargs)
        if not burning.is_set():
            burning.set()
            waited.append(await _until_blocked_by(factory, db, unless=tasks["fire"]))
        return taken

    monkeypatch.setattr(stock, "consume", burnt_and_held)

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await rig.tick_rigs(db, current(), now=moment)

    async def erupt() -> None:
        await burning.wait()
        async with factory() as db, db.begin():
            place = await db.get(Node, field.id)
            await plates.erupted(db, _eruption(place, at=datetime.now(UTC)))

    tasks["tick"] = asyncio.ensure_future(tick())
    tasks["fire"] = asyncio.ensure_future(erupt())
    outcome = await asyncio.gather(tasks["tick"], tasks["fire"], return_exceptions=True)

    assert not _failures(outcome), outcome
    assert waited == [True], "огонь не встал за строками, которые держит тик"
    assert outcome[0] > 0, "тик намыл руду"
    async with factory() as db:
        assert await db.get(Item, machine.id) is None, "огонь сжёг машину после тика"
        assert await db.get(Item, fuel.id) is None, "и уголь"
        row = await db.get(Rig, installation.id)
        assert row is not None and row.counted_at == moment, "проход тика записан целиком"


async def test_the_tick_and_the_fire_take_a_fields_veins_in_one_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fire takes a field's veins in id order before anything else; the
    tick took each rig's vein as it came to the rig, in the order of the rigs.
    Two rigs on two veins of one field, numbered the other way round, and the
    fire held the one vein the tick wanted next while waiting on the one it had
    already. The tick walks the rigs in the order of their veins."""
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

    burning = asyncio.Event()
    waited: list[bool] = []
    tasks: dict[str, asyncio.Future] = {}
    consume = stock.consume

    async def burnt_and_held(db, *args, **kwargs):
        #: One rig's pass is done with its vein still held, the other's is ahead.
        taken = await consume(db, *args, **kwargs)
        if not burning.is_set():
            burning.set()
            waited.append(await _until_blocked_by(factory, db, unless=tasks["fire"]))
        return taken

    monkeypatch.setattr(stock, "consume", burnt_and_held)

    async def tick() -> float:
        async with factory() as db, db.begin():
            return await rig.tick_rigs(db, current(), now=moment)

    async def erupt() -> None:
        await burning.wait()
        async with factory() as db, db.begin():
            place = await db.get(Node, field.id)
            await plates.erupted(db, _eruption(place, at=datetime.now(UTC)))

    tasks["tick"] = asyncio.ensure_future(tick())
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
