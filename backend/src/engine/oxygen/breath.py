# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The breathing itself: the step out that demands air, the suit that does not
come off where it is the only breath, the body's hours settled from what it
carries, the hull's hours breathed off the life support's line -- and the
deaths when either runs dry.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, gear, stock, travel
from src.engine import ship as vessels
from src.engine.oxygen import garden
from src.engine.oxygen._base import (
    _EPS,
    ASPHYXIA,
    SUIT,
    Breath,
    NoAir,
    airless_planets,
    free_air,
    sealed,
    without_air,
)
from src.engine.oxygen.supply import (
    breathable_stacks,
    carried,
    cylinders,
    hull_draw,
    is_suit,
    off_line,
    reserve,
    suited,
)
from src.engine.ship import lines
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.ship import Ship
from src.models.world import Node
from src.units import (
    ROUND_AMOUNT,
    ROUND_REMAINDER,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    on_grid,
)

# --- the step out --------------------------------------------------------------


async def require_air(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    target: Node,
    *,
    seconds: float = 0.0,
) -> None:
    """Refuse a step into a place there is nothing to breathe with (D-233).

    Asked **before** the walk, never at the far end: death by ignorance in one
    click is not this world's way, and a body that set out is a body that will
    arrive. What is checked is the destination's air, then the hull's tanks if
    the destination is a hull, then what the body itself carries.

    And carried **enough for the road**: a drop in the bottom of a cylinder is
    not a licence for a six-hour crossing of the black fields, and letting one
    be would be the very death the refusal exists to prevent -- one click later
    than the click, but no more foreseen.
    """
    if body.state is not BodyState.ALIVE:  # pragma: no cover -- the dead do not walk
        return
    if await free_air(session, target):
        return
    ship = await vessels.of_node(session, target)
    if ship is not None and await reserve(session, constants, catalog, ship) > _EPS:
        return
    #: The body's row before the suit is asked for (D-343): taking the suit off
    #: asks where the body is under the same lock (`require_suit_kept`). Without
    #: it a step that saw the suit and an undressing that saw the body still
    #: aboard both pass, and a bare body walks out onto the rock. A command has
    #: taken it already (`_alive`); a door does not lean on its caller for that.
    await _lock(session, body)
    if not await suited(session, catalog, body):
        raise NoAir(key="oxygen-no-suit", node=target.name, suit=SUIT)
    have = await carried(session, body)
    need = seconds / SECONDS_PER_HOUR * constants[R.OXYGEN_BODY_DRAW]
    if have <= _EPS:
        raise NoAir(key="oxygen-tanks-empty", node=target.name)
    if have + _EPS < need:
        raise NoAir(key="oxygen-not-enough", node=target.name, need=need, have=have)


async def require_suit_kept(
    session: AsyncSession,
    catalog: Catalog,
    body: Body,
    slot: str,
    *,
    putting_on: Item | None = None,
) -> None:
    """Refuse the act that leaves a body bare where only its suit breathes for it (D-343).

    Taking the suit off, or putting on in its slot something that pushes it
    off, is the one way to lose the body's connection to its air (D-234) that
    no reading counts down: the suit is there, and then it is not. So it is a
    door, like the step out, and it refuses before the act -- the tick would
    otherwise find a bare body a minute later and kill it a minute after that.
    The air itself is not guarded here: it is a quantity, the bar counts it
    down, and a refusal at nought would be dodged by a thousandth left behind.

    A suit for a suit keeps the connection and passes. Aboard is the hull's
    air and needs no suit. On the road both ends are asked, and the refusal
    names the road: the road is outside, and a body's node changes only when
    it arrives -- stepping off a hull, the body still stands aboard.

    **The body's row first, whatever the answer**, and what is worn read
    under it. Every change of dress comes through here, so dressing
    serialises with dressing and with the step (`require_air` takes the same
    row before it asks for the suit). A command holds it already (`_alive`),
    and the door does not lean on that. A door that decided on a reading
    taken before the lock -- or let a change it did not mind through without
    one -- could see a coat in the slot, wait, and then take off the suit a
    second tab had put on meanwhile.
    """
    locked = await _lock(session, body)
    if putting_on is not None and is_suit(catalog, putting_on.type_key):
        return
    worn = await gear.equipped(session, locked)
    leaving = worn.get(slot)
    if leaving is None or not is_suit(catalog, leaving.type_key):
        return
    if any(is_suit(catalog, thing.type_key) for held, thing in worn.items() if held != slot):
        return
    here = await session.get(Node, locked.node_id)
    going = await travel.current(session, locked)
    if going is not None:
        there = await session.get(Node, going.to_node_id)
        if there is not None and (await _outside(session, here) or await _outside(session, there)):
            raise NoAir(key="oxygen-suit-stays-on-road", node=there.name, suit=leaving.type_key)
        return
    if here is not None and await _outside(session, here):
        raise NoAir(key="oxygen-suit-stays-on", node=here.name, suit=leaving.type_key)


