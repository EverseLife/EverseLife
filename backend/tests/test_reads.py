# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Reads write nothing and wait for nothing.

The rule from the review of 2026-08-23 ("чтение не пишет"), pinned from both
sides: `look` and the forecasts leave no row behind -- no account, no yard, no
stall -- and they answer while a real action holds the body's lock, because a
read queued behind a write is the wrong way round. The races proper live in
`test_races*.py`; these tests share their origin, not their technique.

Here: the scene and the other views of what already is -- `look` and its
parts, the statement, the farm survey -- and the sweep of every `readonly`
command as a class (`READS`). The forecasts, which answer what *would* happen,
are `test_reads_forecast.py`'s; the guard and the forecaster's world both files
stand on are in `reads_kit.py`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reads_kit import _forecaster, _writes_forbidden
from src.constants import current
from src.engine import ledger, market, world
from src.models.ledger import AccountKind
from src.models.world import Layer


async def test_reads_do_not_write(session: AsyncSession) -> None:
    """`look`-class reads create nothing: no account for a fresh identity, no
    container for an untouched chest, no stall in a node never traded in."""
    from src.engine import storage

    identity = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    assert await ledger.find_account(session, AccountKind.IDENTITY, identity.id) is None
    node = await world.create_node(
        session, f"terra.quiet.{uuid.uuid4().hex[:6]}", "Тихо", area_m2=100
    )
    assert await market.stall(session, node, identity.id, create=False) is None
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    chest = await world.grant_item(session, pocket, "chest", quality=50, origin="тест")
    assert await storage.content(session, chest) == []
    assert await storage.inside(session, chest, create=False) is None
    assert await storage.is_empty(session, chest)


