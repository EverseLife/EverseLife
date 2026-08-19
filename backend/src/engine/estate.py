"""Real estate: plot purchase, deed, building (D-089, D-106, D-116, D-125).

## Buying civic land

An empty civic node may be bought by **any** player -- land is no longer only
handed out by the authority. The price per square metre is set by the state
via the code-law `land_price`; with each ring from the bioprinter -- the city
centre -- a plot gets cheaper by `land.price_decay_per_ring`. Distance is
measured over the graph: in steps from the node with the bioprinter, not by a
property written at generation. Proceeds go to the city treasury: the city
sells its land, not the engine.

## Deed

Ownership is documented by a deed (`models/estate.Deed`) -- an electronic
document. The deed lives in the Net: the body's death does not touch it, it
is managed remotely, like the account and orders. Sale is a sale contract: the
holder lists a price (open or addressed), the buyer pays -- and the deed
together with the plot passes to them in one transaction. No escrow is needed:
both money and title change hands at one moment.

## Building

On an empty plot a building is built first, and only in a building are
machines placed: a machine takes `build.slots_per_area` square metres, so a
house's area is its capacity, not decoration (D-106). Construction is work:
materials of the first durability tier (`build.materials_per_m2`) are written
off at once, the building rises on schedule at `build.labor_per_m2` hours per
metre -- as a journal job, like every long-running task.
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, ledger, travel
from src.engine.jobs import enqueue, handler
from src.models.city import City
from src.models.estate import Building, Deed
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Edge, Node
from src.units import MINUTES_PER_HOUR, PERCENT, amount_float, money
from src.units import amount as to_amount


class EstateError(Exception):
    pass


class NotForSale(EstateError):
    """This land is not for sale: it is either occupied or not civic."""


class NotEnoughMoney(EstateError):
    pass


class NotOwner(EstateError):
    """The owner disposes of land; of civic land -- the authority with the `land` right."""


class BadName(EstateError):
    """The name is empty or longer than reasonable. A nameplate is not a letter."""


class NoBuilding(EstateError):
    """No building on the plot: build first, then place machines (D-106)."""


class NoRoom(EstateError):
    """No room in the building: machines take area, and it ran out."""


class TooTall(EstateError):
    """Higher than the durability tier holds (D-145): a timber house is not eight storeys."""


# --- land price (D-089) ------------------------------------------------------


async def rings_from_center(session: AsyncSession, node: Node) -> int:
    """The plot's distance from the city centre -- in graph steps from the bioprinter.

    The centre is the node where the bioprinter stands (for the capital -- the
    Forerunners' Printer); the city grows in rings around it, and land value
    falls with each ring. Measured by edges, not by the "ring" property: the
    property is a record at generation, edges are how people really walk the city.
    """
    from src.engine import world

    center = await world.spawn_point(session)
    if center is None or center.id == node.id:
        return 0

    #: Breadth-first search over edges. The graph is small; when it grows large
    #: there will be a reason to precompute rather than count per request.
    edges = (await session.execute(select(Edge))).scalars().all()
    neighbours: dict[uuid.UUID, list[uuid.UUID]] = {}
    for edge in edges:
        neighbours.setdefault(edge.node_a_id, []).append(edge.node_b_id)
        neighbours.setdefault(edge.node_b_id, []).append(edge.node_a_id)

    seen = {center.id}
    queue: deque[tuple[uuid.UUID, int]] = deque([(center.id, 0)])
    while queue:
        ring_node, step_count = queue.popleft()
        if ring_node == node.id:
            return step_count
        for neighbour in neighbours.get(ring_node, ()):  # noqa: B007
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append((neighbour, step_count + 1))
    #: No road to the node -- the land is on the outskirts, beyond the farthest ring.
    return len(seen)


async def price_of(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    node: Node,
) -> int:
    """The plot price in minor units: city rate x decay x area.

    The rate at the centre is set by the city via the code-law `land_price`
    (TC/m2); with each ring from the bioprinter the price falls by
    `land.price_decay_per_ring`.
    """
    from src.engine import city as town

    rate = town.law_number(constants, catalog, city, "land_price")
    if rate <= 0:
        raise NotForSale("город не назначил цену земли: код-закон `land_price` пуст")
    decline = 1 - constants[R.LAND_PRICE_DECAY_PER_RING] / PERCENT
    ring_count = await rings_from_center(session, node)
    per_metre = rate * (decline ** ring_count)
    return max(1, money(per_metre * float(node.area_m2)))


async def is_vacant(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Whether the node is empty: only land with nothing on it is sold.

    The city's buildings (forge, market, administration) and nodes with a vein
    are not sold by this button: they are not an "empty plot" but working city
    property, and disposing of it is the authority's business, not a price list's.
    """
    if await built_area(session, node) > 0:
        return False
    #: The city's transit gate is a common road, not a plot (D-176).
    if (node.properties or {}).get("выход"):
        return False
    _, occupied = await slots(session, constants, node)
    if occupied > 0:
        return False
    from src.models.world import Vein

    vein = await session.scalar(
        select(Vein.id).where(Vein.node_id == node.id).limit(1)
    )
    return vein is None


async def buy(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    node: Node,
) -> Deed:
    """Buy an empty civic plot. In person: land is inspected on foot.

    Money goes to the city treasury, the buyer is issued a deed. Land outside a
    city is neither bought nor taken (D-198): there is nobody to set a price,
    nowhere to pay and nobody to issue the paper -- yet everyone may work and
    build on it.
    """
    from src.engine import city as town

    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело не покупает")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("участок покупают ногами: дойдите до него")
    if node.owner_identity_id is not None:
        raise NotForSale("участок уже за кем-то")
    if node.owner_city_id is None:
        raise NotForSale(
            "это не городская земля: за городом её не продают и не присваивают, "
            "но работать и строить там может всякий"
        )
    if not await is_vacant(session, constants, node):
        raise NotForSale(
            "узел не пустой: застройку и жилы города прейскурант не продаёт"
        )

    city = await town.by_id(session, node.owner_city_id)
    if city is None:  # pragma: no cover -- civic land without a city is a bug
        raise NotForSale("узел приписан к несуществующему городу")

    #: Who may take plots in the rings is answered by the code-law `build_permit`
    #: (D-089). By default -- citizens, and before D-160 that read as "everyone".
    if not town.may_take_city_land(
        catalog, city, await town.is_citizen(session, body.identity_id, city)
    ):
        raise NotForSale(
            f"«{city.name}» продаёт землю не всякому: код-закон build_permit — "
            f"«{town.law(catalog, city, 'build_permit')}». Вступите в граждане"
        )

    price = await price_of(session, constants, catalog, city, node)
    account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
    remainder = await ledger.balance(session, account.id)
    if remainder < price:
        raise NotEnoughMoney(
            f"участок стоит {price} минорных единиц, а на счету {remainder}"
        )

    treasury = await town.treasury(session, city)
    await ledger.transfer(
        session,
        PostingReason.TRADE,
        debit=account.id,
        credit=treasury.id,
        amount=price,
        memo={"выкуп участка": node.key, "город": city.name},
    )

    node.owner_identity_id = body.identity_id
    deed = await issue_deed(session, node, body.identity_id, paid=price)

    await events.record(
        session,
        EventKind.LAND_BOUGHT,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        city_id=str(city.id),
        price=price,
        deed_id=str(deed.id),
    )
    return deed


async def may_name(session: AsyncSession, body: Body, node: Node) -> bool:
    """Whether this body may name the node (D-178).

    The owner disposes of their own land, of civic land -- the authority with
    the `land` right: the same one it hands out that land with (D-089).
    Unowned land bears no name.
    """
    from src.engine import city as town
    from src.models.city import Power

    if node.owner_identity_id is not None:
        return node.owner_identity_id == body.identity_id
    if node.owner_city_id is None:
        return False
    city = await town.by_id(session, node.owner_city_id)
    return city is not None and await town.may(
        session, body.identity_id, city, Power.LAND
    )


async def rename(
    session: AsyncSession, body: Body, node: Node, name: str
) -> Node:
    """Name a plot. The nameplate is nailed on the spot, not from the Net (D-178).

    The label changes, not the node key: `terra.capital.lot2` is referenced by
    deeds, edges and events, and renaming may not break them.
    """
    from src.runtime import LAND_NAME_LIMIT

    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело ничего не переименовывает")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("до участка надо дойти: табличку прибивают на месте")
    if not await may_name(session, body, node):
        raise NotOwner(
            "участок не ваш: имя даёт хозяин, а городской земле — власть с "
            "правом на участки"
        )

    title = name.strip()
    if not title:
        raise BadName("у участка должно быть имя")
    if len(title) > LAND_NAME_LIMIT:
        raise BadName(f"имя длиннее {LAND_NAME_LIMIT} знаков")

    before, node.name = node.name, title
    await session.flush()
    await events.record(
        session,
        EventKind.LAND_RENAMED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        was=before,
        now=title,
    )
    return node


# --- deed (D-116) ------------------------------------------------------------


async def issue_deed(
    session: AsyncSession, node: Node, owner_id: uuid.UUID, *, paid: int = 0
) -> Deed:
    """Issue a deed for a plot. One per node: a repeated issue rewrites the
    holder -- that is a change of title, not a second deed."""
    existing = (
        await session.execute(select(Deed).where(Deed.node_id == node.id))
    ).scalar_one_or_none()
    if existing is not None:
        existing.owner_identity_id = owner_id
        existing.sale_price = None
        existing.sale_to_identity_id = None
        await session.flush()
        return existing

    deed = Deed(node_id=node.id, owner_identity_id=owner_id, paid=paid)
    session.add(deed)
    await session.flush()
    await events.record(
        session,
        EventKind.DEED_ISSUED,
        actor_identity_id=owner_id,
        node_id=node.id,
        deed_id=str(deed.id),
        paid=paid,
    )
    return deed


async def offer_deed(
    session: AsyncSession,
    identity: Identity,
    deed: Deed,
    price: int,
    *,
    to: Identity | None = None,
) -> Deed:
    """List a deed for sale: open or addressed. A remote action.

    A zero price takes the deed off sale.
    """
    if deed.owner_identity_id != identity.id:
        raise EstateError("бумага не ваша: продают своё")
    if price <= 0:
        deed.sale_price = None
        deed.sale_to_identity_id = None
    else:
        deed.sale_price = price
        deed.sale_to_identity_id = None if to is None else to.id
    await session.flush()

    await events.record(
        session,
        EventKind.DEED_OFFERED,
        actor_identity_id=identity.id,
        node_id=deed.node_id,
        deed_id=str(deed.id),
        price=deed.sale_price,
        to=None if to is None else to.name,
    )
    return deed


async def buy_deed(
    session: AsyncSession, buyer: Identity, deed: Deed
) -> Deed:
    """Buy a listed deed: money to the seller, title to the buyer.

    A sale contract in one transaction: no escrow is needed because both money
    and deed change hands at one moment. A remote action -- documents live in
    the Net.
    """
    if deed.sale_price is None:
        raise NotForSale("бумага не выставлена на продажу")
    if deed.owner_identity_id == buyer.id:
        raise EstateError("своя бумага не покупается")
    if deed.sale_to_identity_id is not None and deed.sale_to_identity_id != buyer.id:
        raise NotForSale("договор адресный: бумага обещана другому")

    price = int(deed.sale_price)
    account = await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)
    remainder = await ledger.balance(session, account.id)
    if remainder < price:
        raise NotEnoughMoney(f"бумага стоит {price}, а на счету {remainder}")

    seller = await ledger.account_for(
        session, AccountKind.IDENTITY, deed.owner_identity_id
    )
    await ledger.transfer(
        session,
        PostingReason.TRADE,
        debit=account.id,
        credit=seller.id,
        amount=price,
        memo={"договор купли-продажи": str(deed.id)},
    )

    previous = deed.owner_identity_id
    deed.owner_identity_id = buyer.id
    deed.sale_price = None
    deed.sale_to_identity_id = None

    #: The title is the ownership: the node passes together with the deed.
    node = await session.get(Node, deed.node_id)
    if node is not None:
        node.owner_identity_id = buyer.id
    await session.flush()

    await events.record(
        session,
        EventKind.DEED_SOLD,
        actor_identity_id=buyer.id,
        node_id=deed.node_id,
        deed_id=str(deed.id),
        price=price,
        seller=str(previous),
    )
    return deed


