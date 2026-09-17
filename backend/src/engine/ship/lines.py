# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the lines from a machine to the vessels it drinks from and pours
into (D-288, D-340).

**Ports.** A machine that eats or gives a liquid has ports, and they are not a
new thing in the vault: an engine's port is the fuel class it burns
(`ship.thrust` names the engine, the ship-fuel class names the fuel), the life
support's is the oxygen it breathes for the crew, the hydroponics' is the
oxygen its beds breathe out. The machine that makes the air -- the station of
the recipe whose output is the air, and the automat `auto.covers` stands in
for it -- has the ports of that recipe: its liquid inputs drink, its liquid
output pours, and its liquid byproduct pours and lets out what finds no room
(D-340). The automat drinks its lubricant through a port of its own. A port
has a name -- `fuel`, `oxygen`, `water`, `hydrogen`, `lube` -- and the name is
what a line is keyed by.

**Which way.** An inlet drinks from its vessels in line order. An outlet pours
into them in line order, and when every one of them is full the machine
stands with its backlog (D-253: the well does not spill). A vent pours the
same way and lets its surplus go: the breath of the hydroponic beds into the
compartment's air -- a bed does not wait for a cylinder -- and a vent gas, the
hydrogen of electrolysis, where `engine.vent` sends it (D-340): overboard from
a sealed hull, where it must not stop the air being made (owner, 2026-09-13),
and nowhere from a hull under a sky with air, where the vent line is its one
place and holds the machine like an outlet.

**Lines.** A line is one vessel standing on one port, in a chosen order. A
port with **no line at all** reaches nothing (D-288 as amended 2026-09-04):
the line is a duty, not an upgrade -- a hull nobody has plumbed has no fuel to
burn and no air to breathe, however full its tanks.

**What stands on a line.** Only an **installed** vessel aboard: a tank, a
canister or a cylinder put up in a compartment the way furniture is
(`station.place`). In the hands, on the floor or packed in a chest it is
luggage, and no line reaches it. One word in place of the depth-of-stowage
rule of D-234 -- and one the crew can see on the thing itself.

**The hull is one building.** Lines cross compartments: the rooms aboard are
the sub-nodes of one delegate node, and a line from the engine room to a tank
in the hold is the ordinary case.

Nothing here is locked: this module says **where** a port may draw and pour,
and whoever works through a port locks every vessel on it before a stack in them
(`liquid.lock_vessels`) and keeps to the vessels that lock found in place
(`Plumbing.keeping`). The floor of the ship package -- it
asks `belonging` and the catalog and nothing above itself: `physics` burns
through it, `oxygen` breathes through it, `craft` and `automat` work through
it and `feed`, the orders, writes through it.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants
from src.constants import registry as R
from src.constants.catalog import Recipe
from src.engine import storage, world
from src.engine.ship._base import AIR, FUEL, HYDROPONICS, LIFE_SUPPORT, LUBE
from src.engine.ship.belonging import nodes_of, of_node
from src.models.inventory import Container, ContainerKind, Item
from src.models.lines import FeedLine, VesselName
from src.models.ship import Ship
from src.models.world import Node, is_aboard

#: The port names that are not a liquid's own key: keys of the schema
#: (`FeedLine.port`), never words of the locale. A recipe's port is named by
#: its liquid (`water`, `hydrogen`), so these are the class-bound ones.
FUEL_PORT = "fuel"
AIR_PORT = "oxygen"
LUBE_PORT = "lube"

#: Which way a port's liquid runs (D-340). Keys of the wire, never words.
INLET = "in"
OUTLET = "out"
VENT = "vent"


@dataclass(frozen=True, slots=True)
class Port:
    """One port of a machine: its name, the liquids it takes, by goods key,
    and which way they run."""

    name: str
    liquids: tuple[str, ...]
    way: str = INLET

    @property
    def pours(self) -> bool:
        """An outlet or a vent: the machine gives this liquid, it does not take it."""
        return self.way != INLET


