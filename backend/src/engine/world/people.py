# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""How a person gets into the world: identity, body, the door they come out of.

One subject, because none of it can be done by halves. An identity without a
body stands nowhere; a body is printed at a machine, and not every machine is
an entrance (D-208); and the door a newcomer picks is a city with its print
conditions and its grant (D-184, D-153). `spawn` is that whole chain in one
transaction, and `doors` is the same chain shown before it is walked -- the
list and the check are one rule (`is_door`), or a client from before it would
enter by a key no longer offered.

Knowledge is here for the same reason it is not a thing: it is copied into an
identity, free and forever (D-053), and it belongs to the person the way the
body does -- not to the container the book lay in.

This is the only part of `world` that knows about cities, and so the only one
with an edge to `engine.city`. That is why it is the last of the three, though
it leans on them lightly: the node a body is printed onto arrives from the
caller, so of `land` only the refusal `LandError` is borrowed, and of `things`
only the printer's lookup (`has_station`, `station_names`). Neither of those
two knows a person exists, and the arrow never turns round.

`printer_nodes` joins the node's yard by hand instead of asking `things`:
the question is "every node in the world with a printer in it", and the
lookups over there all start from nodes already in hand -- one of them
(`has_station`) or a named set of them (`nodes_with_station`). There is no
third caller for a world-wide one, and inventing one to save this join would
put a `SELECT` over every container in the world within easy reach.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import account as accounts
from src.engine import city as town
from src.engine import events
from src.engine.world.land import LandError
from src.engine.world.things import has_station, station_names
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
from src.models.world import Layer, Node
from src.units import ROUND_STAMINA, on_grid
from src.units import money as to_money


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


def stamina_roof(constants: Constants) -> float:
    """`body.stamina_max` as the column can hold it: one ceiling for every door.

    Stamina keeps hundredths, and whoever fills a body -- the printer, sleep,
    a meal -- can only fill it to the grid. A fractional maximum in the vault
    therefore means a full body a hundredth short of the raw number, and
    anyone measuring fullness against the raw number would never see one:
    sleep refused nobody and credited nothing, night after night. Floored,
    because the row holds what the writers wrote, and they wrote the floor;
    rounded the column's way instead, a meal would lift the body a hundredth
    *above* what sleep can reach.
    """
    return float(on_grid(constants[R.BODY_STAMINA_MAX], ROUND_STAMINA, ROUND_FLOOR))


async def print_body(session: AsyncSession, identity: Identity, node: Node) -> Body:
    """Print a body. The identity does not change -- it is eternal (D-012)."""
    stamina = stamina_roof(current())
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
BIOPRINTER = "bioprinter"


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
                    #: Put up, not lying (D-278): nobody is printed out of a crate.
                    Item.installed.is_(True),
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
    from src.engine import justice  # noqa: PLC0415 -- lazy: breaks the import cycle with justice
    from src.engine.death import (  # noqa: PLC0415 -- lazy: breaks the import cycle with death
        PRECURSOR,
    )

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
    from src.engine.death import (  # noqa: PLC0415 -- lazy: breaks the import cycle with death
        PRECURSOR,
    )

    open_ = [node for node in await printer_nodes(session) if await is_door(session, node)]
    for node in open_:
        if (node.properties or {}).get(PRECURSOR):
            return node
    if open_:
        return open_[0]

    #: Nothing prints anywhere: the world is either brand new or in a state
    #: nobody designed. The oldest built-up node is the least arbitrary answer
    #: -- the world grew from it -- and it is only ever a last resort.
    nodes = (
        (
            await session.execute(
                select(Node).where(Node.layer == Layer.CITY).order_by(Node.created_at)
            )
        )
        .scalars()
        .all()
    )
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

    Citizenship is not a key here, and that is D-225: a city's door gives it
    and a door with no city around it cannot, so `city` already says it. What
    used to stand in its place was a condition the city switched on and a term
    it held newcomers by (D-184) -- D-281 took both away, and one condition of
    the three is left, the tax.
    """
    from src.engine.death import (  # noqa: PLC0415 -- lazy: breaks the import cycle with death
        PRECURSOR,
    )

    listing: list[dict[str, Any]] = []
    for node in await printer_nodes(session):
        if not await is_door(session, node):
            continue
        city = await town.of_node(session, node)
        forerunners = bool(node.properties.get(PRECURSOR))
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

    **The door gives citizenship** (D-281): whoever chose this city is its
    citizen from the moment of the print, and it takes effect together with the
    body -- otherwise what the card promised would be an announcement. The
    Forerunners' Printer enrols too, into the city whose land it stands on: the
    machine is nobody's, the person who steps out of it is not. A printer with
    no city around it enrols into nothing -- there is nowhere to write.
    """

    exists = (
        await session.execute(select(Identity).where(Identity.name == name))
    ).scalar_one_or_none()
    if exists is not None:
        raise LandError(key="land-name-taken", name=name)

    identity = await create_identity(
        session, name, email=email, password=password, line=line, profile=profile
    )
    body = await print_body(session, identity, node)

    city = await town.of_node(session, node)
    if city is not None:
        constants, catalog = current(), current_catalog()
        #: Citizenship first, then the grant: the city pays its own.
        await town.enrol_newcomer(session, city, identity)
        await town.welcome(session, constants, catalog, city, identity)
    return identity, body


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
