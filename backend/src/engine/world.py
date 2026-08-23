# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Creation of what exists in the world: nodes, identities, bodies, property.

No function here creates matter out of nothing just like that: items appear
only through mining and harvest (invariant I1). `grant_item` is a tool for
development sessions and scripts, and it writes an event with an explicit
ground so that such an arrival is visible in telemetry.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.db.base import remember
from src.engine import events
from src.models.event import EventKind
from src.models.identity import (
    Account,
    Body,
    BodyState,
    Identity,
    Knowledge,
    KnowledgeKind,
    Line,
)
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Layer, Node, Planet, Vein
from src.units import amount as to_amount
from src.units import money as to_money

#: Where a planet stands on the space layer (D-045). Radius and period are
#: **display** numbers: orbits are not to scale (10-world/06), and the period
#: says how often a launch window comes round rather than anything about a
#: planet's astronomy. The phase is where the planet stood at the world's
#: epoch, so every client draws one and the same sky.
ORBIT = "орбита"
ORBIT_RADIUS = "радиус"
ORBIT_PERIOD = "период"
ORBIT_PHASE = "фаза"
#: Drawn but not yet playable (D-104). Aquatica is on the map from the first
#: day so that a player sees where they cannot go -- the vault asks for exactly
#: that: unreachable routes are shown, marked as unreachable.
DEFERRED = "отложена"


async def epoch(session: AsyncSession) -> datetime | None:
    """When the world began: the birth of its first node.

    The world is eternal and has no wipes (D-007), so that moment never moves
    -- which is what makes it usable as the origin of every count that must
    agree between the server and every client: the planet's clock (D-029) and
    the angle a planet stands at on its orbit.
    """

    #: Once per command: the clock and the orbit both ask, and the answer
    #: never changes. The index on `created_at` makes the one ask cheap.
    async def find() -> datetime | None:
        return await session.scalar(select(func.min(Node.created_at)))

    return await remember(session, ("epoch",), find)


def orbit_of(node: Node) -> dict[str, float] | None:
    """The node's orbit for the client, or None if the node does not go round anything.

    The data keys are the world's own ("радиус", "период", "фаза"), the wire
    keys are the code's. The translation lives here alone: two places for it
    would drift apart on the first added field.
    """
    circle = (node.properties or {}).get(ORBIT)
    if not isinstance(circle, dict):
        return None
    return {
        "radius": float(circle[ORBIT_RADIUS]),
        "period_days": float(circle[ORBIT_PERIOD]),
        "phase": float(circle[ORBIT_PHASE]),
    }


async def create_node(
    session: AsyncSession,
    key: str,
    name: str,
    *,
    planet: Planet = Planet.TERRA,
    area_m2: float,
    properties: dict[str, Any] | None = None,
    layer: Layer = Layer.CITY,
    parent: Node | None = None,
) -> Node:
    node = Node(
        key=key,
        name=name,
        planet=planet,
        layer=layer,
        parent_id=None if parent is None else parent.id,
        area_m2=Decimal(str(area_m2)),
        properties=properties or {},
    )
    session.add(node)
    await session.flush()
    #: The yard is born with the node, as the pocket is with the body:
    #: otherwise the first `look` at a new place creates it, and a read
    #: must not write (review 2026-08-23). Old nodes without one are still
    #: caught by `node_container`.
    session.add(Container(kind=ContainerKind.NODE, owner_id=node.id))
    await session.flush()
    return node


class LandError(Exception):
    pass


async def grant_node(session: AsyncSession, node: Node, owner: Identity) -> Node:
    """Hand a plot to a person: title plus the deed for it (D-116, D-198).

    Land outside a city is not taken by anybody -- there used to be
    `claim_node`, which took a wild node on foot and issued a deed for it. That
    let the first comer lock up a grove, a meadow or a stony slope whole, and
    the foraging on it (D-196, D-210): somebody else's place gives no work.
    Title is issued by a city and only by a city, so the plot arrives here
    already civic -- through purchase (`estate.buy`) or the founding of a city.

    Working on nobody's land stays open to everyone: build, fell, gather, drop
    things on the ground. The ban is on the title, not on the labour.
    """
    from src.engine import estate

    if node.owner_city_id is None:
        raise LandError(
            "землю за городом не присваивают: бумагу на владение выдаёт город, "
            "а здесь его нет. Строить и работать тут может всякий"
        )
    if node.owner_identity_id is not None:
        raise LandError("участок уже за кем-то")

    node.owner_identity_id = owner.id
    await session.flush()
    await estate.issue_deed(session, node, owner.id)

    await events.record(
        session,
        EventKind.LAND_CLAIMED,
        actor_identity_id=owner.id,
        node_id=node.id,
    )
    return node