def fuel_port() -> Port:
    """The engines' port: what a passage burns, by the fuel class (D-230, D-252)."""
    return Port(FUEL_PORT, tuple(world.station_names(FUEL)))


def air_port() -> Port:
    """The life support's port: the one air there is (D-233)."""
    return Port(AIR_PORT, (AIR,))


def breath_port() -> Port:
    """The hydroponics' port: what the beds breathe out (D-288). A vent, not
    an outlet: a bed does not stand for a full cylinder, and what finds no
    room stays in the compartment's air (D-340)."""
    return Port(AIR_PORT, (AIR,), VENT)


def lube_port() -> Port:
    """The automat's lubricant, by the class (D-253): drunk by the hour."""
    return Port(LUBE_PORT, tuple(world.station_names(LUBE)))


def air_recipe(catalog: Catalog) -> Recipe | None:
    """The recipe that makes the air (D-288): found by its output, the one
    substance the engine names, never by the name of a machine (D-215)."""
    try:
        return catalog.recipes.recipe(AIR)
    except ConstantError:  # pragma: no cover -- a test book without the air
        return None


def recipe_ports(catalog: Catalog, recipe: Recipe) -> tuple[Port, ...]:
    """A recipe's liquids as ports: inputs drink, the output pours, a
    byproduct vents (D-340). What is not a liquid has no port -- it lies."""
    book = catalog.recipes
    found: list[Port] = [
        Port(name, (name,))
        for name in dict.fromkeys(book.resolve(one) for one in recipe.inputs)
        if book.is_liquid(name)
    ]
    if book.is_liquid(recipe.type_key):
        found.append(Port(recipe.type_key, (recipe.type_key,), OUTLET))
    found.extend(
        Port(name, (name,), VENT)
        for name in dict.fromkeys(book.resolve(one) for one in recipe.byproduct)
        if book.is_liquid(name)
    )
    return tuple(found)


def _air_machine(
    constants: Constants, catalog: Catalog, type_key: str
) -> tuple[Recipe | None, bool]:
    """The air recipe, if a machine of this kind makes the air, and whether it
    does so as an automat.

    The station is the air recipe's own; the automat is whatever `auto.covers`
    stands in for that station with. Both by data: a second electrolyser or a
    second reactor is a line in the vault.
    """
    recipe = air_recipe(catalog)
    if recipe is None or not recipe.station:
        return None, False
    station = catalog.recipes.resolve(recipe.station)
    automat = type_key in constants[R.AUTO_COVERS].get(station, {})
    if automat or type_key in world.station_names(station):
        return recipe, automat
    return None, False


def ports_of(constants: Constants, catalog: Catalog, type_key: str) -> tuple[Port, ...]:
    """The ports a machine of this kind has. Empty for one that runs no liquid.

    By table, by class and by recipe, never by name (D-215): an engine is
    whatever `ship.thrust` names, the life support and the hydroponics
    whatever stands in their classes, the air machine whatever the air
    recipe's station and its automat are.
    """
    found: list[Port] = []
    if type_key in constants[R.SHIP_THRUST]:
        found.append(fuel_port())
    if type_key in world.station_names(LIFE_SUPPORT):
        found.append(air_port())
    if type_key in world.station_names(HYDROPONICS):
        found.append(breath_port())
    recipe, automat = _air_machine(constants, catalog, type_key)
    if recipe is not None:
        found.extend(recipe_ports(catalog, recipe))
        if automat:
            found.append(lube_port())
    return tuple(found)


def port_of(constants: Constants, catalog: Catalog, type_key: str, name: str) -> Port | None:
    """The named port of a machine of this kind, or nothing."""
    return next(
        (port for port in ports_of(constants, catalog, type_key) if port.name == name), None
    )


