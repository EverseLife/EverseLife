# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""View builders: what of the world goes to the client, in the client's shape.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _body, _node, _stamp, goods_key, tier_key
from src.api.registry import Refused
from src.constants import Constants, current, current_catalog
from src.constants.catalog import ItemKind
from src.engine import (
    breed,
    climate,
    craft,
    energy,
    estate,
    ledger,
    library,
    market,
    mining,
    station,
    storage,
    transport,
    world,
)
from src.engine import city as town
from src.models.craft import BatchState, CraftBatch
from src.models.identity import Body, Identity, Knowledge, KnowledgeKind
from src.models.ledger import AccountKind
from src.models.market import (
    Order,
    OrderSide,
    OrderState,
    Reservation,
    ReservationState,
)
from src.models.mining import MiningSession
from src.models.plant import Variety
from src.models.travel import Harness
from src.models.world import Node
from src.units import amount_float, money_str


async def _deed_view(db: AsyncSession, deed) -> dict[str, Any]:
    node = await db.get(Node, deed.node_id)
    holder = await db.get(Identity, deed.owner_identity_id)
    to_whom = (
        None
        if deed.sale_to_identity_id is None
        else await db.get(Identity, deed.sale_to_identity_id)
    )
    return {
        "id": str(deed.id),
        "node": None if node is None else node.key,
        "name": None if node is None else node.name,
        "area": None if node is None else float(node.area_m2),
        "owner": None if holder is None else holder.name,
        "paid": deed.paid,
        "sale_price": deed.sale_price,
        "sale_to": None if to_whom is None else to_whom.name,
        "issued_at": deed.issued_at.isoformat(),
    }


