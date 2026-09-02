# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The body's reserve of warm hours (D-231): the limit the suit multiplies,
the settling that spends hours on the road and fills them under a roof,
and the warmer that buys a few more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.engine import events, stock, world
from src.engine.frost._base import WARMER, FrostError, NotWarmer, climate_of
from src.engine.frost.warmth import is_warm
from src.models.event import EventKind
from src.models.gear import Equipped
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.travel import Travel, TravelState
from src.models.world import Node
from src.units import ROUND_STAMINA, ROUND_WARMTH, SECONDS_PER_HOUR, amount, on_grid

# --- the body's reserve -------------------------------------------------------


async def limit_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    """The reserve this body can hold, hours: the bare one times what is worn.

    Keyed by thing class the way the exoskeleton's lift is (`inventory.exo_bonus`):
    a warmer coat is a line in the vault, never a line here.
    """
    return constants[R.FROST_RESERVE_MAX] * await _suit_k(session, constants, catalog, body)


async def _suit_k(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    table: dict[str, float] = constants[R.FROST_SUIT_K]
    if not table:
        return 1.0
    worn = (
        (
            await session.execute(
                select(Item)
                .join(Equipped, Equipped.item_id == Item.id)
                .where(Equipped.body_id == body.id)
            )
        )
        .scalars()
        .all()
    )
    multiplier = 1.0
    for thing in worn:
        multiplier *= table.get(catalog.recipes.resolve(thing.type_key), 1.0)
    return multiplier


async def _on_the_road(session: AsyncSession, body: Body) -> bool:
    """Whether the body is between nodes right now.

    On the road there is no shelter: a transit across the ice is the cold
    itself, and the node left behind must not go on heating a body that is no
    longer in it. Where the planet has no climate this changes nothing --
    `is_warm` has already said the ground is livable.
    """
    found = await session.scalar(
        select(Travel.id)
        .where(Travel.body_id == body.id, Travel.state == TravelState.GOING)
        .limit(1)
    )
    return found is not None


async def drain_multiplier(session: AsyncSession, constants: Constants, body: Body) -> float:
    """How much more the cold makes any work cost (D-231).

    Multiplied into the spend next to the satiety one (`food.drain_multiplier`):
    hunger and cold are two states of the same body, and both work on the same
    number.

    **Settles first**, and therefore writes: the price of a swing must be the
    price at this second and not at the last tick -- the screen counts the hand
    itself and would otherwise disagree with the till. On a planet without a
    climate it costs one remembered read and settles nothing: there is no cold
    there to charge for.
    """
    node = await session.get(Node, body.node_id)
    if node is None or await climate_of(session, node) is None:
        return 1.0
    left = await settle(session, constants, current_catalog(), body)
    return constants[R.FROST_FROZEN_DRAIN_K] if left <= 0 else 1.0


@dataclass(frozen=True, slots=True)
class Spell:
    """What one settling did: what is left, and what the reserve did not cover."""

    left: float
    #: Hours of the elapsed stretch the reserve did **not** cover -- the part
    #: spent frozen. Zero while there was anything left to spend, and it is
    #: what stamina is burned for.
    uncovered: float


async def _lock(session: AsyncSession, body: Body) -> Body:
    """The body's row, locked for this transaction.

    The reserve and the stamina are quantities of the body, and the tick moves
    both while the player is spending them (CLAUDE.md, review 2026-08-23). A
    command has already locked the same row through `_alive`; locking it again
    inside one transaction costs nothing and makes every other caller safe.
    """
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
) -> float:
    """Bring the body's reserve up to "now" and return the hours left.

    **Charges as it counts**: the stretch the reserve did not cover is paid in
    stamina here and now, on the locked row, by whoever settles first. The tick
    is only the settling of a body that is doing nothing.
    """
    locked = await _lock(session, body)
    return (await _advance(session, constants, catalog, locked, now=now)).left


