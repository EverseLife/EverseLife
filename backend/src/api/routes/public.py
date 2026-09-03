# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Public reads: catalogs and world state.

Nothing here changes the world, and never will. Prices, statistics and the
code are public on purpose: everyone knows the prices (D-047), and closing
the catalogs is pointless -- they lie in the vault anyway.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.constants import HOLDER, current, current_renames
from src.constants import current_catalog as catalog
from src.constants import registry as R
from src.db.base import session_factory
from src.engine import account, estate, market, places, sight, world
from src.engine import city as town
from src.engine import ship as vessels
from src.engine import travel as roads
from src.engine.errors import Refusal
from src.models.identity import Body, BodyState, Identity
from src.models.world import Layer, Node
from src.runtime import MARKET_BOOK_DEPTH, MARKET_BOOK_STEPS
from src.settings import settings

router = APIRouter(prefix="/public", tags=["reads"])

#: Where this code lives. Not a setting: a copy that moved elsewhere must
#: say so by editing this line, and a fork that says nothing keeps pointing
#: at the source it was actually taken from.
SOURCE_URL = "https://github.com/EverseLife/EverseLife"


@router.get("/constants")
async def constants() -> dict[str, Any]:
    """The current set of balance numbers and its fingerprint.

    The client computes the quality forecast and batch cost by the same
    numbers as the server -- otherwise the forecast before starting a batch
    (D-092) would diverge from the result.
    """
    snapshot = HOLDER.current()
    return {"digest": snapshot.digest, "values": snapshot.raw()}


@router.get("/i18n/{locale}")
async def words(locale: str) -> dict[str, Any]:
    """The words of one language, as the FTL the server itself renders (D-251).

    One file feeds both ends: the client parses this with `@fluent/bundle`, so
    a refusal it chooses to redraw from `code` and `args` comes out saying
    exactly what the server said. Which languages exist is here too -- the
    switcher must not guess.
    """
    asked = i18n.normalize(locale)
    return {
        "locale": asked,
        "locales": list(i18n.LOCALES),
        "ftl": i18n.current().source(asked),
    }


@router.get("/renames")
async def renames() -> dict[str, Any]:
    """The D-251 key tables and the name of every thing, in every language.

    The wire and the catalog speak ids; the words live here, because catalog
    constants belong in /public rather than in `look` (D-225). Every language
    is served at once and the client picks: the table is small, it changes
    only when the vault does, and a language switch that had to go back to the
    server for words would be a visible stutter for no reason.
    """
    table = current_renames()
    return {
        "names_ru": table.names_ru,
        "names_en": table.names_en,
    }


@router.get("/recipes")
async def recipes() -> dict[str, Any]:

    book = catalog().recipes
    return {
        "raw": list(book.raw),
        #: What is measured rather than counted (D-212): the client reads an
        #: amount of everything else as whole pieces and writes "шт." by it.
        "bulk": list(book.bulk),
        #: Liquids (D-230): they exist only inside a vessel, and the client
        #: reads which things those are the same way the engine does.
        "liquid": list(book.liquid),
        #: What to draw next to a quantity: "5 шт", "3 м" (display only).
        "units": book.units,
        "operations": [operation.model_dump(by_alias=True) for operation in book.operations],
        "recipes": [recipe.model_dump(by_alias=True) for recipe in book.recipes],
        #: Thing classes (D-215): class -> members. `tool_classes` is the
        #: tools-only view kept for older client code.
        "classes": {name: list(members) for name, members in book.classes.items()},
        "materials": [material.model_dump(by_alias=True) for material in book.materials],
        "tool_classes": {name: list(tools) for name, tools in book.tool_classes.items()},
        "synonyms": book.synonyms,
        "labor_hours": book.labor_hours,
    }


@router.get("/plants")
async def plants() -> dict[str, Any]:

    #: The feeding table stays out (D-293): what a fertilizer does in a stage
    #: is the Library's text, read on foot, not a catalog constant.
    return {"plants": [plant.model_dump(exclude={"feeding"}) for plant in catalog().plants.plants]}


