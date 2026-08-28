# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Oxygen: the second scale of survival, and only where there is no air
(D-233, D-234).

Warmth (`engine.frost`) is the first scale and this is the second, deliberately
built to the same shape so that two scales do not become two mechanics: a
property of the **planet** decides whether the question arises at all, and on
Terra and Aurora it never does. There is air there, the reading is empty, and
nothing in this module is ever asked.

Where there is none -- in flight and on Pyroxis -- two things breathe, and they
breathe from different places:

* **a hull** breathes what stands in it. Oxygen is a liquid (D-230) and exists
  only inside a vessel, so the ship's reserve is what lies in any vessel
  **standing in a compartment** -- a tank, a canister, a bottle. Wider than the
  fuel a passage burns, and on purpose: the engines are plumbed to the tanks and
  reach nothing else, while the life support is a machine standing in a room,
  and what a crew carries to it, it uses. Narrower than the hold, and for the
  same reason: a canister packed into a chest is stowed cargo, and nothing
  rummages through luggage. The crew draws `oxygen.crew_draw` an hour a head,
  and
  the **life support makes air** to cover it: water out of the same tanks plus
  charge out of the batteries of its own node, by the vault's own recipe for
  «Кислород». One system covers as many people as it holds
  (`ship.life_support_crew`) -- more crew than that wants a second system, the
  same number that has always decided how many the ship may carry (D-202).
  What it makes is **breathed, never stored**: the vessels aboard are what the
  crew lives on when the water or the charge runs out, and filling them -- or a
  cylinder for going outside -- is deliberate work at an «Электролизёр», which
  is the very recipe this runs by;
* **a body outside** breathes a cylinder, and only through a suit. A cylinder
  in the bag gives nothing by itself: the suit is what connects the body to it
  (D-234), and a bare body on an airless node dies however many cylinders it
  carries. Outside the draw is `oxygen.body_draw` -- five times the hull's,
  because the work is harder and the suit leaks.

## Why there is no reserve on the body

Because a reserve on the body would be a second place to keep the same thing.
The cylinder is the reserve, it is a stack of a liquid like any other, it can
be filled, carried, dropped and traded, and the engine keeps no copy of how
full it is. What the body keeps is a **stamp** -- the moment its breathing was
last settled -- the way `body.warmth_at` is a stamp for the cold.

## Dying is arithmetic, never a surprise

The engine refuses the step onto an airless node without a suit and without a
cylinder with something in it (D-233): death by ignorance in one click is not
this world's way. After that the countdown is on the screen the whole time --
the cylinder's units and the ship's tanks are both ordinary readings -- and
what kills is the mistake somebody watched, not the door.

One settling of grace is deliberate: a stretch the oxygen only half covered
drains the reserve to nothing and kills nobody. It is the next stretch, begun
with nothing at all, that ends the body. Otherwise a tick landing a second
after the last unit was spent would be indistinguishable from suffocation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants
from src.constants import registry as R
from src.db.base import remember
from src.engine import events, stock, world
from src.engine import ship as vessels
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.gear import Equipped
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.ship import Ship
from src.models.world import Node, Planet
from src.units import (
    AMOUNT_SCALE,
    ROUND_MASS,
    ROUND_RATIO,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
)

#: The planet's own property, written into its node on the space layer by the
#: seed (D-234) -- beside «мерзлота» and «пекло», and read the same way.
AIRLESS = "без воздуха"

#: What is breathed. A single name rather than a class, because it is a single
#: substance: D-215 binds behaviour to classes so that a second stove or a
#: second engine is data, and there is no second air.
AIR = "Кислород"
#: What the life support turns into air, together with charge. Both come from
#: the vault's recipe for «Кислород», never from a number here.
WATER = "Вода"
ENERGY = "Энергия"

#: The class that connects a body to a cylinder. Without one worn, a cylinder
#: is luggage (D-234).
SUIT = "Скафандр"

#: Amounts split into thousandths, so "was there enough" must tolerate the last
#: digit -- otherwise exactly enough oxygen turns out to be short.
_EPS = 1 / AMOUNT_SCALE