async def deeds_of(session: AsyncSession, identity_id: uuid.UUID) -> list[Deed]:
    return list(
        (
            await session.execute(
                select(Deed).where(Deed.owner_identity_id == identity_id)
            )
        ).scalars().all()
    )


async def deeds_on_sale(session: AsyncSession, identity_id: uuid.UUID) -> list[Deed]:
    """Deeds this identity may buy: open ones and those addressed to it."""
    rows = (
        await session.execute(
            select(Deed).where(
                Deed.sale_price.is_not(None),
                Deed.owner_identity_id != identity_id,
            )
        )
    ).scalars().all()
    return [
        deed
        for deed in rows
        if deed.sale_to_identity_id is None or deed.sale_to_identity_id == identity_id
    ]


# --- building (D-106, D-125) -------------------------------------------------


async def buildings_of(session: AsyncSession, node: Node) -> list[Building]:
    return list(
        (
            await session.execute(select(Building).where(Building.node_id == node.id))
        ).scalars().all()
    )


async def built_area(session: AsyncSession, node: Node, *, ground: bool = False) -> float:
    """Built area of the node: usable by default, the footprint when asked.

    Since storeys arrived (D-125) these are two different numbers: a two-storey
    house of ten metres takes ten metres of the plot and gives twenty of floor.
    Whatever is measured against the plot must ask for `ground`; machines,
    cargo and upkeep go by the usable area.
    """
    column = Building.footprint_m2 if ground else Building.area_m2
    total = await session.scalar(
        select(func.coalesce(func.sum(column), 0)).where(Building.node_id == node.id)
    )
    return float(total or 0)


