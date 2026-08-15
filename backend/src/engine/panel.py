"""The city's economic panel (D-124, D-140).

The authority got rates and bans but until now had not a single way to learn
what is going on: the decision "raise the duty" would be made on chat
complaints, i.e. on whoever shouts loudest. The panel answers "what is going
on" with figures the engine knows anyway.

## Six sections, all from the event journal

| Section | Where it comes from |
|---|---|
| **Goods** | balance: produced inside + mined - consumed by processing |
| **Market** | deals of the city's nodes: turnover, median price, deal count |
| **Treasury** | postings of the city account by ground: collected and spent |
| **Energy** | pool, station generation, release by meter and for work |
| **People** | who is in the city now and who printed here over the period |
| **Production** | swings at faces, plot harvests, finished batches |

## Three rules without which the panel is harmful

**The step is slower than the market.** The window is `trade.report_window`
hours. Instant data would turn the panel into an exchange terminal and give
the authority a trading advantage over its own merchants. A governing tool
must be slower than the market it governs (D-124).

**The public snapshot is visible to all**, guests included: balances,
turnovers, prices, population. This continues the rule "everyone knows the
prices" (D-047). If only the ruler sees the figures, there is nothing to argue
with them, and elections turn into a matter of taste. The full set -- to those
with the `dashboard` right.

**Nothing personal for anyone.** Neither incomes of specific players, nor
routes, nor connections: otherwise the city panel turns into surveillance, and
privacy (D-081) into decoration.

## Without an administration the city is blind

The panel lives **while the administration stands and is maintained** (D-140).
Demolished, disconnected for non-payment -- the data does not update, and the
authority decides blindly. That both makes the building meaningful and adds a
step to the decay order of an empty treasury (D-127).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.models.city import City
from src.models.event import Event, EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.ledger import LedgerEntry, LedgerTransaction
from src.models.market import Trade
from src.models.world import Node
from src.telemetry.metrics import median
from src.units import AMOUNT_SCALE, MONEY_SCALE


async def city_nodes(session: AsyncSession, city: City) -> list[Node]:
    """The city's territory: nodes it owns."""
    return list(
        (
            await session.execute(select(Node).where(Node.owner_city_id == city.id))
        ).scalars().all()
    )


async def blind(session: AsyncSession, city: City) -> bool:
    """Whether the city went blind: no administration, or it is disconnected (D-140)."""
    from src.engine import city as town
    from src.engine import utility, world

    for node in await city_nodes(session, city):
        yard = await world.node_container(session, node)
        costs = await session.scalar(
            select(Item.id)
            .where(Item.container_id == yard.id, Item.type_key == town.HALL)
            .limit(1)
        )
        if costs is not None and not await utility.cut_off(session, node):
            return False
    return True


async def collect(
    session: AsyncSession,
    constants: Constants,
    city: City,
    *,
    full: bool = False,
    now: datetime | None = None,
) -> dict:
    """Take the city panel over the `trade.report_window` window.

    `full` adds the treasury by ground -- for those with the `dashboard` right.
    Nothing personal in either the public snapshot or the full one.
    """
    moment = now or datetime.now(UTC)
    window = timedelta(hours=constants[R.TRADE_REPORT_WINDOW])
    src = moment - window

    nodes = await city_nodes(session, city)
    keys = [node.id for node in nodes]
    is_blind = await blind(session, city)

    summary: dict = {
        "city": city.name,
        "window_hours": constants[R.TRADE_REPORT_WINDOW],
        "at": moment.isoformat(),
        #: A blind city gives the last thing it knew and says so honestly:
        #: silently showing yesterday's numbers as today's is not allowed.
        "blind": is_blind,
        "market": await _market(session, keys, since=src),
        "people": await _people(session, keys, since=src),
        "production": await _production(session, keys, since=src),
        "energy": await _energy(session, constants, city, since=src),
        "goods": await _goods(session, keys, since=src),
        #: Imports, exports and collected duty are the summary's main line: by
        #: it one sees that ore is leaking away, not getting pricier by itself (D-124).
        "trade": await _trade(session, constants, city, since=src),
    }
    if full:
        summary["treasury"] = await _treasury(session, city, since=src)
    return summary