class OxygenError(Refusal):
    pass


class NoAir(OxygenError):
    """Nothing to breathe where the step leads, and nothing to breathe it from."""


@dataclass(frozen=True, slots=True)
class Breath:
    """What one settling of a body's breathing did."""

    #: Units of oxygen the body can still reach after the settling.
    left: float
    #: Hours of the elapsed stretch nothing covered. Above zero means the body
    #: was breathing vacuum, and that is what kills.
    uncovered: float


# --- the planet and the node --------------------------------------------------


async def airless_planets(session: AsyncSession) -> frozenset[Planet]:
    """Which planets have no air of their own. Four rows, one reading."""

    async def read() -> frozenset[Planet]:
        spheres = (
            (
                await session.execute(
                    select(Node).where(Node.key.in_([planet.value for planet in Planet]))
                )
            )
            .scalars()
            .all()
        )
        return frozenset(
            sphere.planet for sphere in spheres if (sphere.properties or {}).get(AIRLESS)
        )

    return await remember(session, ("airless_planets",), read)


async def free_air(session: AsyncSession, node: Node) -> bool:
    """Whether one simply breathes here, with nothing to spend and nothing to wear.

    Ground: the planet's own air, and Terra and Aurora both have it (D-232 --
    a leaky dome is not a vacuum). Aboard: the air is free while the hull sits
    at a port of a planet that has some, because then the hatch may as well be
    open. Undocked -- in flight -- there is nothing outside to open onto, and
    the hull is on its own however Terran the port it left was.
    """
    airless = await airless_planets(session)
    if not vessels.is_aboard(node):
        return node.planet not in airless
    ship = await vessels.of_node(session, node)
    if ship is None:  # pragma: no cover -- an aboard node always has its ship
        return False
    return not await sealed(session, ship)


async def sealed(session: AsyncSession, ship: Ship) -> bool:
    """Whether this hull has to make its own air: in flight, or down on an airless world."""
    if ship.docked_node_id is None:
        return True
    port = await session.get(Node, ship.docked_node_id)
    return port is None or port.planet in await airless_planets(session)


# --- what a hull holds ---------------------------------------------------------


async def breathable_stacks(
    session: AsyncSession, ship: Ship, *what: str, things: list[Item] | None = None
) -> list[Item]:
    """The named liquids in any vessel **standing in a compartment**.

    Wider than the fuel a passage burns, and narrower than "everything aboard".
    Both edges are meant:

    * fuel goes from the tanks and nowhere else (D-230), because the engines are
      plumbed to them: a canister in the hold weighs and does not burn. Air and
      the water it is made of are plumbed nowhere -- the life support is a
      machine standing in a room, and what a crew carries to it, it uses. A crew
      suffocating beside a hold full of oxygen because the bottles were the
      wrong shape is not a rule, it is a bug with an explanation;
    * a vessel **packed into a chest** is stowed cargo, and the system does not
      reach into somebody's luggage for it. It is the same rule one step along,
      so it is said out loud here and in D-240 rather than left to be discovered
      by a crew that put the spare oxygen away tidily.

    Hence exactly one level: what stands in the room, and what is inside it.
    `things` is that reading when the caller already has it (`ship._things`
    walks precisely those two levels).
    """
    hold = things if things is not None else await vessels._things(session, ship)
    wanted = set(what)
    return sorted((one for one in hold if one.type_key in wanted), key=lambda one: str(one.id))


async def reserve(session: AsyncSession, ship: Ship) -> float:
    """Oxygen the crew can actually breathe: what lies in the vessels aboard."""
    stacks = await breathable_stacks(session, ship, AIR)
    return sum(amount_float(stack.amount) for stack in stacks)


async def water_aboard(
    session: AsyncSession, ship: Ship, *, things: list[Item] | None = None
) -> float:
    """Water aboard: what the life support turns into air."""
    stacks = await breathable_stacks(session, ship, WATER, things=things)
    return sum(amount_float(stack.amount) for stack in stacks)