async def test_look_writes_nothing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A fresh identity in a fresh node: `look` creates no account, no
    container, no row of any kind (review 2026-08-23).

    The node is stripped of its yard first: `create_node` has made one with the
    node since that review, and a yard already there hides the leak this test
    is for -- the scene reached the yard through the **creating**
    `node_container`, so a look at a node from an old world wrote to it.
    """
    from sqlalchemy import delete, func

    from src.api.commands import look as api
    from src.models.inventory import Container, ContainerKind
    from src.models.ledger import LedgerAccount

    node = await world.create_node(
        session, f"terra.fresh.{uuid.uuid4().hex[:6]}", "Тихо", area_m2=100
    )
    identity = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    await world.print_body(session, identity, node)
    await session.execute(
        delete(Container).where(Container.kind == ContainerKind.NODE, Container.owner_id == node.id)
    )
    await session.commit()

    async def count(db: AsyncSession) -> tuple[int, int]:
        containers = await db.scalar(select(func.count()).select_from(Container))
        accounts = await db.scalar(select(func.count()).select_from(LedgerAccount))
        return containers, accounts

    before = await count(session)
    async with factory() as db, db.begin(), _writes_forbidden(db):
        await api._look({"identity_id": identity.id}, db, {"cmd": "look"})
    assert await count(session) == before


async def test_look_counts_the_polls_without_opening_an_account(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], catalog
) -> None:
    """The counter on the Net tab asks the census, and the census is a read.

    A city may set the property census (`vote_qualification`), and asking it
    used to walk to `ledger.account_for`, which **creates** the account it does
    not find. With the counter now on `look`, every read by a citizen of such
    a city wrote a row -- and the older test above could not see it: its
    identity has no citizenship, so the walk ended at the first query.
    """
    from sqlalchemy import func

    from src.api.commands import look as api
    from src.engine import city as town
    from src.engine import vote
    from src.models.city import Citizen
    from src.models.ledger import LedgerAccount

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.census.{stamp}", "Ценз", area_m2=1, layer=Layer.PLANET, parent=planet
    )
    core = await world.create_node(
        session, f"terra.census.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    city = await town.found(session, catalog, delegate, "Ценз")
    core.owner_city_id = city.id
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")
    city.charter = {**city.charter, vote.QUALIFICATION: vote.PROPERTY}
    city.charter_params = {vote.QUALIFICATION: 1}
    await session.flush()

    identity = await world.create_identity(session, f"Гражданин-{stamp}")
    await world.print_body(session, identity, core)
    session.add(Citizen(identity_id=identity.id, city_id=city.id))
    await session.flush()
    await vote.open_law(session, current(), city, identity, "tax_trade", "4")
    await session.commit()

    before = await session.scalar(select(func.count()).select_from(LedgerAccount))
    async with factory() as db, db.begin(), _writes_forbidden(db):
        seen = await api._look({"identity_id": identity.id}, db, {"cmd": "look"})
    #: No property, no voice -- and no account opened to find that out.
    assert seen["look"]["net_votes"] == 0
    assert await session.scalar(select(func.count()).select_from(LedgerAccount)) == before


async def test_look_at_a_city_plot_measures_nothing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], catalog
) -> None:
    """The plot screen shows the day's land tax, and the tax wants the distance
    to the city's printer (D-220). That distance is a cache, and filling it
    meant writing a row for every plot of the city -- from inside a command
    declared readonly. On a world nobody had measured yet, which is every world
    the moment it is seeded, `look` therefore wrote on its first call and the
    guard killed it; in production the guard only warns, so the write went
    through in silence.

    The cache is the tick's business now (`estate.measure_cities`), and the
    answer is the same either way: the number is worked out afresh while it is
    cold. Both halves are checked here -- the read leaves the columns empty,
    and says the same tax the measured city says.
    """
    from src.api.commands import look as api
    from src.engine import city as town
    from src.engine import estate, travel
    from src.models.world import Node, Surface

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.tax.{stamp}", "Мера", area_m2=1, layer=Layer.PLANET, parent=planet
    )
    core = await world.create_node(
        session, f"terra.tax.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    #: The printer is what distance is counted from: without one the city has
    #: no centre and never walks the graph at all.
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=60, origin="тест")
    plot = await world.create_node(
        session, f"terra.tax.{stamp}.lot", "Участок", area_m2=100, parent=delegate
    )
    await travel.connect(session, core, plot, base_seconds=30, surface=Surface.PAVED)
    city = await town.found(session, catalog, delegate, f"Мера-{stamp}")
    for node in (core, plot):
        node.owner_city_id = city.id
    await session.flush()

    identity = await world.create_identity(session, f"Житель-{stamp}")
    await world.print_body(session, identity, plot)
    await session.commit()

    assert plot.center_steps is None, "город ещё никто не мерил"

    async with factory() as db, db.begin(), _writes_forbidden(db):
        seen = (await api._look({"identity_id": identity.id}, db, {"cmd": "look"}))["look"]
    cold = seen["node"]["tax"]

    await session.refresh(plot)
    assert plot.center_steps is None, "чтение не пишет: мера осталась неснятой"

    #: And the tick takes the measuring on itself -- one walk for the city.
    assert await estate.measure_cities(session) == 1
    await session.commit()
    assert (await session.get(Node, plot.id)).center_steps == 1

    async with factory() as db, db.begin(), _writes_forbidden(db):
        seen = (await api._look({"identity_id": identity.id}, db, {"cmd": "look"}))["look"]
    assert seen["node"]["tax"] == cold, "измеренный город берёт тот же налог"


async def test_look_gives_the_city_no_channel(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], constants, catalog
) -> None:
    """A citizen's `look` counts the unread of the city's channel -- and does
    not open one where there is none.

    The channel used to exist "from the first time it is asked for", and the
    first ask is this very read: the Net tab's count walks the reader's
    channels, and the city's is among them by citizenship. One INSERT behind
    the hottest read in the game -- and it fired so rarely, once per city on
    its first citizen's first look, that nothing caught it. The city is
    founded with its channel now; the one here is from before that, founded
    and left voiceless.
    """
    from sqlalchemy import delete, func

    from src.api.commands.look import _look
    from src.engine import city as town
    from src.engine import net
    from src.models.net import NetChannel

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.mute.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET, parent=planet
    )
    core = await world.create_node(
        session, f"terra.mute.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    city = await town.found(session, catalog, delegate, "Столица")
    core.owner_city_id = city.id
    citizen = await world.create_identity(session, f"Гражданин-{stamp}")
    await world.print_body(session, citizen, core)
    await town._enroll(session, city, citizen.id, why="test")
    #: That the read reaches the city's channel at all, before it is taken
    #: away. Without this the test would pass just as green in a world where
    #: the citizen belongs to no city: `channels()` would never enter the
    #: official branch, and nothing would be left of what it was written for.
    views = await net.channels(session, constants, citizen.id)
    assert [view.official for view in views] == [True], views
    #: A city as an old world left it: standing, with no channel of its own.
    await session.execute(delete(NetChannel).where(NetChannel.city_id == city.id))
    await session.commit()

    async def channels() -> int:
        return await session.scalar(
            select(func.count()).select_from(NetChannel).where(NetChannel.city_id == city.id)
        )

    assert await channels() == 0, "канал снесён -- это город старого мира"
    async with factory() as db, db.begin(), _writes_forbidden(db):
        seen = await _look({"identity_id": citizen.id}, db, {"cmd": "look"})
    assert seen["look"]["net_unread"] == 0, seen
    assert await channels() == 0, "взгляд завёл городу канал -- чтение не пишет"


async def test_a_look_at_the_face_opens_it_no_container(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], constants, catalog
) -> None:
    """The sight of a working counts the haul and opens no container for it.

    What is mined lies apart, in a container of the session's own, and
    `session_container` makes one where it finds none -- so the sight went to
    the creating door for a number it could add up without it. Every `look` of
    a miner ran through there, and `mining.sight` behind it. Nothing ever
    fired, because `face.start` opens the container in the same breath as the
    session; a face left by an older world, or one whose container is gone, is
    all it takes.
    """
    from sqlalchemy import delete, func

    from src.api.commands.look import _look
    from src.engine import mining
    from src.models.inventory import Container, ContainerKind
    from src.models.mining import MiningSession

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.face.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, "iron_ore", richness=60, remaining=100_000)
    identity = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    #: The vault requires a pickaxe at the face (D-215).
    await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="тест")
    face = await mining.start(session, constants, body, vein, catalog=catalog)
    #: A working as an older world left it: open, with no container of its own.
    await session.execute(
        delete(Container).where(
            Container.kind == ContainerKind.MINING_SESSION, Container.owner_id == face.id
        )
    )
    await session.commit()

    async def hauls() -> int:
        return await session.scalar(
            select(func.count())
            .select_from(Container)
            .where(Container.kind == ContainerKind.MINING_SESSION, Container.owner_id == face.id)
        )

    assert await hauls() == 0, "контейнер снесён -- это забой старого мира"

    async with factory() as db, db.begin(), _writes_forbidden(db):
        seen = await _look({"identity_id": identity.id}, db, {"cmd": "look"})
        assert seen["look"]["mining"]["mined"] == 0, seen["look"]["mining"]
        #: And the same through the engine's own door, not only through `look`.
        sight = await mining.sight(db, current(), await db.get(MiningSession, face.id))
        assert sight.mined == 0, sight
    assert await hauls() == 0, "взгляд завёл забою контейнер -- чтение не пишет"


async def test_the_statement_and_its_rows_write_nothing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A page of the statement and a row of it opened (D-190, D-292) are
    reads like `look`: no account for whoever has none, no row of any kind
    for whoever has a history."""
    from src.engine import finance
    from src.models.ledger import PostingReason
    from src.units import money

    nobody = await world.create_identity(session, f"Никто-{uuid.uuid4().hex[:6]}")
    payer = await world.create_identity(session, f"Хём-{uuid.uuid4().hex[:6]}")
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    wallet = await ledger.account_for(session, AccountKind.IDENTITY, payer.id)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=wallet.id, amount=money(5)
    )
    await session.commit()

    async with factory() as db, db.begin(), _writes_forbidden(db):
        assert await finance.statement(db, nobody.id) == ([], False)
        rows, more = await finance.statement(db, payer.id)
        assert len(rows) == 1 and not more
        opened = await finance.posting(db, payer.id, rows[0]["id"])
        assert [side["side"] for side in opened["sides"]] == ["genesis", None]
    assert await ledger.find_account(session, AccountKind.IDENTITY, nobody.id) is None


