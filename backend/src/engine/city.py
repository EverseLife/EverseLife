"""City administration: office, right, treasury (D-127, D-130, D-154, D-155).

The charter and code-laws had lain as data since D-130, but nobody could change
them: "city authority" did not exist as an entity. Exactly three things appear
here and not one more.

**Office** -- a record "identity holds a post in the city". What the post is
called is the city's decision: the engine does not care whether it is a
president or a minister of economy.

**Right** -- what the engine checks, and it can be narrow:

    law:import_duty   edit one code-law
    laws              all code-laws at once; covers any law:<id>
    charter           answer charter questions
    treasury          spend the treasury
    offices           appoint and dismiss offices
    land              allot civic plots
    dashboard         full snapshot of the economic panel
    justice           court and sanctions (declared, mechanics separate)

The list of specific laws is **not written** in code: it is exactly the one in
the vault's `data/laws.yaml`. Add a new code-law and a right for it appears at
once. There is and will be no branching on office titles here: otherwise every
new form of government would need a release (01-tech-notes, pattern 3).

**Treasury** -- the existing `city_treasury` account. Whoever has `treasury`
spends it; each spend is an ordinary posting with a ground, i.e. visible.

## Authority is in-person (D-155)

Decisions are made **in the city administration**: changing a law, answering
the charter, appointing, spending the treasury, allotting plots. Authority that
can be exercised from across the ocean needs neither a capital nor roads to it,
and seizing power becomes a matter of one click rather than geography.

Reading the panel stays remote (D-140): figures are information, they travel
over the Net. Presence is needed to **decide**, not to look.

## Where a law's value comes from

Three sources, and the order between them is strict:

1. the city's decision -- what the authority wrote into `city.laws`;
2. the vault default -- `laws.json`, so a new city works without filling in
   anything (D-130);
3. a vault constant, if the default is written as a reference like
   `` `energy.tariff_default` ``.

A city that decided nothing lives on defaults. These are not "default
settings" but the starting state: as soon as the authority decides otherwise,
the default stops meaning anything.

## Change of power (D-160, D-161, D-162)

Citizenship, polls and elections live next door: the citizen register is here,
the procedure is in `engine/vote.py`. From here the ruler is determined in two
ways: by appointment (default "founder indefinitely") and by **election**, if
the charter answered `ruler_selection: elected_citizens`. A recall vacates the
office and the city goes straight to an election.

For the engine the ruler is not a named post but **the office with the widest
set of rights**: no branching on titles here, ever (D-154).

## What is not here

The council (`council_exists`), sortition and inheritance of power, and
amending the charter itself by vote. The charter describes them, the data is
there, the mechanics will arrive as their own task.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import events, ledger
from src.engine.jobs import enqueue, handler
from src.models.city import (
    LAW_SCOPE,
    Citizen,
    CitizenshipRequest,
    City,
    CityGrant,
    Office,
    Power,
)
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import money, money_str


class CityError(Exception):
    pass


class NoCity(CityError):
    pass


class NotAllowed(CityError):
    """No power. Authority is a record, not self-confidence."""


class NotEnoughTreasury(CityError):
    """The treasury does not have that much. An empty treasury is a political event."""


class NotReady(CityError):
    """No city yet: buildings are missing without which it is not a city (D-023)."""


class NotYours(CityError):
    """A city is founded on your own land. Somebody else's yard will not do."""


#: The founder's powers. A city arises governed: if the founder got an empty
#: set, the very first city would have no authority at all (D-130).
FOUNDER_POWERS: tuple[str, ...] = tuple(power.value for power in Power)
FOUNDER_TITLE = "Президент"

#: The thing class of machines that make a node an administration: what a
#: building is, is set by the machine in it (D-106, D-215).
HALL = "Администрация"


# --- lookup ------------------------------------------------------------------


async def by_id(session: AsyncSession, city_id: uuid.UUID) -> City | None:
    return await session.get(City, city_id)


async def by_node(session: AsyncSession, node_id: uuid.UUID) -> City | None:
    """The city whose delegate node this is."""
    return (
        await session.execute(select(City).where(City.node_id == node_id))
    ).scalar_one_or_none()


async def of_node(session: AsyncSession, node: Node) -> City | None:
    """The city on whose territory the node stands.

    A city's territory is its children in the display hierarchy (D-045). The
    floodplain and the mine hang directly on the planet and are covered by no
    authority -- there are no laws there, and that is geography, not an omission.
    """
    if node.owner_city_id is not None:
        return await by_id(session, node.owner_city_id)
    #: The delegate node is the territory of its own city (D-159). Otherwise a
    #: person standing in it is formally outside the city, and in-person
    #: authority in a just-founded city turns out to be unreachable.
    own = await by_node(session, node.id)
    if own is not None:
        return own
    if node.parent_id is None:
        return None
    parent = await session.get(Node, node.parent_id)
    if parent is None or parent.layer is not Layer.PLANET:
        return None
    return await by_node(session, parent.id)


async def territory(session: AsyncSession, city: City) -> Sequence[Node]:
    """Every node of the city: the delegate, its built-up area, its land.

    The same three ways of belonging `of_node` reads, only from the other end.
    """
    return (
        (
            await session.execute(
                select(Node).where(
                    (Node.owner_city_id == city.id)
                    | (Node.id == city.node_id)
                    | (Node.parent_id == city.node_id)
                )
            )
        )
        .scalars()
        .all()
    )


#: Node property: the ring of the built-up area, a record made at generation
#: (D-089). The zero ring is the centre, and the bioprinter stands in it.
RING = "кольцо"


async def core(session: AsyncSession, city: City) -> Node | None:
    """The city core -- the node with the bioprinter the city grew from (D-089).

    A city is founded where a bioprinter already stands (`establish`), so a city
    on one node is its own core: that very machine became the ground of the
    city. The capital is laid out otherwise -- the delegate node holds no
    machines -- and there the core is the zero ring under it, the node with the
    Forerunners' Printer the capital was rebuilt from.

    Only the core is a door into the world (D-208, `world.is_door`). Printers
    built later print the dead and the returning, but a newcomer does not come
    out of somebody's workshop.
    """
    from src.engine import world
    from src.engine.death import PRECURSOR

    own = await session.get(Node, city.node_id)
    if own is not None and await world.has_station(session, own, world.BIOPRINTER):
        return own
    #: The centre of the built-up area is marked twice -- by the zero ring and by
    #: the Forerunners' machine -- and either mark will do: a world laid out
    #: before one of them still has a core rather than none.
    for place in await territory(session, city):
        marks = place.properties or {}
        if not (marks.get(PRECURSOR) or marks.get(RING) == 0):
            continue
        if await world.has_station(session, place, world.BIOPRINTER):
            return place
    return None