async def _outside(session: AsyncSession, place: Node | None) -> bool:
    """Whether a body here breathes through its suit: no air, and not aboard."""
    if place is None or vessels.is_aboard(place):
        return False
    return not await free_air(session, place)


# --- the body's own breathing --------------------------------------------------


async def _lock(session: AsyncSession, body: Body) -> Body:
    """The body's row, locked for this transaction -- the same lock the cold takes."""
    return (
        (
            await session.execute(
                select(Body)
                .where(Body.id == body.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )


async def settle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    now: datetime | None = None,
) -> Breath:
    """Bring a body's breathing up to "now".

    Charges **only a body outside**: a body aboard breathes the hull, and the
    hull is settled once for its whole crew (`tick_ships`). The two never
    overlap, and the split is by where the body stands -- there is no third
    place to be.

    The stamp moves in every case all the same, so hours spent in a Terran yard
    are never charged to a cylinder afterwards.
    """
    moment = now or datetime.now(UTC)
    locked = await _lock(session, body)
    node = await session.get(Node, locked.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        return Breath(left=0.0, uncovered=None)

    hours = (moment - locked.air_at).total_seconds() / SECONDS_PER_HOUR
    #: "Up to now" does not work backwards: a tick step carries the nominal
    #: moment of its tick and can arrive behind a command that settled a second
    #: ago. Writing the older stamp back would hand those seconds to the next
    #: settling to charge again -- the same rule the cold keeps. A stretch of
    #: no length asked nothing, and says nothing about the cylinder.
    if hours <= 0:
        return Breath(left=await carried(session, locked), uncovered=None)

    if await free_air(session, node) or vessels.is_aboard(node):
        #: Nothing was owed for the stretch, so it is over and done with.
        locked.air_at = moment
        await session.flush()
        return Breath(left=await carried(session, locked), uncovered=0.0)

    draw = constants[R.OXYGEN_BODY_DRAW]
    need = hours * draw
    if not await suited(session, catalog, locked):
        #: A bare body on an airless node breathes nothing at all, whatever it
        #: is carrying. The whole stretch is uncovered -- and settled by the
        #: choking below, so the stamp goes all the way.
        locked.air_at = moment
        await session.flush()
        return Breath(left=0.0, uncovered=hours)

    #: What the last stretch breathed and could not be charged for is asked
    #: for first. Down to the thousandth air is split into, never up:
    #: `amount()` rounds to the nearest and would take one the stretch had not
    #: earned. Flooring alone would be worse than the disease -- an error that
    #: cancelled would become one that always took -- which is why the shaving
    #: is kept rather than dropped. In decimals, as on the hull, so the rest is
    #: below a thousandth exactly and not by a float's grace: the column's
    #: check would refuse the tick otherwise.
    owed = Decimal(str(need)) + Decimal(str(locked.air_owed))
    whole = on_grid(owed, ROUND_AMOUNT, ROUND_FLOOR)
    want = amount(whole)
    if want <= 0:
        #: Not a whole thousandth to ask for, so nothing is learnt about the
        #: cylinder either: full or dry, it answers the same to a question
        #: nobody put. The breath waits on the body, and the stretch is
        #: neither covered nor short: read as covered, the tick would give the
        #: grace back to an empty bottle. The same rule the hull keeps
        #: (`_breathe`).
        locked.air_owed = on_grid(owed, ROUND_REMAINDER, ROUND_FLOOR)
        locked.air_at = moment
        await session.flush()
        return Breath(left=await carried(session, locked), uncovered=None)
    stacks = await stock.lock_items(session, await cylinders(session, locked))
    took = await stock.consume(session, stacks, want)
    #: Asked and given are both whole thousandths, so short means short and
    #: never a rounding, exactly as on the hull: nothing below the grid was
    #: asked, and the last digit of an hour needs no forgiving. The tolerance
    #: that used to stand here was a thousandth wide, and a dry bottle owing
    #: exactly one read as covered.
    short = took < want
    if short:
        #: A real shortage. The body choked for it and is not billed twice:
        #: nothing is carried on top of choking.
        locked.air_owed = Decimal(0)
    else:
        #: The breath the cylinder could not be asked for. It waits on the
        #: body, not on the stamp: this stretch may have ended aboard, and
        #: arriving in air moves the stamp to now -- which would forgive the
        #: debt every time a body stepped back up its own gangway.
        locked.air_owed = on_grid(owed - whole, ROUND_REMAINDER, ROUND_FLOOR)
    locked.air_at = moment
    await session.flush()
    #: Asked again rather than summed off the stacks in hand: a stack spent to
    #: nothing is **deleted** by `consume`, and its object keeps the amount it
    #: had -- the sum would count air that no longer exists.
    left = await carried(session, locked)
    #: What nothing covered: all that was owed, less what the cylinder gave.
    missing = float(owed) - amount_float(took)
    return Breath(left=left, uncovered=missing / draw if short else 0.0)


async def tick_bodies(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> int:
    """Settle every body standing where there is no air; kill the ones it ran out on.

    Returns how many died. Bodies aboard are not here: their air is the hull's,
    and `tick_ships` settles them by the hull.

    A world where every planet has air is swept all the same: an orbit is the
    void over any of them (D-245), and a body can stand in one.
    """
    moment = now or datetime.now(UTC)
    bodies = (
        (
            await session.execute(
                select(Body)
                .join(Node, Node.id == Body.node_id)
                .where(
                    Body.state == BodyState.ALIVE,
                    without_air(await airless_planets(session)),
                )
                #: In id order: this sweep locks a body row per body
                #: (`_lock`), and it runs beside every other sweep that does
                #: -- `wear.daily_gear_wear`, `gear.wear_exoskeletons`, the
                #: other of cold and air -- each in a transaction of its own
                #: (`tick.tick_step`). Same rows in two orders is a deadlock.
                .order_by(Body.id)
            )
        )
        .scalars()
        .all()
    )
    dead = 0
    for found in bodies:
        node = await session.get(Node, found.node_id)
        if node is None or vessels.is_aboard(node):  # pragma: no cover -- the hull's business
            continue
        breath = await settle(session, constants, catalog, found, now=moment)
        if breath.uncovered is None:
            #: The stretch asked the cylinder for nothing, so it neither gives
            #: the grace back nor takes it. Every step settles the breathing,
            #: and the tick can land seconds after one -- or, carrying the
            #: nominal moment of its tick, before it.
            continue
        if breath.uncovered <= 0:
            #: Breathing again gives the grace back. Without this a body that
            #: once ran dry and then refilled would carry the mark to its death
            #: and be killed on the first incomplete stretch, with none of the
            #: settling of warning this module promises.
            await _breathing(session, found)
            continue
        if await _choked(session, constants, found, now=moment):
            dead += 1
    return dead


async def _choked(
    session: AsyncSession, constants: Constants, body: Body, *, now: datetime
) -> bool:
    """One settling of grace, then death.

    A stretch the reserve only half covered drains it and kills nobody: the
    tick that lands a second after the last unit is spent must not be
    indistinguishable from suffocation. The next stretch begins with nothing,
    and that one ends the body.

    Decided by the settling alone (D-343): uncovered hours are a body with
    nothing to breathe, whether the cylinders ran dry or nothing connects the
    body to them. This used to ask the cylinders once more, and a bare body
    with air in the bag was neither charged nor choked -- it breathed nothing,
    for nothing and for ever, the opposite of what D-234 says.
    """
    if body.choking_since is None:
        body.choking_since = now
        await session.flush()
        #: Said once, when the countdown starts -- the body's own
        #: `ship.airless` (D-343). The grace is one settling, a minute, so this
        #: rescues nobody who is away: it is the journal's why. The countdown
        #: itself is the bar's, and a death outside was the one death whose
        #: cause the journal never named.
        await events.record(
            session,
            EventKind.BODY_AIRLESS,
            actor_identity_id=body.identity_id,
            node_id=body.node_id,
        )
        return False

    from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

    await death.die(session, constants, body, cause=ASPHYXIA, now=now)
    return True


async def _breathing(session: AsyncSession, body: Body) -> None:
    """The body has air again: the grace is given back."""
    if body.choking_since is not None:
        body.choking_since = None
        await session.flush()


def _counting_down(crew: Sequence[Body]) -> bool:
    """Whether anybody aboard is already on the countdown.

    Their row is written whichever way the stretch ends -- the grace back, or
    the death -- so a hull with one of them has business with its crew before
    it touches a thing aboard (`_breathe`).
    """
    return any(member.choking_since is not None for member in crew)


def _on_the_line(stacks: Sequence[Item]) -> int:
    """Thousandths of air standing on the line, as the reading found them.

    What the stretch asks of the line is compared against this and against
    what `stock.consume` actually takes, so both are counted in the grid the
    line is kept in -- a float sum would make "exactly enough" a coin toss.

    Asked of the **first** reading of a stretch and no other. A reading taken
    after a wait hands back rows already in the session, with the amounts they
    were loaded with (`lines.stacks_in` does not `populate_existing`, as
    `lines.hold_of` can be asked to), so a later one is a list of ids for
    `stock.lock_items` to relock -- and the amounts that matter after that are
    the ones the lock reread.
    """
    return sum(stack.amount for stack in stacks)


# --- the hull's own hours ------------------------------------------------------


async def tick_ships(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> tuple[float, int]:
    """Every sealed hull breathes its stretch. Returns (air breathed, crew lost).

    A hull is settled once for its whole crew: the draw is a number of people
    times an hourly rate, and asking it body by body would read the same
    vessels once a head.
    """
    moment = now or datetime.now(UTC)
    #: Only the hulls with a stretch to settle: a fleet grows with the players,
    #: and a tick that walked all of it every minute to write the same stamp
    #: back would be the cost of owning a shipyard.
    afloat = (
        (await session.execute(select(Ship).where(Ship.air_at < moment).order_by(Ship.id)))
        .scalars()
        .all()
    )
    breathed = 0.0
    dead = 0
    open_hulls: list[uuid.UUID] = []
    for ship in afloat:
        if not await sealed(session, ship):
            #: A hull with the hatch open still moves its stamp: otherwise a
            #: month at a Terran pier would be charged to the line the moment
            #: it cast off. Gathered and written in one statement -- most of a
            #: world's ships stand at a pier, and each of them is not worth a
            #: round trip.
            open_hulls.append(ship.id)
            continue
        drawn, lost = await _breathe(session, constants, catalog, ship, now=moment)
        breathed += drawn
        dead += lost
    if open_hulls:
        await session.execute(update(Ship).where(Ship.id.in_(open_hulls)).values(air_at=moment))
        await session.flush()
    return breathed, dead


async def _breathe(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    now: datetime,
) -> tuple[float, int]:
    """One hull's stretch: breathe it off the life support's line, count the dead.

    **The hull's row, then its crew, then the hull's things.** Everybody who
    acts takes their own body first and the things after -- `_alive` is the
    prologue of every command (D-211) -- so a sweep that will write a crew row
    takes the crew before it touches anything aboard, the way the loss of a
    hull does (`ship.fate._lose`). Taken the other way round, this stretch held
    the oxygen standing on the line and waited for a body, while the pour
    emptying that very vessel held the body and waited for the stack, and the
    database untied the two by killing one of them -- the player's own command
    as readily as the tick (`test_races_ship_air.py`). The pour is only the
    nearest door: every command that reaches a thing aboard holds a body first.

    **And only when it has business with them.** A stretch the line covers
    writes no crew row at all, and holding the whole crew every minute would
    queue their every act behind the tick for nothing -- the rows are held to
    the end of the whole oxygen step (`tick._oxygen`), not just this hull's
    stretch. What it will write is decided under the hull's row alone, before
    the first thing aboard is touched, off a reading of the line: a draw the
    line does not cover means the countdown or the death, and a member already
    counting down means the grace to give back. The bays are poured out after
    that decision and not before it -- a vessel on a bay's line is one a hand
    can be holding too.

    The reading is taken before the bays breathe, so it is short by what they
    are about to give: a hull living off its hydroponics with nothing standing
    on the line reads short every minute and takes its crew every minute, for
    a stretch the bays then cover. Bearable because such a hull is a hull with
    no buffer at all -- a stretch's draw is a thousandth or two -- and one that
    fills up within a few stretches; counting what the bays will give would
    cost a reading of the beds before every stretch that does not need one.

    The decision can also be wrong the other way, when a hand empties the line
    between the reading and the lock: that is the `skip_locked` below, and the
    one case where the crew's rows are taken late and so not waited for.
    """
    locked = (
        (
            await session.execute(
                select(Ship)
                .where(Ship.id == ship.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )
    hours = (now - locked.air_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return 0.0, 0
    locked.air_at = now

    crew = await vessels.crew_of(session, locked)
    #: The hold, once, where something will read it: which systems and bays
    #: stand there and which vessels their lines reach. An empty hull with no
    #: beds is most of a fleet under way and costs one small query, not the
    #: whole hold. It is a **reading**; the write-off below relocks its stacks
    #: by id under `FOR UPDATE`, and a pour locks the vessels it fills, so
    #: nothing is decided from it.
    hold = (
        await lines.hold_of(session, locked)
        if crew or await garden.has_bays(session, locked)
        else []
    )

    #: What the last stretches breathed and the line could not be asked for is
    #: asked for first, and only whole thousandths are asked -- the rest waits
    #: on the hull, under the lock `air_at` is written under. A stretch is a
    #: `time.tick`, and its breath is not a whole number of thousandths:
    #: rounded to the nearest, at a one-minute tick and `oxygen.crew_draw` of
    #: 0.1 a crew of one breathed a fifth more than the rate and a crew of two
    #: a tenth less (measured 2026-09-13). In decimals, so the rest is below a
    #: thousandth exactly and not by a float's grace -- the column's check
    #: would refuse the tick otherwise. The same carry as a body outside
    #: (`settle`), and kept on the hull, not on the stamp, for the same reason:
    #: an open hatch moves the stamp and would forgive it.
    #:
    #: Counted here rather than after the bays, because the crew is taken by
    #: this number and the crew comes before the bays: what they give this
    #: stretch changes what the line holds, never what it owes.
    owed = Decimal(str(hull_draw(constants, len(crew)) * hours)) + Decimal(str(locked.air_owed))
    whole = on_grid(owed, ROUND_AMOUNT, ROUND_FLOOR)
    want = amount(whole)

    #: The line, read: what the decision above is made of, and -- when nothing
    #: pours into it meanwhile -- the very list the write-off locks, so the
    #: reading is taken once and not twice.
    stacks = (
        await breathable_stacks(session, constants, catalog, locked, things=hold)
        if want > 0
        else []
    )
    #: `None` while the crew has not been taken: an empty list is a crew that
    #: stepped off while its rows were waited for, and the two end differently.
    held: list[Body] | None = None
    if want > 0 and (_on_the_line(stacks) < want or _counting_down(crew)):
        held = await vessels.lock_crew(session, locked)
        #: Reread after the wait, as `lock_crew` rereads the crew: whoever
        #: held a row may have been unbolting the system or the vessel the
        #: line hangs on, and the hold is what says which of them still stand.
        hold = await lines.hold_of(session, locked, fresh=True)
        stacks = await breathable_stacks(session, constants, catalog, locked, things=hold)

    #: The beds breathe (D-340): what they gave this stretch is air the crew
    #: may breathe in it. They breathe with nobody aboard as well -- a culture
    #: grows whoever watches it -- and they pour into vessels, so they come
    #: after the crew's rows and not before them.
    if await garden.breathe_out(session, constants, catalog, locked, hold, hours) > 0:
        #: Something landed in the vessels: the reading above no longer says
        #: what stands on the line, and a stack poured into an empty one is not
        #: in it at all.
        stacks = await breathable_stacks(session, constants, catalog, locked, things=hold)

    if not crew:
        #: Nobody aboard breathes nothing, and the life support has no reason
        #: to run: an empty hull in flight arrives with its tanks as it left.
        await session.flush()
        return 0.0, 0
    if want <= 0:
        #: Nothing whole to ask for, so nothing is learnt about the line
        #: either: the crew's countdown stands as the last settling left it.
        locked.air_owed = on_grid(owed, ROUND_REMAINDER, ROUND_FLOOR)
        await session.flush()
        return 0.0, 0
    stacks = await stock.lock_items(session, stacks, ordered=True)
    #: What was **actually** written off is what was breathed, not what the
    #: reading promised: another hand may have poured the cylinder out between
    #: the two, and a crew credited with air it never had would live through an
    #: hour it did not live through.
    took = await stock.consume(session, stacks, want)
    drawn = amount_float(took)
    #: Asked and given are both whole thousandths, so short means short and
    #: never a rounding: the last digit of an hour no longer needs forgiving,
    #: because nothing below the grid was asked. A short crew chokes for it
    #: and is not billed twice -- nothing is carried on top of choking.
    short = took < want
    locked.air_owed = Decimal(0) if short else on_grid(owed - whole, ROUND_REMAINDER, ROUND_FLOOR)
    await session.flush()

    if not short:
        #: The grace back, to the rows that were taken for it. Where none were,
        #: the reading said nobody was counting down -- and a member who walked
        #: aboard counting down since is given it by the next stretch, which
        #: reads them and takes their row first.
        for member in held or ():
            await _breathing(session, member)
        return drawn, 0

    if held is None:
        #: The line covered the draw as it was read and did not as it was
        #: locked: a hand emptied it in between, and the reading the decision
        #: was made of was wrong. The rows are taken now all the same -- a
        #: stretch that let the crew off would be a stretch bought by winning
        #: that race, and it can be entered again every minute -- but
        #: **without waiting**: waiting here, with the line's stacks in hand,
        #: is the knot this order exists to untie. Whoever emptied the line
        #: has committed to have emptied it, so their row is free and they are
        #: settled like everybody else; only a row somebody is holding at this
        #: instant is left out, and left to the next stretch, which reads the
        #: dry line and takes the rows in their proper place.
        held = await vessels.lock_crew(session, locked, skip_locked=True)

    #: The hull ran dry. One settling of grace, exactly as outside: a stretch
    #: only half covered kills nobody, and the next one begun on empty tanks
    #: does. The whole crew shares one hull, so it shares one countdown.
    #:
    #: Every member's row is written below -- the countdown or the death --
    #: and each was taken above, in id order, before the first thing aboard;
    #: the death then reaches into hands the stretch already holds
    #: (`vessels.lock_crew`).
    crew = held
    if not crew:
        #: All of them stepped off while the rows were waited for -- or, on
        #: the way in above, were all of them busy: nobody is left to choke,
        #: and nobody to tell.
        return drawn, 0
    dead = 0
    for member in crew:
        if member.choking_since is None:
            member.choking_since = now
            continue
        from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

        await death.die(session, constants, member, cause=ASPHYXIA, now=now)
        dead += 1
    await session.flush()
    if dead == 0:
        #: Said once, when the tanks first fail to cover the hour: the crew has
        #: one settling to do something about it, and a silent hull would make
        #: the deaths that follow arrive out of nowhere.
        #:
        #: Addressed to **everybody aboard**, not to the owner and the
        #: connector: a hired hand in the engine room is the one this warning is
        #: for, and the node it stands in is not the one the event is written
        #: at. `push` hands an event to every party named by a key ending in
        #: `_identity_id`, so the crew is named that way.
        aboard = {
            f"crew{seat}_identity_id": str(member.identity_id) for seat, member in enumerate(crew)
        }
        await events.record(
            session,
            EventKind.SHIP_AIRLESS,
            actor_identity_id=locked.owner_identity_id,
            node_id=locked.connector_node_id,
            ship_id=str(locked.id),
            name=locked.name,
            crew=len(crew),
            #: What the crew could have breathed and did not (D-288): oxygen
            #: standing in a vessel the line does not name. The same number the
            #: bridge is shown, out of the same call -- the decision names one
            #: quantity, and a hull whose journal and whose console disagree
            #: about it is worse than either (2026-09-08). The journal says it
            #: when it has already cost an hour.
            off_line=round(
                await off_line(session, constants, catalog, locked, things=hold), ROUND_AMOUNT
            ),
            **aboard,
        )
    return drawn, dead
