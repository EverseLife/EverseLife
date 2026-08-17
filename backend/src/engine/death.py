"""Death and body printing (D-012, D-013, D-028, D-032, D-033, D-040).

The identity is immortal, the body is a consumable. Death destroys the shell
together with everything it carried, but touches neither knowledge, nor the
account, nor reputation: they live in the identity, and the identity in the
Net (09-death).

## What is lost and what is not

| Lost | Kept |
|---|---|
| The body and the whole pocket: tool, gear, coin | Identity, name, knowledge, agrotech |
| Resources spent printing a new body | Terracoin on the account: it does not belong to the body |
| Your place in the chain: you are at the printer again | Goods in the terminal, plots, orders |

The real price of death is a **logistical setback**, not a timer. The
punishment is already contained in the loss, so unavailability lasts minutes,
not hours.

**Part of what was worn stays at the place of death** -- `death.salvage_ratio`,
and in damaged form. This is done for the economy of violence that will
arrive with combat: robbing the living pays better than killing, because the
dead leave a third, and that one damaged.

## Printing: two doors

* **city printer** -- `energy.body_print` of energy from the pool at the
  tariff plus `death.iron_cost` of iron from the yard; ready in
  `death.print_time_city` minutes;
* **the Forerunners' Printer in the capital** -- free and with no limit on the
  number of bodies, but `death.print_time_capital` hours.

Hence the whole design of the resurrection market: **the city sells not life
but speed**, and nobody will pay for a body more than twelve hours are worth
to them (D-028). There is no hostage: one can print at any node of the Net
that has a printer and something to pay with (D-033).

**Who pays** is decided by the code-law `body_print` (D-032). "no" -- the
player pays; "citizens" or "everyone" -- the treasury pays. Energy is written
off the same either way: the city does not print energy, it gives it away.

**The first body is printed instantly and for free** (D-040): a person
starting the game for the first time does not wait half a day. That is a
one-off exception, and it lives in `world.spawn`, not here.

## What is not here

* **credit for a body** -- arrives with the bank (E4, D-030): while there is
  nothing to pay with, the capital's free door remains, and it is always open;
* **an insurer** -- that is a profession on top of contracts, not an engine mechanic;
* **nymphs** (`death.nymph_grow_multiplier`) -- there is no second line in the alpha.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.engine import events, world
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node

#: An hour in minutes is presentation, not balance: the Forerunners' twelve
#: hours and the city's three minutes cannot otherwise be compared in one column.
from src.units import MINUTES_PER_HOUR as MINUTES_IN_HOUR
from src.units import PERCENT, amount, amount_float, money_str

#: The machine that prints bodies. The name comes from `build/recipes.json` (D-090).
PRINTER = "Биопринтер"
#: The processor metal. The vault names the price "10 iron" (D-033).
IRON = "Слиток железа"
#: Node property: that very Forerunners' Printer, printing for free and slowly
#: (D-028). A place property, not a node name: names change, the world's structure does not.
PRECURSOR = "предтечи"


class DeathError(Exception):
    pass


class Alive(DeathError):
    """The body is alive. A second cannot be printed: one account -- one identity (D-011)."""


class NoPrinter(DeathError):
    """No bioprinter in the node. One prints where there is something to print with."""


class AlreadyPrinting(DeathError):
    """A print is already in progress. There are no two bodies of one identity."""


class CannotPay(DeathError):
    """Nothing to pay with. The capital's door meanwhile always stays open."""


# --- death -------------------------------------------------------------------