async def gate(session: AsyncSession, city: City) -> Node | None:
    """The city's gate: where the built-up area meets the road beyond it (D-206).

    Founding marks one, so a live city always has it. Nothing comes back only
    for a city from before that decision which the catch-up seed has not reached
    yet -- and then a road into it is refused rather than tied to a random node.
    """
    from src.engine import travel

    for node in await territory(session, city):
        if (node.properties or {}).get(travel.EXIT):
            return node
    return None


# --- founding and offices ----------------------------------------------------


async def found(
    session: AsyncSession,
    catalog: Catalog,
    node: Node,
    name: str,
    founder: Identity | None = None,
) -> City:
    """Found a city on a delegate node. Repeated -- return the existing one.

    The charter is filled with `laws.json` defaults: the city arises working,
    not as an empty questionnaire of forty questions (D-130).
    """
    existing_ = await by_node(session, node.id)
    if existing_ is not None:
        return existing_

    city = City(
        node_id=node.id,
        name=name,
        founder_identity_id=None if founder is None else founder.id,
        charter=dict(catalog.laws.charter_defaults()),
        charter_params={},
        laws={},
    )
    session.add(city)
    await session.flush()
    await _mark_gate(session, city, node)

    if founder is not None:
        await _office(
            session,
            city,
            founder.id,
            title=FOUNDER_TITLE,
            powers=FOUNDER_POWERS,
            by=founder.id,
        )

    await events.record(
        session,
        EventKind.CITY_FOUNDED,
        actor_identity_id=None if founder is None else founder.id,
        node_id=node.id,
        city_id=str(city.id),
        name=name,
    )
    return city


async def _mark_gate(session: AsyncSession, city: City, node: Node) -> None:
    """A founded city gets a gate at once (D-206).

    Without it the city would have no door: a road from beyond the walls could
    be tied nowhere, and exploration from inside would refuse instead of laying
    a trail. The node the city stands on becomes the gate -- for a city founded
    on one node it is the only node there is, and that node **is** the whole
    city.

    A city that already has a gate keeps it: the capital's gate is a node of its
    own, and the seed marked it long before founding.
    """
    from src.engine import travel

    ground = await territory(session, city)
    if any((place.properties or {}).get(travel.EXIT) for place in ground):
        return
    node.properties = {**(node.properties or {}), travel.EXIT: True}
    await session.flush()


#: What a city cannot be without (D-023, D-159). The list is four roles, not
#: four names: any energy source will do, as long as somebody fills the pool.
#: There is no warehouse here not because it is unneeded but because the vault
#: describes no "warehouse" item: the engine may not require the nonexistent.
def foundation_needs() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """What must stand in the node before founding: role -> what satisfies it."""
    from src.engine import death, energy, market
    from src.engine.world import station_names

    return (
        ("биопринтер", station_names(death.PRINTER)),
        ("администрация", station_names(HALL)),
        ("рынок", station_names(market.TERMINAL)),
        (
            "источник энергии",
            tuple(
                name
                for thing_class in energy.GENERATOR_CLASSES
                for name in station_names(thing_class)
            ),
        ),
    )


async def missing_for_foundation(
    session: AsyncSession, node: Node
) -> tuple[str, ...]:
    """What the node lacks to become a city. Empty -- founding is possible."""
    from src.engine.world import node_container
    from src.models.inventory import Item

    yard = await node_container(session, node)
    costs = set(
        (
            await session.execute(
                select(Item.type_key).where(Item.container_id == yard.id).distinct()
            )
        )
        .scalars()
        .all()
    )
    return tuple(
        role for role, with_what in foundation_needs() if not set(with_what) & costs
    )


async def establish(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body,
    name: str,
) -> City:
    """Found a city on your own planet node (D-023, D-098, D-159).

    The entry threshold is buildings, not a coin: `city.foundation_cost` in the
    vault is an estimate of materials and labour, there is nobody to pay it to
    and no reason. Expensive founding cuts off fly-by-night cities, and every
    founding becomes an event.

    The land under the city stops being private: the node is registered to the
    city and the deed for it is cancelled -- civic land is handed out by the
    authority, not the market (D-089).
    """
    from src.engine import travel
    from src.models.identity import BodyState

    if body.state is not BodyState.ALIVE:
        raise CityError("мёртвое тело городов не основывает")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        raise CityError("тело вне узла")
    if node.layer is not Layer.PLANET:
        raise CityError(
            "город закладывают на узле планеты: в чужой застройке города не заводят"
        )
    #: Nobody's land needs no title before founding -- outside a city there is
    #: none to be had (D-198). Somebody else's plot is still somebody else's:
    #: a city is not founded over a living owner's head.
    if node.owner_identity_id not in (None, body.identity_id):
        raise NotYours("это чужой участок: город на нём не закладывают")
    if await by_node(session, node.id) is not None:
        raise CityError("здесь уже стоит город")
    if node.owner_city_id is not None:
        raise CityError("это уже городская земля")

    shortfall = await missing_for_foundation(session, node)
    if shortfall:
        raise NotReady(
            "для города не хватает: " + ", ".join(shortfall) +
            ". Порог входа — постройки, а не монета"
        )

    title = name.strip()
    if not title:
        raise CityError("у города должно быть имя")

    identity = await session.get(Identity, body.identity_id)
    city = await found(session, catalog, node, title, founder=identity)

    #: The location becomes city territory (40-society/00). The deed for it is
    #: cancelled: civic land is not traded by deed, otherwise there would be a
    #: shadow way to change the city's owner past the charter (D-159).
    node.owner_city_id = city.id
    node.owner_identity_id = None
    await _retire_deed(session, node, city)
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_FOUNDED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(city.id),
        name=title,
        founded_by_player=True,
    )
    return city


async def _retire_deed(
    session: AsyncSession,
    node: Node,
    city: City,
    *,
    why: str = "земля ушла городу при основании",
) -> None:
    """Cancel the deed for a node that went to the city.

    Two ways lead here, and the event must tell them apart: the founding of a
    city over the land, and the holder handing the plot back (`cede`).
    """
    from src.models.estate import Deed

    deed = (
        await session.execute(select(Deed).where(Deed.node_id == node.id))
    ).scalar_one_or_none()
    if deed is None:
        return
    await session.delete(deed)
    await session.flush()
    await events.record(
        session,
        EventKind.DEED_RETIRED,
        node_id=node.id,
        city_id=str(city.id),
        why=why,
    )


