# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The fuel plant from the outside (D-189): what it holds, who pours into it,
and whose its pile is.

Anyone who came with coal may pour it in, and nobody takes it back: the fuel
lying where a fuel plant stands is the plant's tank, not the yard's store. The
same answer is given at three doors -- the hand picking it up
(`storage.pick`), a work gathering its materials (`reach`, D-315) and an
automat taking its inputs off the yard (D-342) -- so it is asked here, once
(`off_the_pile`), and nowhere re-decided.

How the plant burns that pile is the pool's business (`energy.produce`).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, travel, world
from src.engine.energy import FUEL_PLANT, EnergyError
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float


async def plant_view(session: AsyncSession, constants: Constants, node: Node) -> dict | None:
    """What the station looks like from the outside: stock, draw, output.

    Supply is a matter of agreement between the city and the haulers, and both
    sides must see the same number -- hence the stock and the hours it lasts.
    """

    #: A look at the station, so the yard is read and not made for the look.
    machines = await world.node_things(session, node)
    plant_names = set(world.station_names(FUEL_PLANT))
    plants = [thing for thing in machines if thing.type_key in plant_names and thing.installed]
    if not plants:
        return None

    fuels: dict[str, float] = constants[R.ENERGY_FUEL_ENERGY]
    stock = sum(amount_float(stack.amount) for stack in machines if stack.type_key in fuels)
    draw = constants[R.ENERGY_COAL_PLANT_FUEL_DRAW] * len(plants)
    return {
        "station": plants[0].type_key,
        "count": len(plants),
        #: What burns here -- every material with a fuel value (D-215).
        "fuel": ", ".join(sorted(fuels)),
        "fuels": sorted(fuels),
        "stock": round(stock, 1),
        #: Per hour: how much it eats and how much it gives while it eats.
        "draw": draw,
        "output": constants[R.ENERGY_COAL_PLANT_RATE] * len(plants),
        "hours_left": round(stock / draw, 1) if draw > 0 else None,
    }


async def fuel(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    quantity: float | None = None,
) -> float:
    """Pour fuel from the hands into the station standing here (D-189).

    Anyone who came with coal may do it: hauling fuel is the supply mechanic
    itself, not a privilege of the authority. There is no way back -- pouring
    in is a handover, otherwise the city's fuel pile would be a common pocket.
    """

    if body.state is not BodyState.ALIVE:
        raise EnergyError(key="energy-dead-loads")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise EnergyError(key="energy-body-off-node")
    view = await plant_view(session, constants, node)
    if view is None:
        raise EnergyError(key="energy-no-station")
    if item.type_key not in view["fuels"]:
        raise EnergyError(
            key="energy-wrong-fuel",
            goods=item.type_key,
            station=view["station"],
            fuel=view["fuel"].lower(),
        )

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise EnergyError(key="energy-fuel-from-hands")
    qty = amount_float(item.amount) if quantity is None else quantity
    if qty <= 0:
        raise EnergyError(key="energy-nothing-to-load")

    yard = await world.node_container(session, node)
    fuel_key = item.type_key
    poured = await world.move_stack(session, item, yard, qty)
    await events.record(
        session,
        EventKind.ENERGY_FUELLED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=fuel_key,
        amount=poured,
    )
    #: Fuel the city ordered hauled pays per unit as it lands (D-248): the
    #: pour is the handover, the engine just watched it happen.
    from src.engine import works_city  # noqa: PLC0415 -- lazy: works_city imports fuel_plant

    await works_city.pay_fuel_delivery(session, constants, node, fuel_key, poured, body.identity_id)
    return poured


async def off_the_pile(session: AsyncSession, constants: Constants, node: Node) -> frozenset[str]:
    """What this place will not give up off its own pile: every fuel, where a
    fuel plant stands (D-189). Nothing anywhere else.

    The hand (`storage.pick`), a work's reach (D-315) and an automat's inputs
    (D-342) all ask this, so none of them can disagree about whose coal the
    plant's pile is.
    """
    if await plant_view(session, constants, node) is None:
        return frozenset()
    return frozenset(constants[R.ENERGY_FUEL_ENERGY])
