"""Creation of what exists in the world: nodes, identities, bodies, property.

No function here creates matter out of nothing just like that: items appear
only through mining and harvest (invariant I1). `grant_item` is a tool for
development sessions and scripts, and it writes an event with an explicit
ground so that such an arrival is visible in telemetry.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants import registry as R
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
    return node


class LandError(Exception):
    pass


async def grant_node(session: AsyncSession, node: Node, owner: Identity) -> Node:
    """Hand a plot to a person: title plus the deed for it (D-116, D-198).

    Land outside a city is not taken by anybody -- there used to be
    `claim_node`, which took a wild node on foot and issued a deed for it. That
    let the first comer lock up a grove, a meadow or a stony slope whole, and
    barehand gathering with it (D-196): somebody else's place gives no work.
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


#: The machine at which bodies are printed (D-033). While no bioprinter stands
#: anywhere, printing happens at the city core -- the zero-ring node (D-089).
BIOPRINTER = "Биопринтер"


async def spawn_point(session: AsyncSession) -> Node | None:
    """Where a new body is printed: a bioprinter, otherwise the city core.

    Searched across the world, not by a seed key: the world may consist of
    other nodes, and people have to print somewhere.
    """
    from src.models.inventory import Container, ContainerKind

    prints = (
        await session.execute(
            select(Node)
            .join(Container, Container.owner_id == Node.id)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE, Item.type_key == BIOPRINTER)
            .limit(1)
        )
    ).scalars().first()
    if prints is not None:
        return prints

    nodes = (
        await session.execute(select(Node).where(Node.layer == Layer.CITY))
    ).scalars().all()
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

    The prison printer is not shown: it prints only those the prison holds and
    is not a door into the world (D-174).
    """
    from src.engine import city as town
    from src.engine import justice
    from src.engine.death import PRECURSOR

    from_node = (
        await session.execute(
            select(Node)
            .join(Container, Container.owner_id == Node.id)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE, Item.type_key == BIOPRINTER)
            .distinct()
        )
    ).scalars().all()

    listing: list[dict[str, Any]] = []
    for node in from_node:
        if await justice.is_prison(session, node):
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
    printer and the prison printer are equally unavailable to a newcomer.
    """
    node = (
        await session.execute(select(Node).where(Node.key == key))
    ).scalar_one_or_none()
    if node is None:
        return None
    if not await has_station(session, node, BIOPRINTER):
        return None
    from src.engine import justice

    if await justice.is_prison(session, node):
        return None
    return node


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
    stmt = select(Container).where(
        Container.kind == ContainerKind.BODY, Container.owner_id == body.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:  # pragma: no cover -- a body without an inventory is a bug
        raise RuntimeError(f"у тела {body.id} нет инвентаря")
    return container


async def node_container(session: AsyncSession, node: Node) -> Container:
    """What stands and lies in the node: machines, products at the machine.

    Before buildings (E3) this is the only place a machine can stand. With
    buildings it will move into them -- the machine sets what a building is (D-106).
    """
    stmt = select(Container).where(
        Container.kind == ContainerKind.NODE, Container.owner_id == node.id
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:
        container = Container(kind=ContainerKind.NODE, owner_id=node.id)
        session.add(container)
        await session.flush()
    return container


#: The "Library" machine (D-176): the library window is shown where it stands.
LIBRARY = "Библиотека"


async def has_station(session: AsyncSession, node: Node, name: str) -> bool:
    """Whether a machine with this name stands in the node: the node scene is
    built from machines (D-176), and this is the only way to ask what a place is."""
    yard = await node_container(session, node)
    found = await session.scalar(
        select(Item.id)
        .where(Item.container_id == yard.id, Item.type_key == name)
        .limit(1)
    )
    return found is not None


async def is_library(session: AsyncSession, node: Node) -> bool:
    """The library is a machine, not a node property (D-176). The `library`
    property remains a legacy of old worlds: the catch-up seed places the
    machine, but a world that was not caught up must not lose the window."""
    if (node.properties or {}).get("library"):
        return True
    return await has_station(session, node, LIBRARY)


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
    from src.units import AMOUNT_SCALE
    from src.units import amount as to_units

    qty = min(to_units(quantity), item.amount)
    if qty >= item.amount:
        item.container_id = target.id
    else:
        item.amount -= qty
        session.add(
            Item(
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
            )
        )
    await session.flush()
    return qty / AMOUNT_SCALE


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

    knowledge = Knowledge(
        identity_id=identity.id, kind=kind, key=key, discovered=discovered
    )
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
    return item