async def _office(
    session: AsyncSession,
    city: City,
    identity_id: uuid.UUID,
    *,
    title: str,
    powers: tuple[str, ...],
    by: uuid.UUID | None,
) -> Office:
    office = Office(
        city_id=city.id,
        identity_id=identity_id,
        title=title,
        powers=list(powers),
        appointed_by_identity_id=by,
    )
    session.add(office)
    await session.flush()
    return office


async def install_founder(session: AsyncSession, city: City, who: Identity) -> Office:
    """Put the founder at the head of the city.

    The only way to establish authority where there is none yet: a city without
    offices has nobody who could appoint the first. From then on authority is
    passed only by appointment or by charter.
    """
    if city.founder_identity_id is not None:
        raise CityError(f"у города «{city.name}» уже есть основатель")
    city.founder_identity_id = who.id
    office = await _office(
        session, city, who.id, title=FOUNDER_TITLE, powers=FOUNDER_POWERS, by=who.id
    )
    #: Founding makes the founder a citizen of this city (D-195). Otherwise the
    #: ruler is a stranger at home: no vote (the franchise is for citizens), a
    #: newcomer's rate at the bank, a visitor's duties. Any previous
    #: citizenship ends -- there is one per person (D-160).
    await _enrol_founder(session, city, who)
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=who.name,
        title=FOUNDER_TITLE,
        powers=list(FOUNDER_POWERS),
        founder=True,
    )
    return office


async def _enrol_founder(session: AsyncSession, city: City, who: Identity) -> None:
    """Make the founder a citizen of the city they have just founded (D-195)."""
    entry = await citizenship(session, who.id)
    if entry is not None:
        if entry.city_id == city.id:
            return
        #: One citizenship per person: the previous one ends here and now.
        await session.delete(entry)
        await session.flush()
        await events.record(
            session,
            EventKind.CITIZENSHIP_ENDED,
            actor_identity_id=who.id,
            city_id=str(entry.city_id),
            reason="основал свой город",
        )

    session.add(Citizen(identity_id=who.id, city_id=city.id))
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_GRANTED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        founder=True,
    )


