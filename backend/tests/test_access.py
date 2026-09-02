# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The door of one's own location, and the two lists behind it (D-199, D-204).

Checked is not "the flag is stored" but what the rules exist for:

* two lists: the white one gets into a shut location, the black one gets in
  nowhere, and where they contradict each other black wins;
* the holder cannot lock themselves out;
* shutting stops **entry**, not passage: a route goes straight through a shut
  location, so one holder's will never cuts a neighbour off from their home;
* the door stops arrivals and never departures -- otherwise shutting it on a
  guest would be a way to take a body away;
* nobody's land has no door: there is nothing to shut and nobody to shut it
  (D-198);
* a road **into** somebody's shut location is refused before it starts;
* passage is walked to its end: one does not turn back in the middle of
  somebody's shut location, and does not touch its floor.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import access, travel, world
from src.models.world import PLOT, Layer


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
    """A plot with a holder: title is issued by a city, and only by one (D-198).

    Marked as a plot, because that is what it is: the gate belongs to a plot
    the authority hands out, not to every node a city owns (D-199).
    """
    node.owner_city_id = uuid.uuid4()
    node.properties = {**(node.properties or {}), PLOT: True}
    await session.flush()
    return await world.grant_node(session, node, identity)


async def test_open_location_lets_everyone_in_except_the_black_list(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    yard = await _plot(session, "yard")
    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    guest, _ = await _person(session, yard, "guest")

    assert await access.may_enter(session, yard, guest.id)

    await access.add(session, yard, holder, guest, allowed=False)
    assert not await access.may_enter(session, yard, guest.id)
    assert await access.roster(session, yard, allowed=False) == [guest.name]
    #: The white list stays empty: the lists are two and do not borrow names.
    assert await access.roster(session, yard, allowed=True) == []


async def test_shut_location_lets_in_only_the_white_list(
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


async def test_black_list_beats_white(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One name is in one list: naming it in the other moves it (D-204)."""
    yard = await _plot(session, "yard")
    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    guest, _ = await _person(session, yard, "guest")

    await access.set_gate(session, yard, holder, closed=True)
    await access.add(session, yard, holder, guest, allowed=True)
    assert await access.may_enter(session, yard, guest.id)

    await access.add(session, yard, holder, guest, allowed=False)
    assert not await access.may_enter(session, yard, guest.id)
    assert await access.roster(session, yard, allowed=True) == []
    assert await access.roster(session, yard, allowed=False) == [guest.name]

    #: And back again: the door is not a one-way decision.
    await access.add(session, yard, holder, guest, allowed=True)
    assert await access.may_enter(session, yard, guest.id)


async def test_holder_is_never_locked_out(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Neither the door nor a list shuts a person out of their own location."""
    yard = await _plot(session, "yard")
    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)

    await access.set_gate(session, yard, holder, closed=True)
    assert await access.may_enter(session, yard, holder.id)

    with pytest.raises(access.AccessError):
        await access.add(session, yard, holder, holder)


async def test_nobodys_land_has_no_door(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Outside a city there is no owner (D-198), so there is nothing to shut."""
    grove = await _plot(session, "grove")
    passerby, _ = await _person(session, grove, "passerby")

    with pytest.raises(access.NotYours):
        await access.set_gate(session, grove, passerby, closed=True)
    assert await access.may_enter(session, grove, passerby.id)


async def test_a_city_location_has_no_door(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The gate is a property of the plot, not of the city (D-199).

    The capital's core, its market, its administration are the city's own
    places: a title over one of them is a title and not a door. The engine used
    to read "somebody holds it" as "somebody's yard", and one allotment signed
    at the town hall shut the centre of the capital -- with the printer people
    come back to life at -- to everybody but its new holder.
    """
    core = await _plot(session, "core")
    holder, _ = await _person(session, core, "holder")
    #: Held, and civic, and **not** a plot: exactly the case that shut the core.
    core.owner_city_id = uuid.uuid4()
    await session.flush()
    await world.grant_node(session, core, holder)
    stranger, body = await _person(session, core, "stranger")

    #: There is no door here to shut, and so none to be refused at.
    with pytest.raises(access.NotYours):
        await access.set_gate(session, core, holder, closed=True)
    core.gated = True
    await session.flush()
    assert await access.may_enter(session, core, stranger.id)
    await access.require_entry(session, core, body)


async def test_stranger_does_not_run_the_door(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    yard = await _plot(session, "yard")
    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    stranger, _ = await _person(session, yard, "stranger")

    with pytest.raises(access.NotYours):
        await access.set_gate(session, yard, stranger, closed=True)
    with pytest.raises(access.NotYours):
        await access.add(session, yard, stranger, holder, allowed=False)


async def test_shut_location_refuses_the_road_before_it_starts(
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


async def test_route_goes_through_a_shut_location(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Shutting stops entry, not passage (D-204).

    The point of the rule is a neighbour behind somebody else's location: before
    D-204 a shut yard was cut out of the graph, and with one road that left a
    person unable to reach their own home.
    """
    start = await _plot(session, "start")
    yard = await _plot(session, "yard")
    home = await _plot(session, "home")
    await travel.connect(session, start, yard, base_seconds=30)
    await travel.connect(session, yard, home, base_seconds=30)

    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    walker, walker_body = await _person(session, start, "walker")
    await _held(session, home, walker)

    #: Shut, and the walker is even in the black list -- passage is still passage.
    await access.set_gate(session, yard, holder, closed=True)
    await access.add(session, yard, holder, walker, allowed=False)

    assert await travel.route(session, constants, start.id, home.id) == [
        yard.id,
        home.id,
    ]
    going = await travel.depart(session, constants, walker_body, home)
    assert going is not None, "до своего дома доходят через чужую локацию"

    #: Stopping there is another matter, and it is refused by name.
    _, other = await _person(session, start, "other")
    with pytest.raises(access.Barred):
        await travel.depart(session, constants, other, yard)


async def test_passage_is_not_turned_back_from(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Turning back in somebody's shut location would leave the body standing in it."""
    yard = await _plot(session, "yard")
    home = await _plot(session, "home")
    await travel.connect(session, yard, home, base_seconds=30)

    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    #: The walker is standing in the shut location -- a leg of a passage through it.
    walker, walker_body = await _person(session, yard, "walker")
    await access.set_gate(session, yard, holder, closed=True)
    await travel.depart(session, constants, walker_body, home)

    with pytest.raises(access.Barred):
        await travel.turn_back(session, walker_body)


async def test_a_guest_walks_out_of_a_location_shut_behind_them(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The door stops arrivals, never departures: no locking a body in."""
    street = await _plot(session, "street")
    yard = await _plot(session, "yard")
    await travel.connect(session, street, yard, base_seconds=30)

    holder, _ = await _person(session, yard, "holder")
    await _held(session, yard, holder)
    guest, guest_body = await _person(session, yard, "guest")

    await access.set_gate(session, yard, holder, closed=True)
    going = await travel.depart(session, constants, guest_body, street)
    assert going is not None

    #: And the way out is not only the neighbouring node: a route from a location
    #: shut behind the guest must build, or the door locks a body in through
    #: autopath instead of through the rule.
    far = await _plot(session, "far")
    await travel.connect(session, street, far, base_seconds=30)
    assert await travel.route(session, constants, yard.id, far.id) == [
        street.id,
        far.id,
    ]