def plumbed_for(
    constants: Constants, catalog: Catalog, type_key: str, output: str
) -> tuple[Port, ...]:
    """The ports a machine of this kind works **this output** through aboard.

    Only the air is plumbed (D-340): the electrolyser making oxygen and the
    reactor programmed with it. The same machine making anything else --
    oxidiser at the electrolyser, spirit in the reactor -- works room by room
    as before, and says nothing about lines. Empty -- not plumbed.
    """
    recipe, automat = _air_machine(constants, catalog, type_key)
    if recipe is None or catalog.recipes.resolve(output) != recipe.type_key:
        return ()
    return (*recipe_ports(catalog, recipe), *((lube_port(),) if automat else ()))


async def hold_of(session: AsyncSession, ship: Ship, *, fresh: bool = False) -> list[Item]:
    """What lies and stands in the rooms aboard -- one level, no insides.

    The lines want the vessels themselves and the machines beside them;
    `physics._things` walks into the vessels as well, for the mass. Read here
    without that second level, so the oxygen floor can ask for a reading of
    the hull without pulling physics in behind it.

    `fresh` rereads the rows **in place** -- for a caller that read the hold,
    then waited for a lock, and must not go on by what it read before the
    wait (`oxygen._breathe` waits there for the crew's rows). A plain query
    would not do it: a row already in the session is handed back with the
    attributes it was loaded with, and whether the thing still **stands** is
    one of them -- so a system unbolted during the wait went on breathing for
    a crew it no longer connects (D-288, D-308).
    """
    nodes = await nodes_of(session, ship)
    if not nodes:  # pragma: no cover -- a ship always has its connector
        return []
    yards = select(Container.id).where(
        Container.kind == ContainerKind.NODE, Container.owner_id.in_([node.id for node in nodes])
    )
    stmt = select(Item).where(Item.container_id.in_(yards))
    if fresh:
        stmt = stmt.execution_options(populate_existing=True)
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def hull_under(session: AsyncSession, node: Node | None) -> Ship | None:
    """The hull a room belongs to, or nothing for a room on the ground."""
    if node is None or not is_aboard(node):
        return None
    return await of_node(session, node)


def vessels_among(catalog: Catalog, things: Sequence[Item]) -> list[Item]:
    """The installed vessels in a reading of the hold, in id order: what a
    line may stand on. A reading that walked into the vessels (`_things`) is
    fine too -- what lies inside one is never installed."""
    return sorted(
        (one for one in things if one.installed and storage.is_vessel(catalog, one.type_key)),
        key=lambda one: one.id,
    )


async def hull_vessels(
    session: AsyncSession, catalog: Catalog, ship: Ship, *, things: Sequence[Item] | None = None
) -> list[Item]:
    """The installed vessels aboard, in id order."""
    hold = things if things is not None else await hold_of(session, ship)
    return vessels_among(catalog, hold)


async def lines_of(session: AsyncSession, machine_id: uuid.UUID, port: str) -> list[FeedLine]:
    """The rows of one port, in rank order."""
    rows = await session.execute(
        select(FeedLine)
        .where(FeedLine.machine_item_id == machine_id, FeedLine.port == port)
        .order_by(FeedLine.rank, FeedLine.id)
    )
    return list(rows.scalars().all())


async def lines_for(
    session: AsyncSession, machine_ids: Sequence[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], list[FeedLine]]:
    """Every row of these machines at once, by (machine, port), each in rank
    order -- one query for a reading of the whole hull."""
    if not machine_ids:
        return {}
    rows = await session.execute(
        select(FeedLine)
        .where(FeedLine.machine_item_id.in_(list(machine_ids)))
        .order_by(FeedLine.rank, FeedLine.id)
    )
    found: dict[tuple[uuid.UUID, str], list[FeedLine]] = {}
    for row in rows.scalars().all():
        found.setdefault((row.machine_item_id, row.port), []).append(row)
    return found