async def offices(session: AsyncSession, city: City) -> list[Office]:
    """The city's current offices. Vacated ones stay in the journal but not here."""
    rows = (
        await session.execute(
            select(Office).where(
                Office.city_id == city.id, Office.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    return list(rows)


async def powers_of(
    session: AsyncSession, identity_id: uuid.UUID, city: City
) -> set[str]:
    """This identity's rights in this city, as strings (D-155).

    A right can be broad (`treasury`) or narrow (`law:import_duty`). The engine
    stores them the same way -- as a string -- because the list of specific laws
    comes from the vault and is not in code.
    """
    found: set[str] = set()
    for office in await offices(session, city):
        if office.identity_id != identity_id:
            continue
        found.update(str(raw) for raw in office.powers or ())
    return found


def covers(held: set[str], needed: str) -> bool:
    """Whether the set of rights covers the required one. `laws` covers any `law:<id>`."""
    if needed in held:
        return True
    return needed.startswith(LAW_SCOPE) and Power.LAWS.value in held


async def may(
    session: AsyncSession, identity_id: uuid.UUID, city: City, power: Power | str
) -> bool:
    needed = power.value if isinstance(power, Power) else str(power)
    return covers(await powers_of(session, identity_id, city), needed)


async def require(
    session: AsyncSession, identity_id: uuid.UUID, city: City, power: Power | str
) -> None:
    needed = power.value if isinstance(power, Power) else str(power)
    if not await may(session, identity_id, city, needed):
        raise NotAllowed(
            f"нет права «{needed}» в городе «{city.name}»: "
            "власть — это должность, а не намерение"
        )


async def require_at_hall(
    session: AsyncSession, body, city: City
) -> None:
    """Governing is done **in the administration** of this city (D-155).

    Authority that can be exercised from across the ocean needs neither a
    capital nor roads to it: the administration becomes decoration, and seizing
    power a matter of one click rather than geography.

    Reading the panel is unaffected: figures travel over the Net (D-140).
    """
    from src.engine import travel, utility, world
    from src.models.identity import BodyState
    from src.models.inventory import Item
    from src.models.world import Node

    if body is None or body.state is not BodyState.ALIVE:
        raise NotAllowed("управлять городом можно только живым телом")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise NotAllowed("тело вне узла")
    if node.owner_city_id != city.id:
        raise NotAllowed(
            f"это не территория города «{city.name}»: власть осуществляется у себя"
        )
    yard = await world.node_container(session, node)
    costs = await session.scalar(
        select(Item.id)
        .where(
            Item.container_id == yard.id,
            Item.type_key.in_(world.station_names(HALL)),
        )
        .limit(1)
    )
    if costs is None:
        raise NotAllowed(
            "здесь нет администрации: решения города принимаются в ней"
        )
    if await utility.cut_off(session, node):
        raise NotAllowed(
            "администрация отключена за неуплату: город без неё слеп и нем"
        )


async def appoint(
    session: AsyncSession,
    by: Identity,
    city: City,
    whom: Identity,
    *,
    title: str,
    powers: tuple[str, ...],
    body=None,
) -> Office:
    """Appoint to an office. In person: decisions are made in the town hall (D-155).

    Only what you have yourself can be given -- with coverage in mind: a holder
    of `laws` may grant `law:toll`, a holder of `law:toll` may not. Otherwise
    anyone given `offices` would appoint themselves everything else.
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.OFFICES)
    own_items = await powers_of(session, by.id, city)
    extra = {right for right in powers if not covers(own_items, right)}
    if extra:
        raise NotAllowed(
            "нельзя передать то, чего нет у себя: "
            + ", ".join(sorted(extra))
        )
    if not powers:
        raise CityError("должность без полномочий — это не должность")

    #: Re-appointment rewrites the office rather than creating a second one.
    for prior in await offices(session, city):
        if prior.identity_id == whom.id:
            prior.revoked_at = datetime.now(UTC)
    await session.flush()

    office = await _office(
        session, city, whom.id, title=title, powers=tuple(powers), by=by.id
    )
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=whom.name,
        title=title,
        powers=list(powers),
    )
    return office


async def revoke(
    session: AsyncSession, by: Identity, city: City, office: Office, *, body=None
) -> Office:
    """Vacate an office. The founder cannot be removed: that is the charter's business, not the
    engine's."""
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.OFFICES)
    if office.city_id != city.id:
        raise CityError("должность не этого города")
    if office.identity_id == city.founder_identity_id:
        raise NotAllowed(
            "основателя снимает устав, а не приказ: см. `ruler_recall` и "
            "`charter.silence_days`"
        )
    office.revoked_at = datetime.now(UTC)
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        office_id=str(office.id),
    )
    return office


# --- laws and charter --------------------------------------------------------


def law(catalog: Catalog, city: City, law_id: str):
    """A code-law's value: the city's decision, otherwise the vault default.

    Returned **as is**. A law is not only a number or a word: for duty it is a
    map "goods -> rate and norm" (D-123), and casting it to a string would break
    the law for the sake of uniformity. The consumer parses its own value -- no
    branching on law type here (D-094).
    """
    own_items = city.laws or {}
    if law_id in own_items:
        return own_items[law_id]
    return catalog.laws.code_law_defaults().get(law_id)


def law_number(
    constants: Constants, catalog: Catalog, city: City | None, law_id: str
) -> float:
    """The same as a number. A default like `` `energy.tariff_default` `` expands
    into a vault constant: a law may reference it, the engine may not (D-065)."""
    raw = (
        catalog.laws.code_law_defaults().get(law_id)
        if city is None
        else law(catalog, city, law_id)
    )
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if text.startswith("`") and text.endswith("`"):
        from src.constants.spec import Num

        return float(constants[Num(text.strip("`"))])
    try:
        return float(text)
    except ValueError:
        #: "no", "empty", "free" -- a law given not as a number. For a numeric
        #: consumer that is zero, and that is more honest than an exception.
        return 0.0


async def set_law(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    by: Identity,
    city: City,
    law_id: str,
    value: str,
    *,
    body=None,
) -> City:
    """Write a code-law. Only the power and the law's existence are checked.

    The engine does not interpret the value: a law is interpreted data, and
    branching on its meaning lives at the consumer (tax at the order book,
    tariff at the pool), not here. The one exception is the tariff: it lies in
    the pool as a separate column, and that column has to be moved, otherwise
    the authority's decision never reaches the meter.
    """
    await require_at_hall(session, body, city)
    known = {law.id for law in catalog.laws.code_laws}
    if law_id not in known:
        raise CityError(f"нет такого код-закона: {law_id}")
    #: The right is narrow: the "minister of economy" edits duties and does not
    #: touch the tax (D-155). A `laws` holder is covered by the same check. The
    #: charter may add a council to the authority: "the council proposes laws"
    #: means as many legislators as seats, and the ruler is not the only one
    #: among them (D-164).
    from src.engine import vote as ballots

    if not await ballots.may_propose(session, city, by.id):
        await require(session, by.id, city, f"{LAW_SCOPE}{law_id}")

    #: The charter may hand approval to the citizens (D-161). Then the authority
    #: does not change the law but convenes a poll: the right to propose a law
    #: and the right to approve it are different things, and that is exactly
    #: what `law_approval` asks. Both the council and all citizens may approve
    #: -- the charter decides which (D-161, D-164). In both cases the ruler
    #: convenes rather than decides.
    from src.models.vote import VoteKind

    voters = ballots.voters_for(city, VoteKind.LAW)
    if ballots.by_citizens(city) or voters == ballots.COUNCIL_VOTERS:
        await ballots.open_law(session, constants, city, by, law_id, value)
        return city

    laws = dict(city.laws or {})
    before = laws.get(law_id)
    laws[law_id] = value
    city.laws = laws
    await session.flush()

    if law_id == "energy_tariff":
        await _apply_tariff(session, constants, catalog, city)

    await events.record(
        session,
        EventKind.CITY_LAW_SET,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        law=law_id,
        was=before,
        now=value,
    )
    return city


async def _apply_tariff(
    session: AsyncSession, constants: Constants, catalog: Catalog, city: City
) -> None:
    """Push the tariff through to the pool: the pool holds it as a column (D-085)."""
    from decimal import Decimal

    from src.models.energy import EnergyPool

    node = await session.get(Node, city.node_id)
    if node is None:  # pragma: no cover
        return
    pool = (
        await session.execute(select(EnergyPool).where(EnergyPool.node_id == node.id))
    ).scalar_one_or_none()
    if pool is None:
        return
    pool.tariff = Decimal(str(law_number(constants, catalog, city, "energy_tariff")))
    await session.flush()


async def set_charter(
    session: AsyncSession,
    catalog: Catalog,
    by: Identity,
    city: City,
    question_id: str,
    option_id: str,
    param: float | None = None,
    *,
    body=None,
) -> City:
    """Answer a charter question. The question and the option must exist in the vault."""
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.CHARTER)
    question = next((q for q in catalog.laws.charter if q.id == question_id), None)
    if question is None:
        raise CityError(f"нет такого вопроса устава: {question_id}")
    option = next((o for o in question.options if o.id == option_id), None)
    if option is None:
        raise CityError(f"нет такого варианта: {option_id}")
    if option.requires_option is not None:
        #: An option that depends on another answer is meaningless without it:
        #: "the council decides" with no council is a typo, not a charter.
        needed_ = (city.charter or {}).get(option.requires_option)
        if needed_ in (None, "none"):
            raise CityError(
                f"вариант «{option.label}» требует ответа на «{option.requires_option}»"
            )

    #: The charter is amended by the procedure it names itself (D-163): `never`
    #: -- not amended at all, two thirds or unanimity -- by a citizens' vote.
    #: Otherwise the ruler could single-handedly forbid their own recall.
    from src.constants import current
    from src.engine import vote as ballots

    if ballots.sealed(city):
        raise ballots.Sealed("устав этого города не меняется: так решил он сам")
    if ballots.amends_by_vote(city):
        await ballots.open_charter(
            session, current(), city, by, question_id, option_id, param
        )
        return city

    charter = dict(city.charter or {})
    charter[question_id] = option_id
    city.charter = charter
    if param is not None:
        params = dict(city.charter_params or {})
        params[question_id] = param
        city.charter_params = params
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_CHARTER_SET,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        question=question_id,
        option=option_id,
        param=param,
    )
    return city


#: Print conditions -- what a newcomer accepts by choosing the city's door (D-184).
SPAWN_CITIZENSHIP = "spawn_citizenship"
SPAWN_TERM = "spawn_term"
TRADE_TAX = "tax_trade"