async def die(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    cause: str,
    now: datetime | None = None,
) -> float:
    """Kill the body. Returns how many units of the worn survived at the place.

    What survived lands in the node where the body died: matter does not vanish
    with its owner and does not follow them. The rest is a sink, and an honest
    one: in an eternal world (D-007) what is lost does not come back.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        return 0.0

    #: The roll is seeded by the body: replaying the episode after a failure gives the same.
    dice = random.Random(str(body.id))
    share = constants[R.DEATH_SALVAGE_RATIO] / PERCENT

    node = await session.get(Node, body.node_id)
    pocket = await world.body_container(session, body)
    things = (
        await session.execute(select(Item).where(Item.container_id == pocket.id))
    ).scalars().all()

    yard = await world.node_container(session, node) if node is not None else None
    survived = 0.0
    for thing in things:
        left = amount(amount_float(thing.amount) * share)
        #: The indivisible survives by roll: there is no half a pickaxe, and the
        #: rule must be one for everything worn.
        if left <= 0:
            left = thing.amount if dice.random() < share else 0
        if left <= 0 or yard is None:
            await session.delete(thing)
            continue
        thing.amount = left
        thing.container_id = yard.id
        #: "In damaged form": condition drops by the same share as the amount
        #: survived. The vault gives no second number for this.
        thing.condition = Decimal(str(float(thing.condition) * share))
        survived += amount_float(left)

    #: An ongoing transit breaks off: a dead body arrives nowhere.
    from src.models.travel import Travel, TravelState

    transits = (
        await session.execute(
            select(Travel).where(
                Travel.body_id == body.id, Travel.state == TravelState.GOING
            )
        )
    ).scalars().all()
    for transit in transits:
        transit.state = TravelState.CANCELLED

    #: The harness falls apart: the dead pull nothing. The convoy with its
    #: cargo stays standing where it stopped -- like any matter without an owner (D-157).
    from src.engine import transport

    await transport.unharness(session, body)

    body.state = BodyState.DEAD
    body.died_at = moment
    body.sleeping_since = None
    await session.flush()

    await events.record(
        session,
        EventKind.BODY_DIED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        body_id=str(body.id),
        cause=cause,
        salvaged=survived,
    )
    return survived


# --- printing ----------------------------------------------------------------


async def printers(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID | None = None,
) -> list[dict]:
    """Where in the world one can print and for how much. Read from the cloud, i.e. always.

    The identity is in the Net and sees the whole printer network (D-033): the
    choice is limited not by access but by money and geography. Who asks
    matters: a city prints at its own expense for its citizens, not for
    everyone (D-160).
    """
    from src.engine import city as town
    from src.engine import energy

    nodes = (
        await session.execute(
            select(Node)
            .join(Container, Container.owner_id == Node.id)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE, Item.type_key == PRINTER)
            .distinct()
        )
    ).scalars().all()

    out: list[dict] = []
    for node in nodes:
        #: The prison printer is not another door into the world (D-174): it
        #: prints only those the prison holds and is not shown to the rest at all.
        from src.engine import justice

        if await justice.is_prison(session, node) and not (
            identity_id is not None
            and await justice.held(session, constants, identity_id)
        ):
            continue
        forerunners = bool(node.properties.get(PRECURSOR))
        energy_amount = 0.0 if forerunners else constants[R.ENERGY_BODY_PRINT]
        iron = 0.0 if forerunners else constants[R.DEATH_IRON_COST]
        city = await town.of_node(session, node)
        out.append(
            {
                "node": node.key,
                "name": node.name,
                "city": None if city is None else city.name,
                "precursor": forerunners,
                "energy": energy_amount,
                "iron": iron,
                "cost": (
                    0 if forerunners
                    else await energy.price_of(session, constants, node, energy_amount)
                ),
                #: Minutes are the common display unit: the Forerunners' twelve
                #: hours and the city's three minutes cannot otherwise be compared.
                "minutes": (
                    constants[R.DEATH_PRINT_TIME_CAPITAL] * MINUTES_IN_HOUR
                    if forerunners
                    else constants[R.DEATH_PRINT_TIME_CITY]
                ),
                "iron_here": await _iron_here(session, node),
                "at_city_expense": await _city_pays(
                    session, constants, node, identity_id
                ),
            }
        )
    return sorted(out, key=lambda door: door["minutes"])


async def order(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    identity: Identity,
    node: Node,
    *,
    now: datetime | None = None,
) -> Job:
    """Order a body print in this node. The body arrives as a journal job.

    The fee is taken **up front**, like batch materials: a print that is not
    paid for does not start. The Forerunners' free door requires nothing but
    patience.
    """
    moment = now or datetime.now(UTC)
    alive = await alive_body(session, identity.id)
    if alive is not None:
        raise Alive("тело живо: второго одной личности не бывает")
    if await pending(session, identity.id) is not None:
        raise AlreadyPrinting("печать уже идёт")

    yard = await world.node_container(session, node)
    has_printer = await session.scalar(
        select(Item.id)
        .where(Item.container_id == yard.id, Item.type_key == PRINTER)
        .limit(1)
    )
    if has_printer is None:
        raise NoPrinter(f"в узле «{node.name}» нет биопринтера")

    forerunners = bool(node.properties.get(PRECURSOR))
    if forerunners:
        minutes = constants[R.DEATH_PRINT_TIME_CAPITAL] * MINUTES_IN_HOUR
        paid_ = 0
    else:
        paid_ = await _charge(session, constants, identity, node, moment=moment)
        minutes = constants[R.DEATH_PRINT_TIME_CITY]

    ready_ = moment + timedelta(minutes=minutes)
    event = await events.record(
        session,
        EventKind.BODY_PRINT_ORDERED,
        actor_identity_id=identity.id,
        node_id=node.id,
        precursor=forerunners,
        paid=paid_,
        ready_at=ready_.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BODY_PRINT,
        ready_,
        payload={"identity": str(identity.id), "node": str(node.id)},
        dedup_key=f"body.print:{identity.id}:{event.id}",
        cause_event_id=event.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise AlreadyPrinting("печать уже поставлена")
    return job


async def _charge(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    node: Node,
    *,
    moment: datetime,
) -> int:
    """Write off energy and iron for the print. Returns what was paid in money.

    Energy comes from the city pool and is paid at its tariff; iron is taken
    from the node's yard -- somebody brought it there, and that is the whole
    point of D-013: the city must keep a stock in the printer, otherwise there
    is nothing to print with.
    """
    from src.engine import energy, ledger

    pool = await energy.pool_of(session, constants, node)
    if pool is None:
        raise CannotPay(
            "городской сети здесь нет: печать требует энергии, а её негде взять"
        )
    await energy.produce(session, constants, pool, now=moment)

    energy_needed = constants[R.ENERGY_BODY_PRINT]
    if float(pool.stored) < energy_needed:
        raise CannotPay(
            f"в пуле {float(pool.stored):.0f} энергии, а печать требует "
            f"{energy_needed:.0f}: город без топлива не печатает"
        )

    iron_needed = constants[R.DEATH_IRON_COST]
    yard = await world.node_container(session, node)
    ingots = (
        await session.execute(
            select(Item).where(Item.container_id == yard.id, Item.type_key == IRON)
        )
    ).scalars().all()
    have = sum(amount_float(ingot.amount) for ingot in ingots)
    if have < iron_needed:
        raise CannotPay(
            f"в принтере {have:.0f} железа из {iron_needed:.0f}: "
            "процессор не из чего собрать"
        )

    from src.engine import justice

    if await justice.is_prison(session, node) and not await justice.held(
        session, constants, identity.id
    ):
        raise DeathError(
            "тюремный принтер печатает только заключённых: это не дверь в мир"
        )

    price = await energy.price_of(session, constants, node, energy_needed)
    at_city_expense = await _city_pays(session, constants, node, identity.id)
    if price > 0 and not at_city_expense:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        remainder = await ledger.balance(session, account.id)
        if remainder < price:
            raise CannotPay(
                f"печать стоит {money_str(price)} ₭, а на счету {money_str(remainder)} ₭. "
                "Принтер Предтеч в столице печатает бесплатно — но двенадцать часов"
            )
        treasury = await ledger.account_for(
            session, AccountKind.CITY_TREASURY, pool.node_id
        )
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=account.id,
            credit=treasury.id,
            amount=price,
            memo={"печать тела": node.key, "энергии": energy_needed},
        )
    else:
        #: At the city's expense no money moves: the treasury pays with energy
        #: it could have sold -- the same way as for its own buildings (D-149).
        price = 0

    left = amount(iron_needed)
    for ingot in ingots:
        if left <= 0:
            break
        take = min(left, ingot.amount)
        if take == ingot.amount:
            await session.delete(ingot)
        else:
            ingot.amount -= take
        left -= take

    pool.stored = Decimal(str(float(pool.stored) - energy_needed))
    await session.flush()
    return price


async def _city_pays(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    identity_id: uuid.UUID | None = None,
) -> bool:
    """Whether the city prints at its own expense. The answer is in its code-law (D-032).

    "citizens" means **citizens** (D-160): before citizenship existed the
    engine read this option as "everyone", and the city paid for strangers.
    """
    from src.engine import city as town

    city = await town.of_node(session, node)
    if city is None:
        return False
    decision = (town.law(current_catalog(), city, "body_print") or "").strip().lower()
    if decision in ("", "нет", "-"):
        return False
    if "гражд" in decision:
        return identity_id is not None and await town.is_citizen(
            session, identity_id, city
        )
    return True


async def _iron_here(session: AsyncSession, node: Node) -> float:
    yard = await world.node_container(session, node)
    ingots = (
        await session.execute(
            select(Item).where(Item.container_id == yard.id, Item.type_key == IRON)
        )
    ).scalars().all()
    return sum(amount_float(ingot.amount) for ingot in ingots)


@handler(JobKind.BODY_PRINT)
async def printed(session: AsyncSession, job: Job) -> None:
    """The body is ready. The identity returns to the world -- where it ordered the print."""
    identity = await session.get(Identity, uuid.UUID(job.payload["identity"]))
    node = await session.get(Node, uuid.UUID(job.payload["node"]))
    if identity is None or node is None:  # pragma: no cover
        raise DeathError(f"печать {job.id} ссылается в никуда")
    if await alive_body(session, identity.id) is not None:
        #: A job retry after a failure does not become a second body (D-011).
        return
    await world.print_body(session, identity, node)


# --- helpers -----------------------------------------------------------------


async def alive_body(session: AsyncSession, identity_id: uuid.UUID) -> Body | None:
    return (
        await session.execute(
            select(Body).where(
                Body.identity_id == identity_id, Body.state == BodyState.ALIVE
            )
        )
    ).scalars().first()


async def pending(session: AsyncSession, identity_id: uuid.UUID) -> Job | None:
    """This identity's ongoing print, if any."""
    return (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.BODY_PRINT.value,
                Job.state == JobState.PENDING,
                Job.payload["identity"].astext == str(identity_id),
            )
        )
    ).scalars().first()


