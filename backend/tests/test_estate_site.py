# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The construction site: materials by contribution, the house by time and body (D-266).

A house used to be bought in one motion out of the builder's hands, and
nothing bigger than a shed fit in those hands (playtest 2026-09-02). Here
the three phases are walked -- the ground taken at the laying, the bill
filled by parts and never past itself, the start paid with the body, the
term ripened by the job and the house raised by the owner's hand -- and two
contributions at once are made to share the last gap of the bill.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, estate, jobs, world
from src.models.estate import BuildSite, SiteState
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item


async def _plot(session: AsyncSession, area: float = 100):
    """Own civic plot, empty: the owner stands on it."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.plot.{stamp}", "Участок", area_m2=area)
    node.owner_city_id = uuid.uuid4()
    await session.flush()
    identity = await world.create_identity(session, f"Застройщик-{stamp}")
    body = await world.print_body(session, identity, node)
    await world.grant_node(session, node, identity)
    return node, identity, body


async def _give(session: AsyncSession, body: Body, goods: dict[str, float], extra: float = 0):
    pocket = await world.body_container(session, body)
    for name, qty in goods.items():
        await world.grant_item(session, pocket, name, amount=qty + extra, quality=60, origin="тест")


async def _held(session: AsyncSession, body: Body, type_key: str) -> float:
    pocket = await world.body_container(session, body)
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == type_key
        )
    )
    return float(int(total or 0)) / 1000


async def _fill(session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, site):
    await _give(session, body, site.needed)
    for name, qty in site.needed.items():
        await estate.contribute_to_site(session, constants, catalog, body, site, name, qty)


async def test_a_site_holds_the_ground(session: AsyncSession, constants: Constants) -> None:
    """Laid, the site has spent its footprint: a second house has no room."""
    node, _, body = await _plot(session, area=100)
    site = await estate.lay_site(session, constants, body, node, 60)
    assert site.state is SiteState.GATHERING and site.needed
    assert await estate.free_ground(session, node) == pytest.approx(40)
    with pytest.raises(estate.NoRoom):
        await estate.lay_site(session, constants, body, node, 60)
    assert any(
        work.get("site") == str(site.id) for work in await estate.under_construction(session, node)
    )


async def test_materials_come_by_parts_and_never_past_the_bill(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _plot(session)
    site = await estate.lay_site(session, constants, body, node, 20)
    name, need = next(iter(site.needed.items()))
    await _give(session, body, {name: need}, extra=5)

    first = await estate.contribute_to_site(session, constants, catalog, body, site, name, need / 2)
    assert 0 < first <= need / 2 + 1
    #: Asked for more than the bill still wants: the site takes the rest and no more.
    second = await estate.contribute_to_site(session, constants, catalog, body, site, name, need)
    assert site.brought[name] == pytest.approx(need)
    assert first + second == pytest.approx(need)
    assert await _held(session, body, name) == pytest.approx(5), "лишнее осталось в руках"
    with pytest.raises(estate.SiteError):
        await estate.contribute_to_site(session, constants, catalog, body, site, name, 1)
    with pytest.raises(estate.SiteError):
        await estate.contribute_to_site(session, constants, catalog, body, site, "coin", 1)


async def test_start_needs_the_full_bill_and_the_body(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _plot(session)
    site = await estate.lay_site(session, constants, body, node, 20, floors=2)
    with pytest.raises(estate.SiteError):
        await estate.start_site(session, constants, body, site)
    await _fill(session, constants, catalog, body, site)
    assert not estate.short_of_site(site)

    cost = estate.start_stamina(constants, site)
    assert cost == pytest.approx(constants[R.BUILD_START_STAMINA_PER_M2] * 20 * 2)
    body.stamina = Decimal(str(cost / 2))
    await session.flush()
    with pytest.raises(estate.SiteNoStrength):
        await estate.start_site(session, constants, body, site)

    body.stamina = Decimal("50")
    await session.flush()
    job = await estate.start_site(session, constants, body, site)
    assert site.state is SiteState.BUILDING and site.ready_at == job.run_at
    assert float(body.stamina) == pytest.approx(50 - cost)
    #: The site's job speaks through the site: the works list names it once.
    works = await estate.under_construction(session, node)
    assert [work.get("site") for work in works] == [str(site.id)]


async def test_the_job_ripens_and_the_owner_raises_the_house(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    async with factory() as session, session.begin():
        node, identity, body = await _plot(session)
        site = await estate.lay_site(session, constants, body, node, 30)
        await _fill(session, constants, catalog, body, site)
        job = await estate.start_site(session, constants, body, site)
        ready, site_id, node_id, identity_id = job.run_at, site.id, node.id, identity.id

    assert await jobs.run_one(factory, now=ready) is not None

    async with factory() as session, session.begin():
        site = await session.get(BuildSite, site_id)
        assert site is not None and site.state is SiteState.READY
        node = await session.get(type(node), node_id)
        assert node is not None
        assert not await estate.buildings_of(session, node), "дом ждёт руки хозяина"
        told = (
            (
                await session.execute(
                    select(Event).where(
                        Event.kind == EventKind.ESTATE_SITE_READY.value,
                        Event.actor_identity_id == identity_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(told) == 1

        body = (
            await session.execute(select(Body).where(Body.identity_id == identity_id))
        ).scalar_one()
        building = await estate.finish_site(session, constants, body, site)
        assert float(building.footprint_m2) == 30 and site.state is SiteState.DONE
        assert len(await estate.buildings_of(session, node)) == 1
        assert await estate.under_construction(session, node) == []


async def test_only_the_owner_starts_and_finishes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Anybody may bring; the owner alone starts and raises."""
    node, _, owner = await _plot(session)
    helper = await world.print_body(
        session, await world.create_identity(session, f"Помощник-{uuid.uuid4().hex[:6]}"), node
    )
    site = await estate.lay_site(session, constants, owner, node, 20)
    await _fill(session, constants, catalog, helper, site)
    with pytest.raises(estate.SiteError):
        await estate.start_site(session, constants, helper, site)
    await estate.start_site(session, constants, owner, site)
    #: Ripe by hand -- the job is another test's -- and the helper's hand is
    #: refused at the finish as it was at the start.
    site.state = SiteState.READY
    await session.flush()
    with pytest.raises(estate.SiteError):
        await estate.finish_site(session, constants, helper, site)
    building = await estate.finish_site(session, constants, owner, site)
    assert float(building.footprint_m2) == 20