async def _deeds(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Own deeds -- for the sidebar's "holdings" tab (D-116)."""
    return [await _deed_view(db, deed) for deed in await estate.deeds_of(db, identity_id)]


async def _city(state: dict, db: AsyncSession, message: dict):
    """The city in question: named by node key, or the one where the body stands."""
    if message.get("city"):
        node = await _node(db, str(message["city"]))
    else:
        body = await _body(db, state["identity_id"])
        if body is None:
            raise Refused(key="cmd-no-live-body-name-city")
        node = await db.get(Node, body.node_id)
    city = await town.of_node(db, node)
    if city is None:
        raise Refused(key="cmd-no-city-here")
    return city


async def _identity_by_name(db: AsyncSession, name: str) -> Identity:
    found = (await db.execute(select(Identity).where(Identity.name == name))).scalar_one_or_none()
    if found is None:
        raise Refused(key="cmd-no-identity-named", name=name)
    return found


def _tiers(message: dict) -> dict[str, str]:
    """`tiers: {input: tier}` from a request; anything else reads as "no choice".

    Canonicalized like every other inbound spelling: a pre-release tab sends
    Russian goods and tier words, and without the translation the choice would
    be silently ignored rather than refused.
    """
    raw = message.get("tiers")
    if not isinstance(raw, dict):
        return {}
    return {goods_key(name): tier_key(tier) for name, tier in raw.items() if tier}


def _sight(session: MiningSession, sight: mining.Sight) -> dict[str, Any]:
    """Only what the player sees goes out.

    Built from `Sight`, not from the session model -- so that a hidden number
    physically cannot end up in the reply by oversight.
    """
    payload = asdict(sight)
    payload["pace"] = sight.pace.value
    payload["state"] = sight.state.value
    payload["session"] = str(session.id)
    return payload


async def _bench(
    db: AsyncSession, node: Node, body: Body, *, furniture: bool = False
) -> list[dict[str, Any]]:
    """The node's machines by name: quality, condition and who occupies them (D-150).

    The node scene is built from this list (D-176): which windows to show, and
    which machine one can stand at right now. With `furniture=True` the same
    for furniture: a bed and a shelf are not machines, and the client shows
    them in a separate window.
    """

    expected_value = ItemKind.FURNITURE if furniture else ItemKind.STATION
    book = current_catalog().recipes
    items = await world.node_things(db, node)

    out: list[dict[str, Any]] = []
    for item in items:
        #: A relic has no recipe -- nobody made it -- but it is machinery all
        #: the same, and the scene of a Forerunner city is built out of exactly
        #: those (D-232). Furniture it never is.
        relic = book.is_relic(item.type_key)
        if not relic:
            try:
                recipe = book.recipe(item.type_key)
            except Exception:  # noqa: BLE001 -- raw material at the machine has no recipe
                continue
            if recipe.kind is not expected_value:
                continue
        elif furniture:
            continue
        out.append(
            {
                "id": str(item.id),
                "goods": item.type_key,
                "quality": None if item.quality is None else float(item.quality),
                "condition": float(item.condition),
                "busy": item.busy_body_id is not None,
                "mine": item.busy_body_id == body.id,
                #: Charge belongs to the battery standing here as a machine
                #: (D-179). The sign is the thing's type, not whether the field is
                #: filled: an empty battery is zero energy, not "not a battery".
                "charge": (
                    round(energy.charge_of(current(), item), 2)
                    if item.type_key in world.station_names(energy.BATTERY)
                    else None
                ),
            }
        )
    return sorted(out, key=lambda machine: machine["goods"])


async def _clock(db: AsyncSession, constants, node: Node) -> dict[str, Any]:
    """The planet's local clock: where the count starts and how long a day is.

    The origin is when the world's first node appeared: the world is eternal
    and has no wipes (D-007), so that moment is stable forever. Day length is
    the vault's (D-029) -- Terra's day is 38 hours, and none of them match the
    player's own clock on purpose.
    """

    origin = await world.epoch(db)
    return {
        "planet": node.planet.value,
        "epoch": None if origin is None else origin.isoformat(),
        #: Each planet counts its own day (OQ-028, D-261): the clock used to
        #: say Terra's 38 hours on every planet.
        "day_hours": climate.day_hours_of(constants, node.planet),
    }


async def _climate(db: AsyncSession, constants, node: Node) -> dict[str, Any] | None:
    """The place's climate for the land window (D-261).

    Only what the client cannot derive (D-225): the node's mean and the
    planet's swing, the day's light and the rainfall. The current temperature
    and the night are the client's arithmetic over `look.clock` -- computed
    there they stay alive between looks, and by construction agree with the
    drawn hand. Empty where exploration never wrote a temperature: such a
    place has no climate gate.
    """
    mean = climate.mean_temperature(node)
    if mean is None:
        return None
    return {
        "temperature": {
            "mean": mean,
            "swing": climate.swing_of(constants, node.planet),
        },
        "light": {"day": await climate.daylight(db, constants, node)},
        "precipitation": climate.precipitation(node),
    }


async def _storages(db: AsyncSession, constants, node: Node, body: Body) -> list[dict[str, Any]]:
    """Node storages with contents (D-181).

    That a chest exists is visible to all -- it stands in the room. What is
    inside is seen only by whoever may open it: otherwise "look" would become a
    way around the rule "do not touch what is not yours".
    """
    catalog = current_catalog()
    things = await world.node_things(db, node)
    allowed = await station.may_build(db, body, node)

    out: list[dict[str, Any]] = []
    for thing in things:
        limit = storage.capacity(catalog, thing.type_key)
        if not limit:
            continue
        out.append(
            {
                "id": str(thing.id),
                "goods": thing.type_key,
                "capacity": limit,
                "mass": round(await storage.stored_mass(db, catalog, thing), 2),
                "mine": allowed,
                "content": (
                    await _things(db, constants, inside_)
                    if allowed and (inside_ := await storage.inside(db, thing, create=False))
                    else []
                ),
            }
        )
    return sorted(out, key=lambda chest: chest["goods"])


async def _vehicles(db: AsyncSession, constants, node: Node) -> list[dict[str, Any]]:
    """Vehicles standing in this node (D-157).

    Separate from machines: nobody stands at a wagon to work, they harness to
    it. Whether a vehicle is taken by somebody else's harness is visible at once
    -- otherwise the player would learn it only from a refusal.
    """

    cat = current_catalog()
    things = await world.node_things(db, node)
    harnessed_ = (
        set(
            (
                await db.execute(
                    select(Harness.item_id).where(Harness.item_id.in_([t.id for t in things]))
                )
            )
            .scalars()
            .all()
        )
        if things
        else set()
    )
    out: list[dict[str, Any]] = []
    for item in things:
        if not transport.is_vehicle(cat, item.type_key):
            continue
        try:
            capacity = transport.capacity(constants, item.type_key)
        except transport.NotVehicle:
            #: The vault did not name its capacity -- show it as is, and let the
            #: harness refuse: lying with a number is worse than not showing it.
            capacity = None
        out.append(
            {
                "id": str(item.id),
                "goods": item.type_key,
                "condition": float(item.condition),
                "capacity": capacity,
                "speed_k": transport.speed(constants, item.type_key),
                "taken": item.id in harnessed_,
            }
        )
    return sorted(out, key=lambda cart: cart["goods"])


async def _money(db: AsyncSession, identity_id: uuid.UUID) -> str:
    """The identity's account. The balance is the sum of postings; there is no
    "money" field. No account yet -- nothing was ever posted -- is zero, not a
    new row: a read does not write."""
    account = await ledger.find_account(db, AccountKind.IDENTITY, identity_id)
    return money_str(0 if account is None else await ledger.balance(db, account.id))


async def _things(db: AsyncSession, constants, container) -> list[dict[str, Any]]:
    """Container contents as the owner sees them: with a number and a tier.

    A vessel comes with its fill (D-230): a canister in the hands is the
    water in it, and the client cannot see inside otherwise -- a liquid
    never lies in the pocket by itself.
    """
    return await _shown(db, constants, await world.contents(db, container))


async def _shown(db: AsyncSession, constants, items) -> list[dict[str, Any]]:
    """The same rows for things already read -- for a yard that may not exist:
    `world.node_things` answers about a place without writing to it."""
    catalog = current_catalog()
    #: One reading for every vessel at once, not one per canister.
    vessels = [item for item in items if storage.is_vessel(catalog, item.type_key)]
    held = await storage.contents_of(db, vessels)
    fills = {
        vessel.id: await _listed(db, constants, catalog, held[vessel.id]) for vessel in vessels
    }
    return await _listed(db, constants, catalog, items, fills)


async def _listed(
    db: AsyncSession,
    constants,
    catalog,
    items,
    fills: dict[uuid.UUID, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """The rows of a list of things: the names behind the marks and cultivars
    are read once for the whole list."""
    if not items:
        return []
    #: The mark is shown as a name: the player must see whose work it is (D-058).
    marks = await _makers(db, items)
    cultivars = await _varieties(db, catalog, items)
    return [
        {
            "id": str(item.id),
            "goods": item.type_key,
            "amount": amount_float(item.amount),
            "quality": None if item.quality is None else float(item.quality),
            "tier": market.tier_of(
                constants, None if item.quality is None else float(item.quality)
            ),
            "condition": float(item.condition),
            "flavor": item.flavor,
            #: For a knowledge carrier: what is written on it, and the name the
            #: counter knows it by -- "Рецепт: Стекло" (D-209).
            "recipe": item.recipe_key,
            "key": market.goods_key(item),
            "food": _edible(catalog, item.type_key),
            "ingredient": catalog.recipes.is_ingredient(item.type_key),
            "spoils_at": None if item.spoils_at is None else item.spoils_at.isoformat(),
            #: Coin fineness is visible to all: the vault data has no assay tool,
            #: and hiding fineness without a way to learn it is not allowed (OQ 01-currency).
            "fineness": None if item.fineness is None else float(item.fineness),
            "maker": marks.get(item.maker_identity_id),
            #: Weight and slot come from vault data (D-146). An item unknown to
            #: the catalog gets no mass: the hole must be visible.
            "mass": catalog.recipes.mass_of(item.type_key),
            "slot": catalog.recipes.slot_of(item.type_key),
            #: For seeds: whose cultivar and how much strength is left in the batch (D-057).
            "variety": cultivars.get(item.variety_id),
            "vigor": None if item.vigor is None else float(item.vigor),
            #: For a battery: charge with self-discharge -- what is really in it
            #: now, not what was poured in yesterday (D-071). One never charged
            #: shows zero, not nothing: otherwise it is invisible in "holdings"
            #: until the first charge.
            "charge": (
                round(energy.charge_of(constants, item), 1)
                if item.type_key in world.station_names(energy.BATTERY)
                else None
            ),
            #: For a vessel only: what is poured in (D-230). The capacity is
            #: the catalog's (`store`), the client has it already (D-225).
            **({"content": fills[item.id]} if fills and item.id in fills else {}),
        }
        for item in items
    ]


async def _varieties(db: AsyncSession, catalog, items) -> dict[uuid.UUID, dict[str, str | int]]:
    """How the cultivar behind each seed lot is named on the wire (D-251).

    A plants-domain key, the author's literal mark, or a nameless hybrid's
    generation -- `breed.shown_as` decides, and the client says the words.
    """
    ids = {item.variety_id for item in items if item.variety_id is not None}
    if not ids:
        return {}
    rows = await db.execute(select(Variety).where(Variety.id.in_(ids)))
    return {cultivar.id: breed.shown_as(catalog, cultivar) for cultivar in rows.scalars().all()}


async def _makers(db: AsyncSession, items) -> dict[uuid.UUID, str]:
    """Craftsmen's names by item marks, in one query."""
    ids = {item.maker_identity_id for item in items if item.maker_identity_id is not None}
    if not ids:
        return {}
    rows = await db.execute(select(Identity.id, Identity.name).where(Identity.id.in_(ids)))
    return {row[0]: row[1] for row in rows}


def _edible(catalog, type_key: str) -> bool:
    try:
        return catalog.recipes.recipe(type_key).food
    except Exception:  # noqa: BLE001 -- raw material has no recipe
        return False


async def _knowledge(
    db: AsyncSession,
    identity_id: uuid.UUID,
    *,
    kind: KnowledgeKind = KnowledgeKind.RECIPE,
) -> list[str]:
    rows = await db.execute(
        select(Knowledge.key).where(Knowledge.identity_id == identity_id, Knowledge.kind == kind)
    )
    return sorted(row[0] for row in rows)


async def _discovered(db: AsyncSession, identity_id: uuid.UUID) -> list[str]:
    """Recipes this identity opened by experiment: the discoverer's mark (D-064)."""
    rows = await db.execute(
        select(Knowledge.key).where(
            Knowledge.identity_id == identity_id,
            Knowledge.kind == KnowledgeKind.RECIPE,
            Knowledge.discovered.is_(True),
        )
    )
    return sorted(row[0] for row in rows)


async def _pioneer_names(
    db: AsyncSession, keys: Sequence[str] | Select[tuple[str]]
) -> dict[str, str]:
    """The first discoverer's name per recipe key (D-064, D-259).

    The pioneer is whoever holds the **earliest** `discovered` row for the
    key: the name is bound to the recipe forever and shown to everyone who
    uses it. Founding recipes were opened by nobody and have no entry.
    `keys` is whatever `IN` takes: a list of keys, or a selectable of them.
    """
    rows = await db.execute(
        select(Knowledge.key, Identity.name)
        .join(Identity, Identity.id == Knowledge.identity_id)
        .where(
            Knowledge.kind == KnowledgeKind.RECIPE,
            #: The bare column, not `.is_(True)`: the latter renders `IS true`,
            #: which the planner refuses to match against the partial
            #: `ix_knowledge_pioneer` (`WHERE discovered`) -- see the model.
            Knowledge.discovered,
            Knowledge.key.in_(keys),
        )
        #: DISTINCT ON keeps the first row per key of the given order: the
        #: earliest acquisition wins, the id breaks a same-instant tie.
        .distinct(Knowledge.key)
        .order_by(Knowledge.key, Knowledge.acquired_at, Knowledge.id)
    )
    return {key: name for key, name in rows.all()}


async def _pioneers(db: AsyncSession, identity_id: uuid.UUID) -> dict[str, str]:
    """The map the `knowledge` part rides (D-259): only the recipes this
    identity knows are answered -- the details window is where it is read."""
    mine = select(Knowledge.key).where(
        Knowledge.identity_id == identity_id, Knowledge.kind == KnowledgeKind.RECIPE
    )
    return await _pioneer_names(db, mine)


def _buy_floor(constants: Constants, order: Order) -> int | None:
    """The buy's quality floor (D-239), but only the part the tier does not say.

    A floor named by hand inside the band cannot be recovered from the tier,
    so it rides along (D-225). The band's own start is derivable from the tier
    and the `/public` tiers constant, and a sell has no floor at all -- both
    come back as None.
    """
    if order.side is not OrderSide.BUY or order.min_quality is None:
        return None
    try:
        band_start = int(market.tier_span(constants, order.tier)[0])
    except market.BadOrder:
        #: A tier redrawn out of the constants must not silence the whole
        #: readonly `orders`: the stored floor goes out as it is.
        return order.min_quality
    if order.min_quality == band_start:
        return None
    return order.min_quality


async def _orders(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Own active orders, with the node each one stands in.

    The node is here for the same reason it is on a reservation: without it
    nothing outside the server can tell how much of the goods on the terminal
    shelf is still free to sell. The shelf is `look.stall`, what is committed
    is the sum of one's own sell orders *in this node* -- and orders in another
    node must not be subtracted from it (D-225: the key is here because the
    client cannot derive it).
    """
    constants = current()
    rows = (
        await db.execute(
            select(Order, Node.name, Node.key)
            .join(Node, Node.id == Order.node_id)
            .where(Order.identity_id == identity_id, Order.state == OrderState.ACTIVE)
        )
    ).all()
    return [
        {
            "id": str(order.id),
            "side": order.side.value,
            "goods": order.type_key,
            "tier": order.tier,
            #: Present only when there is something the tier does not say:
            #: a null on every sell and default buy would be dead weight.
            **(
                {"min_quality": floor}
                if (floor := _buy_floor(constants, order)) is not None
                else {}
            ),
            "price": order.price,
            "left": amount_float(order.amount_left),
            "node": name,
            "node_key": key,
        }
        for order, name, key in rows
    ]


async def _reservations(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Own reservations: where, what, until when and how much was deposited."""
    rows = (
        await db.execute(
            select(Reservation, Node.name, Node.key)
            .join(Node, Node.id == Reservation.node_id)
            .where(
                Reservation.buyer_identity_id == identity_id,
                Reservation.state == ReservationState.HELD,
            )
        )
    ).all()
    return [
        {
            "id": str(reservation.id),
            "goods": reservation.type_key,
            "tier": reservation.tier,
            "amount": amount_float(reservation.amount),
            "price": reservation.price,
            "deposit": reservation.deposit,
            "node": name,
            "node_key": key,
            #: When it was taken, so the deadline bar has a beginning to
            #: measure the remainder against -- the same reason as for a batch.
            "placed_at": reservation.created_at.isoformat(),
            "expires_at": reservation.expires_at.isoformat(),
        }
        for reservation, name, key in rows
    ]


async def _batches(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Jobs: the works under way and the ones waiting their turn (D-209).

    A work goes on only while the master stands at the machine; the rest of
    theirs wait in the order they were started, and a frozen one waits for the
    master to come back. All of it is shown: the queue is the player's plan.
    """
    rows = (
        (
            await db.execute(
                select(CraftBatch)
                .join(Body, Body.id == CraftBatch.body_id)
                .where(
                    Body.identity_id == identity_id,
                    CraftBatch.state.in_([BatchState.RUNNING, BatchState.WAITING]),
                )
                .order_by(CraftBatch.started_at.asc(), CraftBatch.id.asc())
            )
        )
        .scalars()
        .all()
    )

    #: Where each work is, by name: a frozen batch is waited for in its node,
    #: and the player must see which one to walk back to.
    places: dict[uuid.UUID, str] = {}
    wanted = {batch.node_id for batch in rows}
    if wanted:
        for node in (await db.execute(select(Node).where(Node.id.in_(wanted)))).scalars().all():
            places[node.id] = node.name

    body = await _body(db, identity_id)

    out: list[dict[str, Any]] = []
    for batch in rows:
        running = batch.state is BatchState.RUNNING
        #: Why a waiting batch is not moving -- the client says it in words:
        #: behind another work of yours, frozen while you are away (on the
        #: road, in the field, elsewhere), or no free machine here.
        if running:
            why = None
        elif body is None or not await craft.present(db, body, batch.node_id):
            why = "away"
        elif any(other.state is BatchState.RUNNING for other in rows):
            why = "queued"
        else:
            why = "no_station"
        out.append(
            {
                "id": str(batch.id),
                "work": batch.kind.value,
                "output": batch.output,
                "units": amount_float(batch.units),
                "quality": float(batch.quality),
                #: The machine's name -- the location screen lists the node's
                #: objects with what each is doing, and "Кузница · гвозди ×200"
                #: cannot be assembled otherwise. Made by hand has no station.
                "station": batch.station,
                "state": batch.state.value,
                "waiting": why,
                "node": world.get(batch.node_id),
                #: Both ends of the term, not just the far one: the deadline bar
                #: shows a share of the whole, and a share needs a beginning.
                #: The near end is the current run, not the first start -- a
                #: frozen and resumed batch shows the time left, not the days away.
                "started_at": _stamp(batch.run_started_at or batch.started_at),
                "ready_at": _stamp(batch.ready_at),
                "left_seconds": (
                    None if batch.remaining_seconds is None else float(batch.remaining_seconds)
                ),
                "recipe": batch.recipe_key,
            }
        )
    return out


async def _shelf(db: AsyncSession, node: Node) -> list[dict[str, Any]]:
    """The library's list with contributors' names (D-209) and, where the
    recipe was opened by somebody's experiment, the first discoverer's
    (D-259). The contribution and the discovery are different names -- who
    brought the carrier and who opened the recipe -- and the shelf says
    both. A key carrying nothing is not sent (D-225): founding recipes
    have no pioneer at all.
    """
    rows = await library.entries(db, node)
    names = await library.contributors(db, rows)
    pioneers = {} if not rows else await _pioneer_names(db, [entry.recipe for entry in rows])
    return [
        {
            "recipe": entry.recipe,
            "contributor": names.get(entry.contributor_identity_id),
            **({"pioneer": pioneers[entry.recipe]} if entry.recipe in pioneers else {}),
        }
        for entry in rows
    ]


def _optional_uuid(value: str | None) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(value)