async def sources(
    session: AsyncSession, machine: Item, port: str, hull: Sequence[Item]
) -> list[Item]:
    """The vessels this port draws from or pours into, in order: its lines,
    and nothing else.

    `hull` is the installed vessels aboard (`hull_vessels`). A line whose
    vessel is not among them -- taken down, carried off, packed away -- is
    skipped, not obeyed: the row is a memory, and what answers is what stands.
    A port without a line reaches **nothing** (D-288 as amended 2026-09-04):
    the line is a duty, not an upgrade, and a crew beside full cylinders
    nobody plumbed has not been given air. The rows stay, so the bottle put
    back stands on its line again -- and the reading (`feed.view`) says
    exactly what this does, because it filters the rows the same way.
    """
    rows = await lines_of(session, machine.id, port)
    aboard = {one.id: one for one in hull}
    return [aboard[row.vessel_item_id] for row in rows if row.vessel_item_id in aboard]


async def port_vessels(
    session: AsyncSession,
    catalog: Catalog,
    ship: Ship,
    machines: Sequence[Item],
    port: str,
    *,
    things: Sequence[Item] | None = None,
) -> list[Item]:
    """The vessels on one port of these machines, in line order, each once.

    Several machines of one kind -- two engines, two hydroponic units in one
    bay -- share one port and reach the union of their lines, each vessel
    once, in the order the first machine names it. No machine at all reaches
    nothing, and so does a machine with no line (D-288 as amended 2026-09-04).
    """
    if not machines:
        return []
    hull = await hull_vessels(session, catalog, ship, things=things)
    order: list[Item] = []
    seen: set[uuid.UUID] = set()
    for machine in machines:
        for vessel in await sources(session, machine, port, hull):
            if vessel.id not in seen:
                seen.add(vessel.id)
                order.append(vessel)
    return order


async def stacks_for(
    session: AsyncSession,
    catalog: Catalog,
    ship: Ship,
    machines: Sequence[Item],
    port: Port,
    *,
    things: Sequence[Item] | None = None,
) -> list[Item]:
    """The stacks of the port's liquids these machines can reach, in line order.

    Unlocked: the spender relocks by id (`stock.lock_items`), so a stale
    reading cannot overspend.
    """
    order = await port_vessels(session, catalog, ship, machines, port.name, things=things)
    return await stacks_in(session, order, port.liquids)