async def test_two_contributions_at_once_share_the_last_gap(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the site's row lock both bringers read the same gap and both fill it."""
    _slow(monkeypatch, craft, "_stock")
    node, _, owner = await _plot(session)
    site = await estate.lay_site(session, constants, owner, node, 20)
    name, need = next(iter(site.needed.items()))
    bringers = []
    for number in range(2):
        who = await world.create_identity(session, f"Носильщик-{uuid.uuid4().hex[:6]}-{number}")
        body = await world.print_body(session, who, node)
        await _give(session, body, {name: need})
        bringers.append(body.id)
    site_id = site.id
    await session.commit()

    async def bring(body_id: uuid.UUID) -> float:
        async with factory() as db, db.begin():
            body = await db.get(Body, body_id)
            assert body is not None
            site = await estate.site_of(db, site_id)
            try:
                return await estate.contribute_to_site(
                    db, constants, catalog, body, site, name, need
                )
            except estate.SiteError:
                return 0.0

    taken = await asyncio.gather(*(bring(one) for one in bringers))
    assert sum(taken) == pytest.approx(need), f"площадка взяла не ровно смету: {taken}"
    async with factory() as db:
        again = await db.get(BuildSite, site_id)
        assert again is not None and again.brought[name] == pytest.approx(need)
        held = 0.0
        for body_id in bringers:
            body = await db.get(Body, body_id)
            assert body is not None
            held += await _held(db, body, name)
        assert held == pytest.approx(2 * need - need), "списано ровно то, что взято"


async def test_two_starts_at_once_spend_the_strength_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two sites of one owner, strength for one start: the body's row lock
    makes the second start read the first's spending, and nobody goes below
    nought."""
    from src.engine.estate.building import site as site_module

    #: The pause sits after the strength is spent and before the row is let go:
    #: `enqueue` is the first await past the deduction.
    _slow(monkeypatch, site_module, "enqueue")
    node, _, owner = await _plot(session, area=100)
    sites = []
    for _ in range(2):
        site = await estate.lay_site(session, constants, owner, node, 20)
        await _fill(session, constants, catalog, owner, site)
        sites.append(site.id)
    cost = estate.start_stamina(constants, sites and await estate.site_of(session, sites[0]))
    owner.stamina = Decimal(str(cost * 1.5))
    body_id = owner.id
    await session.commit()

    async def start(site_id: uuid.UUID) -> str:
        async with factory() as db, db.begin():
            body = await db.get(Body, body_id)
            assert body is not None
            try:
                await estate.start_site(db, constants, body, await estate.site_of(db, site_id))
            except estate.SiteNoStrength:
                return "refused"
            return "started"

    outcomes = await asyncio.gather(*(start(one) for one in sites))
    assert sorted(outcomes) == ["refused", "started"], outcomes
    async with factory() as db:
        body = await db.get(Body, body_id)
        assert body is not None
        assert float(body.stamina) == pytest.approx(cost * 0.5)


async def test_a_plot_with_a_living_site_is_not_sold(
    session: AsyncSession, constants: Constants
) -> None:
    """The site is the owner's, and the ground it holds goes with the owner:
    a deed is not offered nor bought while a site stands (D-266)."""
    from src.models.estate import Deed

    node, identity, body = await _plot(session)
    deed = (await session.execute(select(Deed).where(Deed.node_id == node.id))).scalar_one()
    await estate.lay_site(session, constants, body, node, 20)
    with pytest.raises(estate.EstateError):
        await estate.offer_deed(session, identity, deed, 100)
    #: Taking it off sale is not a sale: allowed at any time.
    await estate.offer_deed(session, identity, deed, 0)