async def _liquids(
    session: AsyncSession, ship: Ship, *, things: list[Item] | None = None
) -> tuple[float, float]:
    """Air and water at once, in **one** reading of the hold.

    The console asks both of every hull it lists, and the walk into a vessel is
    three joins: asking twice was the same fan-out `profile` was cut down for
    once already (review 2026-08-23).
    """
    stacks = await breathable_stacks(session, ship, AIR, WATER, things=things)
    air = sum(amount_float(one.amount) for one in stacks if one.type_key == AIR)
    water = sum(amount_float(one.amount) for one in stacks if one.type_key == WATER)
    return air, water


def _per_unit(catalog: Catalog, what: str) -> float:
    """How much of `what` one unit of air costs, by the vault's recipe.

    Read from the catalog rather than written here: the electrolysis line is
    content (D-065), and the life support is that line running by itself.
    """
    try:
        made = catalog.recipes.recipe(AIR)
    except ConstantError:  # pragma: no cover -- the vault always knows the air
        return 0.0
    return float(made.amounts.get(what, 0.0))


def hull_draw(constants: Constants, crew: int) -> float:
    """What a crew of this size breathes an hour aboard."""
    return crew * constants[R.OXYGEN_CREW_DRAW]


async def hull_output(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    things: list[Item] | None = None,
    water: float | None = None,
) -> float:
    """What the life support can actually make an hour, here and now.

    Three ceilings, and the reading must respect all three or it is a lie about
    the one refusal it exists for:

    * **the systems** -- `life_support` already says how many people they hold
      (D-202), and a system breathes for exactly as many. A crew too big for
      them starts to suffocate for the very reason such a ship may not cast off;
    * **the water** in the tanks, and
    * **the charge** in the batteries of the ship's rooms.

    A hull with a system and empty water tanks makes nothing, and if this said
    otherwise the console would draw a full bar and a calm rate right up to the
    tick that starts killing -- which is exactly the death by surprise the whole
    module is built against.
    """
    holds = await vessels.life_support(session, constants, ship, things=things)
    made = holds * constants[R.OXYGEN_CREW_DRAW]
    if made <= 0:
        return 0.0
    per_water = _per_unit(catalog, WATER)
    if per_water > 0:
        have = water if water is not None else await water_aboard(session, ship)
        made = min(made, have / per_water)
    per_energy = _per_unit(catalog, ENERGY)
    if per_energy > 0:
        made = min(made, await _charge_aboard(session, constants, ship) / per_energy)
    return max(0.0, made)


