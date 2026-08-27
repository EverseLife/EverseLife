# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: citizenship (D-160); with founding and offices, laws and charter.

Split out of `engine/city.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.constants.spec import Num
from src.engine import death, energy, events, market, travel, utility, world
from src.engine import vote as ballots
from src.engine.city._base import (
    FOUNDER_POWERS,
    FOUNDER_TITLE,
    HALL,
    CityError,
    NotAllowed,
    NotReady,
    NotYours,
)
from src.engine.city.lookup import by_id, by_node, territory
from src.engine.jobs import enqueue, handler
from src.engine.world import node_container, station_names
from src.models.city import (
    LAW_SCOPE,
    Citizen,
    CitizenshipRequest,
    City,
    Office,
    Power,
)
from src.models.energy import EnergyPool
from src.models.estate import Deed
from src.models.event import EventKind
from src.models.identity import BodyState, Identity
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.vote import VoteKind
from src.models.world import Layer, Node
from src.runtime import CITY_ABOUT_LIMIT


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


async def missing_for_foundation(session: AsyncSession, node: Node) -> tuple[str, ...]:
    """What the node lacks to become a city. Empty -- founding is possible."""

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
    return tuple(role for role, with_what in foundation_needs() if not set(with_what) & costs)


async def establish(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body,
    name: str,
) -> City:
    """Found a city on a planet node that is nobody's or one's own (D-023, D-098,
    D-159). Outside a city land is never privatized (D-198), so a wild node needs
    no title -- only the buildings.

    The entry threshold is buildings, not a coin: `city.foundation_cost` in the
    vault is an estimate of materials and labour, there is nobody to pay it to
    and no reason. Expensive founding cuts off fly-by-night cities, and every
    founding becomes an event.

    The land under the city stops being private: the node is registered to the
    city and the deed for it is cancelled -- civic land is handed out by the
    authority, not the market (D-089).
    """

    if body.state is not BodyState.ALIVE:
        raise CityError("мёртвое тело городов не основывает")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        raise CityError("тело вне узла")
    if node.layer is not Layer.PLANET:
        raise CityError("город закладывают на узле планеты: в чужой застройке города не заводят")
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
            "для города не хватает: "
            + ", ".join(shortfall)
            + ". Порог входа — постройки, а не монета"
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

    deed = (await session.execute(select(Deed).where(Deed.node_id == node.id))).scalar_one_or_none()
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
        whom_identity_id=str(who.id),
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
        (
            await session.execute(
                select(Office).where(Office.city_id == city.id, Office.revoked_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def powers_of(session: AsyncSession, identity_id: uuid.UUID, city: City) -> set[str]:
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
            f"нет права «{needed}» в городе «{city.name}»: власть — это должность, а не намерение"
        )


async def require_at_hall(session: AsyncSession, body, city: City) -> None:
    """Governing is done **in the administration** of this city (D-155).

    Authority that can be exercised from across the ocean needs neither a
    capital nor roads to it: the administration becomes decoration, and seizing
    power a matter of one click rather than geography.

    Reading the panel is unaffected: figures travel over the Net (D-140).
    """

    if body is None or body.state is not BodyState.ALIVE:
        raise NotAllowed("управлять городом можно только живым телом")
    await travel.require_here(session, body)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise NotAllowed("тело вне узла")
    if node.owner_city_id != city.id:
        raise NotAllowed(f"это не территория города «{city.name}»: власть осуществляется у себя")
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
        raise NotAllowed("здесь нет администрации: решения города принимаются в ней")
    if await utility.cut_off(session, node):
        raise NotAllowed("администрация отключена за неуплату: город без неё слеп и нем")
    #: A frozen node closes the administration as surely as an unpaid bill
    #: does (D-231): heat is a condition of the office, not its comfort.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the import cycle with frost

    if not await frost.is_warm(session, current(), node):
        raise NotAllowed(f"«{node.name}» промёрз: администрация закрыта, пока узел не обогрет")


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
        raise NotAllowed("нельзя передать то, чего нет у себя: " + ", ".join(sorted(extra)))
    if not powers:
        raise CityError("должность без полномочий — это не должность")

    #: Re-appointment rewrites the office rather than creating a second one.
    for prior in await offices(session, city):
        if prior.identity_id == whom.id:
            prior.revoked_at = datetime.now(UTC)
    await session.flush()

    office = await _office(session, city, whom.id, title=title, powers=tuple(powers), by=by.id)
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=whom.name,
        whom_identity_id=str(whom.id),
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
            "основателя снимает устав, а не приказ: см. `ruler_recall` и `charter.silence_days`"
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
        whom_identity_id=str(office.identity_id),
    )
    return office


def law(catalog: Catalog, city: City, law_id: str):
    """A code-law's value: the city's decision, otherwise the vault default.

    Returned **as is**, and no branching on law type here (D-094): the consumer
    parses its own value.

    What "as is" holds is worth naming once, because three consumers read it
    (`customs`, the city panel, `vote.open_law`). A law written by the interface
    is **text**: `set_law` takes a string, and a law that is not a number or a
    word -- duty as a map "goods -> rate and norm" (D-123), a ban as a list --
    arrives as the JSON of it and is stored as that JSON. A vault default, on
    the other hand, is whatever the vault wrote, map included. So a consumer of
    a table law takes both: a mapping, or the text of one (`customs._unpacked`).
    """
    own_items = city.laws or {}
    if law_id in own_items:
        return own_items[law_id]
    return catalog.laws.code_law_defaults().get(law_id)


def law_number(constants: Constants, catalog: Catalog, city: City | None, law_id: str) -> float:
    """The same as a number. A default like `` `energy.tariff_default` `` expands
    into a vault constant: a law may reference it, the engine may not (D-065)."""
    raw = (
        catalog.laws.code_law_defaults().get(law_id) if city is None else law(catalog, city, law_id)
    )
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if text.startswith("`") and text.endswith("`"):
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

    if not await ballots.may_propose(session, city, by.id):
        await require(session, by.id, city, f"{LAW_SCOPE}{law_id}")

    #: The charter may hand approval to the citizens (D-161). Then the authority
    #: does not change the law but convenes a poll: the right to propose a law
    #: and the right to approve it are different things, and that is exactly
    #: what `law_approval` asks. Both the council and all citizens may approve
    #: -- the charter decides which (D-161, D-164). In both cases the ruler
    #: convenes rather than decides.

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

    if ballots.sealed(city):
        raise ballots.Sealed("устав этого города не меняется: так решил он сам")
    if ballots.amends_by_vote(city):
        await ballots.open_charter(session, current(), city, by, question_id, option_id, param)
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


def spawn_terms(constants: Constants, catalog: Catalog, city: City | None) -> tuple[bool, float]:
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

    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.CITIZENS)

    word = text.strip()
    if len(word) > CITY_ABOUT_LIMIT:
        raise CityError(
            f"слово города длиннее {CITY_ABOUT_LIMIT} знаков: карточку читают за десять секунд"
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


async def is_citizen(session: AsyncSession, identity_id: uuid.UUID, city: City) -> bool:
    entry = await citizenship(session, identity_id)
    return entry is not None and entry.city_id == city.id


async def citizens_of(session: AsyncSession, city: City) -> list[Citizen]:
    return list(
        (await session.execute(select(Citizen).where(Citizen.city_id == city.id))).scalars().all()
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


async def requests_of(session: AsyncSession, city: City) -> list[CitizenshipRequest]:
    """The queue: who applies and who was invited. Reference, not a decision."""
    return list(
        (
            await session.execute(
                select(CitizenshipRequest).where(CitizenshipRequest.city_id == city.id)
            )
        )
        .scalars()
        .all()
    )


async def join(session: AsyncSession, body, city: City) -> Citizen | CitizenshipRequest:
    """Apply for citizenship. What comes of it is decided by the city charter (D-160).

    In person, in the administration: citizens are enrolled where the city
    makes every decision (D-155). Returns either citizenship or an application
    -- per the charter's answer to `citizenship_admission`.
    """

    await travel.require_here(session, body)
    await require_at_hall(session, body, city)

    existing_amount = await citizenship(session, body.identity_id)
    if existing_amount is not None:
        if existing_amount.city_id == city.id:
            raise AlreadyCitizen("вы уже гражданин этого города")
        raise AlreadyCitizen("гражданство одно на человека: сначала выйти из прежнего города")

    order_of = admission(city)
    call = await request_of(session, body.identity_id, city)
    #: An invitation beats the order: invited means admitted, however strict
    #: the charter.
    if order_of == OPEN or (call is not None and call.kind == INVITE):
        if call is not None:
            await session.delete(call)
        return await _enroll(session, city, body.identity_id, why=order_of)

    if order_of == INVITE:
        raise NotAllowed("в этот город принимают только по приглашению: ждите зова власти")

    #: An application remains: it is filed and waits for the authority's decision.
    if call is not None:
        return call
    order = CitizenshipRequest(identity_id=body.identity_id, city_id=city.id, kind=APPLICATION)
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


async def admit(session: AsyncSession, by: Identity, city: City, who: Identity) -> Citizen:
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


async def exile(session: AsyncSession, by: Identity, city: City, who: Identity) -> None:
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
    entry = Citizen(identity_id=identity_id, city_id=city.id, bound_until=bound_until)
    session.add(entry)
    await session.flush()
    await events.record(
        session,
        EventKind.CITIZENSHIP_GRANTED,
        actor_identity_id=by or identity_id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom_identity_id=str(identity_id),
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
