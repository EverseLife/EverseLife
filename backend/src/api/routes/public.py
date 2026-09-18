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

from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.api import cached
from src.constants import HOLDER, current, current_renames
from src.constants import current_catalog as catalog
from src.constants import registry as R
from src.db.base import session_factory
from src.engine import account, mapshot, market, rasters, terrain, world
from src.engine import city as town
from src.engine.errors import Refusal
from src.models.identity import Body, BodyState, Identity
from src.models.world import Node, Planet
from src.runtime import MARKET_BOOK_DEPTH, MARKET_BOOK_STEPS, PUBLIC_MAP_MAX_AGE_S, TILE_MAX_AGE_S
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
        #: Vent gases (D-340): the client offers to let one out or burn it,
        #: and it knows which things those are by the vault's flag, as the engine does.
        "vent": list(book.vent),
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

    #: The feeding table stays out (D-296): what a fertilizer does in a stage
    #: is the Library's text, read on foot, not a catalog constant.
    #: The written paragraph stays out for the same reason (D-311): it is
    #: the opening of that same Library article, and it is said in the
    #: reader's language by key -- raw Russian in an open catalogue would
    #: be neither the text nor a translation of it.
    return {
        "plants": [
            plant.model_dump(exclude={"feeding", "care_note"}) for plant in catalog().plants.plants
        ]
    }


async def _standing(db: AsyncSession, authorization: str | None) -> Body | None:
    """The asker's body, if the header names one at all -- it says where one
    stands and whose memory the map is.

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
    return body


@router.get("/map")
async def world_map(
    response: Response, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """The map as it looks from where the asker stands (D-240, D-319).

    With a token: the asker's own map -- what their body sees within
    `map.sight_km`, what their identity remembers, the cities of the planet,
    and the sky (`mapshot.personal`). Without one: the sky as it is and the
    public surface as it **was** `map.public_delay_days` ago -- the daily
    snapshot (`mapshot.anonymous`). The route stays under `/public` because
    what it gives an anonymous reader is public (D-097).

    The token is read from the ordinary `Authorization: Bearer` header and is
    **optional**; a bad or stale one names nobody and gets the public map,
    not an error -- a stale tab is a distant map, not a broken one.
    """
    constants = current()
    now = datetime.now(UTC)
    response.headers["Vary"] = "Authorization"
    async with session_factory()() as db:
        asker = await _standing(db, authorization)
        if asker is None:
            #: Everybody's answer, and the heaviest: every anonymous reader
            #: may share it for a while, and name it by the snapshot served.
            answer, old = await mapshot.anonymous(db, constants, now)
            response.headers["Cache-Control"] = f"public, max-age={PUBLIC_MAP_MAX_AGE_S}"
            if old is not None:
                response.headers["ETag"] = f'"{old.id}"'
            return answer
        #: The answer depends on where the asker stands and what they
        #: remember: a shared cache would hand one player another player's
        #: map, and that is the one failure this route must not have.
        response.headers["Cache-Control"] = "private, no-store"
        return await mapshot.personal(db, constants, catalog(), asker, now)


@router.get("/terrain/{planet}")
async def terrain_of(planet: str, request: Request) -> Response:
    """A planet's relief: the height grid, the water lines, the rivers (D-319).

    Everybody's from the world's first day, and the same for everybody: the
    shape of a planet is arithmetic over the vault, not intelligence, and it is
    what a farmer walks a river by. No session: nothing here is anybody's.

    Sent squeezed once and named by its bytes, and asked about at every
    visit (`api.cached`): it names the version the rasters are asked for by,
    so the two are always one set.
    """
    try:
        which = Planet(planet)
    except ValueError as wrong:
        raise Refusal(key="cmd-no-such-planet", planet=planet) from wrong
    return cached.answer(
        request, cached.packed(cached.sketch_bytes(current(), which)), "application/json"
    )


@router.get("/terrain/{planet}/raster/{kind}")
async def terrain_raster(
    planet: str,
    kind: str,
    request: Request,
    nside: int | None = None,
    v: str | None = None,
) -> Response:
    """A raster of a planet's picture (landscape plan wave 5): the height,
    the biome or the landform, as the sketch's `raster` passport describes
    them. Bytes, not JSON: a texture the shader reads whole.

    At the picture's own fineness, or at the preview's when `nside` names it
    (the passport's `preview_nside`, 2026-09-18): the client draws the real
    planet from that sixteenth of the bytes first. Any other fineness is not
    kept, and is not cut for whoever asks.

    Asked for by the picture's version (`v`, the passport's `version`), it
    is kept by the browser a year; asked for without it, or by one that is
    no longer the picture's, it is the current bytes and asked about again
    at every use -- never an old picture kept under a new name. What is left
    is a page that read its passport before a deploy and asks for the
    rasters after it: the new bytes under the old passport, for that page
    alone and the seconds of the deploy; its next visit reads both anew.

    Before the tile route on purpose: `raster` is not a row number.
    """
    try:
        which = Planet(planet)
    except ValueError as wrong:
        raise Refusal(key="cmd-no-such-planet", planet=planet) from wrong
    constants = current()
    got = rasters.raster_bytes(constants, which, kind, nside)
    if got is None:
        raise HTTPException(status_code=404, detail="no such raster")
    lasting = v is not None and v == cached.version(constants, which)
    return cached.answer(request, cached.packed(got), "application/octet-stream", lasting)


@router.get("/terrain/{planet}/{row}/{col}")
async def terrain_tile(planet: str, row: int, col: int) -> Response:
    """A tile of a planet's local relief (D-323): the heights a close frame
    draws, the same the field reads under a scout's feet.

    A constant of the vault like the sketch, cut into tiles because a
    planet's worth of them is millions of numbers and a frame needs a few.
    """
    try:
        which = Planet(planet)
    except ValueError as wrong:
        raise Refusal(key="cmd-no-such-planet", planet=planet) from wrong
    got = terrain.tile_json(current(), which, row, col)
    if got is None:
        raise HTTPException(status_code=404, detail="no such tile")
    return Response(
        content=got,
        media_type="application/json",
        headers={
            "Cache-Control": f"public, max-age={TILE_MAX_AGE_S}",
            "ETag": f'"{HOLDER.current().digest}"',
        },
    )


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