async def under_construction(session: AsyncSession, node: Node) -> list[dict]:
    """Houses being built here right now: what and by when (D-125).

    Materials are written off at the start, and until this list existed the
    yard looked empty right after them -- as if the timber had vanished.
    """
    from src.models.job import Job, JobKind, JobState

    rows = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.BUILD_FINISH.value,
                Job.state == JobState.PENDING,
            ).order_by(Job.run_at)
        )
    ).scalars().all()
    return [
        {
            "area": float(job.payload.get("area", 0)),
            "floors": int(job.payload.get("floors", 1)),
            "strength": int(job.payload.get("strength", 1)),
            "ready_at": job.run_at.isoformat(),
        }
        for job in rows
        if job.payload.get("node") == str(node.id)
    ]


async def floor_mass(session: AsyncSession, node: Node) -> float:
    """How many kilograms lie on the node's floor (D-192).

    Only what lies loose: goods inside a chest are counted by the chest, not by
    the floor -- that is what a chest is for (D-181).
    """
    from src.constants import current_catalog
    from src.engine import gear, storage, world

    catalog = current_catalog()
    yard = await world.node_container(session, node)
    things = (
        await session.execute(select(Item).where(Item.container_id == yard.id))
    ).scalars().all()
    return sum(
        gear.mass_of(catalog, thing.type_key, amount_float(thing.amount))
        for thing in things
        if not _equipment(catalog, thing.type_key)
        and not storage.is_storage(catalog, thing.type_key)
    )