def spawn_terms(
    constants: Constants, catalog: Catalog, city: City | None
) -> tuple[bool, float]:
    """The city's print conditions: whether citizenship is mandatory and for how many days.

    A term without mandatory citizenship refers to nothing and is therefore
    zero: there is nothing to hold if nobody joined anything.
    """
    if city is None:
        return False, 0.0
    decision = str(law(catalog, city, SPAWN_CITIZENSHIP) or "").strip().lower()
    required = decision.startswith("обяз")
    if not required:
        return False, 0.0
    return True, max(law_number(constants, catalog, city, SPAWN_TERM), 0.0)


async def bind(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    who: Identity,
    *,
    now: datetime | None = None,
) -> Citizen | None:
    """Fulfil the print conditions: enrol as a citizen for a term (D-184).

    No admission is needed: the person consented by choosing the door, and there
    is no reason to ask twice. The term **is written here** rather than read
    from the law later: a city that raises the term retroactively does not
    lengthen an obligation already taken.

    Does nothing if the city sets no conditions or the person already belongs
    somewhere: a print may not fail over a personnel question.
    """
    required, days = spawn_terms(constants, catalog, city)
    if not required:
        return None
    if await citizenship(session, who.id) is not None:
        return None

    moment = now or datetime.now(UTC)
    return await _enroll(
        session,
        city,
        who.id,
        why="печать",
        bound_until=None if days <= 0 else moment + timedelta(days=days),
    )


async def describe(
    session: AsyncSession, by: Identity, city: City, text: str, *, body=None
) -> City:
    """Write the city's word to newcomers -- what stands on the door card (D-183).

    It is edited by whoever admits citizens (D-160): the announcement is
    recruitment, and whoever answers for the inflow of people should control
    it, not the treasurer. In person, like every city decision (D-155).

    The engine **does not parse** what is written and executes nothing from it.
    "A plot for everyone" is a promise, not a code-law; if it is not kept that
    is a lawsuit (D-004), not an engine error. Otherwise we would have to
    either read promises with code or forbid them altogether, leaving the city
    without a voice.
    """
    from src.runtime import CITY_ABOUT_LIMIT

    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.CITIZENS)

    word = text.strip()
    if len(word) > CITY_ABOUT_LIMIT:
        raise CityError(
            f"слово города длиннее {CITY_ABOUT_LIMIT} знаков: карточку читают "
            "за десять секунд"
        )

    before, city.about = city.about, word
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_DESCRIBED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        was=before,
        now=word,
    )
    return city


# --- treasury ----------------------------------------------------------------


async def treasury(session: AsyncSession, city: City):
    return await ledger.account_for(session, AccountKind.CITY_TREASURY, city.node_id)


async def treasury_balance(session: AsyncSession, city: City) -> int:
    account = await treasury(session, city)
    return await ledger.balance(session, account.id)


async def spend(
    session: AsyncSession,
    by: Identity,
    city: City,
    to: Identity,
    amount: int,
    *,
    memo: str = "",
    body=None,
) -> int:
    """Pay an identity from the treasury. Returns what was paid in minor units.

    Neither salary, nor reward, nor contract are separate mechanics: all of
    them are a transfer from the treasury with a named ground. People invent
    the names; the engine only needs the posting.
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.TREASURY)
    if amount <= 0:
        raise CityError("трата на ноль — это не трата")

    treasury_account = await treasury(session, city)
    remainder = await ledger.balance(session, treasury_account.id)
    if remainder < amount:
        raise NotEnoughTreasury(
            f"в казне {money_str(remainder)} ₭, а нужно {money_str(amount)} ₭"
        )

    to_whom = await ledger.account_for(session, AccountKind.IDENTITY, to.id)
    await ledger.transfer(
        session,
        PostingReason.SALARY,
        debit=treasury_account.id,
        credit=to_whom.id,
        amount=amount,
        memo={"город": city.name, "кому": to.name, "основание": memo},
    )
    await events.record(
        session,
        EventKind.CITY_TREASURY_SPENT,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        to=to.name,
        amount=amount,
        memo=memo,
    )
    return amount


# --- settlement grant for newcomers (D-153) ---------------------------------


async def welcome(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    who: Identity,
) -> int:
    """Pay the settlement grant to a newcomer. Returns what was paid; zero is normal.

    This is **a transfer, not an emission**: not a coin appears in the world.
    The city pays from its treasury because a new resident is GDP: they buy,
    sell and pay taxes. Whether the investment pays off is the city's decision,
    not the engine's.

    Once per identity in one city. Moved -- entitled to receive it in the new
    one: that is how cities compete for people.
    """
    qty = money(law_number(constants, catalog, city, "newcomer_grant"))
    if qty <= 0:
        return 0

    before = (
        await session.execute(
            select(CityGrant).where(
                CityGrant.city_id == city.id, CityGrant.identity_id == who.id
            )
        )
    ).scalar_one_or_none()
    if before is not None:
        return 0

    treasury_account = await treasury(session, city)
    remainder = await ledger.balance(session, treasury_account.id)
    if remainder < qty:
        #: An empty treasury does not pay. This is not the newcomer's fault and
        #: not a reason to print money: the city is simply poor, and that shows.
        return 0

    to_whom = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    await ledger.transfer(
        session,
        PostingReason.SALARY,
        debit=treasury_account.id,
        credit=to_whom.id,
        amount=qty,
        memo={"подъёмные": city.name, "кому": who.name},
    )
    session.add(CityGrant(city_id=city.id, identity_id=who.id, amount=qty))
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_GRANT_PAID,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        amount=qty,
    )
    return qty


# --- city land ---------------------------------------------------------------


async def allot(
    session: AsyncSession,
    by: Identity,
    city: City,
    node: Node,
    to: Identity,
    *,
    body=None,
) -> Node:
    """Allot a civic plot to a resident (D-089).

    Civic land is not taken -- the city gives it: who may take plots in the
    rings is answered by the code-law `build_permit`. The engine checks the
    `land` right: allotting land is a separate decision, neither lawmaking nor
    treasury spending (D-155).
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.LAND)
    if node.owner_city_id != city.id:
        raise CityError("это не городской участок")
    if node.owner_identity_id is not None:
        raise CityError("участок уже за кем-то")

    node.owner_identity_id = to.id
    await session.flush()

    #: An allotted plot is documented by a deed, like a bought one (D-116).
    from src.engine import estate

    await estate.issue_deed(session, node, to.id)

    await events.record(
        session,
        EventKind.LAND_CLAIMED,
        actor_identity_id=to.id,
        node_id=node.id,
        city_id=str(city.id),
        allotted_by=by.name,
    )
    return node


