# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""How far the hands reach when a work gathers its materials (D-305).

Until now they reached into one place: the body's own pocket, and since D-230
the vessels lying in it. Everything else in the node did not exist for the
work -- the chest two steps away (D-181), the wagon in the yard (D-157), the
harvest on the floor (D-244) -- and putting any of it into a batch meant
hauling it into the hands first, in loads of `inventory.carry_mass` (D-146).
That limit was priced as logistics and was being paid in clicks: it guarded
nothing, because carrying everything across the yard by hand is always
possible, merely slow.

## The three places

* **the pocket** and the vessels in it, as before;
* **one's own convoy**: the vehicle this body is harnessed to, its hold and
  the vessels in it. The convoy is an extension of the hands -- it walks with
  the body and is held by the body's own harness -- so it is reached wherever
  the body works, somebody else's land included. A vehicle standing here under
  **another** harness is not reached: a guest's loaded wagon stays the guest's,
  or calling on the smith would be a way of handing over cargo;
* **the node**, where the body may dispose of the place (`station.may_build` --
  the same mark that opens a chest, D-181): the floor of the house and the open
  ground (D-244), the storages put up here and the vessels in them and in the
  yard.

## The reach of the work is the reach of the hand

Off the node the work takes exactly what the hand could pick up off the floor
(`storage.pick`): what **stands** is not spent -- a machine, a chest, furniture
work rather than burn (D-278) -- and neither is a relic of the Forerunners
(D-232), a station built in place (D-268), or fuel lying where a fuel plant
stands (D-189: that pile is its tank -- and only that pile: a chest standing
beside the plant is not its bunker, and `storage.take` hands its coal over
without a word). The work must not become a way around a refusal the hand
gets -- and since D-248, where the treasury pays for the haul, not a money
pump either.

Narrower than the hand in one place, and by the decision's own gate: the floor
of a place is open to whoever got in (D-204), while the **work** on it needs
the right to dispose of the node. A guest picks the ore up and then works from
their own hands, as before.

Everything here is a **read**: no yard is made, no hold is created, no chest is
given an inside. `craft.plan` and `craft.most` are `readonly=True` commands and
run under the guard that says so (`db/readonly.py`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.db.base import remember
from src.engine import energy, liquid, station, storage, transport, world
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node


@dataclass(frozen=True, slots=True)
class Reach:
    """The containers a work draws from, in two halves.

    Two and not one because the halves answer differently per material: what
    is carried is carried whatever it is, while the place gives up only what a
    hand could lift off it.
    """

    #: The pocket, one's own hold, and the vessels in both.
    carried: tuple[uuid.UUID, ...]
    #: The node's floor and yard, its storages, and the vessels in those. Empty
    #: where the body may not dispose of the place.
    here: tuple[uuid.UUID, ...]
    #: The node's own store -- the floor and the yard themselves, the one
    #: container a fuel plant counts as its tank (D-189).
    pile: uuid.UUID | None
    #: What this place will not give up **off that pile**: fuel where a fuel
    #: plant stands. A chest is not the plant's tank -- `storage.take` hands
    #: its coal over without a word -- so the bar stops at the lid.
    barred: frozenset[str]

    def of(self, catalog: Catalog, name: str) -> tuple[uuid.UUID, ...]:
        """Where this material may be taken from."""
        if not self.here:
            return self.carried
        #: Never material anywhere, so never worth asking which container: a
        #: relic (D-232) and a thing built in place (D-268) cannot be picked
        #: up at all, and so cannot lawfully be lying in a chest either.
        if catalog.recipes.is_relic(name) or catalog.recipes.built(name):
            return self.carried
        if name in self.barred and self.pile is not None:
            return (*self.carried, *(one for one in self.here if one != self.pile))
        return (*self.carried, *self.here)


async def at_work(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> Reach:
    """What this body's hands reach while it works where it stands (D-305).

    Asked once per command and remembered (`db.base.remember`): a pot fills
    five roles and asks five times, and the answer walks the yard, the chests
    and the right to the place each time. The memory dies on any write, so a
    batch that has just emptied a chest asks again.
    """
    return await remember(
        session, ("reach.at_work", body.id), lambda: _walk(session, constants, catalog, body)
    )


async def _walk(session: AsyncSession, constants: Constants, catalog: Catalog, body: Body) -> Reach:
    """The walk itself: the pocket, one's own hold, and the place where it is ours."""
    pocket = await world.body_container(session, body)
    carried = list(await liquid.reach(session, catalog, pocket))

    #: One's own convoy: the harness is the title, and it is the body's own.
    wagon = await transport.harnessed(session, body)
    if wagon is not None:
        hold = await _hold(session, wagon)
        if hold is not None:
            carried.extend(await liquid.reach(session, catalog, hold))

    here: list[uuid.UUID] = []
    pile: uuid.UUID | None = None
    barred: frozenset[str] = frozenset()
    node = None if body.node_id is None else await session.get(Node, body.node_id)
    if node is not None and await station.may_build(session, body, node):
        #: The yard as it stands, never made: this is a read (`world.node_yard`).
        yard = await world.node_yard(session, node)
        if yard is not None:
            pile = yard.id
            here.extend(await liquid.reach(session, catalog, yard))
            for chest in await _chests(session, catalog, yard):
                inside = await storage.inside(session, chest, create=False)
                if inside is not None:
                    here.extend(await liquid.reach(session, catalog, inside))
        #: Fuel lying where a fuel plant stands is loaded, not stored (D-189).
        if await energy.plant_view(session, constants, node) is not None:
            barred = frozenset(constants[R.ENERGY_FUEL_ENERGY])

    held = dict.fromkeys(carried)
    return Reach(
        carried=tuple(held),
        here=tuple(one for one in dict.fromkeys(here) if one not in held),
        pile=pile,
        barred=barred,
    )


async def _chests(session: AsyncSession, catalog: Catalog, yard: Container) -> list[Item]:
    """The storages **put up** in the node (D-278).

    A chest lying on the floor is cargo, and the window says so too
    (`api.commands.views._storages`): the client derives the reach off what it
    was shown (D-225), and the two must agree or the number on the bench would
    promise what the batch refuses.
    """
    return [
        thing
        for thing in await world.contents(session, yard)
        if thing.installed and storage.is_storage(catalog, thing.type_key)
    ]


async def _hold(session: AsyncSession, vehicle: Item) -> Container | None:
    """The vehicle's hold as it stands -- **without** making one.

    `transport.cargo` creates it on first need, and this is asked by the
    forecast: an empty wagon must not get a hold row from a glance.
    """
    stmt = select(Container).where(
        Container.kind == ContainerKind.VEHICLE, Container.owner_id == vehicle.id
    )
    return (await session.execute(stmt)).scalar_one_or_none()
