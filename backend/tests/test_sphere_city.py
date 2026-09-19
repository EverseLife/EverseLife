# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city the catch-up founded on Terra's sphere comes down (D-356, item 10).

The catch-up took the core's parent for the capital, and since D-330 that is
the planet's sphere: the first deploy of a world laid after D-330 founded a
city there, filled its treasury from `genesis`, flagged it the capital, and
wrote every unowned node of the planet to it. Checked:

* the next deploy takes it down -- land, members, channel, figures, treasury,
  the row -- leaves the capital as it was, lets the capital's line take what
  lies inside it (D-356), and never runs again;
* a citizen it enrolled is left with no city, and keeps the grant it paid;
* land somebody holds from it, or business of its own, stops the step: nothing
  is touched, the owner hears of it, and the next deploy asks again;
* a world laid fresh owes no run;
* the return to `genesis` queues behind a payout from the treasury rather than
  spending money the payout already took.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from src import seed_once, seed_sphere_city
from src import seed_parts as parts
from src.constants import current_catalog
from src.engine import city as town
from src.engine import estate, ledger, world
from src.engine.jobs import enqueue
from src.models.catchup import CatchUpStep
from src.models.city import Citizen, City, CityGrant, Office
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.metrics import DailyMetric
from src.models.net import NetChannel, NetSubscription
from src.models.vote import Vote, VoteKind
from src.models.world import COVERED, Layer, Node
from src.seed import seed
from src.units import money

STEP = seed_once.SPHERE_CITY_TAKEN_DOWN
#: Inside the capital's outline and hanging on the sphere (`test_city_line`).
OILFIELD = "terra.oilfield"


async def _marks(session: AsyncSession) -> list[CatchUpStep]:
    return list(
        (await session.execute(select(CatchUpStep).where(CatchUpStep.step == STEP))).scalars()
    )


async def _old_world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Node:
    """A world laid before the step existed: born past every other one-off step,
    and owing this one."""
    with monkeypatch.context() as patch:
        patch.setattr(seed_once, "ONCE", tuple(step for step in seed_once.ONCE if step != STEP))
        core = await seed(session)
    assert await _marks(session) == []
    return core


async def _find(session: AsyncSession, sphere: Node) -> Node:
    """A scout's find: a node of the surface hanging on the planet's sphere."""
    find = await world.create_node(
        session, f"terra.wild.{uuid.uuid4().hex[:8]}", "", area_m2=100, layer=Layer.PLANET,
        parent=sphere,
    )  # fmt: skip
    await session.flush()
    return find


async def _as_the_old_deploy_left_it(session: AsyncSession, core: Node) -> tuple[City, Node]:
    """What a deploy wrote when the catch-up took the sphere for the capital:
    the city and its treasury, the capital's flag, every unowned node on the
    sphere its built-up area -- and the distances the tick measured for it."""
    sphere = await session.get(Node, core.parent_id)
    assert sphere is not None and sphere.layer is Layer.SPACE
    city = await town.found(session, current_catalog(), sphere, sphere.name)
    city.laws = {"newcomer_grant": parts.NEWCOMER_GRANT}
    await parts.treasury(session, city)
    city.capital = True
    children = (await session.execute(select(Node).where(Node.parent_id == sphere.id))).scalars()
    for node in children:
        if node.owner_city_id is None and node.owner_identity_id is None:
            node.owner_city_id = city.id
    await session.flush()
    await estate.measure_cities(session)
    return city, sphere


async def _balance(session: AsyncSession, kind: AccountKind, owner: uuid.UUID | None) -> int:
    account = await ledger.find_account(session, kind, owner)
    return 0 if account is None else await ledger.balance(session, account.id)