def _equipment(catalog, type_key: str) -> bool:
    """Machines and furniture pay for their place by slots, not by weight."""
    from src.constants.catalog import ItemKind

    try:
        return catalog.recipes.recipe(type_key).kind in (
            ItemKind.STATION,
            ItemKind.FURNITURE,
        )
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


async def space(
    session: AsyncSession, constants: Constants, node: Node
) -> dict[str, float]:
    """The node's area budget: what it holds and what is already taken (D-192).

    A building is the roof over the goods; without one they lie in the yard,
    and the yard is finite too. Equipment pays `build.slots_per_area` per
    piece, loose cargo pays by weight through `build.floor_per_m2`.
    """
    total_slots, taken_slots = await slots(session, constants, node)
    roofed = await built_area(session, node)
    #: No building -- the yard is the floor: the whole plot minus nothing.
    capacity = roofed if roofed > 0 else float(node.area_m2)
    lying = await floor_mass(session, node)
    by_cargo = lying / constants[R.BUILD_FLOOR_PER_M2]
    by_equipment = taken_slots * constants[R.BUILD_SLOTS_PER_AREA]
    return {
        "area": capacity,
        "roofed": roofed,
        "used": by_equipment + by_cargo,
        "cargo_mass": lying,
        "free": max(0.0, capacity - by_equipment - by_cargo),
        "slots": float(total_slots),
        "slots_used": float(taken_slots),
    }