async def test_farm_survey_writes_nothing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], constants, catalog
) -> None:
    """The farm summary is a read even over a broken bed.

    A sown plot whose cultivar row is missing -- a dangling `variety_id` --
    used to fall back to `breed.landrace`, and that is get-or-create: the
    survey inserted a Variety row. Now the fallback is a transient base line,
    and the whole summary flushes nothing.
    """
    from decimal import Decimal

    from src.engine import breed, farm
    from src.models.farm import PlotState
    from src.models.plant import Variety

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.readfield.{stamp}",
        "Поле",
        area_m2=100,
        properties={"water": "none", "fertility": 60},
    )
    identity = await world.create_identity(session, f"Фермер-{stamp}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    cultivar = await breed.landrace(session, catalog, "spelt")
    pocket = await world.body_container(session, body)
    seeds = await breed.seed_lot(session, catalog, pocket.id, cultivar, 500, 100)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    plot.state = PlotState.PLOWED
    plot.fertility = Decimal("60")
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, seeds)
    #: The bed as a broken world leaves it: sown, but the cultivar row is gone.
    plot.variety_id = uuid.uuid4()
    await session.commit()

    from sqlalchemy import func

    async def cultivars() -> int:
        return await session.scalar(select(func.count()).select_from(Variety))

    before = await cultivars()
    async with factory() as db, db.begin(), _writes_forbidden(db):
        (line,) = await farm.survey(db, constants, catalog, identity.id)
    assert line["culture"] == "spelt"
    assert line["variety"] == {"key": "spelt"}
    assert await cultivars() == before, "обзор завёл сорт -- чтение не пишет"


