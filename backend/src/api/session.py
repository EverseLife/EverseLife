"""Client session -- the only place where a player acts.

Anti-cheat rests not on protecting the client but on the fact that **there is
no action API** (60-meta/01-anti-cheat). An in-person action goes only from
here and only after the device fee (D-110).

The protocol is deliberately boring: JSON over WebSocket, one command -- one
reply. The reply to any mining command is a `Sight`, i.e. exactly what the
player sees. Roof stability is not there: it is not "hidden in the UI", it
simply does not exist in the reply.

Craft lives here for the same reason, even though the batch runs offline:
**starting** is an in-person action and there will never be a convenient REST
for it. The forecast (`craft.plan`) and the start (`craft.start`) parse the
request with the same code -- otherwise the player would see one number and
get another (D-092).

**Account identification is email and password** (D-187): `hello` accepts
either those or a token issued by a previous login. The subscription (E7,
D-027) will bind to the same account.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import runtime
from src.constants import current, current_catalog
from src.constants import registry as R
from src.db.base import session_factory
from src.engine import (
    access,
    bank,
    breed,
    chat,
    coin,
    craft,
    death,
    energy,
    estate,
    explore,
    farm,
    finance,
    food,
    gear,
    justice,
    ledger,
    market,
    mining,
    panel,
    rest,
    rig,
    road,
    ship,
    station,
    storage,
    transport,
    travel,
    utility,
    vote,
    world,
)
from src.engine import (
    account as accounts,
)
from src.engine import city as town
from src.engine import pow as device
from src.models.chat import Utterance
from src.models.city import Office, Power
from src.models.craft import BatchState, CraftBatch
from src.models.event import Event, EventKind
from src.models.identity import Body, BodyState, Identity, Knowledge, KnowledgeKind
from src.models.inventory import Item
from src.models.justice import Case, CaseState
from src.models.ledger import AccountKind
from src.models.market import (
    Order,
    OrderSide,
    OrderState,
    Reservation,
    ReservationState,
)
from src.models.mining import MiningSession, Pace, PowChallenge, SessionState
from src.models.plant import Nursery, Variety
from src.models.rig import Rig as RigRow
from src.models.ship import Ship
from src.models.vote import Vote
from src.models.world import Edge, Layer, Node, Vein
from src.telemetry import metrics
from src.units import amount_float, money, money_str

log = logging.getLogger(__name__)

router = APIRouter(tags=["session"])


class Refused(Exception):
    """Command refused by the game rules. This is not a server error."""


@router.websocket("/session/ws")
async def play(socket: WebSocket) -> None:
    await socket.accept()
    state: dict[str, Any] = {"identity_id": None}

    try:
        while True:
            message = await socket.receive_json()
            try:
                answer = await _dispatch(state, message)
            except Refused as refusal:
                answer = {"refused": str(refusal)}
            except mining.MiningError as refusal:
                answer = {"refused": str(refusal)}
            except craft.CraftError as refusal:
                answer = {"refused": str(refusal)}
            except market.MarketError as refusal:
                answer = {"refused": str(refusal)}
            except travel.TravelError as refusal:
                answer = {"refused": str(refusal)}
            except chat.ChatError as refusal:
                answer = {"refused": str(refusal)}
            except rest.RestError as refusal:
                answer = {"refused": str(refusal)}
            except farm.FarmError as refusal:
                answer = {"refused": str(refusal)}
            except food.FoodError as refusal:
                answer = {"refused": str(refusal)}
            except coin.CoinError as refusal:
                answer = {"refused": str(refusal)}
            except breed.BreedError as refusal:
                answer = {"refused": str(refusal)}
            except energy.EnergyError as refusal:
                answer = {"refused": str(refusal)}
            except rig.RigError as refusal:
                answer = {"refused": str(refusal)}
            except gear.GearError as refusal:
                answer = {"refused": str(refusal)}
            except station.StationError as refusal:
                answer = {"refused": str(refusal)}
            except transport.TransportError as refusal:
                answer = {"refused": str(refusal)}
            except road.RoadError as refusal:
                answer = {"refused": str(refusal)}
            except ship.ShipError as refusal:
                answer = {"refused": str(refusal)}
            except vote.VoteError as refusal:
                answer = {"refused": str(refusal)}
            except justice.JusticeError as refusal:
                answer = {"refused": str(refusal)}
            except bank.BankError as refusal:
                answer = {"refused": str(refusal)}
            except explore.ExploreError as refusal:
                answer = {"refused": str(refusal)}
            except death.DeathError as refusal:
                answer = {"refused": str(refusal)}
            except utility.UtilityError as refusal:
                answer = {"refused": str(refusal)}
            except town.CityError as refusal:
                answer = {"refused": str(refusal)}
            except estate.EstateError as refusal:
                answer = {"refused": str(refusal)}
            #: The gate refuses a road (D-199), and that refusal travels from
            #: `travel.depart` -- not only from the `gate.*` commands, where it
            #: is caught locally.
            except access.AccessError as refusal:
                answer = {"refused": str(refusal)}
            except device.PowError as refusal:
                answer = {"refused": str(refusal)}
            except accounts.AccountError as refusal:
                answer = {"refused": str(refusal)}
            await socket.send_json(answer)
    except WebSocketDisconnect:
        #: The player leaving does not close the mining session: it lives until
        #: "leave" or until a collapse. What was mined lies in the face and
        #: waits for a decision.
        log.info("session disconnected, identity %s", state.get("identity_id"))


async def _dispatch(state: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    command = message.get("cmd")
    if command is None:
        raise Refused("команда не названа")

    async with session_factory()() as db, db.begin():
        if command == "hello":
            return await _hello(state, db, message)
        #: New player -- before identification: nobody to identify yet.
        if command == "join":
            return await _join(state, db, message)

        identity_id = state.get("identity_id")
        if identity_id is None:
            raise Refused("сначала hello")

        handler = _COMMANDS.get(command)
        if handler is None:
            raise Refused(f"нет такой команды: {command}")
        return await handler(state, db, message)


async def _hello(state: dict, db: AsyncSession, message: dict) -> dict:
    """Identification: email and password, or the token of a previous login (D-187).

    The password is entered once: a token goes back, and reconnecting the socket
    or refreshing the page is identified by it. The token lives `LOGIN_TOKEN_TTL`
    and is revoked by logging out of the account panel.
    """
    token = message.get("token")
    if token:
        account = await accounts.by_token(db, token)
        issued = str(token)
    else:
        account = await accounts.login(db, message.get("email"), message.get("password"))
        issued = await accounts.issue_token(db, account)

    identity = (
        await db.execute(select(Identity).where(Identity.account_id == account.id))
    ).scalar_one_or_none()
    if identity is None:
        raise Refused("у аккаунта нет личности: регистрация не завершена")

    state["identity_id"] = identity.id
    state["token"] = issued
    body = await _body(db, identity.id)
    return {
        "hello": identity.name,
        "token": issued,
        #: The client computes the device fee itself, and its account is part
        #: of the estimate (D-112).
        "account": str(identity.account_id),
        "body": None if body is None else str(body.id),
        "node": None if body is None else str(body.node_id),
        "constants": current().digest,
    }


async def _join(state: dict, db: AsyncSession, message: dict) -> dict:
    """Registration: account, identity and first body at the chosen door (D-187).

    The client walks the player through four steps -- email and password, line,
    character, door -- but the server receives them as one command: there is no
    half-account. Everything is checked before the first write, and a refusal on
    any field leaves the database untouched.

    **Where to print is the player's decision** (D-013, D-182): the door is named
    by a node key from `/public/doors`. Without it we print at the first printer
    we find -- that is how old clients enter, not how entry is meant to work.

    The balance is **zero**: the world hands out no money (D-153). If the city
    where the bioprinter stands decided to pay a settlement grant, it comes from
    its treasury -- and that is visible in the reply. A zero in the reply is
    honest too: the city is poor or does not pay.
    """
    email = accounts.normalize_email(message.get("email"))
    password = accounts.check_password(message.get("password"))
    if message.get("password_again") is not None and message["password_again"] != password:
        raise Refused("пароли не совпадают")
    if await accounts.by_email(db, email) is not None:
        raise Refused("эта почта уже занята")
    line = accounts.check_line(message.get("line"))
    name = accounts.check_name(message.get("name"))
    profile = accounts.check_profile(message)

    key = str(message.get("node") or "").strip()
    if key:
        where = await world.door(db, key)
        if where is None:
            raise Refused(f"у двери {key!r} не печатают")
    else:
        where = await world.spawn_point(db)
    if where is None:
        raise Refused("мир ещё не создан: печататься негде")
    try:
        identity, body = await world.spawn(
            db, name, where, email=email, password=password, line=line, profile=profile
        )
    except ValueError as refusal:
        raise Refused(str(refusal)) from refusal

    account = await accounts.account_of(db, identity)
    issued = await accounts.issue_token(db, account)
    state["identity_id"] = identity.id
    state["token"] = issued
    return {
        "hello": identity.name,
        "token": issued,
        "account": str(identity.account_id),
        "body": str(body.id),
        "node": str(body.node_id),
        "money": await _money(db, identity.id),
        "constants": current().digest,
    }


async def _account_profile(state: dict, db: AsyncSession, message: dict) -> dict:
    """Account panel: what the account knows about itself (D-187)."""
    identity = await _identity(state, db)
    account = await accounts.account_of(db, identity)
    return {"profile": accounts.profile(account, identity)}


async def _account_update(state: dict, db: AsyncSession, message: dict) -> dict:
    """Change surname, age, description. The name does not change (D-011)."""
    identity = await _identity(state, db)
    accounts.apply_profile(identity, accounts.check_profile(message))
    await db.flush()
    account = await accounts.account_of(db, identity)
    return {"profile": accounts.profile(account, identity)}


async def _account_password(state: dict, db: AsyncSession, message: dict) -> dict:
    """Change password: the old one is required, all previous sessions are
    revoked, and this one gets a new token."""
    identity = await _identity(state, db)
    account = await accounts.account_of(db, identity)
    if not accounts.verify_password(account, str(message.get("old") or "")):
        raise Refused("старый пароль не подходит")
    new = accounts.check_password(message.get("new"))
    if message.get("new_again") is not None and message["new_again"] != new:
        raise Refused("пароли не совпадают")
    account.password_hash = accounts.hash_password(new)
    await accounts.revoke_all(db, account)
    issued = await accounts.issue_token(db, account)
    state["token"] = issued
    return {"token": issued}


async def _account_email(state: dict, db: AsyncSession, message: dict) -> dict:
    """Change email: confirmed by password."""
    identity = await _identity(state, db)
    account = await accounts.account_of(db, identity)
    password = str(message.get("password") or "")
    if not accounts.verify_password(account, password):
        raise Refused("пароль не подходит")
    await accounts.set_credentials(db, account, str(message.get("email") or ""), password)
    return {"profile": accounts.profile(account, identity)}


async def _account_logout(state: dict, db: AsyncSession, message: dict) -> dict:
    """Logout: this session's token is revoked, the socket forgets the identity."""
    await accounts.revoke_token(db, message.get("token") or state.get("token"))
    state["identity_id"] = None
    state["token"] = None
    return {"bye": True}