def _passage(under_way: dict[str, Any] | None, by_id: dict[Any, str]) -> dict[str, str] | None:
    """A ship's passage for the map: the port it is due at and the two moments.

    The destination is given as a node key rather than a planet: the client
    climbs the parent hierarchy to whatever layer it is drawing, and a key
    keeps that one rule instead of adding a second.
    """
    if under_way is None:
        return None
    goal = by_id.get(under_way["to"])
    if goal is None:  # pragma: no cover -- the port is a node like any other
        return None
    return {
        "to": goal,
        "started_at": under_way["started_at"].isoformat(),
        "arrives_at": under_way["arrives_at"].isoformat(),
    }


async def _standing(db: AsyncSession, authorization: str | None) -> Node | None:
    """Where the asker's body stands, if the header names one at all.

    A bad, expired or revoked token is **not** an error here: this route answers
    the whole internet, and the answer to "who are you" being "nobody" is a
    perfectly good one -- it means the sky. Refusing would turn a stale tab into
    a broken map instead of a distant one.
    """
    scheme, _, raw = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        return None
    try:
        who = await account.by_token(db, raw.strip())
    except Refusal:
        return None
    body = (
        (
            await db.execute(
                select(Body)
                .join(Identity, Identity.id == Body.identity_id)
                .where(Identity.account_id == who.id, Body.state == BodyState.ALIVE)
            )
        )
        .scalars()
        .first()
    )
    return None if body is None else await db.get(Node, body.node_id)


