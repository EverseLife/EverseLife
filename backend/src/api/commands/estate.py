# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Land, deeds, building.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _identity
from src.api.commands.views import _deed_view, _identity_by_name, _money, _things, _tiers
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import (
    estate,
    gear,
    world,
)
from src.models.estate import Deed
from src.models.world import Node


@command("land.buy")
async def _land_buy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Buy an empty civic plot: the price depends on the distance to the bioprinter.

    Proceeds go to the city treasury, the buyer gets a deed (D-089, D-116).
    """
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    deed = await estate.buy(db, current(), current_catalog(), body, node)
    return {
        "bought": node.key,
        "deed": str(deed.id),
        "paid": deed.paid,
        "money": await _money(db, state["identity_id"]),
    }


@command("land.cede")
async def _land_cede(state: dict, db: AsyncSession, message: dict) -> dict:
    """Hand your plot back to the city: from now on the treasury maintains it.

    The mirror of `land.buy` and `city.allot`, and it stands in the same place
    as they do -- at the plot. Nothing is paid back: a plot is given up, not
    sold back (D-089, D-149).
    """
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    city = await town.cede(db, body, node)
    return {"ceded": node.key, "city": city.name}


@command("land.rename")
async def _land_rename(state: dict, db: AsyncSession, message: dict) -> dict:
    """Name a plot. In person and only by whoever disposes of it."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    await estate.rename(db, body, node, str(message.get("name", "")))
    return {"renamed": node.key, "name": node.name}


@command("land.emblem")
async def _land_emblem(state: dict, db: AsyncSession, message: dict) -> dict:
    """Nail a map mark on the plot, or take it down: empty means down (D-238).

    The same right and the same spot as the nameplate. The list of marks is
    the engine's -- the map must not be forgeable into the world's own signs.
    """
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    asked = str(message.get("emblem") or "").strip()
    await estate.emblem(db, body, node, asked or None)
    return {"marked": node.key, "emblem": asked or None}


@command("land.describe")
async def _land_describe(state: dict, db: AsyncSession, message: dict) -> dict:
    """Write the plot's description, or wipe it: empty means wiped (D-238).

    The same right and the same spot as the nameplate and the emblem.
    """
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    await estate.describe(db, body, node, str(message.get("about") or ""))
    return {"described": node.key, "about": estate.public_about(node)}


@command("build.construct")
async def _build_construct(state: dict, db: AsyncSession, message: dict) -> dict:
    """Build a house on your own plot. Materials at once, the building on schedule.

    `area` is the footprint of one floor; storeys stand on it (D-125), and the
    type sets the bill, the price of the next floor and the rate of decay
    (D-218). Height has no ceiling -- the bill is the only thing that refuses.
    """
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    job = await estate.construct(
        db,
        current(),
        body,
        node,
        float(message["area"]),
        tiers=_tiers(message),
        floors=int(message.get("floors", 1)),
        kind=message.get("kind") or None,
    )
    return {"building": True, "ready_at": job.run_at.isoformat()}


@command("build.estimate")
async def _build_estimate(state: dict, db: AsyncSession, message: dict) -> dict:
    """The bill before the work: what a house of this size and height costs.

    Shown together with what is already in hand -- the player must see "wood 12
    of 30" rather than find out at the click that the timber is short.
    """

    body = await _alive(state, db)
    constants = current()
    footprint = float(message.get("area", 0) or 0)
    floors = int(message.get("floors", 1))
    kind = message.get("kind") or estate.kinds(constants)[0]
    if footprint <= 0 or floors < 1:
        raise Refused("площадь и этажность считаются от единицы")
    estate.composition(constants, str(kind))

    needed = estate.bill(constants, footprint=footprint, floors=floors, kind=str(kind))
    pocket = await world.body_container(db, body)
    at_hand: dict[str, float] = {}
    for thing in await _things(db, constants, pocket):
        at_hand[thing["goods"]] = at_hand.get(thing["goods"], 0.0) + thing["amount"]

    catalog = current_catalog()
    node = await db.get(Node, body.node_id)
    return {
        "area": footprint,
        "floors": floors,
        "kind": str(kind),
        #: The whole shop window of types with their numbers: the choice is made
        #: before the bill, so the client must have it without a second call.
        "kinds": [
            {
                "kind": name,
                "per_m2": estate.composition(constants, name),
                "growth": estate.floor_growth(constants, name),
                "decay": estate.decay_per_day(constants, name),
            }
            for name in estate.kinds(constants)
        ],
        #: The usable area is what the machines and the cargo will be measured
        #: against; the plot is measured against the footprint alone.
        "usable": footprint * floors,
        #: What the plot still has room for, sites already started deducted --
        #: the ground is the only limit there is, height has none (D-218).
        "free_ground": (await estate.free_ground(db, node)) if node else 0.0,
        "area_min": constants[R.BUILD_AREA_MIN],
        "minutes": estate.build_minutes(
            constants, footprint=footprint, floors=floors, kind=str(kind)
        ),
        "materials": [
            {
                "goods": name,
                "need": round(qty, 2),
                "have": round(at_hand.get(name, 0.0), 2),
                "mass": round(gear.mass_of(catalog, name, qty), 1),
            }
            for name, qty in sorted(needed.items())
        ],
    }


