# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Ships, carts, energy.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _body, _own_item
from src.api.commands.views import _money
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import (
    energy,
    ship,
    transport,
)
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ship import Ship
from src.models.world import Node


@command("energy.grid")
async def _energy_grid(state: dict, db: AsyncSession, message: dict) -> dict:
    """The pool of the city we stand in. An empty pool is visible to all: that is politics
    (D-071).

    A **read**: the pool is brought up to date by the world's own tick, once a
    minute, and not by whoever looks at it. It used to call `produce` here, and
    since that pass took the pool's row for itself and wrote the hour's heat off
    it (D-231, D-232), a glance at the grid would have queued behind every
    charge in the city and moved the world's books besides -- while CLAUDE.md
    says plainly that reads do not write.
    """
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    node = await db.get(Node, body.node_id)
    pool = await energy.pool_of(db, current(), node, create=False)
    if pool is None:
        return {"grid": None}
    city = await db.get(Node, pool.node_id)
    return {
        "grid": {
            "city": city.name if city else "?",
            "stored": round(float(pool.stored), 1),
            "tariff": float(pool.tariff),
        }
    }


@command("energy.charge")
async def _energy_charge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Charge a battery from the pool at the tariff. In person and paid (D-085).

    A battery is a machine (D-179): both the one in hand and the one standing
    here are charged. Whether the thing is reachable is checked by the energy
    engine itself.
    """
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    qty = message.get("amount")
    given = await energy.charge_battery(
        db, current(), body, item, None if qty is None else float(qty)
    )
    return {
        "charged": round(given, 2),
        "charge": round(float(item.charge), 2),
        "money": await _money(db, state["identity_id"]),
    }


@command("energy.plant")
async def _energy_plant(state: dict, db: AsyncSession, message: dict) -> dict:
    """Station of this node: fuel stock, hourly draw and output (D-189)."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    return {"plant": await energy.plant_view(db, current(), node)}


@command("energy.fuel")
async def _energy_fuel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pour fuel into the station standing here. Anyone with coal may (D-189)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    qty = message.get("amount")
    poured = await energy.fuel(
        db,
        current(),
        body,
        item,
        None if qty is None else float(qty),
    )
    return {"fuelled": poured, "goods": item.type_key}


@command("transport.harness")
async def _transport_harness(state: dict, db: AsyncSession, message: dict) -> dict:
    """Harness to a vehicle standing here (D-157)."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    await transport.harness(db, current(), current_catalog(), body, item)
    return {"harnessed": item.type_key}


@command("transport.unharness")
async def _transport_unharness(state: dict, db: AsyncSession, message: dict) -> dict:
    """Unharness. The convoy with its cargo stays standing here."""
    body = await _alive(state, db)
    wagon = await transport.unharness(db, body)
    return {"unharnessed": None if wagon is None else wagon.type_key}


@command("transport.load")
async def _transport_load(state: dict, db: AsyncSession, message: dict) -> dict:
    """Load from the hands into the hold. In person: nothing is moved while on the go."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    qty = message.get("amount")
    carried = await transport.load(
        db,
        current(),
        current_catalog(),
        body,
        item,
        None if qty is None else float(qty),
    )
    return {"loaded": carried}