@router.get("/map")
async def world_map(
    response: Response, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """The map as it looks from where the asker stands (D-240).

    **Not the world any more.** Two steps of the graph around the body, one step
    of the planet's surface, and the sky -- which is everybody's, because a
    planet's place is arithmetic over the epoch and hiding it would only make
    passages unplannable. The surfaces of other planets are simply absent, so
    there is nothing to expand: one reaches a planet by flying to it.

    The token is read from the ordinary `Authorization: Bearer` header and is
    **optional**: without one the answer is the sky alone. The route stays under
    `/public` because what it gives an anonymous reader is still public -- the
    system, its corridors and the hulls in it (D-097 in that part).
    """

    #: The answer stopped being everybody's the day it started depending on
    #: where the asker stands. Said out loud to whatever sits in front of this
    #: one day: a shared cache would hand one player another player's
    #: neighbourhood, and that is the one failure this route must not have.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"

    constants = current()
    async with session_factory()() as db:
        standing = await _standing(db, authorization)
        every, all_edges = await sight.read(db)
        #: A ship's rooms are **not** public (D-201). From outside a ship is one
        #: hull: how many cabins it has, what is joined to what and where the
        #: hold is, is exactly what somebody planning to board it would like to
        #: know -- and the whole point of the single connector is that nothing
        #: is seen past the gangway. The interior comes with `look`, to whoever
        #: is standing in it.
        inside = {
            node.id for node in every if vessels.is_aboard(node) and node.layer is not Layer.SPACE
        }
        #: The neighbourhood is walked over the **whole** graph, hulls included:
        #: standing aboard, the gangway is the step that reaches the pier, and a
        #: walk that could not cross it would show a crew nothing at all.
        seen = sight.around(standing, nodes=every, edges=all_edges)
        nodes = [node for node in every if node.id in seen and node.id not in inside]
        shown = {node.id for node in nodes}
        edges = [edge for edge in all_edges if edge.node_a_id in shown and edge.node_b_id in shown]
        by_id = {node.id: node.key for node in nodes}
        #: The city's two doors are shown on the map (D-206): every road beyond
        #: the walls starts at the gate, every ship couples to the spaceport, and
        #: a player who cannot see that reads the graph as an arbitrary tangle.
        ports = {node.id for node in await vessels.ports(db)}
        #: A ship under way has no edges at all (D-201), so the graph cannot say
        #: where it is. The passage does: from the port it left to the one it is
        #: due at, between two moments.
        under_way = await vessels.passages(db)
        return {
            "nodes": [
                {
                    "key": node.key,
                    "name": node.name,
                    #: Layers are a display abstraction: the world stays one
                    #: graph, and the parent hierarchy groups nodes by layer (D-045, D-097).
                    "layer": node.layer.value,
                    "parent": by_id.get(node.parent_id),
                    "exit": bool(node.properties.get(roads.EXIT)),
                    "port": node.id in ports,
                    #: The space layer paints by planet and lays nodes out by
                    #: orbit: there a place is a function of time, not of the
                    #: spring layout the other layers settle into.
                    "planet": node.planet.value,
                    #: Where the node stands, once and for everybody (D-237).
                    #: The client draws this and settles nothing itself: a
                    #: spring layout has no preferred orientation, so the same
                    #: city came out turned differently on every opening and
                    #: for every player. Empty on the space layer and for nodes
                    #: laid before the rule -- there the client falls back to
                    #: its own layout.
                    "place": places.wire(node),
                    "orbit": world.orbit_of(node),
                    "deferred": bool(node.properties.get(world.DEFERRED)),
                    #: A ship is a group of ordinary nodes (D-201), and only
                    #: this mark tells them from ground: the map draws a hull
                    #: rather than a place, and one does not walk to a hull
                    #: across the void -- one boards it by the gangway.
                    "aboard": vessels.is_aboard(node),
                    "flight": _passage(under_way.get(node.id), by_id),
                    #: Place signs ("лес", "камни"): the map draws the node's
                    #: type glyph by them (D-238). An allowlist on purpose --
                    #: this endpoint answers the whole internet, and `look`'s
                    #: broader everything-true derivation belongs to whoever
                    #: stands in the node.
                    "features": world.public_signs(node),
                    #: The owner's mark, if one is nailed on (D-238): the map
                    #: draws it in place of the type glyph. Belted to the
                    #: allowlist like the signs above.
                    "emblem": estate.public_emblem(node),
                }
                for node in nodes
            ],
            "edges": [
                {
                    "a": by_id[edge.node_a_id],
                    "b": by_id[edge.node_b_id],
                    "surface": edge.surface.value,
                    "seconds": round(roads.edge_seconds(constants, edge)),
                }
                for edge in edges
            ],
            #: The corridors of space. Not edges of the graph -- between planets
            #: there are none, and one does not walk there -- but what a passage
            #: costs: a calendar of the cheapest arc for each of the coming days
            #: (D-271), so the map says when the window opens. Keyed by planet,
            #: so the client ties a corridor to the bodies it already draws.
            "routes": await vessels.corridors(db, constants, at=datetime.now(UTC)),
        }


@router.get("/doors")
async def doors() -> dict[str, Any]:
    """Where a newcomer can print: city, residents, settlement grant (D-013, D-182).

    Read **before any identification**: choosing a door is the first thing a
    person does in the game, and they have no identity at that moment yet.
    """

    async with session_factory()() as db:
        return {"doors": await world.doors(db, current(), catalog())}


@router.get("/lines")
async def lines() -> dict[str, Any]:
    """Character lines and how many play each (D-015, D-104, D-187).

    Read at registration, before identification. Nymphs are in the list and
    marked unplayable: a promise, not a deceptive stub (10-world/03).
    """

    async with session_factory()() as db:
        return {"lines": await account.lines(db)}


@router.get("/market/{node_key}")
async def market_positions(node_key: str) -> dict[str, Any]:
    """What trades in the node at all: goods plus quality tier, and what it went for.

    Public and remote: everyone knows the prices (D-047). Buying from here is
    not possible and will not be -- buying requires legs.

    `prices` is the last deal per goods name, any tier: the picker lists names,
    and a name without a deal behind it carries no price at all (D-002).
    """
    async with session_factory()() as db:
        node = await _node(db, node_key)
        return {
            "node": node.key,
            "positions": [
                {"goods": goods, "tier": tier} for goods, tier in await market.positions(db, node)
            ],
            "prices": await market.last_prices(db, node),
        }


@router.get("/market/{node_key}/book")
async def market_book(
    node_key: str, goods: str, tier: str, step: int | None = None
) -> dict[str, Any]:
    """The book for one position: buy and sell orders with depth.

    `step` glues rows a step apart, in minor units of money; omitted, the
    server picks the finest step the depth can hold and says which in the
    answer. The ladder of steps to choose from is a constant and travels with
    the tiers (`/public/quality/tiers`), not with every read of every book.
    """
    if step is not None and step not in MARKET_BOOK_STEPS:
        raise _refused(400, key="cmd-step-not-on-ladder", step=step)
    async with session_factory()() as db:
        node = await _node(db, node_key)
        goods = catalog().recipes.resolve(goods)
        tier = current_renames().tiers.get(tier, tier)
        book = await market.book(
            db, current(), node, goods, tier, depth=MARKET_BOOK_DEPTH, step=step
        )
        payload = asdict(book)
        payload["node"] = node.key
        payload["spread"] = book.spread
        return payload


@router.get("/quality/tiers")
async def quality_tiers() -> dict[str, Any]:
    """The two rulers a book is read by: quality tiers (D-058) and price steps (D-239).

    In data the scale is continuous, on the market tiers trade: a continuous
    scale would make the order book unreadable. The price steps are the same
    kind of thing for the other axis -- the rungs a book's rows may be glued
    at -- and both are constants, read once, not with every book (D-225).
    """
    tiers = current()[R.QUALITY_TIERS]
    return {
        "tiers": [{"from": t.frm, "to": t.to, "name": t.name} for t in tiers],
        "steps": list(MARKET_BOOK_STEPS),
    }


@router.get("/founding")
async def founding() -> dict[str, Any]:
    """What a place must already have before a city can be founded on it (D-023, D-159).

    A role and the machines that fill it -- the same table for every player,
    every place and every language, changing only when the vault does. So it
    is read once from here rather than carried by every `look` (D-225); what
    `look` says about a particular node is which of these roles it lacks, and
    that is the only part that is not a constant.

    The role travels as a key, and the word for it is the world's own message
    (`city-role-<role>`), which the client already holds from `/public/i18n`:
    the door's refusal quotes that same message, so the window and the refusal
    cannot end up calling one thing by two names.
    """
    return {
        "roles": [
            {"role": role, "any_of": list(with_what)} for role, with_what in town.foundation_needs()
        ]
    }


def _refused(status_code: int, *, key: str, **params: Any) -> HTTPException:
    """A named refusal over plain HTTP (D-251).

    The detail carries the same three fields the socket's refusal does: the
    sentence for the player, the `code` for whoever acts on it, and the `args`
    the sentence was built from. Rendered in the default language -- a public
    read has no session behind it to have chosen one -- and a client reading
    in another redraws from `code` and `args` with its own bundle, exactly as
    it does over the socket.
    """
    return HTTPException(
        status_code=status_code,
        detail={
            "refused": i18n.render(key, params, locale=i18n.DEFAULT_LOCALE),
            "code": key,
            **({"args": params} if params else {}),
        },
    )


async def _node(db, key: str) -> Node:
    node = (await db.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if node is None:
        raise _refused(404, key="cmd-no-such-node", node=key)
    return node


@router.get("/laws")
async def laws() -> dict[str, Any]:
    """Charter, code-laws and sanctions with defaults.

    A new city works on defaults, filling in nothing (D-130).
    """

    book = catalog().laws
    return {
        "charter": [question.model_dump() for question in book.charter],
        "code_laws": [law.model_dump() for law in book.code_laws],
        "sanctions": [sanction.model_dump() for sanction in book.sanctions],
        "charter_defaults": book.charter_defaults(),
        "code_law_defaults": book.code_law_defaults(),
    }


@router.get("/source")
async def source() -> dict[str, str | None]:
    """Where the source of this running version lives (AGPL §13).

    The link is also in the client's header, for a player who is reading a
    screen rather than a JSON body. This one is for everybody else: a mirror,
    a bot, somebody's copy of the world -- and for anyone checking that a
    server they are playing on actually offers what the licence requires.
    """

    return {
        "license": "AGPL-3.0-only",
        "source": SOURCE_URL,
        "revision": settings().release or None,
    }