#: What each read is asked, so that it gets far enough in to write if it is
#: going to. A `readonly=True` command **missing from this map fails the sweep
#: below**: nine leaks were found one at a time because nothing made the class
#: answer as a class, and a class stays closed only if a new member of it
#: cannot join quietly.
READS: dict[str, dict[str, object]] = {
    "agro.view": {},
    "auto.view": {},
    "build.demolish_estimate": {},
    "build.estimate": {"area": 20, "floors": 1},
    "build.repair_estimate": {},
    "craft.most": {"output": "nails"},
    "craft.plan": {"output": "nails", "units": 3},
    "explore.peek": {"lat": 0, "lon": 0},
    "deeds": {},
    "knowledge": {},
    "library.care": {"culture": "spelt"},
    "line.view": {},
    "look": {},
    "orders": {},
    "people.here": {},
    "rig.status": {},
    "road.here": {},
    "ship.course": {"planet": "aurora"},
    "ship.ports": {},
    "ship.view": {},
    "shelf": {},
}

#: The ones this world cannot answer: a landlubber has no hull to plumb, no
#: hull to fly and no library to read a care text in (D-296). A refusal is not
#: a write, and it is the path most of the nine leaked on -- "no such machine
#: here" left a yard behind -- so they are swept too, they are simply not
#: proof that the answering path was walked.
#: The peek has nothing to tell a body that stands on no planet's ground
#: (`explore-not-from-here`), and the forecaster stands in the old world.
REFUSING = {"explore.peek", "library.care", "line.view", "ship.course"}

#: And the ones a place without a yard has nothing to answer either: no yard,
#: no machine standing in it, no batch to price. Pinned rather than left to
#: "at least `look` answered": a read that quietly began refusing in this
#: world would otherwise walk through the sweep untouched.
REFUSING_WITHOUT_A_YARD = REFUSING | {"craft.plan", "craft.most"}