@command("transport.unload")
async def _transport_unload(state: dict, db: AsyncSession, message: dict) -> dict:
    """Unload from the hold into the hands. The hands limit does not go anywhere."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    qty = message.get("amount")
    carried = await transport.unload(
        db,
        current(),
        current_catalog(),
        body,
        item,
        None if qty is None else float(qty),
    )
    return {"unloaded": carried}


@command("ship.found")
async def _ship_found(state: dict, db: AsyncSession, message: dict) -> dict:
    """Lay a ship's foundation at a spaceport (D-202).

    The foundation is written off at once, the first node -- the base and the
    connector in one -- arrives on schedule, like every long-running work.
    """
    body = await _alive(state, db)
    job = await ship.found(db, current(), body, str(message.get("name") or "Корабль"))
    return {"keel": str(job.id), "ready_at": job.run_at.isoformat()}


@command("ship.extend")
async def _ship_extend(state: dict, db: AsyncSession, message: dict) -> dict:
    """Lay one more node aboard, joined to the one you are standing in."""
    body = await _alive(state, db)
    job = await ship.extend(db, current(), body)
    return {"keel": str(job.id), "ready_at": job.run_at.isoformat()}


async def _ship_of(db: AsyncSession, body: Body, asked: str | None) -> Ship:
    """Which ship the command is about: the named one, else the one you stand in."""
    if asked:
        found = await db.get(Ship, uuid.UUID(asked))
        if found is None:
            raise Refused("нет такого корабля")
        return found
    aboard = await ship.aboard_of(db, body)
    if aboard is None:
        raise Refused("вы не на борту: назовите корабль или поднимитесь на него")
    return aboard


@command("ship.view")
async def _ship_view(state: dict, db: AsyncSession, message: dict) -> dict:
    """The ship's summary: thrust, mass, thrust-to-mass and the price of every route.

    Remote, and shown **before** undocking: a refusal by mass must not be a
    surprise sprung after the hold is loaded (D-202).
    """
    body = await _alive(state, db)
    asked = message.get("ship")
    #: `fleet` asks for every hull of one's own whatever the body is standing
    #: in. The ground console wants exactly that (D-242), and it may perfectly
    #: well stand in a compartment of the flagship -- where the plain reading
    #: would collapse to the one hull underfoot and hide the rest of the fleet.
    if not asked and (message.get("fleet") or await ship.aboard_of(db, body) is None):
        mine = await ship.ships_of(db, body.identity_id)
        return {"ships": [await _seen(db, body, one) for one in mine]}
    vessel = await _ship_of(db, body, asked)
    return {"ships": [await _seen(db, body, vessel)]}


async def _seen(db: AsyncSession, body: Body, vessel: Ship) -> dict:
    """The hull's summary, plus whose it is.

    Ownership is the **viewer's** fact, not the ship's, so it is added here
    rather than inside `profile`: a guest standing in somebody's hold reads the
    same card and is offered none of the buttons that would be refused (D-240).
    """
    card = await ship.profile(db, current(), current_catalog(), vessel)
    return {**card, "yours": vessel.owner_identity_id == body.identity_id}


@command("ship.rename")
async def _ship_rename(state: dict, db: AsyncSession, message: dict) -> dict:
    """Name the ship. The nameplate is nailed on the spot, like a plot's (D-240)."""
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    await ship.rename(db, body, vessel, str(message.get("name", "")))
    return {"renamed": str(vessel.id), "name": vessel.name}


@command("ship.arrange")
async def _ship_arrange(state: dict, db: AsyncSession, message: dict) -> dict:
    """Put the ship's rooms into their cells on the ship's own map (D-240).

    The whole arrangement at once, node key -> `[x, y]` in cells: a hull is laid
    out as a shape, and half a shape is one nobody asked for. Nothing about the
    graph changes -- rooms stay joined exactly as they were laid.
    """
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    asked = message.get("spots")
    if not isinstance(asked, dict):
        raise Refused("нужна раскладка: ключ узла — клетка")
    moved = await ship.arrange(db, body, vessel, asked)
    return {"arranged": str(vessel.id), "moved": moved}


async def _target(db: AsyncSession, message: dict) -> Node:
    """The node an order names, by key. One reading for every leg that takes one."""
    node = (
        (await db.execute(select(Node).where(Node.key == str(message.get("port") or ""))))
        .scalars()
        .first()
    )
    if node is None:
        raise Refused("нет такого узла")
    return node


@command("ship.ascend")
async def _ship_ascend(state: dict, db: AsyncSession, message: dict) -> dict:
    """Climb to the orbit of the planet under the pad (D-245).

    What used to be `ship.undock`, and what used to be instant and free. It is
    a leg now: it takes hours by the planet's gravity, it burns fuel, and it
    can be turned back with `ship.recall`.
    """
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    job = await ship.ascend(db, current(), current_catalog(), body, vessel)
    return {"flight": str(job.id), "arrives_at": job.run_at.isoformat()}


@command("ship.fly")
async def _ship_fly(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cross to another planet's orbit. Fuel now, arrival by a journal job."""
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    job = await ship.fly(db, current(), current_catalog(), body, vessel, await _target(db, message))
    return {"flight": str(job.id), "arrives_at": job.run_at.isoformat()}


@command("ship.land")
async def _ship_land(state: dict, db: AsyncSession, message: dict) -> dict:
    """Come down from orbit onto a spaceport of the planet below (D-245)."""
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    job = await ship.land(
        db, current(), current_catalog(), body, vessel, await _target(db, message)
    )
    return {"flight": str(job.id), "arrives_at": job.run_at.isoformat()}


@command("ship.recall")
async def _ship_recall(state: dict, db: AsyncSession, message: dict) -> dict:
    """Turn a passage back to the pier it cast off from (D-242).

    Not a recomputation of the passage -- that stays settled at the casting off
    (D-201) -- but a second one, as long as the first has been under way.
    """
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    job = await ship.recall(db, current(), current_catalog(), body, vessel)
    return {"recalled": str(job.id), "arrives_at": job.run_at.isoformat()}


@command("ship.ports")
async def _ship_ports(state: dict, db: AsyncSession, message: dict) -> dict:
    """Where a ship may actually land. Public: ports are not a secret.

    Only the ones whose beacon shines (D-232): a frozen or unpowered port is
    not a destination, and a console that offered it would be offering a
    flight that ends in a refusal.
    """
    await _alive(state, db)
    return {
        "ports": [
            {"node": port.key, "name": port.name, "planet": port.planet.value}
            for port in await ship.lit_ports(db, current())
        ]
    }