async def _market(session: AsyncSession, nodes: list[uuid.UUID], *, since: datetime) -> dict:
    """Turnover, deal count and median price by goods of one's own city (D-003)."""
    if not nodes:
        return {"trades": 0, "volume": 0.0, "prices": {}}
    rows = (
        await session.execute(
            select(Trade.type_key, Trade.price, Trade.amount).where(
                Trade.node_id.in_(nodes), Trade.at >= since
            )
        )
    ).all()
    by_goods: dict[str, list[int]] = {}
    turnover = 0
    for name, price, qty in rows:
        by_goods.setdefault(name, []).append(int(price))
        turnover += int(price) * int(qty)
    return {
        "trades": len(rows),
        "volume": turnover / MONEY_SCALE / AMOUNT_SCALE,
        "prices": {name: median(prices) / MONEY_SCALE for name, prices in by_goods.items()},
    }


async def _people(session: AsyncSession, nodes: list[uuid.UUID], *, since: datetime) -> dict:
    """How many people are in the city now and how many printed here over the window.

    "Migration is the most honest review of authority" (D-140). There is no
    citizenship in the engine yet, so presence is counted, not allegiance --
    and it is called by its name rather than passed off as a census.
    """
    if not nodes:
        return {"here": 0, "printed": 0}
    here = await session.scalar(
        select(func.count())
        .select_from(Body)
        .where(Body.node_id.in_(nodes), Body.state == BodyState.ALIVE)
    )
    printed_ = await session.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            Event.kind == EventKind.BODY_PRINTED.value,
            Event.node_id.in_(nodes),
            Event.at >= since,
        )
    )
    return {"here": int(here or 0), "printed": int(printed_ or 0)}


async def _production(
    session: AsyncSession, nodes: list[uuid.UUID], *, since: datetime
) -> dict:
    """What the city produced over the window: mined, harvested, output by machines."""
    if not nodes:
        return {"mined": {}, "harvested": 0.0, "crafted": {}}
    events_ = (
        await session.execute(
            select(Event).where(
                Event.node_id.in_(nodes),
                Event.at >= since,
                Event.kind.in_(
                    (
                        EventKind.MINING_SWING.value,
                        EventKind.PLOT_HARVESTED.value,
                        EventKind.CRAFT_FINISHED.value,
                    )
                ),
            )
        )
    ).scalars().all()

    mined: dict[str, float] = {}
    done: dict[str, float] = {}
    harvested = 0.0
    for event in events_:
        cargo = event.payload or {}
        if event.kind == EventKind.MINING_SWING.value:
            #: The species is not named in the swing event: the vein knows it.
            #: We count units -- the panel cares about volume, not sort (the market has sort).
            mined["всего"] = mined.get("всего", 0.0) + float(cargo.get("mined", 0))
        elif event.kind == EventKind.PLOT_HARVESTED.value:
            harvested += float(cargo.get("harvested", 0) or 0)
        else:
            name = str(cargo.get("output") or "?")
            done[name] = done.get(name, 0.0) + float(cargo.get("units", 0) or 0)
    return {"mined": mined, "harvested": harvested, "crafted": done}


async def _energy(
    session: AsyncSession, constants: Constants, city: City, *, since: datetime
) -> dict:
    """The pool, release by meter and for work. Generation runs by time (D-082)."""
    from src.models.energy import EnergyPool

    #: The pool is created **on the city's delegate node**, and `energy.pool_of`
    #: looks for it from a built-up node: for the delegate it would answer "no grid". Take it
    #: directly.
    pool = (
        await session.execute(
            select(EnergyPool).where(EnergyPool.node_id == city.node_id)
        )
    ).scalar_one_or_none()

    events_ = (
        await session.execute(
            select(Event).where(
                Event.at >= since,
                Event.kind.in_(
                    (EventKind.ENERGY_DRAWN.value, EventKind.UTILITY_METERED.value)
                ),
            )
        )
    ).scalars().all()
    for_work = 0.0
    for_household = 0.0
    for event in events_:
        cargo = event.payload or {}
        qty = float(cargo.get("energy", 0) or 0)
        if event.kind == EventKind.ENERGY_DRAWN.value:
            for_work += qty
        else:
            for_household += qty
    return {
        "stored": 0.0 if pool is None else float(pool.stored),
        "tariff": 0.0 if pool is None else float(pool.tariff),
        "spent_work": for_work,
        "spent_home": for_household,
    }


