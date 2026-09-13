# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Workstations and furniture are placed in a building and carried out of it (D-106, D-150).

In the player's language a workstation is «рабочая станция» -- it was «станок»
until D-200; the identifier stays `station`, which reads back as the same word.

A station is placed **in a building**: on an empty plot one builds first
(`estate.construct`) and only then furnishes. Stations and furniture take area
-- `build.slots_per_area` square metres per thing -- so a house's area is its
capacity, not decoration.

The ownership rule is simple and everything rests on it:

* **own node** -- the owner places and removes;
* **civic node** -- whoever the city gave the `laws` power places and removes:
  what the city is built up with is the authority's decision, not a random passer-by's;
* **nobody's node outside a city** -- open to all: the land there has no owner
  and never will (D-198), while what is placed belongs to whoever placed it.

A station is an item `kind: station`, furniture is `kind: furniture` from
`build/recipes.json`. The engine keeps no list of "what is a station": add a
new one in the vault and it is placeable without a code change (D-090). The
one difference between them: one works at a station, furniture furnishes the
household (a bed -- hibernation, a shelf -- storage), and the client shows
them in separate windows.

Standing and being carried are **two** doors, not one (D-308): `take` unbolts
the thing and leaves it lying where it stood, and the hands take it off the
floor through `storage.pick`, where the carry limit stands (D-146). One door
had let a body pocket a machine it could never have lifted.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, current
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.db.base import forget
from src.engine import city as town
from src.engine import craft, estate, events, storage, travel, world
from src.engine.errors import Refusal
from src.models.city import City, Power
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node, storey_of
from src.units import amount_float


class StationError(Refusal):
    pass


class NotStation(StationError):
    """Neither a machine nor furniture. Equipment is placed in a building, not a sack of grain."""


class NotYours(StationError):
    """The node is not yours. A machine is placed at your own place -- that is the point of a
    home."""


class OnePrinter(StationError):
    """A city has one bioprinter, and a second one is not put up beside it."""


class CityPlaces(StationError):
    """Inside a city a bioprinter is put up by the city, not by whoever holds the plot."""


class Busy(StationError):
    """The machine is busy with work: it cannot be carried out from under a worker."""


class NotEmpty(StationError):
    """Things lie in the storage, or ore in the hopper: unpack first, then carry away
    (D-181, D-314)."""


def is_station(catalog: Catalog, type_key: str) -> bool:
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.STATION
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


def is_furniture(catalog: Catalog, type_key: str) -> bool:
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.FURNITURE
    except Exception:  # noqa: BLE001
        return False


def placeable(catalog: Catalog, type_key: str) -> bool:
    """What is placeable in a building at all: a machine, furniture -- or a vessel.

    A vessel put up in a compartment stands on the hull's lines (D-288): the
    engines and the life support drink from what is installed and nothing
    else, so a canister or a cylinder is placed the way a chest is, whatever
    its kind says. Taken down, it is luggage again.
    """
    return (
        is_station(catalog, type_key)
        or is_furniture(catalog, type_key)
        or storage.is_vessel(catalog, type_key)
    )


async def may_build(session: AsyncSession, body: Body, node: Node) -> bool:
    """Whether this body may place and remove equipment in this node.

    A bought plot stays **on the territory** of the city -- taxes and household
    bills come from it -- but its owner is a person (D-089, D-116). So private
    ownership is checked first: the authority disposes of the city's buildings,
    not of somebody's house inside the city. Taking what is not yours is a
    matter for the court (D-166).

    Land outside a city belongs to nobody and is never privatized (D-198), yet
    work on it is open to everyone: whoever comes may put up a machine. What is
    placed belongs to whoever placed it -- the ground under it, to nobody.
    """
    return await may_build_as(session, body.identity_id, node)


async def may_build_as(session: AsyncSession, identity_id: uuid.UUID, node: Node) -> bool:
    """`may_build` asked of an identity with no body at hand: a machine that
    works while its owner is away still works only by its owner's right to the
    node (D-339) -- land sold from under it stops it."""
    if node.owner_identity_id is not None:
        return node.owner_identity_id == identity_id
    #: A storey with no holder of its own is disposed of by the plot under it
    #: (D-247). Read as land, a floor of a **civic** house was nobody's -- no
    #: holder on the row and no city either -- and any passer-by could carry a
    #: machine up into it.
    if node.owner_city_id is None and storey_of(node) is not None and node.parent_id is not None:
        place = await session.get(Node, node.parent_id)
        if place is not None:
            return await may_build_as(session, identity_id, place)
    if node.owner_city_id is None:
        return True
    city = await town.by_id(session, node.owner_city_id)
    return city is not None and await town.may(session, identity_id, city, Power.LAWS)