@command("build.demolish_estimate")
async def _demolish_estimate(state: dict, db: AsyncSession, message: dict) -> dict:
    """What taking the house apart costs, before the work starts (D-205).

    The refusals are shown as reasons, not as one "cannot": the yard empties
    before the demolition, and the player must see exactly what is in the way.
    """
    body = await _alive(state, db)
    constants = current()
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")

    houses = await estate.buildings_of(db, node)
    return {
        "area": await estate.built_area(db, node),
        "floors": max((house.floors for house in houses), default=0),
        "minutes": estate.demolish_minutes(constants, houses),
        "back": [
            {"goods": name, "amount": round(qty, 2)}
            for name, qty in sorted(estate.salvage(constants, houses).items())
        ],
        #: Demolition follows building: one's own plot and any nobody's land
        #: (D-198, D-205); somebody else's civic plot -- by a court order (D-095).
        "mine": node.owner_identity_id == body.identity_id
        or (node.owner_identity_id is None and node.owner_city_id is None),
        "blocking": await estate.demolish_blockers(db, constants, node),
    }


@command("build.repair_estimate")
async def _repair_estimate(state: dict, db: AsyncSession, message: dict) -> dict:
    """What mending the plot's houses costs, before the work starts (D-218).

    Shown the same way round as the building bill: the term, the materials and
    what is in hand -- so that "timber 12 of 30" is read at the plan and not
    discovered at the click.
    """

    body = await _alive(state, db)
    constants = current()
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")

    houses = await estate.buildings_of(db, node)
    needed = estate.repair_bill(constants, houses)
    at_hand: dict[str, float] = {}
    pocket = await world.body_container(db, body)
    for thing in await _things(db, constants, pocket):
        at_hand[thing["goods"]] = at_hand.get(thing["goods"], 0.0) + thing["amount"]

    catalog = current_catalog()
    return {
        "condition": min((float(house.condition) for house in houses), default=None),
        "kind": next((house.kind for house in houses), None),
        "decay": estate.decay_per_day(constants, houses[0].kind) if houses else 0.0,
        "minutes": estate.repair_minutes(constants, houses),
        "going": await estate.repairing(db, node),
        #: One's own plot and any nobody's land beyond the walls -- exactly
        #: where one may build and take apart (D-198, D-205).
        "mine": node.owner_identity_id == body.identity_id
        or (node.owner_identity_id is None and node.owner_city_id is None),
        "materials": [
            {
                "goods": name,
                "need": round(qty, 2),
                "have": round(at_hand.get(name, 0.0), 2),
                "mass": round(gear.mass_of(catalog, name, qty), 1),
            }
            for name, qty in sorted(needed.items())
        ],
    }


@command("build.repair")
async def _build_repair(state: dict, db: AsyncSession, message: dict) -> dict:
    """Mend your own houses. Materials at once, the condition at the end of the work."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    job = await estate.repair(db, current(), body, node, tiers=_tiers(message))
    return {"repairing": True, "ready_at": job.run_at.isoformat()}


@command("build.demolish")
async def _build_demolish(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take your own house apart. The work goes by time, the materials come at its end."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    job = await estate.demolish(db, current(), body, node)
    return {"demolishing": True, "ready_at": job.run_at.isoformat()}


@command("deed.offer")
async def _deed_offer(state: dict, db: AsyncSession, message: dict) -> dict:
    """List your deed for sale: to everyone or to a named buyer. Remote."""
    identity = await _identity(state, db)
    deed = await _deed(db, message)
    to_whom = message.get("to")
    await estate.offer_deed(
        db,
        identity,
        deed,
        int(message.get("price") or 0),
        to=None if not to_whom else await _identity_by_name(db, str(to_whom)),
    )
    return {"offered": str(deed.id), "price": deed.sale_price}


@command("deed.buy")
async def _deed_buy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Buy a listed deed: money to the seller, title to the buyer. Remote."""
    identity = await _identity(state, db)
    deed = await _deed(db, message)
    await estate.buy_deed(db, identity, deed)
    return {"deed": str(deed.id), "money": await _money(db, identity.id)}


@command("deed.market")
async def _deed_market(state: dict, db: AsyncSession, message: dict) -> dict:
    """Deeds that can be bought: open ones and those addressed to this identity."""
    rows = await estate.deeds_on_sale(db, state["identity_id"])
    return {"deeds": [await _deed_view(db, deed) for deed in rows]}


async def _deed(db: AsyncSession, message: dict):

    deed = await db.get(Deed, uuid.UUID(message["deed"]))
    if deed is None:
        raise Refused("нет такой бумаги")
    return deed