async def create_vein(
    session: AsyncSession,
    node: Node,
    resource: str,
    *,
    richness: float,
    remaining: float,
) -> Vein:
    vein = Vein(
        node_id=node.id,
        resource=resource,
        richness=Decimal(str(richness)),
        remaining=to_amount(remaining),
    )
    session.add(vein)
    await session.flush()
    return vein


async def create_identity(
    session: AsyncSession,
    name: str,
    *,
    email: str | None = None,
    password: str | None = None,
    line: Line = Line.HUMAN,
    profile: dict[str, Any] | None = None,
) -> Identity:
    """Account and identity. One account -- one identity (D-011).

    Email and password identify the account (D-187); without them an identity
    is created only by the seed and tests. Surname, age, description are
    self-description.
    """
    from src.engine import account as accounts

    account = Account()
    session.add(account)
    await session.flush()
    if email is not None or password is not None:
        await accounts.set_credentials(session, account, email or "", password or "")

    identity = Identity(account_id=account.id, name=name, line=line)
    if profile:
        accounts.apply_profile(identity, profile)
    session.add(identity)
    await session.flush()

    await events.record(
        session, EventKind.IDENTITY_CREATED, actor_identity_id=identity.id, name=name
    )
    return identity


async def print_body(session: AsyncSession, identity: Identity, node: Node) -> Body:
    """Print a body. The identity does not change -- it is eternal (D-012)."""
    stamina = current()[R.BODY_STAMINA_MAX]
    body = Body(
        identity_id=identity.id,
        node_id=node.id,
        state=BodyState.ALIVE,
        stamina=Decimal(str(stamina)),
    )
    session.add(body)
    await session.flush()

    session.add(Container(kind=ContainerKind.BODY, owner_id=body.id))
    await session.flush()

    await events.record(
        session,
        EventKind.BODY_PRINTED,
        actor_identity_id=identity.id,
        node_id=node.id,
        body_id=str(body.id),
    )
    return body


#: The class of machines bodies are printed at (D-033, D-215). While no
#: bioprinter stands anywhere, printing happens at the city core (D-089).
BIOPRINTER = "Биопринтер"


def station_names(thing_class: str) -> tuple[str, ...]:
    """Concrete item names of a thing class (D-215).

    Behaviour binds to classes, and a class may hold several machines: a
    second bed or printer arrives as data. A word the catalog does not know
    as a class falls back to itself, name-for-name -- so a test world with a
    bare catalog keeps working.
    """
    from src.constants.catalog import current_catalog

    members = current_catalog().recipes.of_class(thing_class)
    return members or (thing_class,)


