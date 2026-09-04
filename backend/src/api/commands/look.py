# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The live look and the cached parts (D-225, D-226).

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.api.commands.common import _body, _identity, _node, speaks
from src.api.commands.views import (
    _batches,
    _bench,
    _climate,
    _clock,
    _deeds,
    _discovered,
    _knowledge,
    _money,
    _orders,
    _pioneers,
    _reservations,
    _shelf,
    _shown,
    _sight,
    _storages,
    _things,
    _vehicles,
)
from src.api.registry import Ctx, command
from src.constants import current, current_catalog
from src.engine import (
    access,
    death,
    energy,
    estate,
    explore,
    farm,
    forage,
    frost,
    gear,
    justice,
    market,
    mining,
    net,
    occupation,
    oxygen,
    plates,
    rig,
    ship,
    station,
    storage,
    transport,
    travel,
    utility,
    vote,
    world,
)
from src.engine import city as town
from src.models.farm import Plot
from src.models.identity import Identity
from src.models.mining import MiningSession, SessionState
from src.models.world import Layer, Node, Vein


@command("knowledge", readonly=True)
async def _knowledge_read(ctx: Ctx) -> dict:
    """What the identity knows: recipes (`knows`), which of them were opened
    by one's own experiment (`discovered`, D-064, D-209), the care texts
    remembered (`care`, D-296), and the first discoverer's name per known
    recipe (`pioneers`, D-259). Read once and kept: `knowledge.learned` and
    `craft.invented` say when to read again (D-226)."""
    who = ctx.identity_id
    return {
        "knowledge": {
            "knows": await _knowledge(ctx.db, who),
            "discovered": await _discovered(ctx.db, who),
            "care": await farm.remembered(
                ctx.db, current(), current_catalog(), who, locale=speaks(ctx.state)
            ),
            "pioneers": await _pioneers(ctx.db, who),
        }
    }


@command("orders", readonly=True)
async def _orders_read(ctx: Ctx) -> dict:
    """One's own standing affairs: orders on the market, reservations held,
    batches in the works. From anywhere, body or no body (D-047). Events of
    `market.*` and `craft.*` say when to read again (D-226)."""
    who = ctx.identity_id
    return {
        "orders": {
            "orders": await _orders(ctx.db, who),
            "reservations": await _reservations(ctx.db, who),
            "batches": await _batches(ctx.db, who),
        }
    }


@command("deeds", readonly=True)
async def _deeds_read(ctx: Ctx) -> dict:
    """Own deeds and deeds listed for sale: electronic documents live in the
    Net and are visible from everywhere (D-116). `deed.*` and `land.*` events
    say when to read again (D-226)."""
    return {"deeds": await _deeds(ctx.db, ctx.identity_id)}


@command("shelf", readonly=True)
async def _shelf_read(ctx: Ctx) -> dict:
    """What the library here holds and who brought each recipe (D-068, D-209):
    the client's catalog table is this shelf, not the whole vault. Empty where
    there is no library. `library.*` events say when to read again (D-226)."""
    body = await ctx.body()
    if body is None:
        return {"shelf": []}
    node = await ctx.db.get(Node, body.node_id)
    if node is None or not await world.is_library(ctx.db, node):
        return {"shelf": []}
    return {"shelf": await _shelf(ctx.db, node)}