async def require_printer_room(
    session: AsyncSession, body: Body, node: Node, *, lock: bool = False
) -> None:
    """Whether a bioprinter may go up in this place at all (D-312).

    Two rules, and both are about the city rather than the machine:

    * **one city, one printer.** While the city has one, no second goes up
      anywhere on its land -- not on civic ground, not in a private yard, not
      by the authority itself. The centre of a city is the machine it counts
      its land from (D-307) and the door its newcomers come through (D-208),
      and both of those are answers that must not have a second candidate: with
      one standing, `city.core` can never change its mind;
    * **and the city puts it up.** Lost the machine, the city has no centre and
      no door until the authority restores one. Leaving that to whoever holds a
      plot would hand the city's door to a private yard -- which is the very
      thing D-208 refuses -- and would move every land rate in town by one
      person's decision.

    Outside a city nothing is refused: land beyond the walls is nobody's, a
    printer on it opens no door (`world.is_door`), and a city is founded where
    one already stands (D-023). That is the road a new city takes.

    Says nothing about the printers already standing: a world seeded before
    this rule keeps what it has, and the capital keeps the several it was built
    with. The rule is about putting one up, not about owning one.
    """

    city = await town.of_node(session, node)
    if city is None:
        return
    #: The prison is the exception, and a named one (D-174): it prints the
    #: prisoners who die on the spot, or a death in the face becomes an escape
    #: through the capital. Its machine is nobody's centre and nobody's door
    #: (`city.core`, `world.is_door`), so it neither counts as the city's one
    #: printer nor is refused for it -- and building the penal colony is the
    #: city's business anyway, by the right to the ground it stands on.
    from src.engine import justice  # noqa: PLC0415 -- lazy: breaks the cycle with justice

    if await justice.is_prison(session, node):
        return
    #: The city's row is taken before the question is asked, and only in the
    #: doors that write: two hands putting a printer up in one printerless city
    #: must not both read "none" and both stand one. `craft.plan` asks the same
    #: question as a read and takes nothing -- a forecast that locks a row is a
    #: read that waits (CLAUDE.md).
    if lock:
        await session.execute(select(City.id).where(City.id == city.id).with_for_update())
        forget(session)
    if await town.has_printer(session, city):
        raise OnePrinter(key="station-city-has-printer", city=city.name)
    if not await town.may(session, body.identity_id, city, Power.LAWS):
        raise CityPlaces(key="station-printer-by-the-city", city=city.name)


