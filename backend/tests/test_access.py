"""The gate of one's own yard (D-199).

Checked is not "the flag is stored" but what the rule exists for:

* one roster, and the gate turns its meaning over;
* the holder cannot lock themselves out;
* the gate stops arrivals and never departures -- otherwise shutting it on a
  guest would be a way to take a body away;
* nobody's land has no gate: there is nothing to shut and nobody to shut it
  (D-198);
* a road into somebody's shut yard is refused before it starts.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import access, travel, world
from src.models.world import Layer


async def _plot(session: AsyncSession, name: str, *, area: float = 100):
    stamp = uuid.uuid4().hex[:8]
    return await world.create_node(
        session, f"terra.{name}.{stamp}", name, area_m2=area, layer=Layer.PLANET
    )


async def _person(session: AsyncSession, node, name: str):
    stamp = uuid.uuid4().hex[:6]
    identity = await world.create_identity(session, f"{name}-{stamp}")
    body = await world.print_body(session, identity, node)
    return identity, body


async def _held(session: AsyncSession, node, identity):
    """A plot with a holder: title is issued by a city, and only by one (D-198)."""
    node.owner_city_id = uuid.uuid4()
    await session.flush()
    return await world.grant_node(session, node, identity)


async def test_open_yard_lets_everyone_in_except_the_named(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    yard = await _plot(session, "yard")
    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    guest, _ = await _person(session, yard, "guest")

    assert await access.may_enter(session, yard, guest.id)

    await access.add(session, yard, holder, guest)
    assert not await access.may_enter(session, yard, guest.id)
    #: The same roster, the gate open: this is a blacklist.
    assert await access.roster(session, yard) == [guest.name]


async def test_shut_yard_lets_in_only_the_named(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    yard = await _plot(session, "yard")
    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    guest, _ = await _person(session, yard, "guest")
    stranger, _ = await _person(session, yard, "stranger")

    await access.set_gate(session, yard, holder, closed=True)
    assert not await access.may_enter(session, yard, guest.id)

    await access.add(session, yard, holder, guest)
    assert await access.may_enter(session, yard, guest.id), "названный входит"
    assert not await access.may_enter(session, yard, stranger.id)


async def test_holder_is_never_locked_out(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Neither the gate nor the roster shuts a person out of their own yard."""
    yard = await _plot(session, "yard")
    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)

    await access.set_gate(session, yard, holder, closed=True)
    assert await access.may_enter(session, yard, holder.id)

    with pytest.raises(access.AccessError):
        await access.add(session, yard, holder, holder)


async def test_nobodys_land_has_no_gate(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Outside a city there is no owner (D-198), so there is nothing to shut."""
    grove = await _plot(session, "grove")
    passerby, _ = await _person(session, grove, "passerby")

    with pytest.raises(access.NotYours):
        await access.set_gate(session, grove, passerby, closed=True)
    assert await access.may_enter(session, grove, passerby.id)


async def test_stranger_does_not_run_the_gate(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    yard = await _plot(session, "yard")
    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    stranger, _ = await _person(session, yard, "stranger")

    with pytest.raises(access.NotYours):
        await access.set_gate(session, yard, stranger, closed=True)


async def test_shut_yard_refuses_the_road_before_it_starts(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The refusal comes at departure and names the reason (D-199)."""
    street = await _plot(session, "street")
    yard = await _plot(session, "yard")
    await travel.connect(session, street, yard, base_seconds=30)

    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    await access.set_gate(session, yard, holder, closed=True)

    _, walker = await _person(session, street, "walker")
    with pytest.raises(access.Barred):
        await travel.depart(session, constants, walker, yard)


async def test_route_goes_around_a_yard_that_named_you(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A blacklisted traveller is not routed through the yard that named them.

    The gate is not the only way to be barred: with the yard open the roster is
    a blacklist, and a route ignoring that would walk the person straight
    through the one place they are not allowed.
    """
    start = await _plot(session, "start")
    yard = await _plot(session, "yard")
    far = await _plot(session, "far")
    await travel.connect(session, start, yard, base_seconds=30)
    await travel.connect(session, yard, far, base_seconds=30)

    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    walker, walker_body = await _person(session, start, "walker")

    #: The yard stays open, but the walker is named in the roster.
    await access.add(session, yard, holder, walker)

    with pytest.raises(travel.NoRoute):
        await travel.route(
            session, constants, start.id, far.id, traveller=walker.id
        )
    #: For anybody else the road through the same yard is there.
    other, _ = await _person(session, start, "other")
    assert await travel.route(
        session, constants, start.id, far.id, traveller=other.id
    ) == [yard.id, far.id]


async def test_a_guest_walks_out_of_a_yard_shut_behind_them(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The gate stops arrivals, never departures: no locking a body in."""
    street = await _plot(session, "street")
    yard = await _plot(session, "yard")
    await travel.connect(session, street, yard, base_seconds=30)

    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    guest, guest_body = await _person(session, yard, "guest")

    await access.set_gate(session, yard, holder, closed=True)
    going = await travel.depart(session, constants, guest_body, street)
    assert going is not None

    #: And the way out is not only the neighbouring node: a route from a yard
    #: shut behind the guest must build, or the gate locks a body in through
    #: autopath instead of through the rule.
    far = await _plot(session, "far")
    await travel.connect(session, street, far, base_seconds=30)
    assert await travel.route(
        session, constants, yard.id, far.id, traveller=guest.id
    ) == [street.id, far.id]