async def printer_nodes(session: AsyncSession) -> Sequence[Node]:
    """Every node where a bioprinter stands. Not every one of them is a door."""
    return (
        (
            await session.execute(
                select(Node)
                .join(Container, Container.owner_id == Node.id)
                .join(Item, Item.container_id == Container.id)
                .where(
                    Container.kind == ContainerKind.NODE,
                    Item.type_key.in_(station_names(BIOPRINTER)),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )


async def is_door(session: AsyncSession, node: Node) -> bool:
    """Whether a newcomer may be printed here.

    **A door is not every bioprinter** (D-208). A person enters the world through
    the printer a city grew from -- the machine the founding was allowed on
    (D-023): it stands in the core (D-089). Printers built later print the dead
    and the returning (D-033), but they are not new entrances into the world:
    otherwise any workshop that put a machine in its yard would show up in the
    newcomer's choice, and choosing a city would turn into choosing somebody's yard.

    The Forerunners' Printer is a door always (D-028): the machine is nobody's,
    and the free entrance must not close -- even in a world with no city standing.

    The prison printer is not a door: it prints only those the prison holds (D-174).
    """
    from src.engine import city as town
    from src.engine import justice
    from src.engine.death import PRECURSOR

    if not await has_station(session, node, BIOPRINTER):
        return False
    if await justice.is_prison(session, node):
        return False
    if (node.properties or {}).get(PRECURSOR):
        return True
    city = await town.of_node(session, node)
    if city is None:
        #: A printer on nobody's land opens no door: no city was founded on it,
        #: and a newcomer would come out at a machine whose owner answers to no charter.
        return False
    centre = await town.core(session, city)
    return centre is not None and centre.id == node.id


async def spawn_point(session: AsyncSession) -> Node | None:
    """Where a new body is printed when the door was not named: the Forerunners'
    Printer, otherwise any other door.

    Searched across the world, not by a seed key: the world may consist of
    other nodes, and people have to print somewhere.
    """
    from src.engine.death import PRECURSOR

    open_ = [node for node in await printer_nodes(session) if await is_door(session, node)]
    for node in open_:
        if (node.properties or {}).get(PRECURSOR):
            return node
    if open_:
        return open_[0]

    nodes = (await session.execute(select(Node).where(Node.layer == Layer.CITY))).scalars().all()
    core = [node for node in nodes if node.properties.get("кольцо") == 0]
    if core:
        return core[0]
    return nodes[0] if nodes else None


async def doors(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> list[dict[str, Any]]:
    """Doors into the world for a newcomer: where a bioprinter stands and which people you come out
    to.

    Neither price nor term here, deliberately: **the first body is printed at
    once and for free** at any door (D-040), and the twelve hours of the
    Forerunners' Printer do not apply to it. So the newcomer's choice is not
    about money but about the city: how many people are there and whether it
    pays a settlement grant (D-182).

    One city -- one door: the printer it grew from (D-208, `is_door`). The second
    printer of a city, a machine on nobody's land and the prison printer are not
    shown -- one comes into the world through the core, not through any yard
    where a printer was assembled.
    """
    from src.engine import city as town
    from src.engine.death import PRECURSOR

    listing: list[dict[str, Any]] = []
    for node in await printer_nodes(session):
        if not await is_door(session, node):
            continue
        city = await town.of_node(session, node)
        forerunners = bool(node.properties.get(PRECURSOR))
        #: Print conditions (D-184): the engine enforces them, so it must show
        #: them **before** the choice, not after the first sale. The
        #: Forerunners have no conditions and cannot: the machine is nobody's,
        #: and the city does not hang conditions on it -- otherwise in a
        #: one-city world no unconditional door would remain.
        citizenship, term = (
            (False, 0.0) if forerunners else town.spawn_terms(constants, catalog, city)
        )
        listing.append(
            {
                "node": node.key,
                "name": node.name,
                "city": None if city is None else city.name,
                #: The city's word is a promise, not a contract (D-183): the
                #: engine neither parses nor enforces it. Empty -- the card is silent.
                "about": "" if city is None else city.about,
                #: The Forerunners' Printer is the eternal machine of real people,
                #: and the only door that does not depend on somebody's treasury.
                "precursor": forerunners,
                "citizens": 0 if city is None else len(await town.citizens_of(session, city)),
                #: How many people stand on the city's land right now: living
                #: bodies, not passports. Whom they will meet matters more to a
                #: newcomer than who is registered where (D-187).
                "population": 0 if city is None else await population(session, city.node_id),
                #: The settlement grant is the city's promise, not the engine's
                #: handout (D-153): the treasury pays, and the city may not pay
                #: at all. In minor units, like every price going out.
                "grant": (
                    0
                    if city is None
                    else to_money(town.law_number(constants, catalog, city, "newcomer_grant"))
                ),
                #: Mandatory citizenship and its term in days.
                "citizenship": citizenship,
                "term": term,
                #: The sales tax -- the very one the engine will withhold at the
                #: first deal. A condition of life here, not of the door.
                "tax": (
                    0.0
                    if city is None
                    else town.law_number(constants, catalog, city, town.TRADE_TAX)
                ),
            }
        )
    #: Populous cities first, the Forerunners' Printer last: it has neither
    #: residents nor a grant, and as a fallback door it reads better at the end of the list.
    return sorted(
        listing,
        key=lambda door: (door["precursor"], -door["population"], door["name"]),
    )


async def population(session: AsyncSession, city_node_id: uuid.UUID) -> int:
    """Living bodies on the city's territory -- nodes under its delegate node."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Body)
                .join(Node, Node.id == Body.node_id)
                .where(Node.parent_id == city_node_id, Body.state == BodyState.ALIVE)
            )
        ).scalar_one()
    )


async def door(session: AsyncSession, key: str) -> Node | None:
    """The door node by key -- or nothing if printing there is not allowed.

    The same is checked as shown in `doors`: a foreign key, a node without a
    printer, a city's second printer and the prison printer are equally
    unavailable to a newcomer (D-208). The list and this check are one rule (`is_door`),
    otherwise a client from before it would keep entering by a key that is no
    longer offered.
    """
    node = (await session.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if node is None:
        return None
    return node if await is_door(session, node) else None


async def spawn(
    session: AsyncSession,
    name: str,
    node: Node,
    *,
    email: str | None = None,
    password: str | None = None,
    line: Line = Line.HUMAN,
    profile: dict[str, Any] | None = None,
) -> tuple[Identity, Body]:
    """A new player: identity, body at the bioprinter and **zero on the account** (D-153).

    The world hands out no money: any such issue would be emission diluting
    everyone else's money. But the city may pay a settlement grant from its
    treasury -- that is a transfer, not emission, and the authority decides it,
    not the engine.

    Why a city goes for it: a new resident is GDP. They buy, sell and pay
    taxes, so the grant pays off. A rich city lures newcomers, a poor one
    cannot afford it.

    **Print conditions** (D-184) are fulfilled here too: citizenship and its
    term, if the city set them. The person accepted them by choosing the door,
    and they must take effect at the same moment as the body -- otherwise the
    condition remains an announcement.
    """
    from src.constants import current_catalog
    from src.engine import city as town

    exists = (
        await session.execute(select(Identity).where(Identity.name == name))
    ).scalar_one_or_none()
    if exists is not None:
        raise ValueError(f"имя {name!r} уже занято: имя сменить нельзя")

    identity = await create_identity(
        session, name, email=email, password=password, line=line, profile=profile
    )
    body = await print_body(session, identity, node)

    city = await town.of_node(session, node)
    if city is not None:
        from src.engine.death import PRECURSOR

        constants, catalog = current(), current_catalog()
        #: Whoever owns the machine sets the conditions. The Forerunners'
        #: Printer is nobody's: the city on whose land it stands may not hang
        #: citizenship on it (D-184).
        if not node.properties.get(PRECURSOR):
            #: Citizenship first, then the grant: the city pays its own.
            await town.bind(session, constants, catalog, city, identity)
        await town.welcome(session, constants, catalog, city, identity)
    return identity, body


async def body_container(session: AsyncSession, body: Body) -> Container:
    async def find() -> Container:
        stmt = select(Container).where(
            Container.kind == ContainerKind.BODY, Container.owner_id == body.id
        )
        container = (await session.execute(stmt)).scalar_one_or_none()
        if container is None:  # pragma: no cover -- a body without an inventory is a bug
            raise RuntimeError(f"у тела {body.id} нет инвентаря")
        return container

    #: Asked from everywhere and always with the same answer inside one command
    #: -- a pocket does not move from a body (`db.base.remember`).
    return await remember(session, ("body_container", body.id), find)


async def node_container(session: AsyncSession, node: Node) -> Container:
    """What stands and lies in the node: machines, products at the machine.

    Before buildings (E3) this is the only place a machine can stand. With
    buildings it will move into them -- the machine sets what a building is (D-106).
    """

    async def find() -> Container:
        stmt = select(Container).where(
            Container.kind == ContainerKind.NODE, Container.owner_id == node.id
        )
        container = (await session.execute(stmt)).scalar_one_or_none()
        if container is None:
            container = Container(kind=ContainerKind.NODE, owner_id=node.id)
            session.add(container)
            await session.flush()
        return container

    return await remember(session, ("node_container", node.id), find)


#: The "Библиотека" thing class (D-176, D-215): the library window is shown
#: where any of its machines stands.
LIBRARY = "Библиотека"


async def contents(session: AsyncSession, container: Container) -> tuple[Item, ...]:
    """What lies in the container -- everything, in one reading.

    The same three containers are read over and over inside one command: the
    pocket is asked for by the carry limit, by the load, by the convoy and by
    the inventory itself, and the node's yard by every window of the place.
    Each of those was a query. A tuple, not a list, so that a reader cannot
    quietly change what the next reader will get (`db.base.remember`).
    """

    async def read() -> tuple[Item, ...]:
        rows = await session.execute(select(Item).where(Item.container_id == container.id))
        return tuple(rows.scalars().all())

    return await remember(session, ("contents", container.id), read)


async def thing_kinds(session: AsyncSession, node: Node) -> frozenset[str]:
    """Which kinds of things stand in the node -- names, without counting them.

    The node scene is asked this a dozen times in a row, once per class:
    is there a workbench here, a hall, a library, a printer. Each of those was
    a query of its own, and every one of them read the same short list.
    """

    async def find() -> frozenset[str]:
        yard = await node_container(session, node)
        rows = await session.execute(
            select(Item.type_key).where(Item.container_id == yard.id).distinct()
        )
        return frozenset(row[0] for row in rows)

    return await remember(session, ("thing_kinds", node.id), find)


async def has_station(session: AsyncSession, node: Node, name: str) -> bool:
    """Whether a machine of this class stands in the node: the node scene is
    built from machines (D-176), and this is the only way to ask what a place
    is. The word is a thing class (D-215); a plain item name still matches
    itself through the fallback in `station_names`."""
    return bool(await thing_kinds(session, node) & frozenset(station_names(name)))


async def is_library(session: AsyncSession, node: Node) -> bool:
    """The library is a machine, not a node property (D-176). The `library`
    property remains a legacy of old worlds: the catch-up seed places the
    machine, but a world that was not caught up must not lose the window."""
    if (node.properties or {}).get("library"):
        return True
    return await has_station(session, node, LIBRARY)


async def locked_stacks(
    session: AsyncSession,
    container_id: uuid.UUID,
    type_keys: Iterable[str],
    *,
    worst_first: bool = False,
) -> list[Item]:
    """Stacks of the named goods in a container, **locked** for the transaction.

    Every consumer of a shared store -- the tick burning coal in the yard,
    a build taking timber, a ship spending its foundation -- reads stacks
    and then decrements them. Without the lock the worker and a player
    carrying the same stack away write over each other (review 2026-08-23,
    wave 2). Order by id so two consumers of one yard never deadlock;
    `worst_first` puts the lowest quality first for write-offs.
    """
    stmt = select(Item).where(
        Item.container_id == container_id, Item.type_key.in_(tuple(type_keys))
    )
    if worst_first:
        stmt = stmt.order_by(Item.quality.asc().nulls_first(), Item.id)
    else:
        stmt = stmt.order_by(Item.id)
    #: `populate_existing`: a stack read earlier in the same command (the
    #: tick counts the coal before it burns it) is reread after the lock,
    #: or the decrement would be written from the value before it.
    stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return list((await session.execute(stmt)).scalars().all())


async def lock_items(session: AsyncSession, items: Sequence[Item]) -> list[Item]:
    """The same items, locked and reread, in id order. For consumers that
    gathered their stacks from several containers (a ship's rooms)."""
    if not items:
        return []
    ids = sorted(item.id for item in items)
    rows = (
        (
            await session.execute(
                select(Item)
                .where(Item.id.in_(ids))
                .order_by(Item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def consume(session: AsyncSession, stacks: Sequence[Item], quantity: int) -> int:
    """Take `quantity` (in amount units) from locked stacks in order, deleting
    what runs empty. Returns what was actually taken -- less than asked when
    the stacks run out. The caller decides whether that is a refusal."""
    left = quantity
    for stack in stacks:
        if left <= 0:
            break
        take = min(left, stack.amount)
        if take == stack.amount:
            await session.delete(stack)
        else:
            stack.amount -= take
        left -= take
    await session.flush()
    return quantity - left


async def move_stack(
    session: AsyncSession, item: Item, target: Container, quantity: float
) -> float:
    """Move a stack or part of it into another container.

    The split-off part is **the same thing**: mark, shelf life, condition,
    fineness, cultivar and charge travel with it. Losing them when splitting a
    stack would depersonalise the goods: fifty seeds of a cultivar would turn
    into fifty seeds in general.

    One function for all moving in the world -- hold, chest, terminal: each
    own copy sooner or later falls behind on the field list, and a thing
    quietly loses part of itself on one of the paths.
    """
    from src.engine import goods
    from src.units import AMOUNT_SCALE
    from src.units import amount as to_units

    #: A counted thing moves in whole pieces (D-212). A fraction is floored,
    #: and a request smaller than one piece is refused rather than silently
    #: doing nothing.
    #: The stack is locked and reread first: every move in the world comes
    #: through here, and whoever moves the same stack at the same time must
    #: see the remainder, not the snapshot (review 2026-08-23).
    await session.refresh(item, with_for_update=True)
    qty = min(to_units(goods.at_least_one(item.type_key, quantity)), item.amount)
    if qty >= item.amount:
        item.container_id = target.id
        landed = item
    else:
        item.amount -= qty
        landed = Item(
            container_id=target.id,
            type_key=item.type_key,
            amount=qty,
            quality=item.quality,
            condition=item.condition,
            condition_cap=item.condition_cap,
            maker_identity_id=item.maker_identity_id,
            made_at=item.made_at,
            made_node_id=item.made_node_id,
            spoils_at=item.spoils_at,
            flavor=item.flavor,
            roles_filled=item.roles_filled,
            fineness=item.fineness,
            variety_id=item.variety_id,
            vigor=item.vigor,
            charge=item.charge,
            charged_at=item.charged_at,
            recipe_key=item.recipe_key,
        )
        session.add(landed)
    await session.flush()
    #: What arrived joins what already lies here, if they are the same thing
    #: (D-214). Hence the amount moved is read off `qty` and not off the stack:
    #: the stack may have just grown by everything it swallowed.
    await stack_up(session, landed)
    return qty / AMOUNT_SCALE


#: Everything a thing is described by. Two stacks are the same thing only when
#: all of it matches -- and that is what makes folding them lossless (D-214):
#: there is nothing left over to average away, shorten or forget.
#:
#: What is not here is not an oversight. Being worn, harnessed, rigged or
#: worked at belongs to machines, tools, gear and wagons, and none of those
#: fold at all -- so a fold can never take a thing out from under its use.
#: The one exception is work on a loose stack, and that is guarded below.
SAMENESS = (
    "type_key",
    "quality",
    "condition",
    "condition_cap",
    "maker_identity_id",
    "made_at",
    "made_node_id",
    "spoils_at",
    "flavor",
    "roles_filled",
    "fineness",
    "variety_id",
    "vigor",
    "charge",
    "charged_at",
    "recipe_key",
)


async def stack_up(session: AsyncSession, item: Item) -> Item:
    """Fold what already lies here into the stack that has just arrived (D-214).

    Called wherever matter lands in a container: mined, harvested, found, made,
    bought, taken out of a chest, handed over. **The arrival is the stack that
    survives** -- so whoever asked for the move still holds a live thing when
    this returns, and the twins it swallowed are the ones that go.

    Only the loose kinds fold at all (`goods.stackable`), and only into a stack
    nothing tells them apart from: different quality stays different stacks.
    Reading those together is the client's work -- the list groups by thing and
    says how much there is in total -- not a reason to average the numbers here.
    """
    from src.engine import goods
    from src.models.craft import BatchState, CraftBatch

    if not goods.stackable(item.type_key):
        return item
    #: The arrival may be brand new: without a flush it has neither an id to
    #: tell itself apart by nor the fields the table fills in.
    await session.flush()
    #: Twins are locked: the merge deletes them, and a stack being taken
    #: from by another transaction must not vanish under its hands.
    rows = (
        (
            await session.execute(
                select(Item)
                .where(
                    Item.container_id == item.container_id,
                    Item.type_key == item.type_key,
                    Item.id != item.id,
                )
                .order_by(Item.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    twins = [other for other in rows if _same(other, item)]
    if not twins:
        return item
    #: A stack being repaired or taken apart stays where it is: the batch finds
    #: its target by id, and swallowing it would leave the work without a thing.
    pinned = set(
        (
            await session.execute(
                select(CraftBatch.target_item_id).where(
                    CraftBatch.target_item_id.in_([twin.id for twin in twins]),
                    CraftBatch.state != BatchState.DONE,
                )
            )
        )
        .scalars()
        .all()
    )
    for twin in twins:
        if twin.id in pinned:
            continue
        item.amount += twin.amount
        await session.delete(twin)
    await session.flush()
    return item


def _same(one: Item, other: Item) -> bool:
    """Whether nothing at all tells two stacks apart (D-214)."""
    return all(_alike(getattr(one, field), getattr(other, field)) for field in SAMENESS)


def _alike(one: Any, other: Any) -> bool:
    """Equality that does not trip over the road a number took to get here.

    The same quality arrives as `12.5` on one path and as `Decimal("12.50")`
    off the database on another, and in Python those two are not equal.
    """
    numbers = (int, float, Decimal)
    if isinstance(one, numbers) and isinstance(other, numbers):
        return Decimal(str(one)) == Decimal(str(other))
    return one == other


async def learn(
    session: AsyncSession,
    identity: Identity,
    key: str,
    *,
    kind: KnowledgeKind = KnowledgeKind.RECIPE,
    discovered: bool = False,
) -> Knowledge | None:
    """Copy knowledge into the identity. Free and forever (D-053)."""
    stmt = select(Knowledge).where(
        Knowledge.identity_id == identity.id, Knowledge.kind == kind, Knowledge.key == key
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return None

    knowledge = Knowledge(identity_id=identity.id, kind=kind, key=key, discovered=discovered)
    session.add(knowledge)
    await session.flush()
    await events.record(
        session,
        EventKind.KNOWLEDGE_LEARNED,
        actor_identity_id=identity.id,
        kind_of_knowledge=kind.value,
        key=key,
    )
    return knowledge


async def grant_item(
    session: AsyncSession,
    container: Container,
    type_key: str,
    *,
    amount: float = 1,
    quality: float | None = None,
    origin: str,
    maker_identity_id: uuid.UUID | None = None,
    made_node_id: uuid.UUID | None = None,
) -> Item:
    """Put an item into a container.

    `origin` is mandatory and lands in the event: any appearance of matter in
    the world must have a named ground -- mining, harvest, craft, a debugging
    script. There is no anonymous arrival (pillar P1).
    """

    #: Matter arrives in whole pieces where the thing is counted (D-212): three
    #: quarters of an ingot is no ingot, and the fourth quarter is not ours to
    #: give. Less than one piece is a refusal rather than a stack of nothing --
    #: the table forbids an empty stack, and an integrity error is a worse way
    #: to learn that.
    from src.engine import goods

    amount = goods.at_least_one(type_key, amount)
    item = Item(
        container_id=container.id,
        type_key=type_key,
        amount=to_amount(amount),
        quality=None if quality is None else Decimal(str(quality)),
        maker_identity_id=maker_identity_id,
        made_at=datetime.now(UTC) if maker_identity_id else None,
        made_node_id=made_node_id,
    )
    session.add(item)
    await session.flush()
    await events.record(
        session,
        EventKind.ITEM_CREATED,
        actor_identity_id=maker_identity_id,
        item_id=str(item.id),
        type_key=type_key,
        amount=amount,
        quality=quality,
        origin=origin,
    )
    #: The event is written before the fold and about the arrival alone: the
    #: journal says what came into the world, not what the stack grew to (D-214).
    return await stack_up(session, item)