async def _goods(session: AsyncSession, nodes: list[uuid.UUID], *, since: datetime) -> dict:
    """Goods balance: how much lies in the city now.

    Imports and exports are counted by customs and given in the `trade`
    section (D-123): here is the remainder, i.e. what is in the city right now.
    """
    if not nodes:
        return {}
    from src.engine import world

    remainders: dict[str, float] = {}
    for node_id in nodes:
        node = await session.get(Node, node_id)
        if node is None:  # pragma: no cover
            continue
        yard = await world.node_container(session, node)
        rows = (
            await session.execute(
                select(Item.type_key, func.sum(Item.amount))
                .where(Item.container_id == yard.id)
                .group_by(Item.type_key)
            )
        ).all()
        for name, qty in rows:
            remainders[name] = remainders.get(name, 0.0) + int(qty or 0) / AMOUNT_SCALE
    return remainders


async def _trade(
    session: AsyncSession, constants: Constants, city: City, *, since: datetime
) -> dict:
    """Imported, exported, trips and duties paid (D-123, D-124)."""
    from src.engine import customs

    return await customs.traffic(session, constants, city, since=since)


async def _treasury(session: AsyncSession, city: City, *, since: datetime) -> dict:
    """The treasury by ground: collected and spent over the window, plus the balance.

    Nothing by name here: who exactly paid the duty is seen by whoever the
    charter shows the treasury to (`treasury_publicity`), and that is a
    separate mechanic (D-124).
    """
    from src.engine import city as town

    account = await town.treasury(session, city)
    rows = (
        await session.execute(
            select(LedgerTransaction.reason, func.sum(LedgerEntry.amount))
            .select_from(LedgerEntry)
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
            .where(LedgerEntry.account_id == account.id, LedgerTransaction.at >= since)
            .group_by(LedgerTransaction.reason)
        )
    ).all()

    collected: dict[str, float] = {}
    spent_: dict[str, float] = {}
    for ground, total in rows:
        value = int(total or 0)
        name = ground.value if hasattr(ground, "value") else str(ground)
        if value >= 0:
            collected[name] = collected.get(name, 0.0) + value / MONEY_SCALE
        else:
            spent_[name] = spent_.get(name, 0.0) - value / MONEY_SCALE
    return {
        "balance": await town.treasury_balance(session, city) / MONEY_SCALE,
        "collected": collected,
        "spent": spent_,
    }


async def store_daily(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Store each city's snapshot into daily metrics. Returns the number of cities.

    History is needed to tell a spike from a trend: a decision on a spike is
    the worst kind of governing (D-140). Depth is `trade.report_retention`
    days, and it is kept by the same table as world metrics: one formula for
    the panel, the dashboard and the invariant check (D-139).
    """

    from src.telemetry.metrics import remember

    moment = now or datetime.now(UTC)
    cities = (await session.execute(select(City))).scalars().all()
    for city in cities:
        snapshot = await collect(session, constants, city, full=True, now=moment)
        marketplace = snapshot["market"]
        await remember(
            session,
            {
                f"city.{city.id}.trades": float(marketplace["trades"]),
                f"city.{city.id}.volume": float(marketplace["volume"]),
                f"city.{city.id}.people": float(snapshot["people"]["here"]),
                f"city.{city.id}.treasury": float(snapshot["treasury"]["balance"]),
                f"city.{city.id}.energy": float(snapshot["energy"]["stored"]),
            },
            now=moment,
        )
    return len(cities)


__all__ = ["blind", "city_nodes", "collect", "store_daily"]