async def _look(state: dict, db: AsyncSession, message: dict) -> dict:
    """What the player sees about themselves: body, pocket, account, jobs, own orders.

    Personal data goes only here. Public reads (`/public/*`) are about prices and
    catalogs; your own pocket is not there and never will be: everyone knows the
    prices, but not what is in whose sack.
    """
    identity = await _identity(state, db)
    body = await _body(db, identity.id)
    constants = current()

    seen: dict[str, Any] = {
        "identity": identity.name,
        #: Account panel in the client header (D-187): self-description next to the name.
        "profile": accounts.profile(await accounts.account_of(db, identity), identity),
        "money": await _money(db, identity.id),
        "knows": await _knowledge(db, identity.id),
        #: Learned agrotech as a separate list: the client shows in the Library
        #: which crops are already studied and does not let you take them twice.
        "agrotech": await _knowledge(db, identity.id, kind=KnowledgeKind.AGROTECH),
        "orders": await _orders(db, identity.id),
        "reservations": await _reservations(db, identity.id),
        "batches": await _batches(db, identity.id),
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
                "printing": (
                    None if ongoing is None else {"ready_at": ongoing.run_at.isoformat()}
                ),
            }
        }

    node = await db.get(Node, body.node_id)
    ongoing = await travel.current(db, body)
    seen["body"] = {
        "id": str(body.id),
        "stamina": float(body.stamina),
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
        None
        if node.owner_identity_id is None
        else await db.get(Identity, node.owner_identity_id)
    )
    stations = await _stations(db, node)
    seen["node"] = {
        "key": node.key,
        "name": node.name,
        "layer": node.layer.value,
        #: The library is a machine (D-176); the node property is a legacy of old worlds.
        "library": await world.is_library(db, node),
        #: What the node has -- the client decides from this which scenes to show.
        "stations": stations,
        #: Place-sign properties ("forest", "outcrop"): the client shows place
        #: extraction (D-177) and other windows tied to the land, not to a machine.
        "features": sorted(
            name for name, value in (node.properties or {}).items()
            if value is True
        ),
        #: Fertility is a place property (D-126): the plots scene is shown by it.
        "fertility": float(node.properties.get("плодородие", 0) or 0),
        #: Whose plot: the holder runs the estate, others by contract (D-116).
        #: Ownership is a public fact: whoever enters sees the owner, whoever it
        #: is, a person or a city (D-178).
        "owner": None if owner is None else owner.name,
        "owner_city": (
            None if node.owner_city_id is None
            else getattr(await town.by_id(db, node.owner_city_id), "name", None)
        ),
        "mine": node.owner_identity_id == identity.id,
        "city": node.owner_city_id is not None,
        #: The location shut for entry (D-199, D-204). Visible from outside: it is
        #: a door, not a trap, and one learns of it before setting out. Passage
        #: through it stays open to everyone.
        "gated": bool(node.gated),
        #: Both lists, and only to the holder: whom they let into a shut location
        #: and whom they let in nowhere (D-204).
        "allowed": (
            await access.roster(db, node, allowed=True)
            if node.owner_identity_id == identity.id
            else []
        ),
        "barred": (
            await access.roster(db, node, allowed=False)
            if node.owner_identity_id == identity.id
            else []
        ),
        #: Whether the viewer may name the plot (D-178).
        "may_name": await estate.may_name(db, body, node),
        #: Wild and unowned: such a node is taken in person (06-farming, D-152).
        "wild": node.owner_identity_id is None and node.owner_city_id is None,
        #: Disconnected for non-payment: machines do not work, and the player
        #: must see that at once, otherwise the meter becomes a trap (D-149).
        "cut_off": await utility.cut_off(db, node),
        "area": float(node.area_m2),
    }
    #: Building and capacity: a machine takes area (D-106), and the player must
    #: see how many places are left before carrying a machine across town.
    total_seats, taken_seats = await estate.slots(db, constants, node)
    houses = await estate.buildings_of(db, node)
    seen["node"]["building"] = {
        "area": await estate.built_area(db, node),
        #: Storeys made these two different numbers (D-125): the plot is spent
        #: by the footprint, the machines live in the usable area.
        "ground": await estate.built_area(db, node, ground=True),
        "floors": max((house.floors for house in houses), default=0),
        "strength": max((house.strength for house in houses), default=0),
        "slots": total_seats,
        "used": taken_seats,
        #: Work in progress: without it the yard looks empty right after the
        #: materials are gone, and that reads as a loss.
        "building": await estate.under_construction(db, node),
    }
    #: An empty civic plot is for sale: the city sets the price by distance to
    #: the bioprinter (D-089). The player must see it before buying. Buildings
    #: and city veins are not for sale -- they have no price at all.
    seen["node"]["price"] = None
    if (
        node.owner_identity_id is None
        and node.owner_city_id is not None
        and await estate.is_vacant(db, constants, node)
    ):
        plot_city = await town.by_id(db, node.owner_city_id)
        if plot_city is not None:
            try:
                seen["node"]["price"] = await estate.price_of(
                    db, constants, current_catalog(), plot_city, node
                )
            except estate.NotForSale:
                seen["node"]["price"] = None
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
            #: Governing is in-person: the client shows it only in the
            #: administration, not in the sidebar (D-155).
            "hall": town.HALL in stations,
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
        seen["citizenship"] = {
            "city": None if native is None else native.name,
            "since": own_.since.isoformat(),
            "leaving_at": (
                None if own_.leaving_at is None else own_.leaving_at.isoformat()
            ),
            #: An obligation accepted as a print condition (D-184): citizenship
            #: does not lapse before this date. It must be shown in advance --
            #: the person must not learn about the term from a refusal.
            "bound_until": (
                None if own_.bound_until is None else own_.bound_until.isoformat()
            ),
        }
    #: Founding a city (D-023, D-159): shown only where it is possible at all --
    #: on your own planet node outside a foreign city. The list of what is
    #: missing must be visible in advance: the entry threshold is buildings, and
    #: the person must understand which ones exactly they lack, not hit a refusal.
    seen["foundation"] = None
    if (
        city is None
        and node.layer is Layer.PLANET
        and node.owner_identity_id == identity.id
    ):
        seen["foundation"] = {
            "missing": list(await town.missing_for_foundation(db, node)),
            "needs": [
                {"role": role, "any_of": list(with_what)}
                for role, with_what in town.foundation_needs()
            ],
        }
    #: An ongoing exploration run: the map grows on foot, and the wait is
    #: real (D-152).
    run = await explore.pending(db, body)
    seen["survey"] = None if run is None else {"returns_at": run.run_at.isoformat()}

    #: Local clock of the planet: a Terran day is `time.day_terra` hours long
    #: (D-029), and the world has been running since its first node appeared.
    #: The client counts the hands itself -- the server names the origin, so
    #: everyone reads one and the same hour.
    seen["clock"] = await _clock(db, constants, node)

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
                travel.stamina_cost(
                    constants, path.seconds,
                    transport=await travel.has_transport(db, body),
                ),
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
    seen["floor"] = {
        "space": await estate.space(db, constants, node),
        #: Only what lies loose: machines, furniture and chests have their own
        #: windows and pay for their place differently (D-106, D-181).
        "things": [
            thing
            for thing in await _things(
                db, constants, await world.node_container(db, node)
            )
            if thing["id"] in loose
        ],
        #: Whether this one may reach the floor at all: everybody inside may.
        "open": await access.may_enter(db, node, identity.id),
        #: Whose the place is -- the window says it in words, and the words differ
        #: for the holder and for a guest.
        "mine": await station.may_build(db, body, node)
        or (node.owner_identity_id is None and node.owner_city_id is None),
    }
    #: Own deeds and deeds listed for sale: electronic documents live in the
    #: Net and are visible from everywhere (D-116).
    seen["deeds"] = await _deeds(db, identity.id)
    seen["inventory"] = await _things(db, constants, await world.body_container(db, body))
    seen["stall"] = await _things(db, constants, await market.stall(db, node, identity.id))
    #: Carried load: how much is carried, how much can be, and what is worn
    #: (D-146). The limit is why wagons exist, and the player must see it as a number.
    worn = await gear.equipped(db, body)
    seen["carry"] = {
        "load": round(await gear.load_of(db, current_catalog(), body), 2),
        "capacity": round(await gear.capacity(db, constants, current_catalog(), body), 2),
        "slots": list(current_catalog().recipes.gear_slots),
        "equipped": {
            slot: {"id": str(thing.id), "goods": thing.type_key}
            for slot, thing in worn.items()
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
        await db.execute(
            select(MiningSession).where(
                MiningSession.body_id == body.id,
                MiningSession.state == SessionState.ACTIVE,
            )
        )
    ).scalars().first()
    seen["mining"] = (
        None if open_ is None
        else _sight(open_, await mining.sight(db, constants, open_))
    )
    #: The penal face is seen only by whoever the prison holds (D-174, D-176):
    #: prison veins are not shown to outsiders, nor is the prison printer.
    face_hidden = await justice.is_prison(db, node) and not await justice.held(
        db, constants, identity.id
    )
    seen["veins"] = [] if face_hidden else [
        {"id": str(vein.id), "resource": vein.resource, "richness": float(vein.richness)}
        for vein in (
            await db.execute(select(Vein).where(Vein.node_id == node.id))
        ).scalars().all()
    ]
    #: Whether a rig stands here at all. The location screen lists the node's
    #: objects, and it cannot list a drilling rig on the strength of an async
    #: query -- without this the row appeared in every location in the world,
    #: including ones where nothing drills anything.
    seen["rig_here"] = bool(await rig.status(db, constants, node.id))
    return {"look": seen}


async def _challenge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Issue a device-fee challenge. The client computes it in a Web Worker."""
    identity = await db.get(Identity, state["identity_id"])
    if identity is None:  # pragma: no cover
        raise Refused("личность исчезла")
    task = await device.issue(db, current(), identity.account_id)
    return {"challenge": str(task.id), "nonce": task.nonce.hex()}


async def _mine_start(state: dict, db: AsyncSession, message: dict) -> dict:
    """Open a face. Without a paid challenge the session does not start."""
    constants = current()
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")

    task = await db.get(PowChallenge, uuid.UUID(message["challenge"]))
    if task is None or task.account_id != (await db.get(Identity, body.identity_id)).account_id:
        raise Refused("задача не ваша")
    await device.verify(db, constants, task, bytes.fromhex(message["answer"]))

    vein = await db.get(Vein, uuid.UUID(message["vein"]))
    if vein is None:
        raise Refused("нет такой жилы")

    session = await mining.start(
        db,
        constants,
        body,
        vein,
        tool_item_id=_optional_uuid(message.get("tool")),
        pace=Pace(message.get("pace", Pace.STEADY.value)),
    )
    task.spent_on_session_id = session.id
    state["session_id"] = session.id
    return _sight(session, await mining.sight(db, constants, session))


async def _mine_swing(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    return _sight(session, await mining.swing(db, current(), session))


async def _mine_timber(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    return _sight(session, await mining.timber(db, current(), session))


async def _mine_pace(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    pace = Pace(message["pace"])
    return _sight(session, await mining.set_pace(db, current(), session, pace))


async def _mine_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    haul = await mining.leave(db, current(), session)
    state.pop("session_id", None)
    return {"left": True, "haul": haul}


async def _craft_plan(state: dict, db: AsyncSession, message: dict) -> dict:
    """Forecast before a batch. Spends nothing and reserves nothing (D-092)."""
    body = await _alive(state, db)
    output, units, extra = _craft_request(message)
    plan = await craft.plan(db, current(), current_catalog(), body, output, units, **extra)
    return {"plan": asdict(plan)}


async def _craft_start(state: dict, db: AsyncSession, message: dict) -> dict:
    """Start a batch. From then on it runs by itself, including while the player is offline."""
    body = await _alive(state, db)
    output, units, extra = _craft_request(message)
    batch = await craft.start(db, current(), current_catalog(), body, output, units, **extra)
    return {
        "batch": str(batch.id),
        "output": batch.output,
        "quality": float(batch.quality),
        "ready_at": batch.ready_at.isoformat(),
    }


async def _craft_repair(state: dict, db: AsyncSession, message: dict) -> dict:
    """Repair a thing: condition comes back, the ceiling drops (15-quality)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await craft.repair(db, current(), current_catalog(), body, item)
    return {"batch": str(batch.id), "ready_at": batch.ready_at.isoformat()}


async def _craft_recycle(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a thing apart for part of the materials. The return is always less than invested."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await craft.recycle(db, current(), current_catalog(), body, item)
    return {"batch": str(batch.id), "ready_at": batch.ready_at.isoformat()}


async def _library_copy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a recipe in the Library: free, unconditional, but only in person (D-053)."""
    body = await _alive(state, db)
    key = message["recipe"]
    await craft.copy_recipe(db, current_catalog(), body, key)
    return {"learned": key}


#: `land.claim` is gone (D-198): land outside a city is not privatized at all,
#: and a command left "just in case" is a way around the rule. Civic plots are
#: bought -- `land.buy`.


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


async def _land_rename(state: dict, db: AsyncSession, message: dict) -> dict:
    """Name a plot. In person and only by whoever disposes of it."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    try:
        await estate.rename(db, body, node, str(message.get("name", "")))
    except estate.EstateError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"renamed": node.key, "name": node.name}


async def _build_construct(state: dict, db: AsyncSession, message: dict) -> dict:
    """Build a house on your own plot. Materials at once, the building on schedule.

    `area` is the footprint; storeys stand on it (D-125), and the durability
    tier sets both the bill and the ceiling of height (D-145).
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
        floors=int(message.get("floors", 1)),
        strength=int(message.get("strength", 1)),
    )
    return {"building": True, "ready_at": job.run_at.isoformat()}


async def _build_estimate(state: dict, db: AsyncSession, message: dict) -> dict:
    """The bill before the work: what a house of this size and height costs.

    Shown together with what is already in hand -- the player must see "wood 12
    of 30" rather than find out at the click that the timber is short.
    """
    from src.engine import gear

    body = await _alive(state, db)
    constants = current()
    footprint = float(message.get("area", 0) or 0)
    floors = int(message.get("floors", 1))
    strength = int(message.get("strength", 1))
    if footprint <= 0 or floors < 1:
        raise Refused("площадь и этажность считаются от единицы")

    needed = estate.estimate(
        constants, footprint=footprint, floors=floors, strength=strength
    )
    pocket = await world.body_container(db, body)
    at_hand: dict[str, float] = {}
    for thing in await _things(db, constants, pocket):
        at_hand[thing["goods"]] = at_hand.get(thing["goods"], 0.0) + thing["amount"]

    catalog = current_catalog()
    return {
        "area": footprint,
        "floors": floors,
        "strength": strength,
        #: The usable area is what the machines and the cargo will be measured
        #: against; the plot is measured against the footprint alone.
        "usable": footprint * floors,
        "max_floors": estate.height_cap(constants, strength),
        "minutes": estate.build_minutes(
            constants, footprint=footprint, floors=floors, strength=strength
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


async def _lists(db: AsyncSession, node: Node) -> dict:
    """The location's door as the client sees it: shut or not, and both lists."""
    from src.engine import access

    return {
        "gated": node.gated,
        "allowed": await access.roster(db, node, allowed=True),
        "barred": await access.roster(db, node, allowed=False),
    }


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


async def _build_demolish(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take your own house apart. The work goes by time, the materials come at its end."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    try:
        job = await estate.demolish(db, current(), body, node)
    except estate.EstateError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"demolishing": True, "ready_at": job.run_at.isoformat()}


async def _gate_set(state: dict, db: AsyncSession, message: dict) -> dict:
    """Shut your own location for entry, or open it (D-199, D-204).

    In person: the door is on the spot. Passage through the location is not
    touched -- shutting stops entry alone.
    """
    from src.engine import access

    body = await _alive(state, db)
    identity = await _identity(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    try:
        await access.set_gate(db, node, identity, closed=bool(message["closed"]))
    except access.AccessError as refusal:
        raise Refused(str(refusal)) from refusal
    return await _lists(db, node)


async def _gate_list(state: dict, db: AsyncSession, message: dict) -> dict:
    """Name a person in a list, or strike them out of both (D-204).

    `allowed` picks the list: the white one lets into a shut location, the black
    one lets in nowhere. A name moves between the lists -- it is never in both.
    """
    from src.engine import access

    body = await _alive(state, db)
    identity = await _identity(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    who = await _identity_by_name(db, str(message["who"]))
    try:
        if message.get("strike"):
            await access.remove(db, node, identity, who)
        else:
            await access.add(
                db, node, identity, who, allowed=bool(message.get("allowed", True))
            )
    except access.AccessError as refusal:
        raise Refused(str(refusal)) from refusal
    return await _lists(db, node)


async def _deed_offer(state: dict, db: AsyncSession, message: dict) -> dict:
    """List your deed for sale: to everyone or to a named buyer. Remote."""
    identity = await _identity(state, db)
    deed = await _deed(db, message)
    to_whom = message.get("to")
    await estate.offer_deed(
        db, identity, deed,
        int(message.get("price") or 0),
        to=None if not to_whom else await _identity_by_name(db, str(to_whom)),
    )
    return {"offered": str(deed.id), "price": deed.sale_price}


async def _deed_buy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Buy a listed deed: money to the seller, title to the buyer. Remote."""
    identity = await _identity(state, db)
    deed = await _deed(db, message)
    await estate.buy_deed(db, identity, deed)
    return {"deed": str(deed.id), "money": await _money(db, identity.id)}


async def _deed_market(state: dict, db: AsyncSession, message: dict) -> dict:
    """Deeds that can be bought: open ones and those addressed to this identity."""
    rows = await estate.deeds_on_sale(db, state["identity_id"])
    return {"deeds": [await _deed_view(db, deed) for deed in rows]}


async def _deed(db: AsyncSession, message: dict):
    from src.models.estate import Deed

    deed = await db.get(Deed, uuid.UUID(message["deed"]))
    if deed is None:
        raise Refused("нет такой бумаги")
    return deed


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
    return [
        await _deed_view(db, deed) for deed in await estate.deeds_of(db, identity_id)
    ]


async def _farm_mark(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    plot = await farm.mark(
        db, current(), body,
        name=str(message.get("name", "")),
        area=float(message["area"]),
    )
    return {"plot": str(plot.id)}


async def _farm_plow(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    plot = await farm.plow(db, current(), body, await _plot(db, message))
    return {"plowing": str(plot.id)}


async def _farm_sow(state: dict, db: AsyncSession, message: dict) -> dict:
    """Sow with seeds of a cultivar: the batch has both a cultivar and its own strength (D-057)."""
    body = await _alive(state, db)
    seeds = await _own_item(db, body, message["seeds"])
    plot = await farm.sow(
        db, current(), current_catalog(), body, await _plot(db, message), seeds
    )
    return {
        "sown": str(plot.id),
        "culture": plot.culture_id,
        "vigor": None if plot.seed_vigor is None else float(plot.seed_vigor),
    }


async def _farm_care(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    plot = await farm.care(db, current(), body, await _plot(db, message))
    return {"cared": str(plot.id), "credits": plot.care_credits}


async def _farm_harvest(state: dict, db: AsyncSession, message: dict) -> dict:
    """Harvest. With selection the fund keeps its strength, without it degrades (D-067)."""
    body = await _alive(state, db)
    got = await farm.harvest(
        db, current(), current_catalog(), body, await _plot(db, message),
        select_seed=bool(message.get("select")),
    )
    return {"harvested": got, "selected": bool(message.get("select"))}


async def _breed_cross(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cross two cultivars in the nursery: the result comes after a full cycle."""
    body = await _alive(state, db)
    one = await _own_item(db, body, message["a"])
    other = await _own_item(db, body, message["b"])
    nursery = await breed.cross(db, current(), current_catalog(), body, one, other)
    return {"nursery": str(nursery.id), "ready_at": nursery.ready_at.isoformat()}


async def _breed_gather(state: dict, db: AsyncSession, message: dict) -> dict:
    """Collect the seedlings. Empty means the cultivar was too similar and did not sprout
    (D-067)."""
    body = await _alive(state, db)
    nursery = await db.get(Nursery, uuid.UUID(message["nursery"]))
    if nursery is None:
        raise Refused("нет такого питомника")
    cultivar = await breed.gather_cross(db, current(), current_catalog(), body, nursery)
    if cultivar is None:
        return {"sprouted": False}
    return {"sprouted": True, "variety": str(cultivar.id), "traits": cultivar.traits}


async def _breed_name(state: dict, db: AsyncSession, message: dict) -> dict:
    """Name a bred cultivar: the author's name is attached to it forever."""
    body = await _alive(state, db)
    cultivar = await db.get(Variety, uuid.UUID(message["variety"]))
    if cultivar is None:
        raise Refused("нет такого сорта")
    cultivar = await breed.name_variety(db, body, cultivar, str(message["name"]))
    return {"variety": str(cultivar.id), "name": cultivar.name}


async def _breed_agrotech(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take the agrotech of a base crop in the Library: free, but on foot."""
    body = await _alive(state, db)
    knowledge = await breed.copy_agrotech(
        db, current_catalog(), body, str(message["culture"])
    )
    return {"learned": knowledge is not None, "culture": message["culture"]}


async def _breed_varieties(state: dict, db: AsyncSession, message: dict) -> dict:
    """Own cultivars and ongoing crossings. Remote: can be viewed from anywhere."""
    body = await _body(db, state["identity_id"])
    cultivars = (
        await db.execute(
            select(Variety).where(Variety.author_identity_id == state["identity_id"])
        )
    ).scalars().all()
    nurseries = (
        await db.execute(
            select(Nursery).where(
                Nursery.body_id == (body.id if body else None),
                Nursery.done.is_(False),
            )
        )
    ).scalars().all() if body else []
    return {
        "varieties": [
            {
                "id": str(src.id),
                "name": src.name,
                "culture": src.culture_id,
                "stable": src.stable,
                "generation": src.generation,
                "traits": src.traits,
            }
            for src in cultivars
        ],
        "nurseries": [
            {"id": str(p.id), "ready_at": p.ready_at.isoformat()} for p in nurseries
        ],
    }


async def _farm_split(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    piece = await farm.split(
        db, current(), body, await _plot(db, message),
        float(message["area"]),
        name=str(message.get("name", "")),
    )
    return {"piece": str(piece.id)}


async def _farm_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """Farm summary. Remote: readable even from the road (D-118)."""
    rows = await farm.survey(db, current(), current_catalog(), state["identity_id"])
    return {"plots": rows}


async def _plot(db: AsyncSession, message: dict):
    from src.models.farm import Plot

    plot = await db.get(Plot, uuid.UUID(message["plot"]))
    if plot is None:
        raise Refused("нет такой делянки")
    return plot


async def _food_eat(state: dict, db: AsyncSession, message: dict) -> dict:
    """Eat a portion. Works on the road too: hardtack en route is normal (D-091)."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    item = await _own_item(db, body, message["item"])
    restored = await food.eat(db, current(), current_catalog(), body, item)
    return {"restored": round(restored, 2), "stamina": float(body.stamina)}


async def _cook_pot(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cook a pot: roles instead of a composition, portions -- cook.pot_portions (D-119)."""
    body = await _alive(state, db)
    batch = await craft.cook(
        db, current(), current_catalog(), body,
        str(message["output"]),
        dict(message.get("filling") or {}),
    )
    return {
        "batch": str(batch.id),
        "flavor": batch.flavor,
        "quality": float(batch.quality),
        "ready_at": batch.ready_at.isoformat(),
    }


async def _coin_mint(state: dict, db: AsyncSession, message: dict) -> dict:
    """Mint a coin. One fineness for the whole world -- 900 per mille, no choice (D-016)."""
    body = await _alive(state, db)
    batch = await coin.mint(
        db, current(), current_catalog(), body,
        str(message["coin"]),
        float(message["count"]),
    )
    return {
        "batch": str(batch.id),
        "coin": batch.output,
        "units": amount_float(batch.units),
        "fineness": float(batch.fineness),
        "spent": batch.spent,
        "ready_at": batch.ready_at.isoformat(),
    }


async def _coin_melt(state: dict, db: AsyncSession, message: dict) -> dict:
    """Melt coins: metal returns by their fineness, minus loss."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await coin.melt(
        db, current(), current_catalog(), body, item, float(message["count"])
    )
    return {
        "batch": str(batch.id),
        "coin": batch.output,
        "units": amount_float(batch.units),
        "fineness": float(batch.fineness),
        "ready_at": batch.ready_at.isoformat(),
    }


async def _gear_equip(state: dict, db: AsyncSession, message: dict) -> dict:
    """Wear a thing. One slot per thing: you cannot wear three backpacks (D-146)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    slot = await gear.equip(db, current(), current_catalog(), body, item)
    return {
        "equipped": slot,
        "goods": item.type_key,
        "capacity": round(
            await gear.capacity(db, current(), current_catalog(), body), 2
        ),
    }


async def _gear_unequip(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take off a worn thing. It stays in the hands -- it was there anyway."""
    body = await _alive(state, db)
    removed = await gear.unequip(db, body, str(message["slot"]))
    return {
        "unequipped": None if removed is None else removed.type_key,
        "capacity": round(
            await gear.capacity(db, current(), current_catalog(), body), 2
        ),
    }


async def _world_metrics(state: dict, db: AsyncSession, message: dict) -> dict:
    """World summary: aggregates and invariant checks (60-meta/04).

    Remote read: world figures are not tied to a place. Nothing personal here --
    only aggregates, and that is a privacy decision, not an official secret.
    """
    constants = current()
    return {
        "metrics": await metrics.collect(db, constants),
        "invariants": await metrics.invariants(db, constants),
    }


async def _world_summary(state: dict, db: AsyncSession, message: dict) -> dict:
    """The most important screen of an asynchronous game (04-notifications).

    Somebody comes back after a day away and must understand what happened in
    ten seconds. Until now the only way was to walk eight sidebar tabs and three
    view modes, and the mechanics that depend on being told -- a court case with
    a reaction window, a vote with a quorum, a debt that cuts a node off -- were
    running blind. The vault states the law plainly: any event with irreversible
    consequences must have both a notification **and** a window to react; one
    without the other is pointless. The windows existed; this is the other half.

    Three levels, and the order is the point:

    - **attention** -- where something can still be done, each with the time
      left. Never longer than five lines: if it grows, importance is marked
      wrong, and that is a design fault rather than a display one;
    - **happened** -- what is done and needs no answer. Read from the event
      journal, which records the identity even for what the worker did while
      nobody was watching;
    - **talk** -- a count. There is no chat history to return to (D-043): a
      conversation in a room is not correspondence.

    Remote: this is the Net, and it is read from the road as well.
    """
    constants = current()
    identity = await _identity(state, db)
    now_ = datetime.now(UTC)

    #: How far back "happened" reaches. The client sends when it last looked;
    #: without that we show a day, which is the absence the screen is built for.
    since = now_ - timedelta(days=1)
    if message.get("since"):
        try:
            told = datetime.fromisoformat(str(message["since"]))
            since = told if told.tzinfo else told.replace(tzinfo=UTC)
        except ValueError:
            pass

    attention: list[dict[str, Any]] = []

    #: A case against you is the most urgent thing there is: it ends in a
    #: sanction whether or not you noticed it.
    for case in (
        await db.execute(
            select(Case).where(
                Case.defendant_identity_id == identity.id,
                Case.state == CaseState.OPEN,
            )
        )
    ).scalars().all():
        window = timedelta(days=constants[R.JUSTICE_CLAIM_WINDOW])
        attention.append(
            {
                "kind": "case",
                "what": f"против вас иск: {case.claim}",
                "since": case.opened_at.isoformat(),
                "until": (case.opened_at + window).isoformat(),
            }
        )

    #: A vote you may cast and have not. Yours alone: what other cities decide
    #: is not your business, and a feed of it would be noise.
    own_ = await town.citizenship(db, identity.id)
    if own_ is not None:
        native = await town.by_id(db, own_.city_id)
        if native is not None:
            for poll in await vote.view(db, current_catalog(), native, identity.id):
                if poll["may_vote"] and poll["mine"] is None and poll["choice"] is None:
                    subject = poll.get("law") or poll["kind"]
                    attention.append(
                        {
                            "kind": "vote",
                            "what": f"голосование: {subject}",
                            "where": native.name,
                            "until": poll["closes_at"],
                        }
                    )

    #: A debt cuts the node off, and the machines in it stop. Property under
    #: threat, and nobody but the owner can clear it.
    for holding in await utility.holdings(db, constants, identity.id):
        if holding.get("debt", 0) > 0:
            attention.append(
                {
                    "kind": "debt",
                    "what": (
                        f"долг за быт: {holding['name']}"
                        + (" — узел отключён" if holding.get("cut_off") else "")
                    ),
                    "where": holding["name"],
                }
            )

    #: A reservation not redeemed in time leaves the deposit with the seller.
    for row in (
        await db.execute(
            select(Reservation).where(
                Reservation.buyer_identity_id == identity.id,
                Reservation.state == ReservationState.HELD,
            )
        )
    ).scalars().all():
        attention.append(
            {
                "kind": "reservation",
                "what": f"забрать бронь: {row.type_key}",
                "since": row.created_at.isoformat(),
                "until": row.expires_at.isoformat(),
            }
        )

    #: Soonest first: what expires today matters more than what expires in a week.
    attention.sort(key=lambda line: line.get("until") or "9999")

    happened = (
        await db.execute(
            select(Event)
            .where(
                Event.actor_identity_id == identity.id,
                Event.at > since,
                Event.kind.in_(TOLD),
            )
            .order_by(Event.at.desc())
            .limit(runtime.SUMMARY_LIMIT)
        )
    ).scalars().all()

    return {
        "at": now_.isoformat(),
        "attention": attention,
        "happened": [
            {"at": row.at.isoformat(), "kind": row.kind, "payload": row.payload}
            for row in happened
        ],
    }


#: What is worth telling about on return. The journal records everything -- the
#: swing of a pick, every ledger posting -- and a feed of that is not a summary
#: but a log. These are the ends of things: what finished, arrived, was found,
#: was decided, was lost.
TOLD = frozenset(
    {
        EventKind.CRAFT_FINISHED.value,
        EventKind.TRAVEL_ARRIVED.value,
        EventKind.PLOT_HARVESTED.value,
        EventKind.EXPLORE_FOUND.value,
        EventKind.EXPLORE_EMPTY.value,
        EventKind.BODY_DIED.value,
        EventKind.BODY_PRINTED.value,
        EventKind.MINING_COLLAPSED.value,
        EventKind.TRADE_EXECUTED.value,
        EventKind.ORDER_EXPIRED.value,
        EventKind.RESERVATION_LAPSED.value,
        EventKind.CITY_LAW_SET.value,
        EventKind.VOTE_CLOSED.value,
        EventKind.CASE_JUDGED.value,
        EventKind.SANCTION_APPLIED.value,
        EventKind.DEBT_WITHHELD.value,
        EventKind.UTILITY_CUT_OFF.value,
        EventKind.TRANSPORT_BROKE.value,
        EventKind.ROAD_LAID.value,
        EventKind.DEED_SOLD.value,
        EventKind.CITY_GRANT_PAID.value,
    }
)


async def _people_here(state: dict, db: AsyncSession, message: dict) -> dict:
    """Who is standing in this location.

    Needed to hand a thing to somebody: a name typed by hand would be a way to
    give things to anyone anywhere, and the point of handing over is that both
    people are in the same room. Those passing through are not in it -- the query
    asks for bodies in the node, and a body in transit is nowhere.
    """
    body = await _alive(state, db)
    rows = (
        await db.execute(
            select(Body, Identity)
            .join(Identity, Identity.id == Body.identity_id)
            .where(
                Body.node_id == body.node_id,
                Body.state == BodyState.ALIVE,
                Body.id != body.id,
            )
        )
    ).all()
    return {
        "people": sorted(
            ({"body": str(who.id), "name": person.name} for who, person in rows),
            key=lambda row: row["name"],
        )
    }


async def _item_hand(state: dict, db: AsyncSession, message: dict) -> dict:
    """Hand a thing to somebody standing here. In person on both sides.

    The hand-over speaks in the room: the chat gets an action line, because a
    transfer between two people is a fact the others in the room can see, and a
    silent one would be a way to move property unobserved.
    """
    giver = await _alive(state, db)
    item = await _own_item(db, giver, message["item"])
    taker = await db.get(Body, uuid.UUID(message["to"]))
    if taker is None:
        raise Refused("такого человека здесь нет")
    qty = message.get("amount")
    try:
        given = await storage.hand(
            db, current(), current_catalog(), giver, taker, item,
            None if qty is None else float(qty),
        )
    except storage.StorageError as refusal:
        raise Refused(str(refusal)) from refusal

    who = await db.get(Identity, taker.identity_id)
    await chat.say(
        db,
        current(),
        giver,
        f"передаёт {'—' if who is None else who.name}: {item.type_key}"
        + (f" ×{given:g}" if given != 1 else ""),
        kind=Utterance.ACTION,
    )
    return {"given": given, "goods": item.type_key}


async def _market_offers(state: dict, db: AsyncSession, message: dict) -> dict:
    """Other people's sell orders in the node: what can be reserved (D-047).

    The book is public by tiers, but a reservation is taken from a specific
    order -- so orders must be named. The seller's name is not shown: the book
    trades goods, not reputation.
    """
    identity_id = state["identity_id"]
    node = await _node(db, message["node"]) if message.get("node") else None
    if node is None:
        body = await _body(db, identity_id)
        if body is None:
            raise Refused("нет живого тела")
        node = await db.get(Node, body.node_id)

    rows = (
        await db.execute(
            select(Order).where(
                Order.node_id == node.id,
                Order.side == OrderSide.SELL,
                Order.state == OrderState.ACTIVE,
                Order.identity_id != identity_id,
            ).order_by(Order.price)
        )
    ).scalars().all()
    return {
        "offers": [
            {
                "id": str(order.id),
                "goods": order.type_key,
                "tier": order.tier,
                "price": order.price,
                "left": amount_float(order.amount_left),
            }
            for order in rows
            if order.amount_left > 0
        ]
    }


async def _market_reserve(state: dict, db: AsyncSession, message: dict) -> dict:
    """Reserve a lot with a deposit. Remote: the reservation is the trip plan."""
    identity = await _identity(state, db)
    order = await db.get(Order, uuid.UUID(message["order"]))
    if order is None:
        raise Refused("нет такой заявки")
    reservation = await market.reserve(
        db, current(), identity, order, float(message["amount"])
    )
    return {
        "reservation": str(reservation.id),
        "deposit": money_str(reservation.deposit),
        "expires_at": reservation.expires_at.isoformat(),
        "money": await _money(db, identity.id),
    }


async def _market_redeem(state: dict, db: AsyncSession, message: dict) -> dict:
    """Redeem a reservation: pay the remainder and take. In person (D-047)."""
    body = await _alive(state, db)
    reservation = await db.get(Reservation, uuid.UUID(message["reservation"]))
    if reservation is None:
        raise Refused("нет такой брони")
    deal = await market.redeem(db, current(), current_catalog(), body, reservation)
    return {
        "trade": str(deal.id),
        "goods": deal.type_key,
        "amount": amount_float(deal.amount),
        "money": await _money(db, state["identity_id"]),
    }


async def _rig_place(state: dict, db: AsyncSession, message: dict) -> dict:
    """Place a drilling rig on a vein. From then on it works without the player (D-115)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    vein = await db.get(Vein, uuid.UUID(message["vein"]))
    if vein is None:
        raise Refused("нет такой жилы")
    installation = await rig.place(db, body, item, vein)
    return {"rig": str(installation.id), "vein": vein.resource}


async def _rig_status(state: dict, db: AsyncSession, message: dict) -> dict:
    """What stands in the node: hopper, fuel, condition. In-person scene."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    return {"rigs": await rig.status(db, current(), body.node_id)}


async def _rig_empty(state: dict, db: AsyncSession, message: dict) -> dict:
    """Empty the hopper. On foot: without a carter the enterprise stands still."""
    body = await _alive(state, db)
    installation = await db.get(RigRow, uuid.UUID(message["rig"]))
    if installation is None:
        raise Refused("нет такой установки")
    taken = await rig.empty_hopper(db, current(), body, installation)
    return {"taken": taken}


async def _energy_grid(state: dict, db: AsyncSession, message: dict) -> dict:
    """The pool of the city we stand in. An empty pool is visible to all: that is politics
    (D-071)."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    node = await db.get(Node, body.node_id)
    pool = await energy.pool_of(db, current(), node, create=False)
    if pool is None:
        return {"grid": None}
    await energy.produce(db, current(), pool)
    city = await db.get(Node, pool.node_id)
    return {
        "grid": {
            "city": city.name if city else "?",
            "stored": round(float(pool.stored), 1),
            "tariff": float(pool.tariff),
        }
    }


async def _energy_charge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Charge a battery from the pool at the tariff. In person and paid (D-085).

    A battery is a machine (D-179): both the one in hand and the one standing
    here are charged. Whether the thing is reachable is checked by the energy
    engine itself.
    """
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    qty = message.get("amount")
    given = await energy.charge_battery(
        db, current(), body, item, None if qty is None else float(qty)
    )
    return {
        "charged": round(given, 2),
        "charge": round(float(item.charge), 2),
        "money": await _money(db, state["identity_id"]),
    }


async def _rest_sleep(state: dict, db: AsyncSession, message: dict) -> dict:
    """Go to sleep. Recovery runs offline -- it needs no tick (D-091)."""
    body = await _alive(state, db)
    await rest.sleep(db, current(), body)
    return {"sleeping": True, "home": body.sleeping_home}


async def _rest_wake(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    restored = await rest.wake(db, current(), body)
    return {"woke": True, "restored": round(restored, 2), "stamina": float(body.stamina)}


async def _chat_say(state: dict, db: AsyncSession, message: dict) -> dict:
    """Say something in the location. The kind is required: speech, action or out-of-game
    (D-050)."""
    body = await _alive(state, db)
    said = await chat.say(
        db,
        current(),
        body,
        str(message.get("text", "")),
        kind=Utterance(message.get("kind", Utterance.SPEECH.value)),
        quiet=bool(message.get("quiet", False)),
    )
    return {"said": str(said.id), "leaked": said.leaked}


async def _chat_hear(state: dict, db: AsyncSession, message: dict) -> dict:
    """What is heard and who whispers with whom. Room talk -- only from the room."""
    body = await _alive(state, db)
    return {
        "lines": [
            {
                "id": line.id,
                "who": line.who,
                "kind": line.kind.value,
                "quiet": line.quiet,
                "text": line.text,
                "overheard": line.overheard,
                "source": line.source,
                "at": line.at.isoformat(),
            }
            for line in await chat.hear(db, body)
        ],
        "circles": [
            {
                "id": circle.id,
                "name": circle.name,
                "members": list(circle.members),
                "mine": circle.mine,
            }
            for circle in await chat.circles(db, body)
        ],
    }


async def _chat_gather(state: dict, db: AsyncSession, message: dict) -> dict:
    """Gather a circle. Visible to all: "these ones are arranging something" (D-043)."""
    body = await _alive(state, db)
    group = await chat.gather(db, body, name=message.get("name") or None)
    return {"circle": str(group.id)}


async def _chat_join(state: dict, db: AsyncSession, message: dict) -> dict:
    body = await _alive(state, db)
    await chat.join(db, body, uuid.UUID(message["circle"]))
    return {"joined": message["circle"]}


async def _chat_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    await chat.leave_groups(db, state["identity_id"])
    return {"left": True}


async def _travel_go(state: dict, db: AsyncSession, message: dict) -> dict:
    """Go to a node -- even a non-adjacent one: the route builds itself (D-045, D-107)."""
    body = await _alive(state, db)
    goal = await _node(db, message["node"])
    transit = await travel.depart(db, current(), body, goal)
    return {
        "travel": str(transit.id),
        "to": goal.name,
        "arrives_at": transit.arrives_at.isoformat(),
        "legs_left": len(transit.plan or []),
    }


async def _market_load(state: dict, db: AsyncSession, message: dict) -> dict:
    """Load goods into the terminal. In person: goods are carried on foot (D-047)."""
    body = await _alive(state, db)
    moved = await market.load(
        db, current(), body, message["goods"], float(message["amount"])
    )
    return {"loaded": moved, "goods": message["goods"]}


async def _market_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take your own from the terminal -- bought goods too. Also on foot."""
    body = await _alive(state, db)
    moved = await market.take(
        db,
        current(),
        body,
        message["goods"],
        float(message["amount"]),
        tier=message.get("tier"),
    )
    return {"taken": moved, "goods": message["goods"]}


async def _market_sell(state: dict, db: AsyncSession, message: dict) -> dict:
    """List a sell order. Remote: the goods are already delivered."""
    identity = await _identity(state, db)
    node = await _node(db, message["node"])
    fill = await market.sell(
        db,
        current(),
        current_catalog(),
        identity,
        node,
        type_key=message["goods"],
        tier=message["tier"],
        price=int(message["price"]),
        quantity=float(message["amount"]),
    )
    return _fill(fill)


async def _market_buy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Buy: a limit order from a present body.

    The node is deliberately not named -- you buy where you stand.
    """
    body = await _alive(state, db)
    fill = await market.buy(
        db,
        current(),
        current_catalog(),
        body,
        type_key=message["goods"],
        tier=message["tier"],
        price=int(message["price"]),
        quantity=float(message["amount"]),
    )
    return _fill(fill)


async def _market_cancel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cancel an order. Disposing requires no presence."""
    identity_id = state["identity_id"]
    order = await db.get(Order, uuid.UUID(message["order"]))
    if order is None:
        raise Refused("нет такого ордера")
    await market.cancel(db, order, by=identity_id)
    return {"cancelled": str(order.id)}


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


async def _body_print(state: dict, db: AsyncSession, message: dict) -> dict:
    """Order a body print. The fee is taken up front, the body arrives on schedule."""
    identity = await _identity(state, db)
    node = await _node(db, str(message["node"]))
    job = await death.order(db, current(), current_catalog(), identity, node)
    return {
        "printing": {"node": node.key, "ready_at": job.run_at.isoformat()},
        "money": await _money(db, identity.id),
    }


async def _travel_cancel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Turn back from the road: the body stays where it left from (D-194)."""
    body = await _alive(state, db)
    try:
        await travel.turn_back(db, body)
    except travel.TravelError as refusal:
        raise Refused(str(refusal)) from refusal
    node = await db.get(Node, body.node_id)
    return {"cancelled": True, "node": None if node is None else node.key}


async def _ground_drop(state: dict, db: AsyncSession, message: dict) -> dict:
    """Put a thing down here: under the roof if there is one, in the yard if not."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    qty = message.get("amount")
    try:
        put_down = await storage.drop(
            db, current(), current_catalog(), body, item,
            None if qty is None else float(qty),
        )
    except storage.StorageError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"dropped": put_down, "goods": item.type_key}


async def _ground_pick(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pick up what lies here. Somebody else's floor is not touched (D-192)."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такой вещи")
    qty = message.get("amount")
    try:
        taken = await storage.pick(
            db, current(), current_catalog(), body, item,
            None if qty is None else float(qty),
        )
    except storage.StorageError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"picked": taken, "goods": item.type_key}


async def _finance_statement(state: dict, db: AsyncSession, message: dict) -> dict:
    """The account statement: the latest operations of this identity (D-190)."""
    return {
        "money": await _money(db, state["identity_id"]),
        "entries": await finance.statement(db, state["identity_id"]),
    }


async def _finance_transfer(state: dict, db: AsyncSession, message: dict) -> dict:
    """Send money to another identity. Remote: the account is the Network."""
    identity = await _identity(state, db)
    try:
        sent = await finance.transfer(
            db,
            identity,
            str(message.get("to") or ""),
            money(float(message.get("amount") or 0)),
            memo=str(message.get("memo") or ""),
        )
    except finance.FinanceError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"sent": sent, "money": await _money(db, state["identity_id"])}


async def _energy_plant(state: dict, db: AsyncSession, message: dict) -> dict:
    """Station of this node: fuel stock, hourly draw and output (D-189)."""
    body = await _alive(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused("тело вне узла")
    return {"plant": await energy.plant_view(db, current(), node)}


async def _energy_fuel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pour fuel into the station standing here. Anyone with coal may (D-189)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    сколько = message.get("amount")
    try:
        залито = await energy.fuel(
            db, current(), body, item,
            None if сколько is None else float(сколько),
        )
    except energy.EnergyError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"fuelled": залито, "goods": item.type_key}


async def _storage_put(state: dict, db: AsyncSession, message: dict) -> dict:
    """Put a thing from the hands into the node storage (D-181)."""
    body = await _alive(state, db)
    chest = await db.get(Item, uuid.UUID(message["storage"]))
    if chest is None:
        raise Refused("нет такого хранилища")
    item = await _own_item(db, body, message["item"])
    qty = message.get("amount")
    try:
        put = await storage.put(
            db, current(), current_catalog(), body, chest, item,
            None if qty is None else float(qty),
        )
    except storage.StorageError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"stored": put, "goods": item.type_key}


async def _storage_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a thing from storage into the hands. The carry limit still applies."""
    body = await _alive(state, db)
    chest = await db.get(Item, uuid.UUID(message["storage"]))
    item = await db.get(Item, uuid.UUID(message["item"]))
    if chest is None or item is None:
        raise Refused("нет такой вещи")
    qty = message.get("amount")
    try:
        taken = await storage.take(
            db, current(), current_catalog(), body, chest, item,
            None if qty is None else float(qty),
        )
    except storage.StorageError as refusal:
        raise Refused(str(refusal)) from refusal
    return {"taken": taken, "goods": item.type_key}


async def _station_place(state: dict, db: AsyncSession, message: dict) -> dict:
    """Place a machine in the node. In person and only at your own place (D-150)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    await station.place(db, current_catalog(), body, item)
    return {"placed": item.type_key}


async def _station_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a machine back into the hands. One busy with work is not given up."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    await station.take(db, current_catalog(), body, item)
    return {"taken": item.type_key}


async def _road_lay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Lay a surface tier on an edge or resurface a sagged one (D-158).

    The surface is written off at once, the road is laid on schedule: the work
    runs offline like every long-running one.
    """
    body = await _alive(state, db)
    edge = await db.get(Edge, uuid.UUID(message["edge"]))
    if edge is None:
        raise Refused("нет такого ребра")
    job = await road.lay(
        db, current(), current_catalog(), body, edge,
        mend=bool(message.get("mend")),
    )
    return {"road": str(job.id), "ready_at": job.run_at.isoformat()}


async def _road_here(state: dict, db: AsyncSession, message: dict) -> dict:
    """Roads from this node: what is laid, what sagged and what it costs."""
    body = await _alive(state, db)
    return {"roads": await road.view(db, current(), body)}


async def _transport_harness(state: dict, db: AsyncSession, message: dict) -> dict:
    """Harness to a vehicle standing here (D-157)."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    await transport.harness(db, current(), current_catalog(), body, item)
    return {"harnessed": item.type_key}


async def _transport_unharness(state: dict, db: AsyncSession, message: dict) -> dict:
    """Unharness. The convoy with its cargo stays standing here."""
    body = await _alive(state, db)
    wagon = await transport.unharness(db, body)
    return {"unharnessed": None if wagon is None else wagon.type_key}


async def _transport_load(state: dict, db: AsyncSession, message: dict) -> dict:
    """Load from the hands into the hold. In person: nothing is moved while on the go."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    qty = message.get("amount")
    carried = await transport.load(
        db, current(), current_catalog(), body, item,
        None if qty is None else float(qty),
    )
    return {"loaded": carried}


async def _transport_unload(state: dict, db: AsyncSession, message: dict) -> dict:
    """Unload from the hold into the hands. The hands limit does not go anywhere."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    qty = message.get("amount")
    carried = await transport.unload(
        db, current(), current_catalog(), body, item,
        None if qty is None else float(qty),
    )
    return {"unloaded": carried}


async def _ship_found(state: dict, db: AsyncSession, message: dict) -> dict:
    """Lay a ship's foundation at a spaceport (D-202).

    The foundation is written off at once, the first node -- the base and the
    connector in one -- arrives on schedule, like every long-running work.
    """
    body = await _alive(state, db)
    job = await ship.found(db, current(), body, str(message.get("name") or "Корабль"))
    return {"keel": str(job.id), "ready_at": job.run_at.isoformat()}


async def _ship_extend(state: dict, db: AsyncSession, message: dict) -> dict:
    """Lay one more node aboard, joined to the one you are standing in."""
    body = await _alive(state, db)
    job = await ship.extend(db, current(), body)
    return {"keel": str(job.id), "ready_at": job.run_at.isoformat()}


async def _ship_of(db: AsyncSession, body: Body, asked: str | None) -> Ship:
    """Which ship the command is about: the named one, else the one you stand in."""
    if asked:
        found = await db.get(Ship, uuid.UUID(asked))
        if found is None:
            raise Refused("нет такого корабля")
        return found
    aboard = await ship.aboard_of(db, body)
    if aboard is None:
        raise Refused("вы не на борту: назовите корабль или поднимитесь на него")
    return aboard


async def _ship_view(state: dict, db: AsyncSession, message: dict) -> dict:
    """The ship's summary: thrust, mass, thrust-to-mass and the price of every route.

    Remote, and shown **before** undocking: a refusal by mass must not be a
    surprise sprung after the hold is loaded (D-202).
    """
    body = await _alive(state, db)
    asked = message.get("ship")
    if not asked and await ship.aboard_of(db, body) is None:
        mine = await ship.ships_of(db, body.identity_id)
        return {
            "ships": [
                await ship.profile(db, current(), current_catalog(), one) for one in mine
            ]
        }
    vessel = await _ship_of(db, body, asked)
    return {"ships": [await ship.profile(db, current(), current_catalog(), vessel)]}


async def _ship_undock(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cast off: the edge to the port is removed, and that is the flight (D-201)."""
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    await ship.undock(db, current(), current_catalog(), body, vessel)
    return {"undocked": vessel.name}


async def _ship_fly(state: dict, db: AsyncSession, message: dict) -> dict:
    """Set out for a spaceport. Fuel now, docking by a journal job."""
    body = await _alive(state, db)
    vessel = await _ship_of(db, body, message.get("ship"))
    port = (
        await db.execute(select(Node).where(Node.key == str(message.get("port") or "")))
    ).scalars().first()
    if port is None:
        raise Refused("нет такого узла")
    job = await ship.fly(db, current(), current_catalog(), body, vessel, port)
    return {"flight": str(job.id), "arrives_at": job.run_at.isoformat()}


async def _ship_ports(state: dict, db: AsyncSession, message: dict) -> dict:
    """Where there is a spaceport at all. Public: ports are not a secret."""
    await _alive(state, db)
    return {
        "ports": [
            {"node": port.key, "name": port.name, "planet": port.planet.value}
            for port in await ship.ports(db)
        ]
    }


async def _explore_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """Go exploring for the named goal. The find arrives on schedule, including offline.

    The player picks the goal: a plot in the city, a place for a city, or a vein
    -- and for a vein a species can be named. A named one is found worse: aiming
    at the rare means coming back empty more often (D-152).
    """
    body = await _alive(state, db)
    job = await explore.survey(
        db, current(), body,
        goal=str(message.get("goal") or explore.SITE),
        resource=message.get("resource") or None,
    )
    return {"survey": str(job.id), "returns_at": job.run_at.isoformat()}


async def _explore_cancel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Turn back: the run is cancelled, the body is free again in the exit node.

    Stamina does not come back, the find will not happen (D-152). Deliberately
    not `_alive` + `require_here`: the scout is exactly the one for whom in-person
    actions are closed, and returning is the only thing available.
    """
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    job = await explore.cancel(db, body)
    return {"cancelled": str(job.id)}


async def _explore_goals(state: dict, db: AsyncSession, message: dict) -> dict:
    """What can be sought and what a run from here will cost.

    The species list comes from the vault (D-151). The forecast is per place: the
    price of exploration grows with every find from this node, and the player
    must see it before leaving, otherwise it reads as engine randomness (D-156).
    """
    #: The goal list is reference data, and the dead are entitled to it too:
    #: they simply have no forecast, because nobody can go into the field.
    body = await _body(db, state["identity_id"])
    #: The forecast is computed for the goal the player has picked right now: a
    #: requested species narrows the chance (D-151), and that must be visible
    #: before leaving rather than discovered after twenty empty runs.
    species = message.get("resource") or None
    goal = str(message.get("goal") or explore.SITE)
    return {
        "goals": list(explore.GOALS),
        "resources": list(explore.mineable(current_catalog())),
        "outlook": (
            None if body is None
            else await explore.outlook(
                db, current(), body,
                goal=goal,
                resource=None if species is None else str(species),
            )
        ),
    }


async def _utility_holdings(state: dict, db: AsyncSession, message: dict) -> dict:
    """Own holdings and household bills. Remote: paying is not done on foot."""
    return {"holdings": await utility.holdings(db, current(), state["identity_id"])}


async def _utility_pay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pay off a node's debt and reconnect it."""
    identity = await _identity(state, db)
    node = await _node(db, message["node"])
    paid = await utility.pay(db, current(), identity, node)
    return {"paid": paid, "money": await _money(db, identity.id)}


async def _city_found(state: dict, db: AsyncSession, message: dict) -> dict:
    """Found a city on your own planet node (D-023, D-098, D-159).

    The entry threshold is four buildings, not a coin. The land goes to the
    city: from then on the authority hands it out, not the yard owner (D-089).
    """
    body = await _alive(state, db)
    city = await town.establish(
        db, current(), current_catalog(), body, str(message.get("name") or "")
    )
    return {"city": str(city.id), "name": city.name}


async def _city_join(state: dict, db: AsyncSession, message: dict) -> dict:
    """Apply for citizenship. What comes of it is decided by the city charter (D-160)."""
    body = await _alive(state, db)
    city = await _city(state, db, message)
    result = await town.join(db, body, city)
    from src.models.city import Citizen

    citizen = isinstance(result, Citizen)
    return {
        "citizen": citizen,
        "city": city.name,
        "waiting": not citizen,
    }


async def _city_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    """Declare leaving. Citizenship lapses after `city.exit_delay` (D-160)."""
    identity = await _identity(state, db)
    entry = await town.leave(db, current(), identity)
    return {"leaves_at": entry.leaving_at.isoformat()}


async def _city_invite(state: dict, db: AsyncSession, message: dict) -> dict:
    """Invite a person to become a citizen. Right `citizens`."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    whom = await _identity_by_name(db, str(message["who"]))
    await town.invite(db, identity, city, whom)
    return {"invited": whom.name}


async def _city_admit(state: dict, db: AsyncSession, message: dict) -> dict:
    """Approve a citizenship application. Right `citizens`."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    whom = await _identity_by_name(db, str(message["who"]))
    await town.admit(db, identity, city, whom)
    return {"admitted": whom.name}


async def _city_exile(state: dict, db: AsyncSession, message: dict) -> dict:
    """Exile from the city. A sanction, not a personnel decision: right `justice`."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    whom = await _identity_by_name(db, str(message["who"]))
    await town.exile(db, identity, city, whom)
    return {"exiled": whom.name}


async def _city_citizens(state: dict, db: AsyncSession, message: dict) -> dict:
    """City residents and the application queue. Remote: reference, not a decision."""
    city = await _city(state, db, message)
    residents = []
    for entry in await town.citizens_of(db, city):
        who = await db.get(Identity, entry.identity_id)
        residents.append(
            {
                "name": None if who is None else who.name,
                "since": entry.since.isoformat(),
                "leaving_at": (
                    None if entry.leaving_at is None else entry.leaving_at.isoformat()
                ),
            }
        )
    orders = []
    for order in await town.requests_of(db, city):
        who = await db.get(Identity, order.identity_id)
        orders.append({"name": None if who is None else who.name, "kind": order.kind})
    return {
        "admission": town.admission(city),
        "citizens": sorted(residents, key=lambda zh: zh["name"] or ""),
        "requests": orders,
    }


async def _city_votes(state: dict, db: AsyncSession, message: dict) -> dict:
    """Ongoing city polls. Remote: can be viewed from anywhere."""
    city = await _city(state, db, message)
    return {
        "votes": await vote.view(db, current_catalog(), city, state["identity_id"])
    }


async def _city_vote(state: dict, db: AsyncSession, message: dict) -> dict:
    """Vote. A vote is participation, not governing: cast over the Net (D-161)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await db.get(Vote, uuid.UUID(message["vote"]))
    if poll is None or poll.city_id != city.id:
        raise Refused("нет такого голосования в этом городе")
    await vote.cast(db, city, identity, poll, bool(message.get("yes")))
    pro, contra = await vote.standing(db, poll)
    return {"yes": pro, "no": contra}


async def _city_election(state: dict, db: AsyncSession, message: dict) -> dict:
    """Convene a ruler election (D-162). Candidates nominate themselves as it goes."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await vote.open_election(db, current(), city, identity)
    return {"vote": str(poll.id), "closes_at": poll.closes_at.isoformat()}


async def _city_recall(state: dict, db: AsyncSession, message: dict) -> dict:
    """Convene a ruler recall. If it passes, the office is vacated and an election follows."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await vote.open_recall(db, current(), city, identity)
    return {"vote": str(poll.id), "closes_at": poll.closes_at.isoformat()}


async def _city_nominate(state: dict, db: AsyncSession, message: dict) -> dict:
    """Nominate yourself for ruler. Yourself, not on somebody's proposal."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await db.get(Vote, uuid.UUID(message["vote"]))
    if poll is None or poll.city_id != city.id:
        raise Refused("нет такого голосования в этом городе")
    await vote.nominate(db, city, identity, poll)
    return {"nominated": identity.name}


async def _city_choose(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cast a vote for a candidate in the election."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await db.get(Vote, uuid.UUID(message["vote"]))
    if poll is None or poll.city_id != city.id:
        raise Refused("нет такого голосования в этом городе")
    candidate = await db.get(Identity, uuid.UUID(message["candidate"]))
    if candidate is None:
        raise Refused("нет такой личности")
    await vote.choose(db, city, identity, poll, candidate)
    return {"chosen": candidate.name}


async def _city_council(state: dict, db: AsyncSession, message: dict) -> dict:
    """Council membership and how it is assembled (D-164). Remote: reference."""
    city = await _city(state, db, message)
    places = []
    for place in await vote.council_of(db, city):
        who = await db.get(Identity, place.identity_id)
        places.append({"name": None if who is None else who.name, "how": place.how})
    return {
        "mode": vote.council_mode(city),
        "seats": vote.council_seats(city),
        "members": places,
    }


async def _city_council_seat(state: dict, db: AsyncSession, message: dict) -> dict:
    """Appoint to the council or vacate a seat. Only where seats are appointed."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    whom = await _identity_by_name(db, str(message["who"]))
    if message.get("out"):
        await town.require(db, identity.id, city, Power.OFFICES)
        removed = await vote.vacate(db, city, whom)
        return {"vacated": removed}
    await vote.appoint_to_council(db, city, identity, whom)
    return {"seated": whom.name}


async def _city_council_election(state: dict, db: AsyncSession, message: dict) -> dict:
    """Convene a council election: as many win as there are seats."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    poll = await vote.open_council_election(db, current(), city, identity)
    return {"vote": str(poll.id), "closes_at": poll.closes_at.isoformat()}


async def _city_sue(state: dict, db: AsyncSession, message: dict) -> dict:
    """File a complaint with the city court. The fee goes to the treasury at once (D-117)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    defendant = await _identity_by_name(db, str(message["who"]))
    case = await justice.sue(
        db, current(), city, identity, defendant, str(message.get("claim") or "")
    )
    return {"case": str(case.id)}


async def _city_judge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Deliver a verdict. Without a sanction it is an acquittal: there are no hanging cases."""
    identity = await _identity(state, db)
    case = await db.get(Case, uuid.UUID(message["case"]))
    if case is None:
        raise Refused("нет такого дела")
    sanction = message.get("sanction") or None
    penalty = await justice.judge(
        db, current(), current_catalog(), identity, case,
        sanction=None if sanction is None else str(sanction),
        days=None if message.get("days") is None else float(message["days"]),
        amount=None if message.get("amount") is None else float(message["amount"]),
        verdict=str(message.get("verdict") or ""),
        #: Where to imprison when there are several penal faces -- the court names it (D-176).
        prison_node=None if message.get("prison") is None else str(message["prison"]),
    )
    return {"judged": case.state.value, "sanction": None if penalty is None else penalty.kind}


async def _city_cases(state: dict, db: AsyncSession, message: dict) -> dict:
    """City cases and sanction primitives from the vault. Remote: reference."""
    city = await _city(state, db, message)
    return {
        "cases": await justice.view(db, city),
        "sanctions": [
            {
                "id": primitive.id,
                "name": primitive.name,
                "enforced": primitive.id in justice.ENFORCED,
            }
            for primitive in current_catalog().laws.sanctions
        ],
        #: The city's penal faces (D-176): with several, the court names which
        #: one to send to -- the client needs the list.
        "prisons": [
            {"key": node.key, "name": node.name}
            for node in await justice.prisons_of(db, city)
        ],
    }


async def _bank_view(state: dict, db: AsyncSession, message: dict) -> dict:
    """The bank through the player's eyes: the rate with an explanation, own loans, the reserve
    (D-167)."""
    from src.models.bank import RateDecision

    constants = current()
    decision = (
        await db.execute(
            select(RateDecision).order_by(RateDecision.decided_at.desc()).limit(1)
        )
    ).scalars().first()
    loans = []
    for loan in await bank.loans_of(db, state["identity_id"]):
        await bank.accrue(db, constants, loan)
        loans.append(
            {
                "id": str(loan.id),
                "principal": loan.principal,
                "outstanding": loan.outstanding,
                "rate": float(loan.rate),
                "taken_at": loan.taken_at.isoformat(),
            }
        )
    return {
        "rate": await bank.key_rate(db, constants),
        "why": None if decision is None else decision.why,
        #: Reserve and circulation are public: monetary policy is never secret (D-030).
        "reserve": await bank.reserve(db),
        "circulating": await bank.circulating(db),
        #: The limit is a public formula from labour (D-173): the player sees
        #: both the number and what it is made of before going for a loan.
        "limit": (limits := await bank.credit_limit(db, constants, state["identity_id"]))[0],
        "limit_why": limits[1],
        #: The rate this borrower would actually get, named before the button
        #: is pressed (D-193): the key rate alone told them nothing.
        "your_rate": (
            offer := await bank.offered_rate(
                db, constants, current_catalog(), await _identity(state, db),
                amount=int(message.get("amount") or 0),
            )
        )[0],
        "your_rate_why": offer[1],
        "loans": loans,
    }


async def _bank_borrow(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a loan. Money comes from the reserve; the shortfall is printed (D-087)."""
    identity = await _identity(state, db)
    loan = await bank.borrow(
        db, current(), current_catalog(), identity, float(message["amount"])
    )
    return {"loan": str(loan.id), "rate": float(loan.rate)}


async def _bank_repay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Repay debt. Money goes to the reserve, not into circulation."""
    from src.models.bank import Loan

    identity = await _identity(state, db)
    loan = await db.get(Loan, uuid.UUID(message["loan"]))
    if loan is None or loan.identity_id != identity.id:
        raise Refused("нет такого займа")
    paid = await bank.repay(
        db, current(), identity, loan,
        None if message.get("amount") is None else float(message["amount"]),
    )
    return {"paid": paid, "left": loan.outstanding}


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


async def _bank_council(state: dict, db: AsyncSession, message: dict) -> dict:
    """Who decides the rate now and in what corridor (D-172). Remote: reference."""
    constants = current()
    from src.constants import registry as R

    recommendation, reason = bank.compute_rate(
        constants,
        previous=await bank.key_rate(db, constants),
        inflation=await bank._inflation(db, constants),
        emission_share=await bank._emission_share(
            db, constants, now=_now()
        ),
    )
    until = await bank.locked_until(db)
    return {
        "council_decides": await bank.council_decides(db, constants),
        "cities_with_hall": await bank.cities_with_hall(db),
        "handover_at": constants[R.BANK_COUNCIL_HANDOVER_CITIES],
        "advised": recommendation,
        "why": reason,
        "corridor": constants[R.BANK_COUNCIL_RATE_DEVIATION],
        "locked_until": None if until is None else until.isoformat(),
    }


async def _bank_council_rate(state: dict, db: AsyncSession, message: dict) -> dict:
    """The city's vote on the rate. Cast by the holder of the `laws` right (D-172)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    decision = await bank.council_set_rate(
        db, current(), city, identity, float(message["rate"])
    )
    return {"rate": float(decision.rate), "why": decision.why}


async def _person_report(state: dict, db: AsyncSession, message: dict) -> dict:
    """Point at a defective print (D-173). Lowers trust, does not kill."""
    identity = await _identity(state, db)
    whom = await _identity_by_name(db, str(message["who"]))
    await bank.report_defect(db, identity, whom)
    return {"reported": whom.name}


async def _person_unreport(state: dict, db: AsyncSession, message: dict) -> dict:
    """Withdraw your report: one may err, and one must be able to correct it."""
    identity = await _identity(state, db)
    whom = await _identity_by_name(db, str(message["who"]))
    return {"withdrawn": await bank.withdraw_report(db, identity, whom)}


async def _city_bail(state: dict, db: AsyncSession, message: dict) -> dict:
    """The city repays a citizen's debt from the treasury (D-175): frees its own line.

    In person and by treasury right: spending is an authority decision (D-155).
    """
    from src.models.bank import Loan

    identity = await _identity(state, db)
    body = await _alive(state, db)
    city = await _city(state, db, message)
    await town.require_at_hall(db, body, city)
    await town.require(db, identity.id, city, Power.TREASURY)
    loan = await db.get(Loan, uuid.UUID(message["loan"]))
    if loan is None:
        raise Refused("нет такого займа")
    treasury = await town.treasury(db, city)
    paid = await bank.repay(
        db, current(), identity, loan,
        None if message.get("amount") is None else float(message["amount"]),
        from_account=treasury,
    )
    return {"paid": paid, "left": loan.outstanding}


async def _city_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """City summary: charter, laws, offices, treasury and own powers.

    Remote read: city figures are not tied to a place. Any city may be viewed --
    your own by body, or one named by node key.
    """
    city = await _city(state, db, message)
    summary = await town.survey(db, current(), current_catalog(), city)
    summary["powers"] = sorted(await town.powers_of(db, state["identity_id"], city))
    #: Whether decisions are made here: governing is in-person (D-155), and the
    #: client needs to know whether to show buttons or send you to the town hall.
    body = await _body(db, state["identity_id"])
    summary["at_hall"] = False
    if body is not None:
        node = await db.get(Node, body.node_id)
        summary["at_hall"] = node is not None and node.owner_city_id == city.id and (
            town.HALL in await _stations(db, node)
        )
    #: Free and allotted plots: land allotment is the first thing people enter
    #: the administration for (D-089).
    plots = (
        await db.execute(select(Node).where(Node.owner_city_id == city.id))
    ).scalars().all()
    summary["lots"] = [
        {
            "key": node.key,
            "name": node.name,
            "area": float(node.area_m2),
            "owner": None if node.owner_identity_id is None else str(node.owner_identity_id),
            "free": node.owner_identity_id is None and bool(node.properties.get("участок")),
        }
        for node in plots
        if node.properties.get("участок")
    ]
    summary["citizens"] = await _citizens(db)
    return {"city": summary}


async def _city_panel(state: dict, db: AsyncSession, message: dict) -> dict:
    """The city's economic panel. Remote read (D-140).

    The public snapshot is visible to all, guests included: prices and turnover
    are common knowledge (D-047). The full set with the treasury by grounds --
    to those with the `dashboard` right. Nothing personal in either snapshot.
    """
    city = await _city(state, db, message)
    full = await town.may(db, state["identity_id"], city, Power.DASHBOARD)
    summary = await panel.collect(db, current(), city, full=full)
    summary["full"] = full
    return {"panel": summary}


async def _city_law(state: dict, db: AsyncSession, message: dict) -> dict:
    """Write a code-law. In person and by narrow right (D-154, D-155)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    await town.set_law(
        db, current(), current_catalog(), identity, city,
        str(message["law"]), str(message["value"]),
        body=await _body(db, identity.id),
    )
    return {"law": message["law"], "value": message["value"]}


async def _city_charter(state: dict, db: AsyncSession, message: dict) -> dict:
    """Answer a charter question."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    param = message.get("param")
    await town.set_charter(
        db, current_catalog(), identity, city,
        str(message["question"]), str(message["option"]),
        None if param is None else float(param),
        body=await _body(db, identity.id),
    )
    return {"question": message["question"], "option": message["option"]}


async def _city_about(state: dict, db: AsyncSession, message: dict) -> dict:
    """Rewrite the city's word to newcomers (D-183)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    await town.describe(
        db, identity, city, str(message.get("text") or ""),
        body=await _body(db, identity.id),
    )
    return {"about": city.about}


async def _city_appoint(state: dict, db: AsyncSession, message: dict) -> dict:
    """Appoint to an office. Only what you have yourself can be given."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    to_whom = await _identity_by_name(db, str(message["whom"]))
    #: A right is a string: broad (`treasury`) or narrow (`law:import_duty`).
    #: No need to check the list here: the engine matches rights against what
    #: the appointer has, and a nonexistent right simply opens nothing.
    powers = tuple(str(raw) for raw in message.get("powers") or ())
    office = await town.appoint(
        db, identity, city, to_whom,
        title=str(message.get("title") or "Должность"),
        powers=powers,
        body=await _body(db, identity.id),
    )
    return {"office": str(office.id), "whom": to_whom.name}


async def _city_revoke(state: dict, db: AsyncSession, message: dict) -> dict:
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    office = await db.get(Office, uuid.UUID(message["office"]))
    if office is None:
        raise Refused("нет такой должности")
    await town.revoke(db, identity, city, office, body=await _body(db, identity.id))
    return {"revoked": str(office.id)}


async def _city_spend(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pay from the treasury. Salary, reward and contract are one posting."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    to_whom = await _identity_by_name(db, str(message["whom"]))
    total = int(message["amount"])
    await town.spend(
        db, identity, city, to_whom, total,
        memo=str(message.get("memo") or ""),
        body=await _body(db, identity.id),
    )
    return {"spent": total, "whom": to_whom.name}


async def _city_allot(state: dict, db: AsyncSession, message: dict) -> dict:
    """Allot a civic plot to a resident: one's own home starts here (D-089)."""
    identity = await _identity(state, db)
    city = await _city(state, db, message)
    plot = await _node(db, str(message["node"]))
    to_whom = await _identity_by_name(db, str(message["whom"]))
    await town.allot(
        db, identity, city, plot, to_whom, body=await _body(db, identity.id)
    )
    return {"allotted": plot.key, "whom": to_whom.name}


async def _city(state: dict, db: AsyncSession, message: dict):
    """The city in question: named by node key, or the one where the body stands."""
    if message.get("city"):
        node = await _node(db, str(message["city"]))
    else:
        body = await _body(db, state["identity_id"])
        if body is None:
            raise Refused("нет живого тела: назовите город явно")
        node = await db.get(Node, body.node_id)
    city = await town.of_node(db, node)
    if city is None:
        raise Refused("здесь нет города: за стенами законов нет")
    return city


async def _identity_by_name(db: AsyncSession, name: str) -> Identity:
    found = (
        await db.execute(select(Identity).where(Identity.name == name))
    ).scalar_one_or_none()
    if found is None:
        raise Refused(f"нет личности {name!r}")
    return found


async def _citizens(db: AsyncSession) -> list[str]:
    """Who can be appointed or paid at all. Names are public (D-058)."""
    rows = await db.execute(select(Identity.name).order_by(Identity.name))
    return [row[0] for row in rows]


_COMMANDS = {
    "look": _look,
    "account.profile": _account_profile,
    "account.update": _account_update,
    "account.password": _account_password,
    "account.email": _account_email,
    "account.logout": _account_logout,
    "pow.challenge": _challenge,
    "mine.start": _mine_start,
    "mine.swing": _mine_swing,
    "mine.timber": _mine_timber,
    "mine.pace": _mine_pace,
    "mine.leave": _mine_leave,
    "craft.plan": _craft_plan,
    "craft.start": _craft_start,
    "craft.repair": _craft_repair,
    "craft.recycle": _craft_recycle,
    "library.copy": _library_copy,
    "travel.go": _travel_go,
    "rest.sleep": _rest_sleep,
    "rest.wake": _rest_wake,
    "food.eat": _food_eat,
    "cook.pot": _cook_pot,
    "coin.mint": _coin_mint,
    "coin.melt": _coin_melt,
    "energy.grid": _energy_grid,
    "energy.charge": _energy_charge,
    "gear.equip": _gear_equip,
    "gear.unequip": _gear_unequip,
    "world.metrics": _world_metrics,
    "world.summary": _world_summary,
    "market.offers": _market_offers,
    "market.reserve": _market_reserve,
    "market.redeem": _market_redeem,
    "rig.place": _rig_place,
    "rig.status": _rig_status,
    "rig.empty": _rig_empty,
    "land.buy": _land_buy,
    "land.rename": _land_rename,
    "build.construct": _build_construct,
    "build.estimate": _build_estimate,
    "build.demolish": _build_demolish,
    "build.demolish_estimate": _demolish_estimate,
    "gate.set": _gate_set,
    "gate.list": _gate_list,
    "deed.offer": _deed_offer,
    "deed.buy": _deed_buy,
    "deed.market": _deed_market,
    "farm.mark": _farm_mark,
    "farm.plow": _farm_plow,
    "farm.sow": _farm_sow,
    "farm.care": _farm_care,
    "farm.harvest": _farm_harvest,
    "farm.split": _farm_split,
    "breed.cross": _breed_cross,
    "breed.gather": _breed_gather,
    "breed.name": _breed_name,
    "breed.varieties": _breed_varieties,
    "breed.agrotech": _breed_agrotech,
    "farm.survey": _farm_survey,
    "chat.say": _chat_say,
    "chat.hear": _chat_hear,
    "chat.gather": _chat_gather,
    "chat.join": _chat_join,
    "chat.leave": _chat_leave,
    "market.load": _market_load,
    "market.take": _market_take,
    "market.sell": _market_sell,
    "market.buy": _market_buy,
    "market.cancel": _market_cancel,
    "body.printers": _body_printers,
    "body.print": _body_print,
    "station.place": _station_place,
    "station.take": _station_take,
    "storage.put": _storage_put,
    "storage.take": _storage_take,
    "energy.plant": _energy_plant,
    "energy.fuel": _energy_fuel,
    "finance.statement": _finance_statement,
    "finance.transfer": _finance_transfer,
    "ground.drop": _ground_drop,
    "ground.pick": _ground_pick,
    "item.hand": _item_hand,
    "people.here": _people_here,
    "travel.cancel": _travel_cancel,
    "road.lay": _road_lay,
    "road.here": _road_here,
    "ship.found": _ship_found,
    "ship.extend": _ship_extend,
    "ship.view": _ship_view,
    "ship.undock": _ship_undock,
    "ship.fly": _ship_fly,
    "ship.ports": _ship_ports,
    "transport.harness": _transport_harness,
    "transport.unharness": _transport_unharness,
    "transport.load": _transport_load,
    "transport.unload": _transport_unload,
    "explore.survey": _explore_survey,
    "explore.cancel": _explore_cancel,
    "explore.goals": _explore_goals,
    "utility.holdings": _utility_holdings,
    "utility.pay": _utility_pay,
    "city.found": _city_found,
    "city.join": _city_join,
    "city.leave": _city_leave,
    "city.invite": _city_invite,
    "city.admit": _city_admit,
    "city.exile": _city_exile,
    "city.citizens": _city_citizens,
    "city.votes": _city_votes,
    "city.vote": _city_vote,
    "city.election": _city_election,
    "city.recall": _city_recall,
    "city.nominate": _city_nominate,
    "city.choose": _city_choose,
    "city.council": _city_council,
    "city.council_seat": _city_council_seat,
    "city.council_election": _city_council_election,
    "city.sue": _city_sue,
    "city.judge": _city_judge,
    "city.cases": _city_cases,
    "bank.view": _bank_view,
    "bank.borrow": _bank_borrow,
    "bank.repay": _bank_repay,
    "bank.council": _bank_council,
    "bank.council_rate": _bank_council_rate,
    "person.report": _person_report,
    "person.unreport": _person_unreport,
    "city.bail": _city_bail,
    "city.survey": _city_survey,
    "city.panel": _city_panel,
    "city.law": _city_law,
    "city.charter": _city_charter,
    "city.about": _city_about,
    "city.appoint": _city_appoint,
    "city.revoke": _city_revoke,
    "city.spend": _city_spend,
    "city.allot": _city_allot,
}


def _fill(fill: market.Fill) -> dict[str, Any]:
    """What came of the order: the order itself and deals, if any happened."""
    return {
        "order": str(fill.order.id),
        "state": fill.order.state.value,
        "left": amount_float(fill.order.amount_left),
        "traded": fill.traded,
        "trades": [
            {"price": trade.price, "amount": amount_float(trade.amount)}
            for trade in fill.trades
        ],
    }


def _craft_request(message: dict) -> tuple[str, float, dict[str, Any]]:
    """Parsing a batch request -- identical for forecast and start.

    Otherwise the forecast would be computed for one request and the batch
    would run on another.
    """
    return (
        message["output"],
        float(message.get("units", 1)),
        {
            "tool_item_id": _optional_uuid(message.get("tool")),
            "proportions": message.get("proportions"),
            #: "Put on automatic" is the master's decision: volume instead of
            #: quality, and an energy bill (D-035, D-058).
            "auto": bool(message.get("auto", False)),
            #: Which operation, when several give the same thing: felling wood
            #: with an axe or gathering deadwood by hand (D-196).
            "way": message.get("way"),
        },
    )


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


async def _body(db: AsyncSession, identity_id: uuid.UUID) -> Body | None:
    stmt = select(Body).where(Body.identity_id == identity_id, Body.state == BodyState.ALIVE)
    return (await db.execute(stmt)).scalars().first()


async def _alive(state: dict, db: AsyncSession) -> Body:
    """The body being acted with. Matter requires presence (D-044)."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")
    return body


async def _bench(
    db: AsyncSession, node: Node, body: Body, *, furniture: bool = False
) -> list[dict[str, Any]]:
    """The node's machines by name: quality, condition and who occupies them (D-150).

    Separate from `stations`: that list answers "which scenes to show", this one
    answers "which machine can I stand at right now". With `furniture=True` the
    same for furniture: a bed and a shelf are not machines, and the client shows
    them in a separate window.
    """
    from src.constants.catalog import ItemKind

    expected_value = ItemKind.FURNITURE if furniture else ItemKind.STATION
    book = current_catalog().recipes
    where = await world.node_container(db, node)
    items = (
        await db.execute(select(Item).where(Item.container_id == where.id))
    ).scalars().all()

    out: list[dict[str, Any]] = []
    for item in items:
        try:
            recipe = book.recipe(item.type_key)
        except Exception:  # noqa: BLE001 -- raw material at the machine has no recipe
            continue
        if recipe.kind is not expected_value:
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
                    if item.type_key == energy.BATTERY
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
    from src.constants import registry as R
    from src.engine import world as places

    origin = await places.epoch(db)
    return {
        "planet": node.planet.value,
        "epoch": None if origin is None else origin.isoformat(),
        "day_hours": constants[R.TIME_DAY_TERRA],
    }


async def _storages(
    db: AsyncSession, constants, node: Node, body: Body
) -> list[dict[str, Any]]:
    """Node storages with contents (D-181).

    That a chest exists is visible to all -- it stands in the room. What is
    inside is seen only by whoever may open it: otherwise "look" would become a
    way around the rule "do not touch what is not yours".
    """
    catalog = current_catalog()
    where = await world.node_container(db, node)
    things = (
        await db.execute(select(Item).where(Item.container_id == where.id))
    ).scalars().all()
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
                    await _things(db, constants, await storage.inside(db, thing))
                    if allowed
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
    from src.models.travel import Harness

    cat = current_catalog()
    where = await world.node_container(db, node)
    things = (
        await db.execute(select(Item).where(Item.container_id == where.id))
    ).scalars().all()
    harnessed_ = set((await db.execute(select(Harness.item_id))).scalars().all())
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


async def _stations(db: AsyncSession, node: Node) -> list[str]:
    """Machines and the terminal standing in the node. The node scene is built from them."""
    where = await world.node_container(db, node)
    rows = await db.execute(
        select(Item.type_key).where(Item.container_id == where.id).distinct()
    )
    return sorted(row[0] for row in rows)


async def _money(db: AsyncSession, identity_id: uuid.UUID) -> str:
    """The identity's account. The balance is the sum of postings; there is no "money" field."""
    account = await ledger.account_for(db, AccountKind.IDENTITY, identity_id)
    return money_str(await ledger.balance(db, account.id))


async def _things(db: AsyncSession, constants, container) -> list[dict[str, Any]]:
    """Container contents as the owner sees them: with a number and a tier."""
    items = (
        await db.execute(select(Item).where(Item.container_id == container.id))
    ).scalars().all()
    catalog = current_catalog()
    #: The mark is shown as a name: the player must see whose work it is (D-058).
    marks = await _makers(db, items)
    cultivars = await _varieties(db, items)
    return [
        {
            "id": str(item.id),
            "goods": item.type_key,
            "amount": amount_float(item.amount),
            "quality": None if item.quality is None else float(item.quality),
            "tier": market.tier_of(constants, None if item.quality is None
                                   else float(item.quality)),
            "condition": float(item.condition),
            "flavor": item.flavor,
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
                if item.type_key == energy.BATTERY
                else None
            ),
        }
        for item in items
    ]


async def _varieties(db: AsyncSession, items) -> dict[uuid.UUID, str]:
    """Cultivar names by seeds. A nameless hybrid gets an honest "hybrid"."""
    ids = {item.variety_id for item in items if item.variety_id is not None}
    if not ids:
        return {}
    rows = await db.execute(select(Variety).where(Variety.id.in_(ids)))
    return {
        cultivar.id: cultivar.name or f"гибрид, поколение {cultivar.generation}"
        for cultivar in rows.scalars().all()
    }


async def _makers(db: AsyncSession, items) -> dict[uuid.UUID, str]:
    """Craftsmen's names by item marks, in one query."""
    ids = {item.maker_identity_id for item in items if item.maker_identity_id is not None}
    if not ids:
        return {}
    rows = await db.execute(
        select(Identity.id, Identity.name).where(Identity.id.in_(ids))
    )
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
        select(Knowledge.key).where(
            Knowledge.identity_id == identity_id, Knowledge.kind == kind
        )
    )
    return sorted(row[0] for row in rows)


async def _orders(db: AsyncSession, identity_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Order).where(
                Order.identity_id == identity_id, Order.state == OrderState.ACTIVE
            )
        )
    ).scalars().all()
    return [
        {
            "id": str(order.id),
            "side": order.side.value,
            "goods": order.type_key,
            "tier": order.tier,
            "price": order.price,
            "left": amount_float(order.amount_left),
        }
        for order in rows
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
    """Jobs: long-running works that go by themselves, including while the player is offline."""
    rows = (
        await db.execute(
            select(CraftBatch)
            .join(Body, Body.id == CraftBatch.body_id)
            .where(Body.identity_id == identity_id, CraftBatch.state == BatchState.RUNNING)
        )
    ).scalars().all()

    #: Which machine each batch occupies. The location screen lists the node's
    #: objects with what each is doing, and "Кузница · гвозди ×200" cannot be
    #: assembled without knowing that this batch is at the forge. Made by hand
    #: has no station, and says so by staying empty.
    benches: dict[uuid.UUID, str] = {}
    wanted = {batch.station_item_id for batch in rows if batch.station_item_id}
    if wanted:
        for item in (
            await db.execute(select(Item).where(Item.id.in_(wanted)))
        ).scalars().all():
            benches[item.id] = item.type_key

    return [
        {
            "id": str(batch.id),
            "work": batch.kind.value,
            "output": batch.output,
            "units": amount_float(batch.units),
            "quality": float(batch.quality),
            "station": benches.get(batch.station_item_id) if batch.station_item_id else None,
            #: Both ends of the term, not just the far one: the deadline bar
            #: shows a share of the whole, and a share needs a beginning. Sent
            #: rather than remembered by the client -- a browser that reloads
            #: must not forget how long the batch has been running.
            "started_at": batch.started_at.isoformat(),
            "ready_at": batch.ready_at.isoformat(),
        }
        for batch in rows
    ]


async def _own_item(db: AsyncSession, body: Body, item_id: str) -> Item:
    """A thing in the hands. You repair and take apart your own, not what lies nearby."""
    from src.engine.world import body_container

    item = await db.get(Item, uuid.UUID(item_id))
    inventory = await body_container(db, body)
    if item is None or item.container_id != inventory.id:
        raise Refused("этой вещи у вас нет")
    return item


async def _identity(state: dict, db: AsyncSession) -> Identity:
    """The identity. It is controlled remotely -- also when the body is dead."""
    identity = await db.get(Identity, state["identity_id"])
    if identity is None:  # pragma: no cover
        raise Refused("личность исчезла")
    return identity


async def _node(db: AsyncSession, key: str) -> Node:
    """A node by stable key: orders are managed from anywhere."""
    node = (await db.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if node is None:
        raise Refused(f"нет узла {key!r}")
    return node


async def _active(state: dict, db: AsyncSession) -> MiningSession:
    session_id = state.get("session_id")
    if session_id is None:
        #: The client may have reconnected -- look for the body's open session.
        body = await _body(db, state["identity_id"])
        if body is None:
            raise Refused("нет живого тела")
        found = (
            await db.execute(
                select(MiningSession).where(
                    MiningSession.body_id == body.id,
                    MiningSession.state == SessionState.ACTIVE,
                )
            )
        ).scalars().first()
        if found is None:
            raise Refused("сессия не открыта")
        state["session_id"] = found.id
        return found

    session = await db.get(MiningSession, session_id)
    if session is None:  # pragma: no cover
        raise Refused("сессия исчезла")
    return session


def _optional_uuid(value: str | None) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(value)
