# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a meter and the land it stands on.

One of the race files (see `test_races.py` for the family's method), split
from `test_races_meter.py`: the meter run holds every meter of the world, and
the city taking a node back -- a holder giving a plot up (`cede`), the city
taking back a location handed out as a plot (`reclaim`, `reclaim_all`) --
settles the node's meter as it goes (D-149, D-282). Who comes second must read
the meter under its lock, and neither may hold what the other reaches for.

The handshake is `automat_kit._until_blocked_by`: the side holding the
contended rows lets go only once the other side has provably walked into them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _pool_left, _until_blocked_by
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import energy, ledger, utility, world
from src.engine.city import land as city_land
from src.models.city import City, UtilityMeter
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.ledger import AccountKind
from src.models.world import Node
from utility_kit import _grids, _held_by, _meter, _open, _resident


def _nobody_billed_for_civic(state) -> None:
    holder, debt, cut_off = state
    if holder is None:
        #: A node the city holds has nobody to bill (D-149): a debt left on it
        #: is never paid and its cut-off never lifted.
        assert debt == 0 and not cut_off, "a debt on a node nobody can be billed for"


@pytest.mark.parametrize("how", ["cede", "reclaim", "centre"])
async def test_land_handed_back_during_the_meter_run_is_not_left_in_debt(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    """Handing a node back to the city settles its meter (`city/land.py`), and
    the meter run adds its bill to the same meter: whoever comes second must
    read it under its lock.

    A holder without a coin, whose house the run is about to bill into debt.
    The house goes back to the city while the run is already drawing the pools
    -- given up by the holder (`cede`), or taken back as a location that was
    never a plot (`reclaim`), the city's own node among them (`centre`). When
    the hand-over read the meter unlocked, it found no debt, committed there
    and then, and the run -- billing the holder it had read -- wrote the debt
    onto a node the city now held. Now the hand-over waits for the run's
    meter: after it, `cede` refuses the debtor, and `reclaim` clears what the
    run wrote.

    And it waits holding nothing the run reaches for. A meter row updated twice
    in one transaction takes its node `FOR KEY SHARE`, and so does the city's
    pool, brought up to now and then drawn: a hand-over holding the node while
    it waited for the meter deadlocked with the run.
    """
    #: A minute on from the pool's last count, so the run brings it up to now
    #: and writes it twice.
    moment = datetime.now(UTC) + timedelta(minutes=1)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((centre, home),) = await _grids(session, constants, catalog, 1)
    owner, body = await _resident(session, home, "Хозяин")
    if how == "centre":
        centre.owner_city_id = home.owner_city_id
        home.owner_city_id = None
        home = centre
    home.owner_identity_id = owner.id
    if how != "cede":
        #: Not a plot: the city's own location, handed out by mistake (D-282).
        home.properties = {}
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    ids = (body.id, home.id, home.owner_city_id, meter.id)
    await session.commit()
    body_id, home_id, city_id, meter_id = ids

    async def hand_back():
        async with factory() as db, db.begin():
            house = await db.get(Node, home_id)
            assert house is not None
            if how == "cede":
                return await town.cede(db, await db.get(Body, body_id), house)
            city = await db.get(City, city_id)
            assert city is not None
            return await town.reclaim(db, house, city)

    waited: list[bool] = []
    handovers: list[asyncio.Future] = []
    runs: list[AsyncSession] = []
    produced = energy.produce

    async def handing_back_midway(db, *args, **kwargs):
        result = await produced(db, *args, **kwargs)
        if runs and db is runs[0] and not handovers:
            handovers.append(asyncio.ensure_future(hand_back()))
            waited.append(await _until_blocked_by(factory, db, unless=handovers[0]))
        return result

    monkeypatch.setattr(energy, "produce", handing_back_midway)

    async with factory() as db, db.begin():
        runs.append(db)
        assert await utility.run_meters(db, constants, now=moment) == 1
    (handed,) = await asyncio.gather(*handovers, return_exceptions=True)

    state = await _held_by(factory, home_id, meter_id)
    _nobody_billed_for_civic(state)
    assert waited == [True], "the hand-over waited for the run's meter"
    if how == "cede":
        assert isinstance(handed, town.CityError) and handed.key == "city-land-debt"
        assert state[0] is not None and state[1] > 0, "the debtor keeps the plot and the debt"
    else:
        assert handed is True
        assert state[0] is None


async def test_a_debt_the_run_writes_during_a_cede_is_not_written_off(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cede` refuses a debtor (D-149), so it must read the debt under the
    meter's lock and keep it to the hand-over.

    A holder without a coin gives the plot up, and the run comes to bill the
    house between the refusal's check and the hand-over -- which clears
    whatever debt a node brings back to the city. When the check read the
    meter unlocked, it found none, the run wrote the holder's debt and
    committed, and the hand-over wrote it off: the holder walked away from a
    bill nobody will ever pay. Now the run waits for the hand-over, and bills
    the city's plot in energy after it.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((_, home),) = await _grids(session, constants, catalog, 1)
    owner, body = await _resident(session, home, "Хозяин")
    home.owner_identity_id = owner.id
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    ids = (body.id, home.id, meter.id)
    await session.commit()
    body_id, home_id, meter_id = ids

    async def run() -> int:
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    waited: list[bool] = []
    runs: list[asyncio.Future[int]] = []
    handed = city_land._into_the_citys_hands

    async def billed_first(db, *args, **kwargs):
        if not runs:
            runs.append(asyncio.ensure_future(run()))
            waited.append(await _until_blocked_by(factory, db, unless=runs[0]))
        return await handed(db, *args, **kwargs)

    monkeypatch.setattr(city_land, "_into_the_citys_hands", billed_first)

    async with factory() as db, db.begin():
        house = await db.get(Node, home_id)
        assert house is not None
        await town.cede(db, await db.get(Body, body_id), house)
    (listed,) = await asyncio.gather(*runs)

    async with factory() as db:
        written = (
            (
                await db.execute(
                    select(Event).where(
                        Event.kind == EventKind.UTILITY_CUT_OFF, Event.node_id == home_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert not written, "the holder's debt was written off by the hand-over"
    assert waited == [True], "the run waited for the cede's meter"
    assert listed == 1
    holder, debt, cut_off = await _held_by(factory, home_id, meter_id)
    assert holder is None and debt == 0 and not cut_off
    assert (await _meter(factory, meter_id)).counted_at == moment


async def test_locations_taken_back_together_take_their_meters_in_the_runs_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deploy's catch-up takes back every city location handed out as a
    plot in one transaction (`reclaim_all`), and a meter run takes every meter
    in id order: the catch-up must take the meters it needs in that order too,
    all of them before the first location.

    Two locations whose meters sort the other way round from the order the
    city lists its land. The catch-up has taken the first one back -- holding
    its meter -- when the run starts. When each location took its own meter as
    it came, the run took the second one's meter and waited for the first's,
    and the catch-up then reached for the second's. Now the catch-up holds both
    before it starts; the run waits, and bills the city after it.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((centre, home),) = await _grids(session, constants, catalog, 1)
    owner, _ = await _resident(session, home, "Захвативший")
    market = await world.create_node(
        session, f"{home.key}.market", "Рынок", area_m2=100, parent=centre
    )
    market.owner_city_id = home.owner_city_id
    for location in (home, market):
        location.owner_identity_id = owner.id
        location.properties = {}
    await session.flush()
    city = await session.get(City, home.owner_city_id)
    assert city is not None
    listed = [one for one in await town.territory(session, city) if one in (home, market)]
    #: The first location the territory lists gets the meter that sorts last.
    meters = await _open(
        session, constants, list(reversed(listed)), since=moment - timedelta(hours=hours)
    )
    ids = ([one.id for one in listed], [meter.id for meter in meters])
    await session.commit()
    node_ids, meter_ids = ids

    async def run() -> int:
        async with factory() as db, db.begin():
            return await utility.run_meters(db, constants, now=moment)

    waited: list[bool] = []
    runs: list[asyncio.Future[int]] = []
    taken_back = city_land.reclaim

    async def run_after_the_first(db, *args, **kwargs):
        result = await taken_back(db, *args, **kwargs)
        if result and not runs:
            runs.append(asyncio.ensure_future(run()))
            waited.append(await _until_blocked_by(factory, db, unless=runs[0]))
        return result

    monkeypatch.setattr(city_land, "reclaim", run_after_the_first)

    async with factory() as db, db.begin():
        taken = await town.reclaim_all(db)
    (walked,) = await asyncio.gather(*runs)

    assert [node.id for _, node in taken] == node_ids
    assert waited == [True], "the run waited for the catch-up's meters"
    assert walked == 2
    for node_id, meter_id in zip(node_ids, meter_ids[::-1], strict=True):
        holder, debt, cut_off = await _held_by(factory, node_id, meter_id)
        assert holder is None and debt == 0 and not cut_off
        assert (await _meter(factory, meter_id)).counted_at == moment


async def test_a_plot_ceded_as_the_run_starts_is_billed_to_the_city(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run bills whoever holds a node once it has the meters, not whoever
    held it when the run first read the node.

    The run opens the missing meters first, and reads every held node to do
    it. A holder without a coin gives the plot up just then -- before the run
    takes the meters, so nothing waits. The run's session still has the node
    as it read it. When the run billed that row, it wrote the holder's debt
    onto a plot the city held; now it reads the nodes again after the meters'
    lock and bills the treasury in energy.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((grid, home),) = await _grids(session, constants, catalog, 1)
    owner, body = await _resident(session, home, "Хозяин")
    home.owner_identity_id = owner.id
    (meter,) = await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    ids = (body.id, home.id, grid.id, meter.id)
    await session.commit()
    body_id, home_id, grid_id, meter_id = ids

    opened = utility.ensure_meters
    #: Held on purpose, as a local further up the run would hold it: the
    #: session keeps its rows weakly, and the run must not rely on nobody
    #: holding the row it read.
    stale: list[Node | None] = []

    async def ceded_after(db, *args, **kwargs):
        result = await opened(db, *args, **kwargs)
        stale.append(await db.get(Node, home_id))
        async with factory() as elsewhere, elsewhere.begin():
            house = await elsewhere.get(Node, home_id)
            assert house is not None
            await town.cede(elsewhere, await elsewhere.get(Body, body_id), house)
        return result

    monkeypatch.setattr(utility, "ensure_meters", ceded_after)

    async with factory() as db, db.begin():
        assert await utility.run_meters(db, constants, now=moment) == 1

    assert stale and stale[0] is not None
    holder, debt, cut_off = await _held_by(factory, home_id, meter_id)
    assert holder is None
    assert debt == 0 and not cut_off, "the former holder's debt on the city's plot"
    assert (await _meter(factory, meter_id)).counted_at == moment
    drawn = 100_000 - await _pool_left(factory, constants, grid_id)
    assert drawn == pytest.approx(utility.draw_for(constants, home, hours), abs=0.01)


async def test_somebody_elses_plot_is_refused_without_waiting_out_the_run(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cede` takes the plot's meter before anything else, and a meter run
    holds every meter until it commits. A stranger giving up a plot that is not
    theirs is refused on the row as read, before the meter: taken first, the
    meter kept a refusal waiting out the whole run."""
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    ((_, home),) = await _grids(session, constants, catalog, 1)
    owner, _ = await _resident(session, home, "Хозяин", funds=1000)
    _, guest = await _resident(session, home, "Гость")
    home.owner_identity_id = owner.id
    await _open(session, constants, [home], since=moment - timedelta(hours=hours))
    ids = (guest.id, home.id)
    await session.commit()
    guest_id, home_id = ids

    async def cede() -> object:
        async with factory() as db, db.begin():
            house = await db.get(Node, home_id)
            assert house is not None
            return await town.cede(db, await db.get(Body, guest_id), house)

    waited: list[bool] = []
    tries: list[asyncio.Future] = []
    runs: list[AsyncSession] = []
    produced = energy.produce

    async def ceding_midway(db, *args, **kwargs):
        result = await produced(db, *args, **kwargs)
        if runs and db is runs[0] and not tries:
            tries.append(asyncio.ensure_future(cede()))
            waited.append(await _until_blocked_by(factory, db, unless=tries[0]))
        return result

    monkeypatch.setattr(energy, "produce", ceding_midway)

    async with factory() as db, db.begin():
        runs.append(db)
        assert await utility.run_meters(db, constants, now=moment) == 1
    (refused,) = await asyncio.gather(*tries, return_exceptions=True)

    assert isinstance(refused, town.NotYours), refused
    assert waited == [False], "the refusal did not wait for the run's meter"


async def test_a_plot_ceded_while_the_run_opens_its_meter_owes_nothing(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A plot taken since the last pass has no meter yet; the run opens one
    while its holder gives the plot up. The cede cannot see a meter the run has
    not committed, and takes none: it waits on the plot's row, which the
    meter's insert holds through its foreign key. What keeps the city's plot
    clear is that a meter counts its hours from its opening, so the pass that
    opened it bills nothing. Should the opening ever be dated back, this fails."""
    moment = datetime.now(UTC)
    ((_, home),) = await _grids(session, constants, catalog, 1)
    owner, body = await _resident(session, home, "Хозяин")
    home.owner_identity_id = owner.id
    ids = (body.id, home.id, owner.id)
    await session.commit()
    body_id, home_id, owner_id = ids

    async def cede() -> object:
        async with factory() as db, db.begin():
            house = await db.get(Node, home_id)
            assert house is not None
            return await town.cede(db, await db.get(Body, body_id), house)

    waited: list[bool] = []
    tries: list[asyncio.Future] = []

    async with factory() as db, db.begin():
        assert await utility.run_meters(db, constants, now=moment) == 1
        tries.append(asyncio.ensure_future(cede()))
        waited.append(await _until_blocked_by(factory, db, unless=tries[0]))
    (ceded,) = await asyncio.gather(*tries, return_exceptions=True)

    assert isinstance(ceded, City), ceded
    assert waited == [True], "the cede waited on the plot the meter's insert holds"
    async with factory() as db:
        meter_id = await db.scalar(select(UtilityMeter.id).where(UtilityMeter.node_id == home_id))
    assert meter_id is not None, "the run opened the plot's meter"
    holder, debt, cut_off = await _held_by(factory, home_id, meter_id)
    assert holder is None and debt == 0 and not cut_off
    async with factory() as db:
        assert await ledger.find_account(db, AccountKind.IDENTITY, owner_id) is None