async def _charge_aboard(session: AsyncSession, constants: Constants, ship: Ship) -> float:
    """How much charge stands in the ship's rooms. A **read**: nothing is spent.

    Read straight off the stacks rather than through `energy.batteries_in`:
    that one creates the room's yard where there is none, and a reading may not
    write (CLAUDE.md).
    """
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    rooms = await vessels.nodes_of(session, ship)
    if not rooms:  # pragma: no cover -- a ship always has its connector
        return 0.0
    cells = (
        (
            await session.execute(
                select(Item)
                .join(Container, Container.id == Item.container_id)
                .where(
                    Container.kind == ContainerKind.NODE,
                    Container.owner_id.in_([room.id for room in rooms]),
                    Item.type_key.in_(world.station_names(energy.BATTERY)),
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(energy.charge_of(constants, cell) * amount_float(cell.amount) for cell in cells)


# --- what a body carries -------------------------------------------------------


async def suited(session: AsyncSession, catalog: Catalog, body: Body) -> bool:
    """Whether a suit is worn. Not carried -- worn: the suit is the connection."""
    worn = (
        (
            await session.execute(
                select(Item)
                .join(Equipped, Equipped.item_id == Item.id)
                .where(Equipped.body_id == body.id)
            )
        )
        .scalars()
        .all()
    )
    suits = world.station_names(SUIT)
    return any(catalog.recipes.resolve(thing.type_key) in suits for thing in worn)


async def cylinders(session: AsyncSession, body: Body) -> list[Item]:
    """The oxygen a body can actually breathe: what lies inside vessels in its hands.

    Inside, not among: a liquid exists only in a vessel (D-230), so this is the
    stacks of air in the storages of the things in the pocket -- and a vessel
    standing in the node is somebody's property of the place, not this body's
    breath.
    """
    pocket = await world.body_container(session, body)
    carried = select(Item.id).where(Item.container_id == pocket.id)
    insides = select(Container.id).where(
        Container.kind == ContainerKind.STORAGE, Container.owner_id.in_(carried)
    )
    rows = await session.execute(
        select(Item).where(Item.container_id.in_(insides), Item.type_key == AIR).order_by(Item.id)
    )
    return list(rows.scalars().all())


async def carried(session: AsyncSession, body: Body) -> float:
    return sum(amount_float(stack.amount) for stack in await cylinders(session, body))


# --- the step out --------------------------------------------------------------


async def require_air(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    target: Node,
    *,
    seconds: float = 0.0,
) -> None:
    """Refuse a step into a place there is nothing to breathe with (D-233).

    Asked **before** the walk, never at the far end: death by ignorance in one
    click is not this world's way, and a body that set out is a body that will
    arrive. What is checked is the destination's air, then the hull's tanks if
    the destination is a hull, then what the body itself carries.

    And carried **enough for the road**: a drop in the bottom of a cylinder is
    not a licence for a six-hour crossing of the black fields, and letting one
    be would be the very death the refusal exists to prevent -- one click later
    than the click, but no more foreseen.
    """
    if body.state is not BodyState.ALIVE:  # pragma: no cover -- the dead do not walk
        return
    if await free_air(session, target):
        return
    ship = await vessels.of_node(session, target)
    if ship is not None and await reserve(session, ship) > _EPS:
        return
    if not await suited(session, catalog, body):
        raise NoAir(
            f"в «{target.name}» нечем дышать: без «{SUIT}» из баллона не подышать, "
            "сколько бы их ни лежало в мешке"
        )
    have = await carried(session, body)
    need = seconds / SECONDS_PER_HOUR * constants[R.OXYGEN_BODY_DRAW]
    if have <= _EPS:
        raise NoAir(f"в «{target.name}» нечем дышать: в баллонах пусто, заправьтесь на борту")
    if have + _EPS < need:
        raise NoAir(
            f"на дорогу в «{target.name}» нужно {need:.1f} кислорода, а в баллонах {have:.1f}: "
            "переход кончится удушьем"
        )


# --- the body's own breathing --------------------------------------------------


async def _lock(session: AsyncSession, body: Body) -> Body:
    """The body's row, locked for this transaction -- the same lock the cold takes."""
    return (
        (
            await session.execute(
                select(Body)
                .where(Body.id == body.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )


async def settle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    now: datetime | None = None,
) -> Breath:
    """Bring a body's breathing up to "now".

    Charges **only a body outside**: a body aboard breathes the hull, and the
    hull is settled once for its whole crew (`tick_ships`). The two never
    overlap, and the split is by where the body stands -- there is no third
    place to be.

    The stamp moves in every case all the same, so hours spent in a Terran yard
    are never charged to a cylinder afterwards.
    """
    moment = now or datetime.now(UTC)
    locked = await _lock(session, body)
    node = await session.get(Node, locked.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        return Breath(left=0.0, uncovered=0.0)

    hours = (moment - locked.air_at).total_seconds() / SECONDS_PER_HOUR
    #: "Up to now" does not work backwards: a tick step carries the nominal
    #: moment of its tick and can arrive behind a command that settled a second
    #: ago. Writing the older stamp back would hand those seconds to the next
    #: settling to charge again -- the same rule the cold keeps.
    if hours <= 0:
        return Breath(left=await carried(session, locked), uncovered=0.0)

    locked.air_at = moment
    if await free_air(session, node) or vessels.is_aboard(node):
        await session.flush()
        return Breath(left=await carried(session, locked), uncovered=0.0)

    draw = constants[R.OXYGEN_BODY_DRAW]
    need = hours * draw
    if not await suited(session, catalog, locked):
        #: A bare body on an airless node breathes nothing at all, whatever it
        #: is carrying. The whole stretch is uncovered.
        await session.flush()
        return Breath(left=0.0, uncovered=hours)

    stacks = await stock.lock_items(session, await cylinders(session, locked))
    took = amount_float(await stock.consume(session, stacks, amount(need)))
    await session.flush()
    #: Asked again rather than summed off the stacks in hand: a stack spent to
    #: nothing is **deleted** by `consume`, and its object keeps the amount it
    #: had -- the sum would count air that no longer exists.
    left = await carried(session, locked)
    #: Exactly enough must not read as short: amounts are split into
    #: thousandths, and the last digit of an hour's draw is rounding, not a
    #: gasp. The same tolerance the fuel check uses before a passage.
    missing = need - took
    return Breath(left=left, uncovered=missing / draw if missing > _EPS else 0.0)


async def tick_bodies(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> int:
    """Settle every body standing where there is no air; kill the ones it ran out on.

    Returns how many died. Bodies aboard are not here: their air is the hull's,
    and `tick_ships` settles them by the hull.
    """
    moment = now or datetime.now(UTC)
    airless = await airless_planets(session)
    if not airless:
        return 0
    bodies = (
        (
            await session.execute(
                select(Body)
                .join(Node, Node.id == Body.node_id)
                .where(
                    Body.state == BodyState.ALIVE,
                    Node.planet.in_([planet.value for planet in airless]),
                )
            )
        )
        .scalars()
        .all()
    )
    dead = 0
    for found in bodies:
        node = await session.get(Node, found.node_id)
        if node is None or vessels.is_aboard(node):  # pragma: no cover -- the hull's business
            continue
        breath = await settle(session, constants, catalog, found, now=moment)
        if breath.uncovered <= 0:
            #: Breathing again gives the grace back. Without this a body that
            #: once ran dry and then refilled would carry the mark to its death
            #: and be killed on the first incomplete stretch, with none of the
            #: settling of warning this module promises.
            await _breathing(session, found)
            continue
        if await _choked(session, constants, found, now=moment):
            dead += 1
    return dead


async def _choked(
    session: AsyncSession, constants: Constants, body: Body, *, now: datetime
) -> bool:
    """One settling of grace, then death.

    A stretch the reserve only half covered drains it and kills nobody: the
    tick that lands a second after the last unit is spent must not be
    indistinguishable from suffocation. The next stretch begins with nothing,
    and that one ends the body.
    """
    if await carried(session, body) > _EPS:
        return False
    if body.choking_since is None:
        body.choking_since = now
        await session.flush()
        return False

    from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

    await death.die(session, constants, body, cause="удушье", now=now)
    return True


async def _breathing(session: AsyncSession, body: Body) -> None:
    """The body has air again: the grace is given back."""
    if body.choking_since is not None:
        body.choking_since = None
        await session.flush()


# --- the hull's own hours ------------------------------------------------------


async def tick_ships(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> tuple[float, int]:
    """Every sealed hull breathes its stretch. Returns (air made, crew lost).

    A hull is settled once for its whole crew: the draw is a number of people
    times an hourly rate, and asking it body by body would read the same tanks
    once a head.
    """
    moment = now or datetime.now(UTC)
    #: Only the hulls with a stretch to settle: a fleet grows with the players,
    #: and a tick that walked all of it every minute to write the same stamp
    #: back would be the cost of owning a shipyard.
    afloat = (
        (await session.execute(select(Ship).where(Ship.air_at < moment).order_by(Ship.id)))
        .scalars()
        .all()
    )
    made = 0.0
    dead = 0
    open_hulls: list[uuid.UUID] = []
    for ship in afloat:
        if not await sealed(session, ship):
            #: A hull with the hatch open still moves its stamp: otherwise a
            #: month at a Terran pier would be charged to the tanks the moment
            #: it cast off. Gathered and written in one statement -- most of a
            #: world's ships stand at a pier, and each of them is not worth a
            #: round trip.
            open_hulls.append(ship.id)
            continue
        grew, lost = await _breathe(session, constants, catalog, ship, now=moment)
        made += grew
        dead += lost
    if open_hulls:
        await session.execute(update(Ship).where(Ship.id.in_(open_hulls)).values(air_at=moment))
        await session.flush()
    return made, dead


async def _breathe(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    now: datetime,
) -> tuple[float, int]:
    """One hull's stretch: make what can be made, spend the rest, count the dead."""
    locked = (
        (
            await session.execute(
                select(Ship)
                .where(Ship.id == ship.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    hours = (now - locked.air_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return 0.0, 0
    locked.air_at = now

    crew = await vessels.crew_of(session, locked)
    if not crew:
        #: Nobody aboard breathes nothing, and the life support has no reason
        #: to run: an empty hull in flight arrives with its tanks as it left.
        await session.flush()
        return 0.0, 0

    #: The hold, once. Everything below asks it something -- how many life
    #: support systems stand there, how much water they have, where the air is
    #: -- and each question used to walk the rooms again: five readings of one
    #: hull per tick. It is a **reading**; every write-off below relocks its
    #: stacks by id under `FOR UPDATE`, so nothing is decided from these numbers.
    hold = await vessels._things(session, locked)
    _, water = await _liquids(session, locked, things=hold)

    need = hull_draw(constants, len(crew)) * hours
    can = (await hull_output(session, constants, catalog, locked, things=hold, water=water)) * hours
    grew = await _make_air(session, constants, catalog, locked, min(need, can), things=hold)
    short = max(0.0, need - grew)
    if short > _EPS:
        stacks = await stock.lock_items(
            session, await breathable_stacks(session, locked, AIR, things=hold)
        )
        short -= amount_float(await stock.consume(session, stacks, amount(short)))
    await session.flush()

    if short <= _EPS:
        for member in crew:
            await _breathing(session, member)
        return grew, 0

    #: The hull ran dry. One settling of grace, exactly as outside: a stretch
    #: only half covered kills nobody, and the next one begun on empty tanks
    #: does. The whole crew shares one hull, so it shares one countdown.
    dead = 0
    for member in crew:
        if member.choking_since is None:
            member.choking_since = now
            continue
        from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

        await death.die(session, constants, member, cause="удушье", now=now)
        dead += 1
    await session.flush()
    if dead == 0:
        #: Said once, when the tanks first fail to cover the hour: the crew has
        #: one settling to do something about it, and a silent hull would make
        #: the deaths that follow arrive out of nowhere.
        #:
        #: Addressed to **everybody aboard**, not to the owner and the
        #: connector: a hired hand in the engine room is the one this warning is
        #: for, and the node it stands in is not the one the event is written
        #: at. `push` hands an event to every party named by a key ending in
        #: `_identity_id`, so the crew is named that way.
        aboard = {
            f"crew{seat}_identity_id": str(member.identity_id) for seat, member in enumerate(crew)
        }
        await events.record(
            session,
            EventKind.SHIP_AIRLESS,
            actor_identity_id=locked.owner_identity_id,
            node_id=locked.connector_node_id,
            ship_id=str(locked.id),
            name=locked.name,
            crew=len(crew),
            **aboard,
        )
    return grew, dead


async def _make_air(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    wanted: float,
    *,
    things: list[Item] | None = None,
) -> float:
    """Run the electrolysis: water out of the vessels, charge out of the batteries.

    Makes less when either runs short, and that is the whole autonomy problem
    (D-234): two tonnes of water and a crate of batteries are the price of
    breathing for a season, and they are mass on every passage.

    `things` is the hold when the caller has read it already; the write-off
    below relocks whatever it takes, so a stale reading cannot overspend.
    """
    if wanted <= _EPS:
        return 0.0
    per_water = _per_unit(catalog, WATER)
    per_energy = _per_unit(catalog, ENERGY)

    if per_water > 0:
        have = await water_aboard(session, ship, things=things)
        wanted = min(wanted, have / per_water)
    if wanted <= _EPS:
        return 0.0
    if per_energy > 0:
        charge = await _charge_for(session, constants, ship, wanted * per_energy)
        wanted = min(wanted, charge / per_energy)
    if wanted <= _EPS:
        return 0.0

    if per_water <= 0:
        return wanted
    #: What was **actually** written off decides how much air there is, not what
    #: the reading above promised: another hand may have drained the tank
    #: between the two, and reporting air that was never made would let a crew
    #: survive an hour it did not survive.
    stacks = await stock.lock_items(
        session, await breathable_stacks(session, ship, WATER, things=things)
    )
    spent = amount_float(await stock.consume(session, stacks, amount(wanted * per_water)))
    return min(wanted, spent / per_water)


async def _charge_for(
    session: AsyncSession, constants: Constants, ship: Ship, wanted: float
) -> float:
    """Charge for the electrolysis, out of the batteries of the ship's own nodes."""
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    left = wanted
    taken = 0.0
    for room in await vessels.nodes_of(session, ship):
        if left <= 0:
            break
        got = await energy.drain_batteries(session, constants, room, left)
        left -= got
        taken += got
    return taken


# --- what the client is told ---------------------------------------------------


async def gauge(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    crew: int,
    things: list[Item] | None = None,
) -> dict[str, object]:
    """The hull's atmosphere in one reading: the level, the water and the rate.

    Given as a level and a rate rather than as hours, so the client counts the
    hand itself and the figure never goes stale between pushes (D-226) -- the
    same shape the cold's reading has. A rate of zero on a sealed hull is a ship
    making exactly what it breathes; a negative one is the countdown.

    `things` is the hold when the caller has read it already: the console asks
    this of every hull it lists.
    """
    air, water = await _liquids(session, ship, things=things)
    shut = await sealed(session, ship)
    drawn = hull_draw(constants, crew) if shut else 0.0
    made = min(
        drawn,
        await hull_output(session, constants, catalog, ship, things=things, water=water),
    )
    return {
        #: What the hull holds and what the life support runs on: both are
        #: liquids in the same tanks (D-230), and both are mass on every passage.
        "units": round(air, ROUND_MASS),
        "water": round(water, ROUND_MASS),
        #: Whether the hull is breathing its own air at all: in port under a sky
        #: that has some, the hatch may as well be open and nothing is spent.
        "sealed": shut,
        "per_hour": round(made - drawn, ROUND_RATIO),
        #: The moment the tanks were last settled at, never the moment of the
        #: reading: the client counts down from what it is given, and "now"
        #: would hand it back the hour the tick has just charged.
        "at": ship.air_at.isoformat(),
    }


async def view(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, node: Node
) -> dict[str, object] | None:
    """What the player is told about the air. Empty where there is air.

    Shaped like the cold's reading (`frost.view`): a stamp, a rate and what is
    in reserve, so the client counts the hand itself and the number on screen
    never goes stale between pushes (D-226).
    """
    if await free_air(session, node):
        return None
    aboard = await vessels.of_node(session, node)
    if aboard is not None:
        crew = len(await vessels.crew_of(session, aboard))
        hull = await gauge(session, constants, catalog, aboard, crew=crew)
        return {
            "where": "борт",
            "units": hull["units"],
            "per_hour": hull["per_hour"],
            "at": hull["at"],
            "suit": False,
        }
    wearing = await suited(session, catalog, body)
    return {
        "where": "скафандр",
        "units": round(await carried(session, body), ROUND_MASS),
        "per_hour": -constants[R.OXYGEN_BODY_DRAW] if wearing else 0.0,
        "at": body.air_at.isoformat(),
        #: Whether the cylinders are connected at all. Without a suit the
        #: reading is a bagful of useless bottles, and it must say so.
        "suit": wearing,
    }