async def _hold_floor(session: AsyncSession, node: Node) -> None:
    """Take the node's row for the transaction, before the thing's own row.

    Both doors spend the node's floor -- what stands pays by slots and what
    lies pays by area (D-192, D-278) -- and two hands spending its last place
    must not both count it free (CLAUDE.md, the remainder rule).

    **First**, in the order a falling house takes the same pair
    (`estate.collapse`: the plot, then what it buries). A door that took the
    thing and then waited on the node crossed the collapse holding the node
    and waiting on the thing, and the database killed one of the two.

    `FOR NO KEY UPDATE`, as `estate.hold_ground` takes the plot: the key is not
    what is guarded, and a plain `FOR UPDATE` refuses the `FOR KEY SHARE` any
    row pointing at the node takes. Reread under the lock, and the command's
    memory with it: whose the place is and what stands on it are read right
    after, and a copy from before the wait would answer for the node as it was
    before whatever was waited out.
    """
    await session.execute(
        select(Node)
        .where(Node.id == node.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    forget(session)


async def place(session: AsyncSession, catalog: Catalog, body: Body, item: Item) -> Item:
    """Put a machine or furniture up in the node's building: from the hands, or
    off the floor it lies on (D-278).

    In person: machines are not teleported. Requires a building with free
    room: a machine takes area, and it does not stand in a yard under the open
    sky (D-106). Putting up is what makes a thing a machine here -- dropped on
    the floor it is cargo, however heavy: it takes no slot, nobody works at
    it, and the scene does not see it (D-278). A station built in place never
    comes this way: it stands where the batch made it (`craft.batch.finish`),
    and the slots answer to it after the fact -- a furnace has no hands to
    pass through.
    """

    if body.state is not BodyState.ALIVE:
        raise StationError(key="station-dead-places")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StationError(key="station-body-off-node")
    await _hold_floor(session, node)
    pocket = await world.body_container(session, body)
    #: The thing's own row next: two hands putting up one machine from the
    #: same floor must not both read it lying. And it may be gone
    #: -- picked up, burnt, fallen with the house -- between the look and the
    #: click: that is the world's ordinary answer, said in words (D-011), not
    #: a failed refresh. The name is read first: a failed refresh leaves none.
    named = item.type_key
    try:
        await session.refresh(item, with_for_update=True)
    except InvalidRequestError as gone:
        raise StationError(key="thing-gone", goods=named) from gone
    yard_now = await world.node_yard(session, node)
    lying = yard_now is not None and item.container_id == yard_now.id and not item.installed
    if item.container_id != pocket.id and not lying:
        raise StationError(key="station-not-in-hands")
    if not placeable(catalog, item.type_key):
        raise NotStation(key="station-not-placeable", goods=item.type_key)
    if not await may_build(session, body, node):
        raise NotYours(key="station-node-not-yours")
    #: After the right to the place, not before it: somebody standing in
    #: another's yard hears whose yard it is, which is the plainer answer. The
    #: printer's own door (D-312) is for those who got past that one -- and the
    #: holder of a plot inside a city is exactly who gets past it.
    if item.type_key in world.station_names(world.BIOPRINTER):
        await require_printer_room(session, body, node, lock=True)

    #: The building is capacity: `build.slots_per_area` m2 per thing. No
    #: building -- no room; the yard stays a yard. Counted under the node's
    #: row, taken at the door (`_hold_floor`).
    constants = current()
    #: The floor's own places, not the node's: a relic standing in the yard
    #: (D-232, D-244) is not in the way of a machine put up indoors, and the
    #: window says the same count (`estate.space`).
    in_total, _ = await estate.slots(session, constants, node)
    occupied = await estate.indoor_slots(session, node)
    if in_total <= 0:
        raise estate.NoBuilding(key="station-no-building")
    if occupied >= in_total:
        raise estate.NoRoom(
            key="station-no-room", slots=in_total, per=constants[R.BUILD_SLOTS_PER_AREA]
        )

    yard = await world.node_container(session, node)
    item.container_id = yard.id
    #: Into the **building**, so under the roof (D-244). A machine carried in
    #: from the yard still bears the mark it was put down with, and left on it
    #: the thing would stand in the house and be spared by its collapse.
    item.outdoors = False
    item.installed = True
    await session.flush()

    await events.record(
        session,
        EventKind.STATION_PLACED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
    )
    #: A machine appeared: whoever stood here waiting for one gets it (D-209).

    await craft.wake_node(session, node)
    return item


async def take(session: AsyncSession, catalog: Catalog, body: Body, item: Item) -> Item:
    """Take a machine or furniture **down**: it stops standing and lies where it stood.

    Two doors, not one (D-308). This one unbolts the thing: it leaves the
    slots and becomes cargo on the surface it stood on -- the floor of the
    house, the ground where there is no house. Into the hands it goes through
    the second door, `storage.pick`, and there the carry limit stands as it
    stands at every door a thing is taken through (D-146). Until D-308 this
    door did both at once and asked nothing, and a body pocketed a tank of
    sixty-nine kilograms on a limit of thirty.

    Whose the place is, is asked here and only here: a guest's `storage.pick`
    refuses what stands (D-278), so the host's workbench is never carried off
    past the host's door. Once the host has taken it down it lies like any
    other cargo, and the floor is open to whoever the door let in (D-204) --
    with one asymmetry the sack of ore beside it does not have: the heavy ones,
    the thirteen this door was fixed for, the host cannot pick back up either.
    What closes the window is standing it up again (`place` takes it off the
    floor) or shutting the door, and whether that is enough is **OQ-131**.

    One busy with work is not given up.
    """
    if body.state is not BodyState.ALIVE:
        raise StationError(key="station-dead-takes")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise StationError(key="station-body-off-node")
    await _hold_floor(session, node)
    #: The thing's own row after the node's -- the order `place` locks in,
    #: because the two doors meet on the same pair. And the thing may be gone
    #: between the look and the click: the world's ordinary answer, said in
    #: words (D-011). The name is read first: a failed refresh leaves none.
    named = item.type_key
    try:
        await session.refresh(item, with_for_update=True)
    except InvalidRequestError as gone:
        raise StationError(key="thing-gone", goods=named) from gone
    yard = await world.node_container(session, node)
    if item.container_id != yard.id:
        raise StationError(key="station-not-in-node")
    #: What lies is picked up like any cargo (`storage.pick`); this door is for
    #: what stands (D-278).
    if not item.installed:
        raise StationError(key="station-not-installed", goods=item.type_key)
    #: Named before the general refusal: a relic **is** machinery, and being
    #: told it is "not a workstation" would read as a bug rather than as the
    #: rule that the Forerunners' things stay where they were found (D-232).
    if catalog.recipes.is_relic(item.type_key):
        raise NotYours(key="station-relic", goods=item.type_key)
    if not placeable(catalog, item.type_key):
        raise NotStation(key="station-not-a-station", goods=item.type_key)
    #: Built in place (D-268): a furnace, a column, a printer stand where they
    #: were made and are not taken down at all.
    if catalog.recipes.built(item.type_key):
        raise NotStation(key="station-built-in-place", goods=item.type_key)
    if not await may_build(session, body, node):
        raise NotYours(key="station-take-not-yours")
    if item.busy_body_id is not None:
        raise Busy(key="station-busy")
    #: A full chest is not taken down (D-181). The reason moved with D-308:
    #: it is no longer the pocket -- taking down fills no pocket -- but the
    #: pick-up after it, which weighs the chest and not what is in it, so a
    #: full one would leave in the hands with a ton nobody weighed.
    #: The rule that a lying chest is cargo and not a storage is D-278's, and
    #: the engine does not yet ask it (`storage._allowed` never looks at
    #: `installed`): the same ton goes round this door through drop-fill-pick,
    #: and that is **OQ-129**, older than this guard and not closed by it.

    if storage.is_storage(catalog, item.type_key) and not await storage.is_empty(session, item):
        raise NotEmpty(key="station-not-empty", chest=item.type_key)
    #: And a rig's hopper by the same rule (D-181, D-314), with one unit in it
    #: enough. Nothing on the way out weighs what is inside a machine: taking
    #: down weighs nothing at all (D-308) and picking up weighs the machine
    #: itself -- so twelve hours of a rig's work, 300 units and 60 kg, would
    #: ride off in the hands past the carry limit (D-146) and past the carter
    #: the hopper is there to require.
    from src.engine import rig  # noqa: PLC0415 -- lazy: breaks station -> rig -> liquid -> station

    if await rig.hopper_left(session, item) > 0:
        raise NotEmpty(key="station-hopper-not-empty", goods=item.type_key)

    #: Taking down is a move between two budgets -- what stands pays by slots
    #: and what lies pays by area (D-192, D-278) -- and both are counted under
    #: the node's row, taken at the door (`_hold_floor`).
    constants = current()
    #: The surface is the node's, the one a person would name and `storage.drop`
    #: asks for: a machine stands in a building, so it comes to lie on its
    #: floor. Not the thing's own mark -- out of the hands everything arrives
    #: "under a roof", so the mark would answer for the hands rather than for
    #: the place. Its own slot comes back with the same move, and the floor is
    #: measured with that place already given back.
    inside = await storage.require_room(
        session,
        constants,
        catalog,
        node,
        item.type_key,
        amount_float(item.amount),
        spare_indoors=constants[R.BUILD_SLOTS_PER_AREA],
    )

    item.outdoors = not inside
    item.installed = False
    #: A generator's stamp is the hour its output was last settled (D-288,
    #: `battery.tick_offgrid`). Taken down it settles nothing, and put up
    #: again it must start from that moment rather than be credited the
    #: months it lay. A cell keeps its stamp: its charge leaks lying as it
    #: does anywhere, and the stamp is what the leak is counted by.
    if item.charge is None:
        item.charged_at = None
    await session.flush()

    await events.record(
        session,
        EventKind.STATION_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(item.id),
        type_key=item.type_key,
    )
    return item