async def stacks_in(
    session: AsyncSession, vessels: Sequence[Item], liquids: Sequence[str]
) -> list[Item]:
    """The stacks of these liquids inside these vessels, in the vessels' order
    and then by id. Unlocked."""
    if not vessels or not liquids:
        return []
    box_of = await insides_of(session, vessels)
    rank = {box_of[vessel.id]: place for place, vessel in enumerate(vessels) if vessel.id in box_of}
    if not rank:
        return []
    found = (
        (
            await session.execute(
                select(Item).where(
                    Item.container_id.in_(list(rank)), Item.type_key.in_(tuple(liquids))
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted(found, key=lambda one: (rank[one.container_id], one.id))


async def insides_of(session: AsyncSession, vessels: Sequence[Item]) -> dict[uuid.UUID, uuid.UUID]:
    """Vessel id -> the id of the storage inside it, for the vessels that have
    one. Read, never made: an empty vessel that was never filled has none."""
    if not vessels:
        return {}
    insides = (
        (
            await session.execute(
                select(Container).where(
                    Container.kind == ContainerKind.STORAGE,
                    Container.owner_id.in_([vessel.id for vessel in vessels]),
                )
            )
        )
        .scalars()
        .all()
    )
    return {box.owner_id: box.id for box in insides}


async def replace(
    session: AsyncSession, machine: Item, port: str, vessel_ids: Sequence[uuid.UUID]
) -> int:
    """Write the port's lines afresh: these vessels, in this order. Empty --
    the port reaches nothing. The caller holds the machine's row."""
    await session.execute(
        delete(FeedLine).where(FeedLine.machine_item_id == machine.id, FeedLine.port == port)
    )
    for rank, vessel_id in enumerate(vessel_ids):
        session.add(
            FeedLine(machine_item_id=machine.id, port=port, vessel_item_id=vessel_id, rank=rank)
        )
    await session.flush()
    return len(vessel_ids)


@dataclass(frozen=True, slots=True)
class Plumbing:
    """How one machine aboard is plumbed for one output, read at one moment."""

    ship: Ship
    machine: Item
    #: The inlet liquids, each with the storages inside the vessels on its
    #: line, in line order: what the gathering reaches instead of the hands.
    inlets: dict[str, tuple[uuid.UUID, ...]]
    #: The outlet and vent liquids, each with the vessels on its line in order.
    outlets: dict[str, list[Item]]
    vents: dict[str, list[Item]]
    #: The inlet and outlet ports with no vessel standing on their line.
    dry: tuple[Port, ...]
    #: Every vessel any of the ports reaches, each once, in id order: what a
    #: machine working on its lines locks before it reads a stack in them.
    vessels: tuple[Item, ...] = ()

    def keeping(self, vessels: Collection[uuid.UUID], insides: Collection[uuid.UUID]) -> Plumbing:
        """The same plumbing with only these vessels on its lines, and only
        these storages inside them: what the lock on the vessels found still
        standing where this reading saw them (`liquid.lock_vessels`). A port
        whose every vessel went reaches nothing for the rest of the transaction,
        as the next reading will find it (D-288 as amended 2026-09-04); the dry
        ports stay as read -- the inlets keep storages rather than vessels, and
        cannot say which port a vessel stood on."""
        return Plumbing(
            ship=self.ship,
            machine=self.machine,
            inlets={
                name: tuple(box for box in boxes if box in insides)
                for name, boxes in self.inlets.items()
            },
            outlets={
                name: [one for one in found if one.id in vessels]
                for name, found in self.outlets.items()
            },
            vents={
                name: [one for one in found if one.id in vessels]
                for name, found in self.vents.items()
            },
            dry=self.dry,
            vessels=tuple(one for one in self.vessels if one.id in vessels),
        )


async def plumbing_of(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    machine: Item | None,
    output: str,
) -> Plumbing | None:
    """The plumbing a batch of `output` at this machine works through, or
    `None`: a machine on the ground, a machine not plumbed for this output, or
    no machine at all. A read: nothing is locked and nothing is made."""
    if machine is None or not machine.installed:
        return None
    ports = plumbed_for(constants, catalog, machine.type_key, output)
    if not ports:
        return None
    yard = await session.get(Container, machine.container_id)
    if yard is None or yard.kind is not ContainerKind.NODE:
        return None
    hull = await hull_under(session, await session.get(Node, yard.owner_id))
    if hull is None:
        return None
    hold = await hold_of(session, hull)
    reached = {
        port.name: await port_vessels(session, catalog, hull, [machine], port.name, things=hold)
        for port in ports
    }
    insides = await insides_of(
        session, [vessel for port in ports if not port.pours for vessel in reached[port.name]]
    )
    return Plumbing(
        ship=hull,
        machine=machine,
        inlets={
            liquid_name: tuple(
                insides[vessel.id] for vessel in reached[port.name] if vessel.id in insides
            )
            for port in ports
            if not port.pours
            for liquid_name in port.liquids
        },
        outlets={
            liquid_name: reached[port.name]
            for port in ports
            if port.way == OUTLET
            for liquid_name in port.liquids
        },
        vents={
            liquid_name: reached[port.name]
            for port in ports
            if port.way == VENT
            for liquid_name in port.liquids
        },
        dry=tuple(port for port in ports if port.way != VENT and not reached[port.name]),
        vessels=tuple(
            sorted(
                {vessel.id: vessel for found in reached.values() for vessel in found}.values(),
                key=lambda one: one.id,
            )
        ),
    )


async def names_of(session: AsyncSession, vessel_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    """The names the owner gave these vessels (D-288), by vessel id."""
    if not vessel_ids:
        return {}
    rows = await session.execute(
        select(VesselName).where(VesselName.vessel_item_id.in_(list(vessel_ids)))
    )
    return {row.vessel_item_id: row.name for row in rows.scalars().all()}