async def cede(session: AsyncSession, body, node: Node) -> City:
    """Hand your own plot back to the city. In person: land is given up on the spot.

    The mirror of `allot` and `buy`, and the only way back. Nobody's leave is
    asked: the land was the city's before it was yours (D-089), and the city
    loses nothing by taking it back. What changes is one thing -- the node has
    no personal holder any more, and from that moment the meter charges the
    city: a node without a holder is maintained by the treasury, which pays
    with energy it could have sold instead of with money (D-149).

    **What goes with the ground.** The deed is cancelled: civic land is not
    traded by deed (D-159). The door is removed with its lists -- on civic land
    there is no door at all, entry is decided by citizenship and duties (D-204).
    Equipment stays where it stands, but from now on it is placed and removed
    by the authority with the `laws` right, not by the last holder (D-166).

    **The debt does not go with it.** Handing over a node with a debt would be
    a way to run machines and write the bill off onto the city; the debt is
    closed first, and only then is there anything to hand over.
    """
    from src.engine import travel, utility
    from src.models.estate import Deed
    from src.models.identity import BodyState
    from src.models.world import NodePass

    if body is None or body.state is not BodyState.ALIVE:
        raise CityError("мёртвое тело участками не распоряжается")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise CityError("участок передают ногами: дойдите до него")
    if node.owner_identity_id != body.identity_id:
        raise NotYours("участок не ваш: городу отдают своё")
    if node.owner_city_id is None:  # pragma: no cover -- own land is always civic
        raise NoCity("это не городская земля: здесь некому её передать")
    city = await by_id(session, node.owner_city_id)
    if city is None:  # pragma: no cover -- civic land without a city is a bug
        raise NoCity("участок приписан к несуществующему городу")

    deed = (
        await session.execute(select(Deed).where(Deed.node_id == node.id))
    ).scalar_one_or_none()
    if deed is not None and deed.sale_price is not None:
        raise CityError(
            "бумага на участок выставлена на продажу: снимите её с торгов, "
            "иначе покупатель заплатит за чужое"
        )

    meter = await utility.meter_of(session, node, create=False)
    if meter is not None and meter.debt > 0:
        raise CityError(
            f"на узле долг {money_str(meter.debt)} ₭: сначала закройте счёт, "
            "город чужих долгов не принимает"
        )

    node.owner_identity_id = None
    #: Civic land has no door: a shut gate and its lists left on the node would
    #: show a lock that nobody can open any more.
    node.gated = False
    await session.execute(delete(NodePass).where(NodePass.node_id == node.id))
    await _retire_deed(session, node, city, why="участок передан городу")
    await session.flush()

    await events.record(
        session,
        EventKind.LAND_CEDED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(city.id),
    )
    return city


async def upkeep_of(
    session: AsyncSession, constants: Constants, city: City
) -> dict:
    """What the city's own household costs it per meter period (D-149).

    The treasury pays for a civic node with energy rather than with money, and
    that spend shows up nowhere in the balance: the pool simply drains. Without
    this line the authority sees energy leaving and cannot tell what into --
    and the decision "should this node be the city's" has no figure behind it.

    `worth` is what the same energy would have fetched at the city's own tariff
    if it had been sold instead. It is not a debt and nobody is billed it: it
    is the price of the decision, and that is exactly what makes it a figure
    worth showing.
    """
    from src.constants import registry as R
    from src.engine import energy, utility
    from src.units import ENERGY_PER_TARIFF_UNIT

    period = constants[R.ENERGY_METER_PERIOD]
    pool = await energy.pool_of(
        session, constants, await session.get(Node, city.node_id), create=False
    )
    tariff = float(pool.tariff) if pool is not None else constants[R.ENERGY_TARIFF_DEFAULT]

    draw = 0.0
    counted = 0
    for node in await territory(session, city):
        #: A holder's node is the holder's bill, wherever it stands: a bought
        #: plot is city territory too, and counting it here would double it.
        if node.owner_identity_id is not None:
            continue
        if await energy.grid_node(session, node) is None:
            continue
        draw += utility.draw_for(constants, node, period)
        counted += 1

    return {
        "nodes": counted,
        "hours": period,
        "energy": round(draw, 1),
        "worth": money(draw / ENERGY_PER_TARIFF_UNIT * tariff),
        "tariff": tariff,
    }


async def survey(
    session: AsyncSession, constants: Constants, catalog: Catalog, city: City
) -> dict:
    """City summary: charter, laws, offices, treasury. Remote read.

    What is visible and to whom is a charter question (`treasury_publicity`),
    and it lies right here. Today the engine gives out everything: there is
    nobody to hide the treasury from until there is a second city, and
    pretending privacy works is worse than not having it.
    """
    people = {}
    for office in await offices(session, city):
        identity = await session.get(Identity, office.identity_id)
        people[str(office.id)] = {
            "id": str(office.id),
            "who": "?" if identity is None else identity.name,
            "identity": str(office.identity_id),
            "title": office.title,
            "powers": list(office.powers or ()),
        }

    return {
        "id": str(city.id),
        "name": city.name,
        #: The city's word to newcomers (D-183): the authority edits it, everyone sees it.
        "about": city.about,
        "node": (await session.get(Node, city.node_id)).key,
        "treasury": await treasury_balance(session, city),
        #: What the city's own nodes burn per meter period. Money is not paid
        #: for them at all -- the treasury pays with energy (D-149).
        "upkeep": await upkeep_of(session, constants, city),
        "offices": list(people.values()),
        "charter": dict(city.charter or {}),
        "charter_params": dict(city.charter_params or {}),
        #: Charter questions in words: the client need not know that
        #: `ruler_recall` means "can the ruler be recalled early". The text lives in the vault.
        "charter_questions": [
            {
                "id": question.id,
                "section": question.section,
                "question": question.question,
                "options": [
                    {"id": option.id, "label": option.label} for option in question.options
                ],
            }
            for question in catalog.laws.charter
        ],
        #: Laws are given out **as in force**: the own decision or the vault
        #: default. The client need not know where the value came from -- it
        #: needs to know which rule it lives by.
        "laws": {
            law.id: {
                "name": law.name,
                "unit": law.unit,
                "note": law.note,
                #: A default like `` `energy.tariff_default` `` expands into a
                #: number: the player must see the rate in force, not a
                #: reference to a vault constant.
                "value": _shown(constants, catalog, city, law.id),
                "own": law.id in (city.laws or {}),
            }
            for law in catalog.laws.code_laws
        },
    }


