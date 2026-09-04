# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The working session: open a face, dig, shore, leave (D-143).

The commands of the "Roof" mechanic, in the order a shift lives them. What
they show the player is a `Sight`; the number behind the sign never leaves
`_base`. The bad ending is next door in `collapse` -- a swing only calls it.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.constants.catalog import CATALOG_HOLDER
from src.engine import bank, customs, events, frost, justice, occupation, travel
from src.engine import city as town
from src.engine import world as world_engine
from src.engine.mining._base import (
    TIMBER,
    MiningError,
    NoStrength,
    NotHere,
    NoTimber,
    NoTool,
    RoofHolds,
    SessionClosed,
    Sight,
    VeinDepleted,
    VeinLiquid,
    _relock,
    _require_active,
    _sight,
    _tool,
    _wear_tool_for_session,
    active,
    crowd_factor,
    deplete,
    pace_factor,
    remember_roof,
    roof_of,
    session_container,
    swing_cost,
    swing_hours,
)
from src.engine.mining.collapse import collapse
from src.engine.world import body_container, node_container
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.mining import MiningSession, Pace, SessionState
from src.models.world import Node, Vein
from src.units import SCALE_MAX, SCALE_MIN, amount, amount_float


async def start(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    vein: Vein,
    *,
    catalog=None,
    tool_item_id: uuid.UUID | None = None,
    pace: Pace = Pace.STEADY,
) -> MiningSession:
    """Open a session. The device fee is checked before the call (`engine.pow`)."""
    if body.node_id != vein.node_id:
        raise NotHere(key="mining-vein-not-here")
    if body.state is not BodyState.ALIVE:
        raise SessionClosed(key="mining-dead-works")
    await travel.require_here(session, body)
    if vein.remaining <= 0:
        raise VeinDepleted(key="mining-vein-depleted", vein=str(vein.id))
    #: A liquid vein is not worked by hand (D-252): oil is pumped by the rig,
    #: and the pick has nothing to grip. Checked at the door, not per swing --
    #: a session that could never yield must never open. Without a catalog
    #: (bare test worlds) there is nothing to read liquidity from.
    book = catalog.recipes if catalog is not None else None
    if book is None and CATALOG_HOLDER.is_loaded():
        book = CATALOG_HOLDER.current().recipes
    if book is not None and book.is_liquid(vein.resource):
        raise VeinLiquid(key="mining-vein-liquid", goods=vein.resource)
    #: The penal face is only for those the prison holds (D-174, D-176): its
    #: vein is neither visible nor given to an outsider.

    node = await session.get(Node, body.node_id)
    if (
        node is not None
        and await justice.is_prison(session, node)
        and not await justice.held(session, constants, body.identity_id)
    ):
        raise SessionClosed(key="mining-penal-face")
    #: A session is not opened by a body that cannot swing even once.
    chill = await frost.drain_multiplier(session, constants, body)
    first_hit = swing_cost(constants, body, pace, datetime.now(UTC), chill=chill)
    if float(body.stamina) < first_hit:
        raise NoStrength(key="mining-no-strength", need=first_hit, have=float(body.stamina))

    if await active(session, body) is not None:
        raise SessionClosed(key="mining-session-open")
    #: A face is an occupation like any other: it is not opened by a body that
    #: is already searching the land or ploughing a plot (D-211).

    await occupation.require_free(session, body, besides=frozenset({occupation.MINE}))

    #: The tool the vault requires (`Добыча requires: [Кирка, Жила]`) is now
    #: checked (D-215): before that the engine let anyone mine bare-handed.
    tool = await _required_tool(session, catalog, body, vein, tool_item_id)
    tool_item_id = tool.id if tool is not None else tool_item_id

    #: The roof belongs to the working, not to the session (D-188): rock does
    #: not knit back together while the miner is away, and it is the vein that
    #: remembers -- an untouched one starts from its richness, a shaken one
    #: meets the next miner as it was left. The session carries no copy of it
    #: at all, which is why opening a face reads nothing here: a copy taken now
    #: would be a second answer to a question the vein already answers, and it
    #: was that second answer that let two bodies at one face overwrite each
    #: other's sag.
    mining = MiningSession(
        body_id=body.id,
        vein_id=vein.id,
        pace=pace,
        tool_item_id=tool_item_id,
    )
    session.add(mining)
    await session.flush()
    await session_container(session, mining)

    await events.record(
        session,
        EventKind.MINING_STARTED,
        actor_identity_id=body.identity_id,
        node_id=vein.node_id,
        session_id=str(mining.id),
        vein_id=str(vein.id),
        resource=vein.resource,
        pace=pace.value,
    )
    return mining