@command("look", readonly=True)
async def _look(state: dict, db: AsyncSession, message: dict) -> dict:
    """What the player sees about themselves **right now**: body, pocket, the
    place, the road, what is under way.

    Personal data goes only here. Public reads (`/public/*`) are about prices and
    catalogs; your own pocket is not there and never will be: everyone knows the
    prices, but not what is in whose sack.

    Only the live part (D-226, 08-session-protocol). What changes rarely is
    read by its own command and kept by the client until an event touches it:
    `knowledge` (recipes, discoveries, care texts), `account.profile`, `orders`
    (orders, reservations, batches), `deeds`, `shelf` (the library here).
    """
    identity = await _identity(state, db)
    body = await _body(db, identity.id)
    constants = current()
    #: The language this answer is said in, read once (D-251 wave III).
    said = speaks(state)

    seen: dict[str, Any] = {
        "identity": identity.name,
        "money": await _money(db, identity.id),
        #: What has arrived in the Net and is not read yet (D-222): the tab's count.
        "net_unread": await net.unread_letters(db, identity.id)
        + await net.unread_posts(db, constants, identity.id),
        #: Polls of one's own city still waiting for an answer. Counted apart
        #: from the letters rather than added into them: an unread letter and
        #: an unanswered ballot are different things, and the tab adds them up
        #: itself. Counted rather than assembled, because this is `look`: see
        #: `vote.waiting` for what that costs (D-161).
        "net_votes": await vote.waiting(db, identity.id),
    }
    if body is None:
        #: No body -- the identity is in the cloud (D-012). It still controls the
        #: account and orders but does nothing by hand, so the only thing that
        #: makes sense right now goes out: where to print and for how much.
        ongoing = await death.pending(db, identity.id)
        return {
            "look": seen
            | {
                "body": None,
                "node": None,
                "inventory": [],
                "printers": await death.printers(db, constants, identity.id),
                "printing": (None if ongoing is None else {"ready_at": ongoing.run_at.isoformat()}),
            }
        }

    node = await db.get(Node, body.node_id)
    ongoing = await travel.current(db, body)
    seen["body"] = {
        "id": str(body.id),
        "stamina": float(body.stamina),
        #: How many cave-ins this body has lived through (D-294). Not derivable
        #: from anything already sent -- `mining.collapsed` does not say whose
        #: body it was, and the count is reset by a death the client would have
        #: to reconstruct from the journal -- so it goes on the wire (D-225).
        #: The roof's stability stays the one hidden number of the face (D-143);
        #: how close this body is to its last cave-in is not a secret, and a
        #: player who cannot see it learns the rule by dying of it.
        "cave_ins": body.cave_ins,
        "sleeping_since": (
            None if body.sleeping_since is None else body.sleeping_since.isoformat()
        ),
        "sleeping_home": body.sleeping_home,
        "satiated_until": (
            None if body.satiated_until is None else body.satiated_until.isoformat()
        ),
    }

    #: In transit the body is nowhere: the node shown is the one it left from,
    #: but everything in-person is closed, and the client must see that (D-107).
    seen["travel"] = None
    if ongoing is not None:
        goal = await db.get(Node, ongoing.to_node_id)
        origin = await db.get(Node, ongoing.from_node_id)
        seen["travel"] = {
            "to": goal.name if goal else "?",
            "to_key": goal.key if goal else "",
            "from_key": origin.key if origin else "",
            "started_at": ongoing.started_at.isoformat(),
            "arrives_at": ongoing.arrives_at.isoformat(),
        }
        #: Autopath (D-045): show the final goal and how many nodes are ahead --
        #: the traveller must understand where they are going and for how long.
        if ongoing.plan:
            final = await db.get(Node, uuid.UUID(ongoing.plan[-1]))
            seen["travel"]["final"] = final.name if final else "?"
            seen["travel"]["final_key"] = final.key if final else ""
            seen["travel"]["legs_left"] = len(ongoing.plan)

    owner = (
        None if node.owner_identity_id is None else await db.get(Identity, node.owner_identity_id)
    )
    #: The node as a set of facts the client cannot derive by itself (D-225).
    #: Whatever follows from other fields is not sent: what stands here is
    #: `bench` and `furniture`, the library and the hall are read off them
    #: through the class book, "mine" is `owner` against one's own name,
    #: "wild" is no owner and no city. Keys that would carry an
    #: empty value -- no shelf, no door lists, not for sale -- are left out
    #: instead of sent as null or [].
    seen["node"] = {
        "key": node.key,
        "name": node.name,
        #: Place-sign properties ("forest", "outcrop"): the client shows place
        #: extraction (D-177) and other windows tied to the land, not to a machine.
        #: The `library` property is a legacy of old worlds (D-176) and is not
        #: a sign of the land; the machine in `bench` answers for it.
        "features": sorted(
            {
                name
                for name, value in (node.properties or {}).items()
                if value is True and name != "library"
            }
            #: The river is a mark like the others and the only one stored as a
            #: word rather than a flag (D-126), so it never reached this list.
            #: Since finds are bound to the land (D-254) it decides what a walk
            #: here turns up and whether a bed needs water carried to it -- and
            #: the client cannot derive it from anything else sent (D-225).
            | ({world.WATER} if world.has_place(node, world.WATER) else set())
        ),
        #: The owner's map mark, if one is nailed on (D-238): the plot window
        #: preselects it in the picker. Belted like the public map's copy.
        "emblem": estate.public_emblem(node),
        #: Fertility is a place property (D-126): the plots scene is shown by it.
        "fertility": float(node.properties.get("fertility", 0) or 0),
        #: The place's climate as farming reads it (D-261): the current values
        #: plus the parameters of the diurnal swing, so the client draws the
        #: day's breath without a data timer (D-225, D-226). Absent where
        #: exploration never wrote a temperature -- there is no gate there.
        "climate": await _climate(db, constants, node),
        #: Whose plot: the holder runs the estate, others by contract (D-116).
        #: Ownership is a public fact: whoever enters sees the owner, whoever it
        #: is, a person or a city (D-178).
        "owner": None if owner is None else owner.name,
        "owner_city": (
            None
            if node.owner_city_id is None
            else getattr(await town.by_id(db, node.owner_city_id), "name", None)
        ),
        #: The location shut for entry (D-199, D-204). Visible from outside: it is
        #: a door, not a trap, and one learns of it before setting out. Passage
        #: through it stays open to everyone.
        "gated": bool(node.gated),
        #: Disconnected for non-payment: machines do not work, and the player
        #: must see that at once, otherwise the meter becomes a trap (D-149).
        "cut_off": await utility.cut_off(db, node),
        #: Whose bill the household of this node is: `owner`, `city`, `nobody`,
        #: or empty outside the grid. Ownership alone does not answer it -- a
        #: bought plot stays civic land yet is paid for by a person (D-149).
        "upkeep": await utility.payer_of(db, node),
        "area": float(node.area_m2),
        #: What the plot costs to **hold** for a day (D-127, D-220). Shown
        #: next to the purchase price on purpose: the rate falls with every
        #: node from the bioprinter, so the centre is dearer both ways, and
        #: the buyer must see the second half before paying the first.
        "tax": await estate.land_tax_of(db, constants, current_catalog(), node),
    }
    #: The place's own words, written by whoever disposes of it (D-238):
    #: absent rather than empty, like every other empty key of the node.
    about = estate.public_about(node)
    if about:
        seen["node"]["about"] = about
    #: How many plots of one's own are marked out here. The client cannot work
    #: it out from anything it already has (D-225), and it decides whether the
    #: farming window exists at all: marking out a strip is something one does
    #: to **land**, and the cycle that follows -- ploughing, sowing, the daily
    #: round, the harvest -- is a place of its own that appears with the first
    #: strip. A count rather than a flag: "земледелие · 3 делянки" is the row
    #: the client draws, and it must not ask a second command for the number.
    marked = await db.scalar(
        select(func.count())
        .select_from(Plot)
        .where(Plot.node_id == node.id, Plot.owner_identity_id == identity.id)
    )
    if marked:
        seen["node"]["plots"] = int(marked)
    #: The Forerunners' reactor fades, and the fading must be visible long
    #: before it matters (D-232): the day it goes silent is the day the city
    #: has to be standing on its own coal. The output itself is not sent --
    #: the line is straight and the catalog holds both its ends (D-225).
    dies_at = energy.reactor_dies_at(constants, node)
    if dies_at is not None:
        seen["node"]["reactor_until"] = dies_at.isoformat()
    #: The announced hour of the eruption, while the window is open (D-197, P6).
    #: The free signal is an event, and an event reaches whoever is connected in
    #: the second it is written -- somebody logging in ten minutes into a
    #: six-hour window would otherwise stand on ground about to move and read
    #: nothing about it. The place carries the warning for as long as it stands.
    shaking_at = await plates.shaking(db, node)
    if shaking_at is not None:
        seen["node"]["shaking_at"] = shaking_at.isoformat()
    #: Both lists, and only to the holder: whom they let into a shut location
    #: and whom they let in nowhere (D-204). Whether there is a door here at all
    #: is the engine's question, asked of the engine (`access.has_door`): it is
    #: the plot that has one, not a floor of the house on it (D-247) and not a
    #: city's own location (D-282). Half of that rule used to live here, and the
    #: window drew a gate switch whose every button refused.
    if node.owner_identity_id == identity.id and access.has_door(node):
        seen["node"]["door"] = {
            "allowed": await access.roster(db, node, allowed=True),
            "barred": await access.roster(db, node, allowed=False),
        }
    #: Whether the viewer may name the plot (D-178): sent only when they may.
    if await estate.may_name(db, body, node):
        seen["node"]["may_name"] = True
    #: Which floor of a house one is standing on (D-247). Sent only where it is
    #: a floor at all: the ground floor is the plot itself, and there the key is
    #: absent like every other key that would carry nothing (D-225). The client
    #: cannot work it out -- a storey has no building record of its own, and its
    #: area alone says nothing about height.
    storey = estate.storey_of(node)
    if storey is not None:
        seen["node"]["storey"] = storey
    #: Building and capacity: a machine takes area (D-106), and the player must
    #: see how many places are left before carrying a machine across town.
    #: An empty plot with nothing under way sends no block at all.
    total_seats, taken_seats = await estate.slots(db, constants, node)
    houses = await estate.buildings_of(db, node)
    sites = await estate.under_construction(db, node)
    #: A site's owner by name (D-266): the window compares it with the name
    #: it knows itself by, and an id would say nothing to it (D-225).
    owners = {
        uuid.UUID(work["owner_identity_id"]) for work in sites if work.get("owner_identity_id")
    }
    if owners:
        named = dict(
            (
                await db.execute(select(Identity.id, Identity.name).where(Identity.id.in_(owners)))
            ).all()
        )
        for work in sites:
            if work.get("owner_identity_id"):
                work["owner"] = named.get(uuid.UUID(work.pop("owner_identity_id")))
    if storey is not None:
        #: A storey carries no building of its own: the house stands on the plot
        #: below and answers there for the bill, the wear, the repair and the tax
        #: (D-247). What the floor has is a floor -- its metres, its places and
        #: how high the plot reaches -- and nothing else is sent: the four keys
        #: the window upstairs draws, and no fifth carrying nothing (D-225).
        under = None if node.parent_id is None else await db.get(Node, node.parent_id)
        seen["node"]["building"] = {
            "area": await estate.storey_area(db, node),
            #: How high the plot reaches, so the floor can say which of how many.
            "floors": 0 if under is None else await estate.height_of(db, under),
            "slots": total_seats,
            "used": taken_seats,
        }
    elif houses or sites:
        seen["node"]["building"] = {
            "area": await estate.built_area(db, node),
            #: Storeys made these two different numbers (D-125): the plot is spent
            #: by the footprint, the machines live in the usable area.
            "ground": await estate.built_area(db, node, ground=True),
            "floors": max((house.floors for house in houses), default=0),
            #: The type and the soundness of the plot (D-218). The worst house
            #: answers for the whole: it is the one that will fall, and repair is
            #: ordered for the plot at once.
            "kind": next((house.kind for house in houses), None),
            "condition": min((float(house.condition) for house in houses), default=None),
            "decay": (estate.decay_per_day(constants, houses[0].kind) if houses else 0.0),
            "slots": total_seats,
            "used": taken_seats,
            #: Work in progress: without it the yard looks empty right after the
            #: materials are gone, and that reads as a loss.
            "sites": sites,
        }
    #: An empty civic plot is for sale: the city sets the price by distance to
    #: the bioprinter (D-089). The player must see it before buying. Buildings
    #: and city veins are not for sale -- they have no price at all, and no key.
    if (
        node.owner_identity_id is None
        and node.owner_city_id is not None
        and await estate.is_vacant(db, constants, node)
    ):
        plot_city = await town.by_id(db, node.owner_city_id)
        if plot_city is not None:
            with contextlib.suppress(estate.NotForSale):
                seen["node"]["price"] = await estate.price_of(
                    db, constants, current_catalog(), plot_city, node
                )
    #: The city whose territory we stand on, and our own powers in it: the client
    #: decides from these whether to show the administration (D-154).
    city = await town.of_node(db, node)
    seen["city"] = None
    if city is not None:
        seen["city"] = {
            "id": str(city.id),
            "name": city.name,
            "node": (await db.get(Node, city.node_id)).key,
            #: Rights as strings: broad and narrow ones mixed (D-155).
            "powers": sorted(await town.powers_of(db, identity.id, city)),
            #: Citizenship (D-160): own status in this city and the admission
            #: order. The client decides from these what to show -- "join",
            #: "application submitted" or "leave".
            "citizen": await town.is_citizen(db, identity.id, city),
            "admission": town.admission(city),
            "requested": await town.request_of(db, identity.id, city) is not None,
        }
    #: Where the identity belongs at all: citizenship is one and visible from
    #: everywhere -- it is a record about the person, not the place.
    own_ = await town.citizenship(db, identity.id)
    seen["citizenship"] = None
    if own_ is not None:
        native = await town.by_id(db, own_.city_id)
        #: No date of a filed exit and no term of a print obligation: the exit
        #: is instant and unannounced (D-281), and nothing holds a citizenship
        #: but an open loan -- which the bank window already says, and the
        #: refusal says at the moment it matters. `since` stays: it is the date
        #: the residency census runs from, and the only thing about a
        #: citizenship there is to show besides the city.
        seen["citizenship"] = {
            "city": None if native is None else native.name,
            "since": own_.since.isoformat(),
        }
    #: Founding a city (D-023, D-159): shown only where it is possible at all --
    #: on a planet node no city covers, by somebody who belongs to no city yet
    #: (D-281). The list of what is missing must be visible in advance: the
    #: entry threshold is buildings, and the person must understand which ones
    #: exactly they lack, not hit a refusal.
    #:
    #: The land here is nobody's, and that is the condition rather than a
    #: loosened one: outside a city land is not privatized (D-198) --
    #: `world.grant_node` refuses off civic ground with `land-outside-city`,
    #: and `estate.buy` sells a city's own plots. Asking
    #: for the reader's **own** node, as this door did until 2026-09-03, was
    #: asking for a plot; a plot stands in a city, so `city is None` cancelled
    #: it and the window opened for nobody, ever. The title is still asked
    #: about, because `establish` asks (`city-found-foreign-land`): the two
    #: conditions are one condition, and a window offering what the door
    #: refuses is the whole defect being repaired here.
    seen["foundation"] = None
    if (
        city is None
        and node.layer is Layer.PLANET
        and node.owner_identity_id in (None, identity.id)
        and own_ is None
    ):
        #: Only what **this** node lacks, and as role keys. The table of roles
        #: and the machines that fill them is a constant of the catalog and is
        #: read once from `/public/founding` (D-225); it used to ride here in
        #: full, in words, and the client told a filled role from an empty one
        #: by comparing two translated strings -- so the tick beside a role
        #: hung on the wording of a sentence.
        seen["foundation"] = {"missing": list(await town.missing_for_foundation(db, node))}
    #: An ongoing exploration run: the map grows on foot, and the wait is
    #: real (D-152).
    run = await explore.pending(db, body)
    seen["survey"] = None if run is None else {"returns_at": run.run_at.isoformat()}
    #: Foraging on the empty land of the place (D-210): the window, its search
    #: and its find. Empty where the land is built up or somebody else's.
    seen["forage"] = await forage.view(db, constants, current_catalog(), body, node)

    #: The cold, and only where there is any (D-231). The hours are not sent as
    #: a number that goes stale in a second: the stamp, the rate and the ceiling
    #: go out and the client counts the hand itself, as it does the clock (D-226).
    #: Only where cold exists (D-231). An absent key, not a null: the same
    #: convention as `shaking_at` and `reactor_until` right above -- a key that
    #: would carry an empty value is left out (D-225), and on Terra that is
    #: every look of every player. (`forage` nearby still sends its null; it is
    #: older than the rule and not this change's to move.)
    cold = await frost.view(db, constants, current_catalog(), body, node)
    if cold is not None:
        seen["frost"] = cold

    #: The air, and only where there is none (D-233, D-234). The second scale
    #: beside the cold and told the same way: the level, the rate and the stamp,
    #: with the client counting the hand. Absent on Terra and Aurora -- for
    #: everybody, always -- by the same rule as the key above.
    air = await oxygen.view(db, constants, current_catalog(), body, node)
    if air is not None:
        seen["air"] = air

    #: Everything the body is at (D-211). Two things live off this list: the
    #: client greys out what would be refused, with the reason on the button,
    #: and "дела" draws every running occupation in one place -- so that a
    #: search is ended where everything else is ended, not in the window it
    #: happened to be started from.
    seen["doings"] = [
        {
            "kind": doing.kind,
            #: Said in the reader's language here, at the edge, the way a
            #: refusal is: the engine named the occupation, and naming is as
            #: far as it goes (D-251).
            "title": i18n.render(doing.title, locale=said),
            "what": i18n.render(doing.says.key, doing.says.params, locale=said),
            "until": None if doing.until is None else doing.until.isoformat(),
        }
        for doing in await occupation.all_of(db, body)
    ]

    #: Local clock of the planet: a Terran day is `time.day_terra` hours long
    #: (D-029), and the world has been running since its first node appeared.
    #: The client counts the hands itself -- the server names the origin, so
    #: everyone reads one and the same hour.
    seen["clock"] = await _clock(db, constants, node)

    #: Asked once for the whole list, not once per road: whether the body drives
    #: a convoy does not change between two exits of the same node.
    harnessed = await travel.has_transport(db, body)
    seen["exits"] = [
        {
            "key": path.key,
            "name": path.name,
            "surface": path.surface.value,
            "seconds": round(path.seconds),
            #: Road cost to the body (D-147): the player must see it before
            #: leaving. Three decimals, not two: a step across town costs
            #: thousandths, and rounding to hundredths would show zero where a
            #: price exists.
            "stamina": round(
                travel.stamina_cost(constants, path.seconds, transport=harnessed),
                3,
            ),
        }
        for path in await travel.exits(db, constants, node)
    ]
    #: What stands in the node and who occupies it: a machine is given to one (D-150).
    seen["bench"] = await _bench(db, node, body)
    #: Furniture apart from machines: nobody works at it, it furnishes the
    #: household, and the client shows it in its own window.
    seen["furniture"] = await _bench(db, node, body, furniture=True)
    #: Node storages and what lies in them (D-181). Contents are visible only to
    #: whoever may dispose of the node: a foreign chest is not inspected, just as
    #: it is not opened -- breaking it open is a matter for the court (D-166).
    seen["storages"] = await _storages(db, constants, node, body)
    #: What lies loose here and how much room is left (D-192). Lying goods are
    #: visible to everyone -- they lie in plain sight -- and taken by everyone who
    #: got in (D-204): the shut door and the chest are the protection, not a rule
    #: against touching. A passer-by through a shut location is not inside.
    loose = {str(thing.id) for thing in await storage.lying(db, node)}
    #: Serialised **once** for both surfaces: one store holds them, and `_shown`
    #: is a walk over makers and cultivars that has no business happening twice.
    #: Through `node_things`, not `node_container`: a look must not make a yard.
    shown = await _shown(db, constants, await world.node_things(db, node))
    #: Two surfaces since D-244, and the pair is the whole point: the floor of
    #: the house is what the house was built for, the yard is the plot left
    #: around it. A roofless node has no floor at all -- `area` is nought and the
    #: window with it -- and a house covering the whole plot leaves no yard.
    seen["floor"] = {
        "space": await estate.space(db, constants, node),
        #: Only what lies loose: what stands -- machines, furniture, chests put
        #: up -- has its own windows and pays for its place differently
        #: (D-106, D-181); a machine dropped here is cargo among the rest (D-278).
        "things": [thing for thing in shown if thing["id"] in loose],
        #: Whether this one may reach the floor at all: everybody inside may.
        "open": await access.may_enter(db, node, identity.id),
        #: Whose the place is -- the window says it in words, and the words differ
        #: for the holder and for a guest.
        "mine": await station.may_build(db, body, node)
        or (node.owner_identity_id is None and node.owner_city_id is None),
    }
    outside = {str(thing.id) for thing in await storage.lying(db, node, indoors=False)}
    #: The open ground: the same store, the other surface (D-244). The door and
    #: the right are the floor's -- one place, one door -- so they are not
    #: repeated here: the client reads them off `floor` (D-225).
    open_air = await estate.yard(db, constants, node)
    #: Left out where a house has grown over the whole plot: there is no ground
    #: to put anything on, and a key carrying an empty surface is the sort of
    #: nothing this answer does not send (D-225).
    if open_air["area"] > 0 or outside:
        seen["ground"] = {
            "space": open_air,
            "things": [thing for thing in shown if thing["id"] in outside],
        }
    seen["inventory"] = await _things(db, constants, await world.body_container(db, body))
    cell = await market.stall(db, node, identity.id, create=False)
    seen["stall"] = [] if cell is None else await _things(db, constants, cell)
    #: Carried load: how much is carried, how much can be, and what is worn
    #: (D-146). The limit is why wagons exist, and the player must see it as a number.
    worn = await gear.equipped(db, body)
    seen["carry"] = {
        "load": round(await gear.load_of(db, constants, current_catalog(), body), 2),
        "capacity": round(await gear.capacity(db, constants, current_catalog(), body), 2),
        "slots": list(current_catalog().recipes.gear_slots),
        "equipped": {
            slot: {"id": str(thing.id), "goods": thing.type_key} for slot, thing in worn.items()
        },
    }
    #: Convoy: what we are harnessed to, what it carries and how much still fits
    #: (D-157). Without this the hands limit is a dead end: the player must see
    #: what gets around it.
    seen["convoy"] = await transport.view(db, constants, current_catalog(), body)
    #: Vehicles standing in the node: you harness to what is nearby.
    seen["vehicles"] = await _vehicles(db, constants, node)
    #: An open face survives the player leaving: the session lives until "leave"
    #: or until a collapse. The client, on return, must see it in place.
    open_ = (
        (
            await db.execute(
                select(MiningSession).where(
                    MiningSession.body_id == body.id,
                    MiningSession.state == SessionState.ACTIVE,
                )
            )
        )
        .scalars()
        .first()
    )
    seen["mining"] = (
        None if open_ is None else _sight(open_, await mining.sight(db, constants, open_))
    )
    #: The penal face is seen only by whoever the prison holds (D-174, D-176):
    #: prison veins are not shown to outsiders, nor is the prison printer.
    face_hidden = await justice.is_prison(db, node) and not await justice.held(
        db, constants, identity.id
    )
    seen["veins"] = (
        []
        if face_hidden
        else [
            {"id": str(vein.id), "resource": vein.resource, "richness": float(vein.richness)}
            for vein in (await db.execute(select(Vein).where(Vein.node_id == node.id)))
            .scalars()
            .all()
        ]
    )
    #: Whether a rig stands here at all. The location screen lists the node's
    #: objects, and it cannot list a drilling rig on the strength of an async
    #: query -- without this the row appeared in every location in the world,
    #: including ones where nothing drills anything.
    seen["rig_here"] = bool(await rig.status(db, constants, node.id))
    #: What of ships is visible from here, and nothing beyond it (D-201): at a
    #: pier the moored ships themselves, aboard the rooms one walks between.
    #: None of it is on the public map -- from outside a ship is one hull, and
    #: its layout is exactly what a boarder would want to know. So it travels
    #: with the look of whoever stands close enough, and appears and goes with
    #: the walking.
    seen["ships"] = await ship.in_sight(db, constants, node)
    return {"look": seen}


@command("body.printers")
async def _body_printers(state: dict, db: AsyncSession, message: dict) -> dict:
    """Where you can print and for how much (D-033). Read from the cloud -- always.

    The identity lives in the Net, not in the body: the list is available to the
    dead too, that is the whole point -- the city holds no hostage.
    """
    ongoing = await death.pending(db, state["identity_id"])
    return {
        "printers": await death.printers(db, current(), state["identity_id"]),
        "printing": None if ongoing is None else {"ready_at": ongoing.run_at.isoformat()},
    }


@command("body.print")
async def _body_print(state: dict, db: AsyncSession, message: dict) -> dict:
    """Order a body print. The fee is taken up front, the body arrives on schedule."""
    identity = await _identity(state, db)
    node = await _node(db, str(message["node"]))
    job = await death.order(db, current(), current_catalog(), identity, node)
    return {
        "printing": {"node": node.key, "ready_at": job.run_at.isoformat()},
        "money": await _money(db, identity.id),
    }