def _shown(
    constants: Constants, catalog: Catalog, city: City, law_id: str
) -> str | None:
    raw = law(catalog, city, law_id)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        #: A composite law (duty) goes to the client as is: showing it as a
        #: string would force the client to parse it back.
        import json

        return json.dumps(raw, ensure_ascii=False)
    text = str(raw).strip()
    if text.startswith("`") and text.endswith("`"):
        return _plain(law_number(constants, catalog, city, law_id))
    return text


def _plain(value: float) -> str:
    """A number without trailing zeros: tariff "5", not "5.0"."""
    whole = int(value)
    return str(whole) if value == whole else str(value)


# --- citizenship (D-160) -----------------------------------------------------

#: The charter question "how are citizens admitted" and its options (`laws.json`).
ADMISSION = "citizenship_admission"
OPEN, APPLICATION, INVITE = "open", "application", "invite"


class NotCitizen(CityError):
    """This is for citizens. Who exactly is decided by the city, not the engine."""


class AlreadyCitizen(CityError):
    """One citizenship per person: leave the previous city first."""


class Bound(CityError):
    """The term of the obligation taken at printing has not expired yet (D-184)."""


def admission(city: City) -> str:
    """How this city admits citizens: the charter's answer, or "open"."""
    return str((city.charter or {}).get(ADMISSION) or OPEN)


async def citizenship(session: AsyncSession, identity_id: uuid.UUID) -> Citizen | None:
    """The identity's citizenship, if any. There is one -- that is how the record works."""
    return (
        await session.execute(select(Citizen).where(Citizen.identity_id == identity_id))
    ).scalar_one_or_none()


async def is_citizen(
    session: AsyncSession, identity_id: uuid.UUID, city: City
) -> bool:
    entry = await citizenship(session, identity_id)
    return entry is not None and entry.city_id == city.id


async def citizens_of(session: AsyncSession, city: City) -> list[Citizen]:
    return list(
        (
            await session.execute(select(Citizen).where(Citizen.city_id == city.id))
        ).scalars().all()
    )


async def request_of(
    session: AsyncSession, identity_id: uuid.UUID, city: City
) -> CitizenshipRequest | None:
    return (
        await session.execute(
            select(CitizenshipRequest).where(
                CitizenshipRequest.identity_id == identity_id,
                CitizenshipRequest.city_id == city.id,
            )
        )
    ).scalar_one_or_none()


async def requests_of(
    session: AsyncSession, city: City
) -> list[CitizenshipRequest]:
    """The queue: who applies and who was invited. Reference, not a decision."""
    return list(
        (
            await session.execute(
                select(CitizenshipRequest).where(CitizenshipRequest.city_id == city.id)
            )
        ).scalars().all()
    )


async def join(
    session: AsyncSession, body, city: City
) -> Citizen | CitizenshipRequest:
    """Apply for citizenship. What comes of it is decided by the city charter (D-160).

    In person, in the administration: citizens are enrolled where the city
    makes every decision (D-155). Returns either citizenship or an application
    -- per the charter's answer to `citizenship_admission`.
    """
    from src.engine import travel

    await travel.require_here(session, body)
    await require_at_hall(session, body, city)

    existing_amount = await citizenship(session, body.identity_id)
    if existing_amount is not None:
        if existing_amount.city_id == city.id:
            raise AlreadyCitizen("вы уже гражданин этого города")
        raise AlreadyCitizen(
            "гражданство одно на человека: сначала выйти из прежнего города"
        )

    order_of = admission(city)
    call = await request_of(session, body.identity_id, city)
    #: An invitation beats the order: invited means admitted, however strict
    #: the charter.
    if order_of == OPEN or (call is not None and call.kind == INVITE):
        if call is not None:
            await session.delete(call)
        return await _enroll(session, city, body.identity_id, why=order_of)

    if order_of == INVITE:
        raise NotAllowed(
            "в этот город принимают только по приглашению: ждите зова власти"
        )

    #: An application remains: it is filed and waits for the authority's decision.
    if call is not None:
        return call
    order = CitizenshipRequest(
        identity_id=body.identity_id, city_id=city.id, kind=APPLICATION
    )
    session.add(order)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_REQUESTED,
        actor_identity_id=body.identity_id,
        node_id=city.node_id,
        city_id=str(city.id),
        kind_of_request=APPLICATION,
    )
    return order


async def invite(
    session: AsyncSession, by: Identity, city: City, who: Identity
) -> CitizenshipRequest:
    """Invite to citizenship. The invitation waits until the person comes and accepts."""
    await require(session, by.id, city, Power.CITIZENS)
    if await is_citizen(session, who.id, city):
        raise AlreadyCitizen(f"{who.name} уже гражданин")

    exists = await request_of(session, who.id, city)
    if exists is not None:
        return exists
    call = CitizenshipRequest(
        identity_id=who.id, city_id=city.id, kind=INVITE, by_identity_id=by.id
    )
    session.add(call)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_REQUESTED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        who=who.name,
        kind_of_request=INVITE,
    )
    return call


async def admit(
    session: AsyncSession, by: Identity, city: City, who: Identity
) -> Citizen:
    """Approve an application. Right `citizens`: the city's personnel is authority too."""
    await require(session, by.id, city, Power.CITIZENS)
    order = await request_of(session, who.id, city)
    if order is None or order.kind != APPLICATION:
        raise CityError("заявки от этого человека нет")
    if await citizenship(session, who.id) is not None:
        raise AlreadyCitizen(f"{who.name} уже состоит в городе")
    await session.delete(order)
    return await _enroll(session, city, who.id, why=APPLICATION, by=by.id)


async def leave(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    *,
    now: datetime | None = None,
) -> Citizen:
    """Declare leaving. Citizenship lapses after `city.exit_delay` (D-160).

    Remote: the declaration goes over the Net. The delay exists so that one
    cannot leave the city right before a verdict.
    """
    from src.constants import registry as R
    from src.engine.jobs import enqueue
    from src.models.job import JobKind

    moment = now or datetime.now(UTC)
    entry = await citizenship(session, identity.id)
    if entry is None:
        raise NotCitizen("вы нигде не состоите")
    if entry.leaving_at is not None:
        return entry
    #: The obligation taken at printing (D-184) holds until its term. It holds
    #: the person, not the city: exile cuts it at any moment.
    if entry.bound_until is not None and entry.bound_until > moment:
        raise Bound(
            "гражданство взято условием печати и держит до "
            f"{entry.bound_until:%d.%m %H:%M} UTC. Этот срок вы приняли, "
            "выбрав дверь города"
        )

    entry.leaving_at = moment + timedelta(days=constants[R.CITY_EXIT_DELAY])
    await session.flush()
    event = await events.record(
        session,
        EventKind.CITIZENSHIP_LEAVING,
        actor_identity_id=identity.id,
        city_id=str(entry.city_id),
        leaves_at=entry.leaving_at.isoformat(),
    )
    await enqueue(
        session,
        JobKind.CITIZENSHIP_EXIT,
        entry.leaving_at,
        payload={"citizen": str(entry.id)},
        dedup_key=f"citizenship.exit:{entry.id}",
        cause_event_id=event.id,
    )
    return entry


