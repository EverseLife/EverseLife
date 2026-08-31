# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: who may give an order, and where a hull may be sent.

Two questions, and they are asked before every leg of every journey (D-245):
**may this body command this hull**, and **will that node take it**. Neither is
about physics -- thrust, fuel and hours live in `physics`, the legs themselves
in `flight` -- and both were repeated, in slightly different words, by every
order that existed. A turn-back kept its own shorter copy of the second one and
sent hulls back to piers whose yard had been carried off while they flew
(review of D-242); that is the whole reason this module is one.

Split out of `flight.py` when it crossed 800 lines.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import travel, world
from src.engine.ship._base import (
    BRIDGE,
    GROUND_BRIDGE,
    SPACEPORT,
    Deaf,
    NoConsole,
    NoPort,
    NotAboard,
    NotYours,
    ShipError,
    is_orbit,
)
from src.engine.ship.belonging import aboard_of, is_aboard
from src.engine.ship.physics import _things
from src.engine.ship.view import beacon_lit, lands_anywhere, lit_ports
from src.models.identity import Body, BodyState
from src.models.ship import Ship
from src.models.world import Node, Planet


async def _will_take(session: AsyncSession, constants: Constants, port: Node, *, why: str) -> None:
    """Whether this node will take a hull at all. Asked of every destination.

    Three questions, and a passage that skips any of them sets a ship down
    where there is nothing to set it down on:

    * a **yard**, or the bare ground of a planet one lands anywhere on (D-233):
      Pyroxis has no port and can have none -- nothing is built there, so there
      is nothing to put a yard into, and a ship simply sets down;
    * not a hull: one does not moor to somebody's cabin;
    * a **lit beacon** (D-231, D-232): the yard does not couple in a frozen node
      and does not shine without power.

    `why` names which order is asking (`dock`, `land`, `turn-back`) -- a message
    variant rather than a sentence: the words are the locale's (D-251).

    Asked **when the order is given**, never on arrival: a passage takes hours,
    and a port that went dark under a flying ship must not leave a crew in the
    void with no port at all. The gamble belongs to whoever gave the order, and
    it is settled the way they gave it.

    One function because there is one rule: a turn-back had its own, shorter
    copy of it and sent hulls back to piers whose yard had been carried off
    while they flew (review of D-242).

    An orbit answers yes to all three without being asked (D-245): it is space,
    and space needs no yard, has no beacon and cannot freeze. What it does need
    is a way **down** again, and that is not a question about the mooring but
    about the planet under it -- so `fly` asks it separately (`_landable`), and
    a turn-back into an orbit is never refused by it.
    """
    if is_orbit(port):
        return
    if not await world.has_station(session, port, SPACEPORT) and not await lands_anywhere(
        session, port
    ):
        raise NoPort(key="ship-no-spaceport", port=port.name, why=why)
    if is_aboard(port):  # pragma: no cover -- a port is never a ship node
        raise NoPort(key="ship-no-mooring-to-hull")
    if not await beacon_lit(session, constants, port):
        raise NoPort(key="ship-beacon-dark", port=port.name)


async def _landable(session: AsyncSession, constants: Constants, planet: Planet) -> bool:
    """Whether anything on this planet would take a hull today (D-232, D-245).

    Asked of the **planet**, not of a node, and only where a crossing is
    decided: an orbit one may reach and never come down from is a trap, and the
    place to refuse it is the end where there is still a choice.

    **Only** there, and the gap is deliberate. A world may go dark under a hull
    already on its way, and the arrival is not asked again -- a crew must not
    learn of a refusal when it is already in the void, and the sky the passage
    was paid for does not get re-read either (D-201). What that leaves is a
    hull over a dead planet with fuel for a descent it may not make; the way
    out is another hull, and the loss is D-232's own: the blackout is
    irreversible, and the world insures nobody against it.

    Bare ground counts (D-233): Pyroxis has no beacon to go out, so it is
    landable for as long as it has a surface.
    """
    return any(port.planet is planet for port in await lit_ports(session, constants))


async def _has_bridge(session: AsyncSession, ship: Ship) -> bool:
    """Whether the hull carries a console of its own -- something to receive an order.

    Read off the hold in one pass rather than asked room by room: a bridge is a
    machine standing somewhere aboard, which compartment is the owner's
    business, and a twenty-room hull was twenty queries for one boolean.
    """
    consoles = frozenset(world.station_names(BRIDGE))
    return any(thing.type_key in consoles for thing in await _things(session, ship))


async def _commanded_by(session: AsyncSession, body: Body, ship: Ship) -> None:
    """Who may move the ship: its owner, at a console -- aboard, or on the ground.

    Two places, and the second is why the first is not enough (D-242). Standing
    at the bridge aboard is the ordinary way (D-230). But a crew that dies in
    flight leaves a hull with no edges: unreachable on foot, deaf to every
    order, hanging with its cargo for ever -- and this world does not build
    traps with no way out (pillar P6). So the owner may also stand at a
    **«Наземная консоль управления»** in a building of their own and give the
    same orders: an order is information, and information travels the Net while
    matter requires presence (D-044).

    What the ground console does **not** do is make a bridge optional: the hull
    must carry one to have anything to receive the order with. A ship built
    without a console does not fly at all, by its crew or by anybody.

    A guest aboard is carried away and cannot object -- that is deliberate
    (D-201): a ban would mean any stranger blocks a passage by standing in the
    hold. The dispute is a matter for the court (D-166), not for the engine.
    Who gets to the console at all is the owner's door (`engine.access`): a
    room aboard is theirs, and they list who may enter it.
    """
    if body.state is not BodyState.ALIVE:
        raise ShipError(key="ship-command-dead")
    await travel.require_here(session, body)
    if ship.owner_identity_id != body.identity_id:
        raise NotYours(key="ship-not-yours")

    here = await session.get(Node, body.node_id)
    if here is None:  # pragma: no cover -- a body always stands in a node
        raise ShipError(key="ship-body-off-node")

    aboard = await aboard_of(session, body)
    if aboard is not None and aboard.id == ship.id:
        if not await world.has_station(session, here, BRIDGE):
            raise NoConsole(key="ship-no-console-here")
        return

    #: Not aboard this hull. Then it is the ground console or nothing -- and the
    #: hull must have something to hear it with.
    if not await world.has_station(session, here, GROUND_BRIDGE):
        raise NotAboard(key="ship-command-from-aboard")
    #: A console one may work at, by the same rule that decides who may put a
    #: machine down at all (`station.may_build`): the owner of the plot, and on
    #: civic land the authority. Not a security measure -- a stranger's ship is
    #: refused above, by its owner -- but the difference between somebody's own
    #: pult and one standing in a city hall for anybody who walks in.
    #:
    #: Wild land outside a city is nobody's and open to everyone (D-198), so a
    #: console left standing there **is** public. That follows from the world's
    #: own rule about machines rather than from this one, and it is left alone:
    #: a console in the wild is as exposed as the ship parts beside it.
    #: Lazy: `station` reaches `estate`, and `estate` reaches back here.
    from src.engine import station  # noqa: PLC0415 -- lazy: breaks the cycle with estate

    if not await station.may_build(session, body, here):
        raise NotYours(key="ship-console-not-yours")
    if not await _has_bridge(session, ship):
        raise Deaf(key="ship-deaf", ship=ship.name)