async def slots(
    session: AsyncSession, constants: Constants, node: Node
) -> tuple[int, int]:
    """Capacity and occupancy: (total places, occupied places).

    A place is `build.slots_per_area` square metres of the building; it is
    taken by machines and furniture standing in the node.
    """
    from src.constants import current_catalog
    from src.constants.catalog import ItemKind
    from src.engine import world

    area = await built_area(session, node)
    in_total = int(area // constants[R.BUILD_SLOTS_PER_AREA])

    book = current_catalog().recipes
    yard = await world.node_container(session, node)
    things = (
        await session.execute(select(Item).where(Item.container_id == yard.id))
    ).scalars().all()
    occupied = 0
    for thing in things:
        try:
            recipe = book.recipe(thing.type_key)
        except Exception:  # noqa: BLE001 -- raw material at the machine has no recipe
            continue
        if recipe.kind in (ItemKind.STATION, ItemKind.FURNITURE):
            occupied += 1
    return in_total, occupied


def height_cap(constants: Constants, strength: int) -> int:
    """The ceiling of height for a durability tier (D-145).

    Timber and rope hold two floors; steel, stone and glass hold eight. The
    whole tier ladder is visible in the silhouette of a town.
    """
    caps = constants[R.BUILD_FLOORS_BY_STRENGTH]
    return int(caps[str(strength)])


def estimate(
    constants: Constants, *, footprint: float, floors: int, strength: int
) -> dict[str, float]:
    """The bill of materials for a house, by the vault formula `build.cost_per_area`.

    Each next floor costs `floor_cost_growth` times more than the one below --
    height grows geometrically, and so does the price of it. From
    `reinforce_from_floor` upwards a frame is needed, and that is `reinforce_k`
    on top. The tier multiplies the lot: a class three times cheaper to keep
    costs four times more to raise (D-125, D-145).
    """
    growth = constants[R.BUILD_FLOOR_COST_GROWTH]
    threshold = constants[R.BUILD_REINFORCE_FROM_FLOOR]
    frame = constants[R.BUILD_REINFORCE_K]
    tier = float(constants[R.BUILD_STRENGTH_K][str(strength)])

    #: The sum over floors, not "area times a coefficient": the eighth floor is
    #: expensive by itself, and averaging would hide exactly that.
    per_footprint = sum(
        growth ** (floor - 1) * (frame if floor >= threshold else 1.0)
        for floor in range(1, floors + 1)
    )
    norms = constants[R.BUILD_MATERIALS_PER_M2]
    return {
        name: float(qty) * footprint * tier * per_footprint
        for name, qty in norms.items()
    }


def bill(
    constants: Constants, *, footprint: float, floors: int, strength: int
) -> dict[str, float]:
    """The same bill, in the amounts the world can actually hand over (D-212).

    A counted material goes into the wall whole: two and a half boards is
    three boards. `estimate` stays as the formula wrote it -- the labour ratio
    in `build_minutes` is about how heavy the lot is, not about what the saw
    could not halve -- and this is what is shown and what is written off.
    """
    from src.engine import goods

    return {
        name: goods.whole(name, qty, up=True)
        for name, qty in estimate(
            constants, footprint=footprint, floors=floors, strength=strength
        ).items()
    }


def build_minutes(
    constants: Constants, *, footprint: float, floors: int, strength: int
) -> float:
    """The term: assembly labour, with the same correction for height and tier."""
    lot = estimate(constants, footprint=footprint, floors=floors, strength=strength)
    plain = estimate(constants, footprint=footprint, floors=1, strength=1)
    #: Effort follows the bill: what is dearer in materials is longer in hands.
    heaviness = (sum(lot.values()) / sum(plain.values())) if sum(plain.values()) else 1.0
    return footprint * constants[R.BUILD_LABOR_PER_M2] * MINUTES_PER_HOUR * heaviness


async def construct(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    area: float,
    *,
    floors: int = 1,
    strength: int = 1,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> Job:
    """Build a house on your own plot. Materials at once, the building on schedule.

    `area` is the **footprint**: the ground the house takes. Storeys stand on
    it, and the usable area is their sum -- that is how a plot stops being a
    hard limit on a workshop (D-125, D-145).

    The first durability tier is wood and rope (`build.materials_per_m2`).
    Civic land is built by the city -- here only your own (D-089); land outside
    a city has no owner and is open to all (D-198).
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело не строит")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("строят ногами: дойдите до участка")
    nobodys = node.owner_identity_id is None and node.owner_city_id is None
    if not nobodys and node.owner_identity_id != body.identity_id:
        raise EstateError("участок не ваш: строят у себя")
    if area <= 0:
        raise EstateError("здание нулевой площади — это двор, он уже есть")
    if floors < 1:
        raise EstateError("дом без этажей — это яма")

    cap = height_cap(constants, strength)
    if floors > cap:
        raise TooTall(
            f"ступень прочности {strength} держит {cap} этажей, просят {floors}: "
            "выше — только на прочном классе"
        )

    occupied = await built_area(session, node, ground=True)
    if occupied + area > float(node.area_m2):
        raise NoRoom(
            f"на участке {float(node.area_m2):.0f} м², застроено {occupied:.0f}: "
            f"ещё {area:.0f} не помещается"
        )

    #: Materials come from the vault, per metre of floor. Written off at once:
    #: construction has started, and the timber is already in the wall, not in the sack.
    from src.engine import craft, world

    needed = bill(constants, footprint=area, floors=floors, strength=strength)
    pocket = await world.body_container(session, body)
    #: Which stacks go into the wall is the builder's choice by tier (D-058).
    stock = await craft._stock(session, pocket, tuple(needed), tiers=tiers)  # noqa: SLF001
    for pick in craft._pick(stock, needed):  # noqa: SLF001
        if pick.item.amount > pick.take:
            pick.item.amount -= pick.take
        else:
            await session.delete(pick.item)
    await session.flush()

    minutes = build_minutes(
        constants, footprint=area, floors=floors, strength=strength
    )
    term = moment + timedelta(minutes=minutes)
    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        work="build",
        area=area,
        floors=floors,
        strength=strength,
        spent=needed,
        ready_at=term.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BUILD_FINISH,
        term,
        payload={
            "node": str(node.id),
            "area": area,
            "floors": floors,
            "strength": strength,
            "identity": str(body.identity_id),
        },
        dedup_key=f"build:{node.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise EstateError("стройка уже поставлена")
    return job


# --- demolition (D-205) ------------------------------------------------------


def demolish_minutes(constants: Constants, houses: list[Building]) -> float:
    """The term of taking apart: `build.demolish_labor_k` of the raising labour."""
    return constants[R.BUILD_DEMOLISH_LABOR_K] * sum(
        build_minutes(
            constants,
            footprint=float(house.footprint_m2),
            floors=house.floors,
            strength=house.strength,
        )
        for house in houses
    )


def salvage(constants: Constants, houses: list[Building]) -> dict[str, float]:
    """Materials coming back: `build.demolish_salvage` of what the houses cost.

    Counted from the bill of the very same houses rather than from their area:
    height and durability tier made the bill non-linear (D-125, D-145), and a
    demolition that returned by area would pay for a tower as for a shed.
    """
    share = constants[R.BUILD_DEMOLISH_SALVAGE]
    back: dict[str, float] = {}
    for house in houses:
        lot = estimate(
            constants,
            footprint=float(house.footprint_m2),
            floors=house.floors,
            strength=house.strength,
        )
        for name, qty in lot.items():
            back[name] = back.get(name, 0.0) + qty * share
    #: What comes back comes back whole, and downwards (D-212): taking a house
    #: apart must not mint a board that was not in it.
    from src.engine import goods

    return {name: goods.whole(name, qty) for name, qty in back.items()}


async def demolishing(session: AsyncSession, node: Node) -> bool:
    """Whether a demolition is already under way here.

    Without this check a second order would be taken while the first is still
    running, and each of them carries its own salvage in the payload: the house
    is one, the materials would come back twice. Matter does not multiply.
    """
    from src.models.job import JobState

    rows = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.BUILD_DEMOLISH.value,
                Job.state == JobState.PENDING,
            )
        )
    ).scalars().all()
    return any(job.payload.get("node") == str(node.id) for job in rows)


async def demolish_blockers(
    session: AsyncSession, constants: Constants, node: Node
) -> list[str]:
    """What stands in the way of demolition, in words -- before the work, not after.

    The yard empties **first**: after the demolition the machines have nowhere
    to stand and the cargo has no room on the ground. Refusing up front is the
    only honest order -- possessions in this world are lost to an eruption
    (D-197), not to a button.
    """
    reasons: list[str] = []
    _, occupied = await slots(session, constants, node)
    if occupied > 0:
        reasons.append(
            f"в здании стоит оборудование ({occupied}): рабочие станции и мебель "
            "забирают до сноса — после него им негде стоять"
        )
    lying = await floor_mass(session, node)
    yard = float(node.area_m2) * constants[R.BUILD_FLOOR_PER_M2]
    if lying > yard:
        reasons.append(
            f"на полу {lying:.1f} кг, а двор держит {yard:.1f} кг: "
            "лишнее увезите или уложите в сундук"
        )
    if await under_construction(session, node):
        reasons.append("здесь идёт стройка: сначала дождитесь её конца")
    if await demolishing(session, node):
        reasons.append("снос уже идёт: второй раз его не заказывают")
    return reasons


async def demolish(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    *,
    now: datetime | None = None,
) -> Job:
    """Take a house apart. The work goes by time, the materials come back at its end.

    Whose house may be taken apart follows exactly whose house may be built
    (`construct`): one's own plot -- and any nobody's land, where work is open to
    everyone and always will be (D-198). A homestead beyond the walls is put up
    by whoever came and taken down by whoever came: there is no title there to
    make one of them the owner.

    Somebody else's **civic** plot is another matter: there the land has a paper,
    and a house on it is demolished by a court order (D-095), not by this button.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело не сносит")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("сносят ногами: дойдите до участка")
    nobodys = node.owner_identity_id is None and node.owner_city_id is None
    if not nobodys and node.owner_identity_id != body.identity_id:
        raise NotOwner(
            "участок не ваш: сносят своё, а чужую городскую застройку "
            "разбирают по решению суда, а не кнопкой"
        )

    houses = await buildings_of(session, node)
    if not houses:
        raise NoBuilding("сносить нечего: здания на участке нет")
    blocking = await demolish_blockers(session, constants, node)
    if blocking:
        raise NoRoom("; ".join(blocking))

    back = salvage(constants, houses)
    minutes = demolish_minutes(constants, houses)
    term = moment + timedelta(minutes=minutes)
    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        work="demolish",
        area=await built_area(session, node),
        back=back,
        ready_at=term.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BUILD_DEMOLISH,
        term,
        payload={
            "node": str(node.id),
            "back": back,
            "identity": str(body.identity_id),
        },
        dedup_key=f"demolish:{node.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise EstateError("снос уже поставлен")
    return job


@handler(JobKind.BUILD_DEMOLISH)
async def finish_demolish(session: AsyncSession, job: Job) -> None:
    """The house is taken apart: the plot is empty again, part of the material is back.

    Where the salvage goes is decided the same way as with a batch at a machine:
    into the hands of whoever is standing here, and onto the floor if they left or
    died. Matter does not vanish with whoever ordered the work.
    """
    from src.engine import world

    node = await session.get(Node, uuid.UUID(job.payload["node"]))
    if node is None:  # pragma: no cover
        raise EstateError(f"снос {job.id} ссылается в никуда")

    houses = await buildings_of(session, node)
    if not houses:
        #: Nothing left to take apart -- the work has already been done, and the
        #: salvage with it. A repeated job must not hand out the materials twice.
        return
    for house in houses:
        await session.delete(house)
    await session.flush()

    body = None if job.body_id is None else await session.get(Body, job.body_id)
    at_hand = (
        body is not None
        and body.state is BodyState.ALIVE
        and body.node_id == node.id
    )
    where = (
        await world.body_container(session, body)
        if at_hand and body is not None
        else await world.node_container(session, node)
    )
    for name, qty in (job.payload.get("back") or {}).items():
        given = to_amount(float(qty))
        if given <= 0:
            continue
        session.add(Item(container_id=where.id, type_key=name, amount=given))
    await session.flush()

    await events.record(
        session,
        EventKind.BUILDING_DEMOLISHED,
        actor_identity_id=uuid.UUID(job.payload["identity"]),
        node_id=node.id,
        back=job.payload.get("back") or {},
        to_hands=at_hand,
    )


@handler(JobKind.BUILD_FINISH)
async def finish_build(session: AsyncSession, job: Job) -> None:
    """Construction is over: the building stands on the plot."""
    node = await session.get(Node, uuid.UUID(job.payload["node"]))
    if node is None:  # pragma: no cover
        raise EstateError(f"стройка {job.id} ссылается в никуда")
    #: Old jobs from before storeys carry no `floors`: one floor, first tier.
    footprint = float(job.payload["area"])
    floors = int(job.payload.get("floors", 1))
    building = Building(
        node_id=node.id,
        #: Usable area is the sum of the floors; the ground taken is the footprint.
        area_m2=footprint * floors,
        footprint_m2=footprint,
        floors=floors,
        strength=int(job.payload.get("strength", 1)),
    )
    session.add(building)
    await session.flush()

    await events.record(
        session,
        EventKind.BUILDING_BUILT,
        actor_identity_id=uuid.UUID(job.payload["identity"]),
        node_id=node.id,
        building_id=str(building.id),
        area=float(building.area_m2),
        floors=floors,
    )