async def exile(
    session: AsyncSession, by: Identity, city: City, who: Identity
) -> None:
    """Exile from the city. A sanction, not a personnel decision: right `justice`.

    The charter options `court` and `citizens_vote` are not enforced while there
    is no court and no polls: the engine checks the right, and who holds it is
    the city's business.
    """
    await require(session, by.id, city, Power.JUSTICE)
    entry = await citizenship(session, who.id)
    if entry is None or entry.city_id != city.id:
        raise NotCitizen(f"{who.name} не гражданин этого города")
    await session.delete(entry)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_ENDED,
        actor_identity_id=by.id,
        city_id=str(city.id),
        who=who.name,
        why="изгнание",
    )


async def _enroll(
    session: AsyncSession,
    city: City,
    identity_id: uuid.UUID,
    *,
    why: str,
    by: uuid.UUID | None = None,
    bound_until: datetime | None = None,
) -> Citizen:
    entry = Citizen(
        identity_id=identity_id, city_id=city.id, bound_until=bound_until
    )
    session.add(entry)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_GRANTED,
        actor_identity_id=by or identity_id,
        node_id=city.node_id,
        city_id=str(city.id),
        how=why,
        bound_until=None if bound_until is None else bound_until.isoformat(),
    )
    return entry


def may_take_city_land(catalog: Catalog, city: City, citizen: bool) -> bool:
    """Whether this person may take civic plots (`build_permit`).

    The law's value is the city's word, not an engine enumeration: options live
    in the vault and grow without code changes (D-094), so what is written is
    read.
    """
    decision = str(law(catalog, city, "build_permit") or "").strip().lower()
    if not decision:
        return True
    if decision.startswith("никто") or decision in ("нет", "-"):
        return False
    if "гражд" in decision:
        return citizen
    return True


@handler(JobKind.CITIZENSHIP_EXIT)
async def exited(session: AsyncSession, job: Job) -> None:
    """The term is up: citizenship lapses (D-160).

    The declaration could have been withdrawn -- then the record is gone or the
    term is cleared, and the job does nothing: a retry after a failure does not
    become a second exit.
    """
    entry = await session.get(Citizen, uuid.UUID(job.payload["citizen"]))
    if entry is None or entry.leaving_at is None:
        return
    city = await by_id(session, entry.city_id)
    await session.delete(entry)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_ENDED,
        actor_identity_id=entry.identity_id,
        city_id=str(entry.city_id),
        why="выход по заявлению",
        city=None if city is None else city.name,
    )


# --- change of power (D-162) -------------------------------------------------


async def ruler(session: AsyncSession, city: City) -> Office | None:
    """The current ruler: the office with the widest set of rights.

    The engine knows rights, not posts (D-154): the "ruler" is whoever has the
    most authority, and what they are called is the city's decision. On a tie
    -- whoever was appointed earlier: seniority settles the dispute without
    inventions.
    """
    offices = (
        await session.execute(
            select(Office).where(
                Office.city_id == city.id, Office.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    if not offices:
        return None
    return sorted(offices, key=lambda office: (-len(office.powers or []), office.created_at))[0]


async def hand_over(session: AsyncSession, city: City, who: Identity) -> Office:
    """Hand authority to the elected (D-162).

    The new ruler receives the previous one's set, not an abstract "authority":
    the engine knows rights, not posts. The previous office is vacated -- not
    deleted: who controlled what last month is a matter for the court.
    """
    previous = await ruler(session, city)
    rights = tuple(previous.powers or ()) if previous is not None else FOUNDER_POWERS
    title = previous.title if previous is not None else FOUNDER_TITLE

    if previous is not None:
        if previous.identity_id == who.id:
            return previous
        previous.revoked_at = datetime.now(UTC)
        await session.flush()
        await events.record(
            session,
            EventKind.CITY_OFFICE_REVOKED,
            node_id=city.node_id,
            city_id=str(city.id),
            title=previous.title,
            why="выборы",
        )

    office = await _office(session, city, who.id, title=title, powers=rights, by=None)
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=who.name,
        title=title,
        powers=list(rights),
        elected=True,
    )
    await schedule_term(session, city, office)
    return office


async def schedule_term(
    session: AsyncSession, city: City, office: Office, *, now: datetime | None = None
) -> None:
    """Set the term of office, if the charter set one (D-163).

    `ruler_term: fixed` in days: on the term the office is vacated by itself.
    Otherwise "elected for thirty days" means "until they remember themselves".
    """
    from src.engine import vote as ballots

    if ballots.answer(city, ballots.TERM, "unlimited") != ballots.FIXED_TERM:
        return
    days = ballots.param(city, ballots.TERM)
    if days <= 0:
        return
    end = (now or datetime.now(UTC)) + timedelta(days=days)
    await enqueue(
        session,
        JobKind.RULER_TERM,
        end,
        payload={"city": str(city.id), "office": str(office.id)},
        dedup_key=f"city.term:{office.id}",
    )


@handler(JobKind.RULER_TERM)
async def term_ended(session: AsyncSession, job: Job) -> None:
    """The term is up: the office is vacated, and the city goes to an election if it can."""
    from src.constants import current
    from src.engine import vote as ballots

    office = await session.get(Office, uuid.UUID(job.payload["office"]))
    city = await by_id(session, uuid.UUID(job.payload["city"]))
    if office is None or city is None or office.revoked_at is not None:
        #: The office was vacated before the term -- by recall or election. A
        #: job retry after a failure does not become a second resignation.
        return

    office.revoked_at = job.run_at
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        node_id=city.node_id,
        city_id=str(city.id),
        title=office.title,
        why="срок полномочий вышел",
    )
    if ballots.elects_ruler(city):
        await ballots.open_election(session, current(), city, None)


async def dismiss(session: AsyncSession, city: City) -> Office | None:
    """Remove the ruler: the recall passed. The city stays without authority until the election."""
    previous = await ruler(session, city)
    if previous is None:
        return None
    previous.revoked_at = datetime.now(UTC)
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        node_id=city.node_id,
        city_id=str(city.id),
        title=previous.title,
        why="отзыв",
    )
    return previous