async def test_the_next_deploy_takes_down_the_city_on_the_sphere(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Everything the city had goes with it, the capital keeps what it had, and
    the money it was given returns to where it came from."""
    core = await _old_world(session, monkeypatch)
    sphere = await session.get(Node, core.parent_id)
    assert sphere is not None
    find = await _find(session, sphere)
    issued = await _balance(session, AccountKind.GENESIS, None)
    capital = await town.by_node(session, core.id)
    assert capital is not None

    #: Laid before D-356, the world had the field wild: no line took it.
    oilfield = await session.scalar(select(Node).where(Node.key == OILFIELD))
    assert oilfield is not None
    oilfield.owner_city_id = None
    oilfield.properties = {k: v for k, v in oilfield.properties.items() if k != COVERED}
    await session.flush()

    city, _ = await _as_the_old_deploy_left_it(session, core)
    assert oilfield.owner_city_id == city.id
    await session.refresh(find)
    assert find.owner_city_id == city.id
    assert find.center_node_id is not None, "тик мерил землю фантома"
    channel = await session.scalar(select(NetChannel).where(NetChannel.city_id == city.id))
    assert channel is not None
    reader = (await town.offices(session, capital))[0].identity_id
    session.add(NetSubscription(channel_id=channel.id, identity_id=reader))
    session.add(DailyMetric(day=datetime.now(UTC).date(), key=f"city.{city.id}.people", value=0))
    await session.flush()
    gone = city.id

    await seed(session)

    assert await session.get(City, gone) is None
    cities = list((await session.execute(select(City))).scalars())
    homes = [await session.get(Node, one.node_id) for one in cities]
    assert all(home is not None and home.layer is Layer.PLANET for home in homes)
    assert [one.id for one in cities if one.capital] == [capital.id]
    await session.refresh(find)
    await session.refresh(sphere)
    await session.refresh(core)
    assert find.owner_city_id is None, "находка снова ничья"
    assert (find.center_node_id, find.center_steps) == (None, None)
    assert (sphere.center_node_id, sphere.center_steps) == (None, None)
    assert core.owner_city_id == capital.id, "ядро осталось столице"
    #: Let go before the lines are asked (D-356): the field inside the
    #: capital's outline is its land in the same deploy.
    await session.refresh(oilfield)
    assert oilfield.owner_city_id == capital.id and oilfield.properties[COVERED] is True
    assert await session.scalar(select(NetChannel).where(NetChannel.city_id == gone)) is None
    assert await session.scalar(
        select(func.count()).select_from(NetSubscription).where(
            NetSubscription.channel_id == channel.id
        )
    ) == 0  # fmt: skip
    assert await session.scalar(
        select(func.count()).select_from(DailyMetric).where(DailyMetric.key.startswith(f"city.{gone}."))
    ) == 0  # fmt: skip
    assert await _balance(session, AccountKind.CITY_TREASURY, sphere.id) == 0
    assert await _balance(session, AccountKind.GENESIS, None) == issued, "выпуск вернулся"

    [mark] = await _marks(session)
    [taken] = mark.result["cities"]
    assert taken["city"] == str(gone)
    assert taken["returned"] == money(parts.CITY_TREASURY_START)
    assert find.key in taken["land"] and core.key not in taken["land"]
    assert taken["channel"] == 1 and taken["figures"] == 1

    #: The next deploy founds nothing on the sphere and runs nothing again.
    await seed(session)
    assert await town.by_node(session, sphere.id) is None
    assert len(await _marks(session)) == 1


async def test_a_citizen_of_it_is_left_with_no_city_and_keeps_its_grant(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Printed on its land, a newcomer became its citizen and was paid its
    grant; the citizenship goes with the city, the money stays in the pocket,
    and only what was left in the treasury returns to `genesis`."""
    core = await _old_world(session, monkeypatch)
    sphere = await session.get(Node, core.parent_id)
    assert sphere is not None
    find = await _find(session, sphere)
    issued = await _balance(session, AccountKind.GENESIS, None)
    city, _ = await _as_the_old_deploy_left_it(session, core)
    ada, _ = await world.spawn(session, "Ада", find)
    grant = await session.scalar(select(CityGrant.amount).where(CityGrant.city_id == city.id))
    assert grant, "дверь дала гражданство фантома и его подъёмные"
    office = await town.install_founder(session, city, ada)
    await enqueue(
        session,
        JobKind.RULER_TERM,
        datetime.now(UTC) + timedelta(days=30),
        payload={"city": str(city.id), "office": str(office.id)},
        dedup_key=f"city.term:{office.id}",
    )
    await session.flush()

    await seed(session)

    assert await town.citizenship(session, ada.id) is None
    assert await session.scalar(
        select(func.count()).select_from(Office).where(Office.id == office.id)
    ) == 0  # fmt: skip
    assert await session.scalar(
        select(func.count()).select_from(Job).where(Job.payload["city"].astext == str(city.id))
    ) == 0  # fmt: skip
    assert await _balance(session, AccountKind.IDENTITY, ada.id) == grant
    assert await _balance(session, AccountKind.GENESIS, None) == issued - grant
    [mark] = await _marks(session)
    [taken] = mark.result["cities"]
    assert taken["members"]["citizen"] == 1 and taken["members"]["city_office"] == 1
    assert taken["members"]["city_grant"] == 1 and taken["jobs"] == 1


async def test_held_land_or_business_stops_the_step_until_the_owner_decides(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Somebody holding its land, or a vote it opened, is not the step's to
    settle: nothing of the city is touched, the log says what waits, and the
    next deploy asks again -- and takes it down once the way is clear."""
    core = await _old_world(session, monkeypatch)
    sphere = await session.get(Node, core.parent_id)
    assert sphere is not None
    find = await _find(session, sphere)
    city, _ = await _as_the_old_deploy_left_it(session, core)
    hyom = await session.scalar(select(Citizen.identity_id).limit(1))
    find.owner_identity_id = hyom
    vote = Vote(
        city_id=city.id,
        kind=VoteKind.LAW,
        threshold="majority",
        closes_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(vote)
    await session.flush()

    with caplog.at_level(logging.ERROR, logger="everselife.seed"):
        await seed(session)

    assert await session.get(City, city.id) is not None
    assert await _marks(session) == []
    await session.refresh(find)
    assert find.owner_city_id == city.id, "ничего не тронуто"
    said = " ".join(record.getMessage() for record in caplog.records)
    assert find.key in said and "vote" in said

    find.owner_identity_id = None
    await session.delete(vote)
    await session.flush()
    await seed(session)
    assert await session.get(City, city.id) is None
    assert len(await _marks(session)) == 1


async def test_a_world_laid_fresh_owes_no_run(session: AsyncSession) -> None:
    """Laid by a catch-up that reads the capital right, a world never had the
    city: it is born with the step done."""
    await seed(session)
    [mark] = await _marks(session)
    assert mark.result == {"born": True}


async def test_the_return_queues_behind_a_payout_from_the_treasury(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A payout from the treasury holds its account while the step comes for
    the balance. Read before the lock, the step would count the money the
    payout is taking, and the return would be refused for want of it -- the
    deploy failing on a transfer that was never there to make. Locked first,
    it waits, reads what the payout left, and returns exactly that."""
    async with factory() as db, db.begin():
        core = await _old_world(db, monkeypatch)
        city, sphere = await _as_the_old_deploy_left_it(db, core)
        payee = await db.scalar(select(Citizen.identity_id).limit(1))
        assert payee is not None
        treasury = (await town.treasury(db, city)).id
        purse = (await ledger.account_for(db, AccountKind.IDENTITY, payee)).id
        issued = await _balance(db, AccountKind.GENESIS, None)
    paid = money(120)

    async with factory() as payer, factory() as deploy:
        await payer.begin()
        await ledger.transfer(
            payer, PostingReason.SALARY, debit=treasury, credit=purse, amount=paid
        )

        async def repair() -> list | None:
            async with deploy.begin():
                return await seed_sphere_city.take_down(deploy)

        step = asyncio.create_task(repair())
        assert await _until_blocked_by(factory, payer, unless=step)
        await payer.commit()
        [taken] = await step

    assert taken["returned"] == money(parts.CITY_TREASURY_START) - paid
    async with factory() as db:
        assert await ledger.balance(db, treasury) == 0
        assert await _balance(db, AccountKind.GENESIS, None) == issued + taken["returned"]
        assert await town.by_node(db, sphere.id) is None