async def swing(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    *,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> Sight:
    """Dig: raw material, wear, roof sag.

    A collapse at stability <= 0 costs **everything mined during the session**
    -- that is the stake, growing as things go.
    """
    noise = rng or random.Random()
    moment = now or datetime.now(UTC)
    body, vein = await _require_active(session, mining)
    #: The body is locked **first**, before its stamina is read: two sockets of
    #: one identity swinging at once would otherwise both read the same reserve,
    #: both find it enough and both write their own remainder -- one swing paid
    #: for two. Stamina is on the same list as money and remainders, and the
    #: order is the one this package keeps everywhere: body -> rig -> vein.
    #:
    #: Flushed before the reread, or the reread would undo it: `refresh` takes
    #: the row as the database has it, and anything set on the body in this
    #: transaction and not yet written -- a meal eaten a line earlier -- would
    #: quietly go back to what it was.
    await session.flush()
    await session.refresh(body, with_for_update=True)
    #: And the face is reread under that same lock, before anything is written.
    #: Two sockets of one identity can send the **last** swing in one moment:
    #: both read the face ACTIVE with the same roof, and each would take that
    #: roof to nought from its own stale copy -- two cave-ins for one swing, and
    #: since D-294 the second of them kills a body that lived through the first.
    #: The reread needs no lock of its own: every swing of this face is a swing
    #: of this body, so the body's row is where the two queue, and the loser
    #: reads what the winner committed -- `_require_active` then tells it the
    #: face is closed. Why the reread takes no lock of its own is written there.
    body, vein = await _require_active(session, mining, fresh=True)

    #: A swing costs stamina, and a body at zero does not swing: the vein is
    #: not mined by free willpower. The check comes before all effects so that a
    #: refusal changes nothing in the world.
    chill = await frost.drain_multiplier(session, constants, body)
    hit_price = swing_cost(constants, body, mining.pace, moment, chill=chill)
    if float(body.stamina) < hit_price:
        raise NoStrength(key="mining-no-strength", need=hit_price, have=float(body.stamina))

    #: The vein is shared by every miner and by the rigs: locked and reread
    #: before its remainder is spent, or two swings mine the same ore twice.
    #: Lock order everywhere: body -> rig -> vein.
    await session.refresh(vein, with_for_update=True)
    #: And the face itself, held for the rest of the transaction, because that
    #: lock is a place to **wait**. The eruption takes the veins of a shaken
    #: node before the sessions at them (`plates.clock`) and then closes those
    #: faces through `leave`, so a swing queued at the vein can wake up behind
    #: a job that has already carried this haul out and moved the rock to the
    #: next node: going on from there would lay the ore into the container of a
    #: closed session -- where nothing reaches it again, `leave` refusing by
    #: state -- and write the roof onto a vein that is no longer at this face.
    #: The rig queues at the same lock (`engine.rig`), so the remainder can be
    #: gone as well, and a swing that mines nothing does not lose quietly:
    #: `item.amount_positive` stops the insert and the socket gets an internal
    #: error where the vault keeps a word for a worked-out vein (pillar P2).
    #: Why the row is taken rather than read, and only here, is in `_relock`.
    body, vein = await _relock(session, mining)

    #: Everything the yield is made of is read **below** the locks, off the
    #: rows they hold. Read above them, a swing that stood in the queue behind
    #: a rig crossing a depletion tier would price its ore by the richness the
    #: vein had before the wait and then stamp that ore with the richness it
    #: has after (`quality`, below): one swing, two answers to what this rock
    #: is. The stamina check stays above, so that a refusal still changes
    #: nothing in the world and takes no lock to say so.
    factor = pace_factor(constants, mining.pace)
    crowd = await crowd_factor(constants, session, vein)
    #: An hour of mining gives `mining.iron_per_hour` on a vein of ordinary richness.
    per_swing = (
        constants[R.MINING_IRON_PER_HOUR]
        * swing_hours(constants)
        * float(vein.richness)
        / constants[R.MINING_RICH_THRESHOLD]
        * factor
        * crowd
    )
    mined = min(amount(per_swing), vein.remaining)

    extracted_before = vein.extracted
    vein.remaining -= mined
    vein.extracted += mined
    deplete(constants, vein, moment, extracted_before)

    #: Raw material quality is determined by the vein (15-quality).
    container = await session_container(session, mining)
    quality = min(SCALE_MAX, max(SCALE_MIN, float(vein.richness)))
    swung = Item(
        container_id=container.id,
        type_key=vein.resource,
        amount=mined,
        quality=Decimal(str(quality)),
    )
    session.add(swung)
    #: Swings at one face give one and the same ore until the vein grows poorer,
    #: so they are one heap and not a row of identical lines (D-214).
    await world_engine.stack_up(session, swung)

    #: Satiety slows the spend, the cold speeds it up (D-119, D-231): the price
    #: written off is the one named above, down to the last multiplier.
    body.stamina = Decimal(str(max(SCALE_MIN, float(body.stamina) - hit_price)))

    mining.swings += 1
    #: The working remembers every swing (D-188): the sag stays after the miner
    #: leaves, so "leave and re-enter" no longer resets the risk. Read off the
    #: vein this swing holds and not off anything this session remembers, so
    #: that a neighbour's swings are already in it: the roof is shared by
    #: everyone digging the vein, and one who shakes it makes it dangerous for
    #: the rest (D-099).
    sagged = roof_of(constants, vein) - constants[R.MINE_ROOF_PER_SWING] * factor
    roof = remember_roof(vein, sagged)
    await session.flush()

    #: A swing is told, not journaled (D-227): the journal is evidence and
    #: metrics, and a swing is neither -- the session's end (`mining.left`,
    #: `mining.collapsed`) carries the totals. A thousand swings an hour were
    #: a thousand rows and a thousand notifications.
    await events.announce(
        session,
        touches=("mining", "inventory"),
        identity_id=body.identity_id,
        event="mining.swing",
        mined=amount_float(mined),
        quality=quality,
    )

    if roof <= SCALE_MIN:
        return await collapse(session, constants, mining, body, vein, roof, noise, moment)

    return await _sight(session, constants, mining, body, roof)


async def timber(session: AsyncSession, constants: Constants, mining: MiningSession) -> Sight:
    """Set a support: spends timber and a turn, restores stability up to the ceiling.

    Whether shoring pays depends on the price of support against the price of
    raw material, and that floats with the market. There is no memorised
    sequence because the optimum moves (D-143).

    Three rows are taken, in the order this package keeps everywhere -- the
    vein, the face, then the timber in the pocket. A support is a write to the
    roof, and a timber is a remainder, so both are on the list that may only
    be changed under a lock (CLAUDE.md). The vein goes **before** the face's
    row for the reason the whole package does: a swing holds the vein and then
    takes the session, and the reverse order here would cross it.

    The roof is read off that locked vein, so a support is set on the working
    as the last swing left it -- a neighbour's swing included (D-188, D-099).
    A working already standing at or above `mine.roof_timber_cap` refuses the
    support instead of spending it (D-300): see `RoofHolds` for why setting
    one there used to make the face worse, and the comment at the check for
    why it is asked after the pocket rather than before it.
    """
    vein = await session.get(Vein, mining.vein_id)
    if vein is None:  # pragma: no cover -- a session without a vein is a bug
        raise MiningError(key="mining-session-dangling")
    #: Flushed before the lock, or `refresh` would undo whatever this
    #: transaction has set on the vein and not yet written (see `swing`).
    await session.flush()
    await session.refresh(vein, with_for_update=True)
    body, _ = await _relock(session, mining)

    inventory = await body_container(session, body)
    #: Every stack of it, taken under the lock and reread there: two sockets
    #: both reading a stack of two and both writing one back spend one timber
    #: for two supports. **All** of them, and not the first with a LIMIT:
    #: Postgres applies the limit before the wait, so a stack deleted by the
    #: winner of the row leaves the loser holding an empty answer rather than
    #: the next stack, and the pocket is called empty with timber in it. Two
    #: stacks of one support are ordinary -- quality keeps them apart, and
    #: `stack_up` never folds those together.
    stacks = (
        (
            await session.execute(
                select(Item)
                .where(
                    Item.container_id == inventory.id,
                    Item.type_key.in_(world_engine.station_names(TIMBER)),
                )
                .order_by(Item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    stock = next((one for one in stacks if one.container_id == inventory.id), None)
    if stock is None:
        raise NoTimber(key="mining-no-timber")

    #: **After** the pocket, and that order is the point. A support that cannot
    #: raise this working is not a support, and the timber is better kept than
    #: spent on making the roof worse -- but the refusal also answers, for
    #: free and exactly, whether the roof is at or above a public number, and
    #: the roof is the one thing the player is never told (D-143). Asked
    #: first, that answer would cost nothing at all: a body with an empty
    #: pocket could press the button after every swing and read the hidden
    #: number off the moment the refusal changed, closer than the sign's lie
    #: ever comes. Behind `NoTimber` it costs at least carrying a timber,
    #: which is the price OQ-123 names for what a support already gives away.
    #: The order is part of the decision, not an implementation detail (D-300).
    cap = constants[R.MINE_ROOF_TIMBER_CAP]
    standing = roof_of(constants, vein)
    if standing >= cap:
        raise RoofHolds(key="mining-roof-holds")

    one = amount(1)
    if stock.amount > one:
        stock.amount -= one
    else:
        await session.delete(stock)

    raised = min(cap, standing + constants[R.MINE_ROOF_PER_TIMBER])
    mining.timbers += 1
    #: A support stands after the shift ends (D-188): that is what makes timber
    #: an investment in the working rather than a consumable of one visit. And
    #: it props the working for everyone at it, since it is the same roof --
    #: the artel answers for the face together (D-099).
    raised = remember_roof(vein, raised)
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_TIMBERED,
        actor_identity_id=body.identity_id,
        session_id=str(mining.id),
        timbers=mining.timbers,
    )
    return await _sight(session, constants, mining, body, raised)


async def set_pace(
    session: AsyncSession, constants: Constants, mining: MiningSession, pace: Pace
) -> Sight:
    body, vein = await _require_active(session, mining)
    mining.pace = pace
    await session.flush()
    return await _sight(session, constants, mining, body, roof_of(constants, vein))


async def leave(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    *,
    now: datetime | None = None,
) -> float:
    """Leave: what was mined moves to the inventory. Returns the mined volume.

    Allowed at a **worked-out** vein: see `_require_active`. Walking away is
    the one thing a face never refuses.
    """
    moment = now or datetime.now(UTC)
    #: The session row first, like every closer of a face (`abandon`,
    #: `plates._close_faces`): the row is the gate, and a leaver who reached
    #: the pocket's rows before it would hold them against a death or an
    #: eruption closing this same face the other way round. Reread after the
    #: lock: taken second, the face may already be closed, and
    #: `_require_active` turns that into the refusal.
    await session.execute(
        select(MiningSession)
        .where(MiningSession.id == mining.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    body, vein = await _require_active(session, mining, working=False)

    #: Prison labour (D-174): in a prison node the insolvent's yield goes to
    #: the city, and the debt is repaid by the treasury at the reference price.
    #: Treasury empty or no price -- the ore stays with the prisoner, who sells it themselves.
    haul = await _prison_workoff(session, constants, mining, body, now=moment)
    if haul is None:
        haul = await _carry_out(session, mining, body)
    await _wear_tool_for_session(session, constants, await _tool(session, mining), extra=0.0)

    mining.state = SessionState.LEFT
    mining.ended_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_LEFT,
        actor_identity_id=body.identity_id,
        node_id=vein.node_id,
        session_id=str(mining.id),
        haul=haul,
        swings=mining.swings,
        timbers=mining.timbers,
    )
    return haul


async def abandon(session: AsyncSession, body: Body, *, now: datetime | None = None) -> float:
    """The miner is gone for good: the face closes and the haul stays at it.

    Called where a body stops being able to come back -- today that is death.
    The ore was already out of the rock and lying at the face, and the face is
    a place in the node, so it stays in the node: the same rule that keeps what
    a dead body carried where the body fell (D-011). It is not salvaged and not
    rolled for, because it was never on the body to be damaged.

    Left ACTIVE instead, the session would hold its haul in a container nobody
    can ever open again -- a leak of matter with no decision behind it, and one
    that also blocks `leave` for whoever inherits the face.

    **The caller takes no row another closer of this face wants before this
    call.** The session row is the gate every closer of a face agrees on --
    `death.die` opens with this call, `plates._close_faces` takes the same rows
    FOR UPDATE before touching anything else, and `leave` starts by locking its
    own row. The winner of the gate plays the whole story out -- haul, heaps,
    state -- while the loser waits at it holding nothing the winner could want.
    A caller that took a contended row first -- the dying body's pocket, say --
    would hold it against the eruption carrying a haul into that same pocket,
    and one of the two would be killed as a deadlock (ABBA). Two rows a caller
    **may** hold, because no closer ever waits on them past its gate: the body
    (every death holds it FOR UPDATE, no closer locks another's body), and the
    vein (`collapse` arrives holding it, and the eruption serializes with a
    swing on that very lock before either reaches a session). Past the gate
    the inside order is the shared one: the session row, the face's things,
    then the node's heaps through `stack_up`.
    """
    moment = now or datetime.now(UTC)
    #: The session row is **taken for the transaction**, and its state reread
    #: after the lock. Dying and leaving the same face are two writes to one
    #: haul: the ground moved under the miner in the same second a rift took
    #: them, and both `_close_faces` and this would carry the ore out -- one
    #: into a pocket about to burn, one into the node. Whichever gets the row
    #: first finishes; the second finds the session closed and does nothing.
    open_faces = (
        (
            await session.execute(
                select(MiningSession)
                .where(
                    MiningSession.body_id == body.id,
                    MiningSession.state == SessionState.ACTIVE,
                )
                .order_by(MiningSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    left = 0.0
    for face in open_faces:
        if face.state is not SessionState.ACTIVE:  # pragma: no cover -- closed under the lock
            continue
        vein = await session.get(Vein, face.vein_id)
        where = await session.get(Node, vein.node_id) if vein is not None else None
        container = await session_container(session, face)
        #: And the haul under the same lock: a stack merged out from under us by
        #: `stack_up` in another transaction would make this UPDATE hit nothing.
        things = (
            (
                await session.execute(
                    select(Item)
                    .where(Item.container_id == container.id)
                    .order_by(Item.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        yard = await node_container(session, where) if where is not None else None
        for thing in things:
            if thing.container_id != container.id:  # pragma: no cover -- carried out first
                continue
            left += amount_float(thing.amount)
            if yard is None:  # pragma: no cover -- a face always has its node
                await session.delete(thing)
                continue
            thing.container_id = yard.id
            thing.installed = False
            #: Two heaps of the same ore lying in the same place are one heap (D-214).
            await world_engine.stack_up(session, thing)
        face.state = SessionState.LEFT
        face.ended_at = moment
    await session.flush()
    return left


async def sight(session: AsyncSession, constants: Constants, mining: MiningSession) -> Sight:
    """Look at the face. Asking again is pointless: the sign does not change.

    Pointless while the rock stands still, that is. The roof is read off the
    vein, so a neighbour who swings between two looks does move the sign --
    and its lie is redrawn with it, which is what keeps asking twice from
    paying (`_noise_of`). The vein is **read**, not locked: a look does not
    write, and it does not queue behind those who do (CLAUDE.md).
    """
    body = await session.get(Body, mining.body_id)
    if body is None:  # pragma: no cover
        raise MiningError(key="mining-session-without-body")
    vein = await session.get(Vein, mining.vein_id)
    if vein is None:  # pragma: no cover -- a session without a vein is a bug
        raise MiningError(key="mining-session-dangling")
    return await _sight(session, constants, mining, body, roof_of(constants, vein))


# --- internal ----------------------------------------------------------------


async def _required_tool(
    session: AsyncSession,
    catalog,
    body: Body,
    vein: Vein,
    tool_item_id: uuid.UUID | None,
) -> Item | None:
    """The tool the vault requires for mining this resource, from the hands.

    Requirements come from the extraction operation that gives the vein's
    resource -- a new operation with its own tool class needs no engine
    change. The named tool must fit; with none named, the best fitting one is
    taken, the same rule as at a workbench. Without a catalog (bare test
    worlds) nothing is required -- there is nothing to read the rule from.
    """
    if catalog is None:
        if not CATALOG_HOLDER.is_loaded():  # pragma: no cover -- test worlds
            return None
        catalog = CATALOG_HOLDER.current()

    book = catalog.recipes
    requirements: list[str] = []
    for operation in book.operations:
        if vein.resource in operation.gives and not operation.consumes:
            requirements = [
                book.resolve(req)
                for req in operation.requires
                if not book.is_raw(book.resolve(req))
            ]
            break
    if not requirements:
        return None

    inventory = await body_container(session, body)
    requirement = requirements[0]
    names = book.of_class(requirement) or (requirement,)
    if tool_item_id is not None:
        chosen = await session.get(Item, tool_item_id)
        if chosen is not None and chosen.container_id == inventory.id and chosen.type_key in names:
            return chosen
    found = (
        await session.execute(
            select(Item)
            .where(Item.container_id == inventory.id, Item.type_key.in_(names))
            .order_by(Item.quality.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if found is None:
        raise NoTool(key="mining-no-tool", tool_class=requirement, names=", ".join(names))
    return found


async def _carry_out(session: AsyncSession, mining: MiningSession, body: Body) -> float:
    container = await session_container(session, mining)
    inventory = await body_container(session, body)
    items = (
        (await session.execute(select(Item).where(Item.container_id == container.id)))
        .scalars()
        .all()
    )

    haul = 0.0
    for item in items:
        haul += amount_float(item.amount)
        item.container_id = inventory.id
        #: The haul joins what is already in the hands (D-214). The tally is
        #: taken before the fold: it is about what was carried out of the face,
        #: not about how big the stack in the hands became.
        await world_engine.stack_up(session, item)
    await session.flush()
    return haul


async def _prison_workoff(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    body: Body,
    *,
    now: datetime,
) -> float | None:
    """Give the yield to the city and credit the debt, if this is a prison (D-174).

    Returns the mined volume if the labour counted, and None if this is an
    ordinary face or there is nothing to credit with: then the yield goes to
    the prisoner in the usual way -- the vault forbids traps without exit (D-063).
    """

    node = await session.get(Node, body.node_id)
    if node is None or not await justice.is_prison(session, node):
        return None
    if await bank.restrained(session, constants, body.identity_id, now=now) is None:
        return None
    city = await town.of_node(session, node)
    if city is None:
        return None

    container = await session_container(session, mining)
    items = (
        (await session.execute(select(Item).where(Item.container_id == container.id)))
        .scalars()
        .all()
    )
    if not items:
        return 0.0

    #: The reference price is the median of real deals: it cannot be set by
    #: collusion. No price -- no credit: first the market, then the penal colony (D-174).
    cost = 0
    for item in items:
        price = await customs.reference_price(session, constants, city, item.type_key, now=now)
        if price is None:
            return None
        cost += int(price * amount_float(item.amount))
    if cost <= 0:
        return None

    credited = await bank.prison_credit(session, constants, city, body.identity_id, cost, now=now)
    if credited <= 0:
        #: The treasury is empty -- the ore stays with the prisoner: a prison is
        #: the city's investment, and an insolvent city earns nothing from penal labour.

        return None

    yard = await node_container(session, node)
    haul = 0.0
    for item in items:
        haul += amount_float(item.amount)
        item.container_id = yard.id
        item.installed = False
        await world_engine.stack_up(session, item)
    await session.flush()
    return haul