async def _advance(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    locked: Body,
    *,
    now: datetime | None = None,
    warm: bool | None = None,
    ceiling: float | None = None,
) -> Spell:
    """The arithmetic of one settling, on a row already locked.

    Everything it reads it reads from that row, so nothing here can be computed
    from a value somebody else has meanwhile moved. `warm` and `ceiling` are
    for the tick, which knows the answer for a whole node and must not ask it
    once per body.
    """
    moment = now or datetime.now(UTC)
    node = await session.get(Node, locked.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        return Spell(left=constants[R.FROST_RESERVE_MAX], uncovered=0.0)
    #: The climate is asked first so that a planet without one costs a single
    #: query: on Terra there is nothing to be on the road from.
    weather = await climate_of(session, node)
    if warm is None:
        warm = weather is None or (
            await is_warm(session, constants, node) and not await _on_the_road(session, locked)
        )
    if ceiling is None:
        ceiling = await limit_of(session, constants, catalog, locked)

    #: Empty means never measured, and a body that has never been cold carries
    #: a full reserve: every body printed before the frost existed is one.
    was = ceiling if locked.warmth is None else min(float(locked.warmth), ceiling)
    #: "Up to now" does not work backwards. A tick step carries the **nominal**
    #: moment of its tick and can arrive behind a command that settled a second
    #: ago; writing the older stamp back would hand those seconds to the next
    #: settling to charge a second time -- and since the cold is paid where it
    #: is counted, that is a double charge, systematically in the world's favour.
    hours = (moment - locked.warmth_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return Spell(left=was, uncovered=0.0)
    #: The reserve is kept to a hundredth of an hour -- six-and-thirty seconds
    #: -- and a stretch shorter than that cannot be written to it. The stamp
    #: used to move over such a stretch anyway, and every command settles the
    #: body, so a player acting oftener than twice a minute paid no cold at
    #: all. The stamp moves only as far as the reserve actually shifted, and
    #: the leftover seconds wait in the clock for the next settling.
    if warm:
        #: Coming back is as much faster than going as the vault says, and the
        #: suit speeds both: a big coat must not take half a day to warm up.
        rate = constants[R.FROST_WARM_RATE] * (ceiling / constants[R.FROST_RESERVE_MAX])
        gain = rate * hours
        #: Down: never more warmth than the hours earned. The ceiling is put
        #: on the grid too -- clamping to a ceiling off it would hand the row
        #: a number the grid never had, which is the whole disease.
        roof = float(on_grid(ceiling, ROUND_WARMTH, ROUND_FLOOR))
        left = min(roof, float(on_grid(was + gain, ROUND_WARMTH, ROUND_FLOOR)))
        uncovered = 0.0
        spent = hours if left >= roof or rate <= 0 else (left - was) / rate
    else:
        #: Toward plus infinity: never more cold than the hours brought.
        left = max(0.0, float(on_grid(was - hours, ROUND_WARMTH, ROUND_CEILING)))
        uncovered = max(0.0, hours - was)
        #: The hours past the reserve are settled below, against stamina, and
        #: how far the stamp goes for them is decided there: stamina is kept to
        #: a hundredth as well, and a charge too thin to write is a charge
        #: nobody paid. Claiming those hours here on the strength of a payment
        #: that never landed would be this very defect, one column down.
        spent = was - left

    locked.warmth = Decimal(str(left))
    #: The stretch the reserve did not cover is **paid here**, in the same
    #: place it is counted, and by whoever settles first. Left to the tick, it
    #: would be paid only by a body that stands still: every command settles
    #: too, and each one would move the stamp out from under the tick -- an
    #: active frozen player would burn a sixth of what a sleeping one does,
    #: while D-231 charges for time and not for idleness.
    if uncovered > 0:
        toll = constants[R.FROST_FROZEN_STAMINA]
        had = float(locked.stamina)
        #: Up, toward what the body had: never charged more than the cold
        #: brought. What the column cannot show is not paid, and the hours it
        #: would have paid for are left in the clock for the next settling --
        #: so a body settled every second pays the same as one settled once.
        rest = float(on_grid(max(0.0, had - toll * uncovered), ROUND_STAMINA, ROUND_CEILING))
        locked.stamina = Decimal(str(rest))
        paid = had - rest
        #: Nothing left to take: the stretch is spent whatever it cost, or a
        #: body at nothing would never move its stamp again.
        spent += uncovered if rest <= 0 or toll <= 0 else paid / toll
    locked.warmth_at = locked.warmth_at + timedelta(hours=max(0.0, min(hours, spent)))
    await session.flush()
    if left <= 0 < was:
        await events.record(
            session,
            EventKind.BODY_FROZE,
            actor_identity_id=locked.identity_id,
            node_id=locked.node_id,
            climate=weather,
        )
    return Spell(left=left, uncovered=uncovered)


async def use_warmer(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> float:
    """Break a warmer: `frost.warmer_hours` straight into the reserve.

    Above the ceiling it is not stored, and a warmer that would store nothing
    is **refused** rather than burned: a thing that vanishes for no effect is a
    silent sink of matter, and on Terra every warmer would be one. Used from the
    hands and by oneself -- matter requires presence (D-044).
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise FrostError(key="frost-dead-warms")
    #: A sleeper does nothing by hand, a meal included (`food.eat`): the reserve
    #: melts in the sleep, and that is exactly the mistake the planet kills for.
    if body.sleeping_since is not None:
        raise FrostError(key="frost-asleep")
    if catalog.recipes.resolve(item.type_key) != WARMER:
        raise NotWarmer(key="frost-not-a-warmer", goods=item.type_key, warmer=WARMER)
    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise FrostError(key="frost-warmer-from-hands")
    node = await session.get(Node, body.node_id)
    if node is None or await climate_of(session, node) is None:
        raise FrostError(key="frost-no-cold-here")

    before = await settle(session, constants, catalog, body, now=moment)
    ceiling = await limit_of(session, constants, catalog, body)
    roof = float(on_grid(ceiling, ROUND_WARMTH, ROUND_FLOOR))
    gained = on_grid(before + constants[R.FROST_WARMER_HOURS], ROUND_WARMTH, ROUND_FLOOR)
    left = min(roof, float(gained))
    if left <= before:
        raise FrostError(key="frost-reserve-full", have=before, ceiling=ceiling)
    #: The stack is locked before it is spent, like every other write-off in the
    #: world: the body's own lock is not a substitute for the thing's.
    await stock.lock_items(session, [item])
    body.warmth = Decimal(str(left))
    #: The stamp is left where `settle` put it: breaking a warmer adds hours to
    #: the reserve, it does not make the cold before it go away. Moved to now,
    #: the seconds the settling could not yet write off would be forgiven with
    #: it -- the very leak this file was straightened out to stop.
    await stock.consume(session, [item], amount(1))
    await session.flush()

    await events.record(
        session,
        EventKind.BODY_WARMED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        type_key=item.type_key,
        hours=left - before,
    )
    return left - before


async def view(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, node: Node
) -> dict[str, Any] | None:
    """What the player is told about the cold. Empty where there is no climate.

    The hours are **not** sent as a number that would go stale in a second: the
    client is given the stamp, the rate and the ceiling and counts the hand
    itself, the way it counts the planet's clock (D-226).
    """
    weather = await climate_of(session, node)
    if weather is None:
        return None
    #: Counted exactly as `settle` counts it, the road included: a hand that
    #: rose while the body walked across the ice would be a lie on the screen.
    warm = await is_warm(session, constants, node) and not await _on_the_road(session, body)
    ceiling = await limit_of(session, constants, catalog, body)
    rate = constants[R.FROST_WARM_RATE] * (ceiling / constants[R.FROST_RESERVE_MAX])
    #: Empty is a body that has never been cold: a full reserve **as of now**,
    #: not as of the stamp it was printed with. The client counts down from
    #: whatever it is given, and an old stamp would have it show a body frozen
    #: that the server holds to be warm.
    never = body.warmth is None
    return {
        "climate": weather,
        "warm": warm,
        "hours": ceiling if never else float(body.warmth),
        "at": datetime.now(UTC).isoformat() if never else body.warmth_at.isoformat(),
        #: Hours of reserve gained per hour here: negative is the countdown.
        "per_hour": rate if warm else -1.0,
        #: The ceiling depends on what is worn, so the client cannot derive it
        #: (D-225). What the frozen body pays is not here for the same reason
        #: reversed: `frost.frozen_stamina` and `frost.frozen_drain_k` are
        #: catalog constants and live in `/public/constants`.
        "max": ceiling,
    }