async def test_every_readonly_command_writes_nothing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:
    """The flag is the check (`db/readonly.py`), and here it is aimed at the
    whole class at once.

    Every command that declares `readonly=True` is run against a furnished
    world, each in a transaction of its own and each under the guard the socket
    puts it under. A write of any kind -- a flush of the unit of work or a
    statement handed to the session -- stops the command with `ReadWrote` and
    fails the sweep naming what was written.

    Twice over, and the second pass is the one that finds things: the same
    place stripped of its yard, i.e. a node of the old world, born before
    `create_node` made a yard with the node. That is where this family lives --
    the read asks `node_container` what stands here, and a node without a yard
    gets one from a glance. `auto.view` was doing exactly that on the day this
    sweep was written (the tenth leak of the nine).

    What it does **not** prove: that every branch of every read was walked.
    Three of them have nothing here to answer about, and the ones that answer
    about an empty place answer from their short branch. The runtime guard is
    what covers the rest -- this holds the floor.
    """
    from sqlalchemy import delete, func  # noqa: PLC0415

    import src.api.session  # noqa: F401, PLC0415 -- registers the commands
    from src.api.registry import COMMANDS  # noqa: PLC0415
    from src.models.identity import Body  # noqa: PLC0415
    from src.models.inventory import Container, ContainerKind, Item  # noqa: PLC0415
    from src.settings import settings  # noqa: PLC0415

    #: Whatever this copy's environment says, the sweep is held to `raise`.
    monkeypatch.setattr(settings(), "readonly_guard", "raise")
    declared = sorted(name for name, one in COMMANDS.items() if one.readonly)
    assert declared == sorted(READS), "a read of the socket is not swept here"

    who = await _forecaster(session, "sweep")
    assert await _swept(factory, who, declared) == sorted(set(READS) - REFUSING)

    body = (await session.execute(select(Body).where(Body.identity_id == who))).scalars().one()
    #: The forge goes with the yard: a container is not deleted from under the
    #: things standing in it, and a node of the old world has neither.
    yard = (
        await session.execute(
            select(Container).where(
                Container.kind == ContainerKind.NODE, Container.owner_id == body.node_id
            )
        )
    ).scalar_one()
    #: The harness goes first, or the wagon cannot be deleted from under it:
    #: a body pulling a wagon that no longer exists is not a world of the old
    #: kind, it is a broken one.
    from src.models.travel import Harness  # noqa: PLC0415

    await session.execute(delete(Harness).where(Harness.body_id == body.id))
    await session.execute(delete(Item).where(Item.container_id == yard.id))
    await session.execute(delete(Container).where(Container.id == yard.id))
    await session.commit()

    async def yards() -> int:
        return await session.scalar(
            select(func.count())
            .select_from(Container)
            .where(Container.kind == ContainerKind.NODE, Container.owner_id == body.node_id)
        )

    assert await yards() == 0, "двор снесён -- это узел старого мира"
    #: Fewer of them answer without a yard -- a workshop with no machine in it
    #: refuses -- and the refusal is the very path the yard used to be made on.
    assert await _swept(factory, who, declared) == sorted(set(READS) - REFUSING_WITHOUT_A_YARD)
    assert await yards() == 0, "чтение завело двор"


async def _swept(
    factory: async_sessionmaker[AsyncSession], who: uuid.UUID, names: list[str]
) -> list[str]:
    """Every named read, each on a transaction of its own, and which of them
    answered. A refusal is not a write: it is swept like the rest, it simply
    does not prove the answering path was walked."""
    from src.api.registry import COMMANDS  # noqa: PLC0415
    from src.engine.errors import Refusal  # noqa: PLC0415

    answered: list[str] = []
    for name in names:
        #: The refusal is caught outside the transaction and not inside it: a
        #: read that refuses must take its transaction down with it, exactly
        #: as the socket does (`api/session._dispatch`). Caught inside, the
        #: block would end normally and commit whatever the refusal left.
        try:
            async with factory() as db, db.begin():
                await COMMANDS[name].run({"identity_id": who}, db, {"cmd": name, **READS[name]})
        except Refusal:
            continue
        answered.append(name)
    return answered
