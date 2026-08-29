# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Warmth: a node is warm or cold, a body carries hours of it (D-231).

Aurora is permafrost and Pyroxis is a furnace, and both planets are unlivable
for the same reason: the ground there does not keep a body alive by itself.
The mechanic is deliberately **binary** -- there are no degrees anywhere in
this module, and no place where "a little warm" could be written:

* **the node is warm** when the planet has no climate of its own at all
  (Terra), when it is a node aboard a ship (life support heats it), or when
  something works in it that heats: a heat plant in the node **or next door**,
  a heater in the node, a lit brazier in the node;
* **the node is cold** otherwise. On the scorching planet it is always cold in
  that sense -- there are no shelters on Pyroxis and never will be (D-230):
  the ship's board and the suit are what a body has there;
* **the body holds a reserve in hours**. In a warm node it comes back
  `frost.warm_rate` times faster than it goes; in a cold one it melts hour by
  hour. Empty reserve -- **frozen**: the body burns stamina on any work at
  `frost.frozen_drain_k` and burns `frost.frozen_stamina` an hour on nothing
  at all. That hour is charged by whatever settles the reserve -- a command as
  readily as the tick -- so acting is no way to outrun the cold. Stamina gone
  while still in the cold -- death, and it is always an explainable one: the
  hours were on the screen the whole time.

## Why the reserve is a pair of columns and not a tick

A body on Terra stands in a warm node for ever, and the world must not write a
row for it every minute. So `body.warmth` holds the hours as of `warmth_at`,
and everything else is arithmetic over the elapsed time -- the way the battery
counts its self-discharge and the plot counts its fallow. The tick sweeps
**only bodies on a planet with a climate**; on Terra the pair is never touched,
and reading it there gives the full reserve however old the stamp is.

## What heat costs

Heat is a round-the-clock drain: `frost.plant_draw` an hour for a plant,
`frost.heater_draw` for a heater, taken from the city pool by `energy.produce`
in the same pass that fills it. An empty pool is a cold city -- that is the
whole price of living on the permafrost, and it is meant to be felt.

## Where this file will split

The file is past the length a file should have, and the roadmap has more coming
to it -- oxygen and the ships' autonomy (D-233). The seam is already visible and
is named here so that the next hand does not have to find it:
**the planet and the node** (`climate_of`, `is_warm`, `heated`, `_standing`),
**the body's reserve** (`settle`, `_advance`, `use_warmer`, `view`) and **the
world's own hours** (`tick_bodies`, `tick_fires`). Splitting is worth doing with the next
thing added, not before: three files with one caller each would be harder to
read than one honest module.

The brazier is the exception the rest rests on: it burns fuel of its own and
asks no pool, so it works where nothing else does -- **including in the frost
itself**. A machine that burns is a machine that works in the cold (there is no
flag for it: the classes that burn are the classes that burn), and without that
rule a frozen city could never be lit again -- the generator that must give the
first heat would itself be standing frozen.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.db.base import remember
from src.engine import events, stock, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.gear import Equipped
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node, Planet
from src.units import SECONDS_PER_HOUR, amount, amount_float

#: The planet's own property, written into its node on the space layer by the
#: seed (D-231). A planet without either is livable ground and asks nothing.
FROST = "мерзлота"
HEAT = "пекло"

#: Thing classes with heat behaviour (D-215). The engine keeps no list of
#: stoves: a second heater is a line in the vault.
#: The plant heats **its node and every neighbour**, the heater only its own,
#: and both eat the city pool. The brazier is carried, burns fuel and needs no
#: grid -- the warmth of a camp and the first spark of a dead city.
PLANT = "ТЭЦ"
HEATER = "Обогреватель"
BRAZIER = "Жаровня"
#: A one-off handful of hours, the thing one walks into the cold with.
WARMER = "Грелка"


class FrostError(Refusal):
    pass


class Frozen(FrostError):
    """The node is cold: what does not burn its own fuel does not work here."""


class NotWarmer(FrostError):
    """Not a warmer. What warms is decided by the vault, not by the engine."""


# --- the planet and the node --------------------------------------------------


async def climate_of(session: AsyncSession, node: Node) -> str | None:
    """«мерзлота», «пекло» -- or nothing, where the ground is livable.

    The property belongs to the **planet**, and a planet is an ordinary node of
    the space layer whose key is the planet's own name (`seed_parts.system`). Asked
    of the planet rather than of a constant on purpose: a climate is a fact of
    the world, and the world is in the database.
    """
    weather = await _planet_marks(session)
    return weather.get(node.planet)


async def _planet_marks(session: AsyncSession) -> dict[Planet, str | None]:
    """Every planet's climate, in one reading -- there are four of them."""

    async def read() -> dict[Planet, str | None]:
        spheres = (
            (
                await session.execute(
                    select(Node).where(Node.key.in_([planet.value for planet in Planet]))
                )
            )
            .scalars()
            .all()
        )
        found: dict[Planet, str | None] = {}
        for sphere in spheres:
            marks = sphere.properties or {}
            for weather in (FROST, HEAT):
                if marks.get(weather):
                    found[sphere.planet] = weather
        return found

    return await remember(session, ("planet_climate",), read)


async def is_warm(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Whether a body and a machine are warm in this node."""
    weather = await climate_of(session, node)
    if weather is None:
        return True
    #: Life support heats the board: a ship is warm wherever it stands (D-231).
    from src.engine import ship  # noqa: PLC0415 -- lazy: breaks the cycle with ship

    if ship.is_aboard(node):
        return True
    #: There are no shelters on the scorching planet and there will be none
    #: (D-230): nothing is built on Pyroxis, so nothing can heat -- or cool -- a
    #: node there. What saves a body is the suit and the board.
    if weather == HEAT:
        return False
    return await heated(session, constants, node)


async def heated(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Whether something that heats works in this node.

    A working brazier is one with fuel lying in the node: it burns what is
    brought, and an empty brazier is cold iron. A plant and a heater work while
    the city pool has anything in it -- an empty pool is a dark, cold city.

    Asked by every `look` on a cold planet, so the node and all its neighbours
    are read in **one** query rather than one apiece, and the yards are read
    without being created: a read may not write (review 2026-08-23).
    """
    here = (await _standing(session, [node])).get(node.id, frozenset())
    if here & _class_names(BRAZIER) and here & frozenset(constants[R.ENERGY_FUEL_ENERGY]):
        return True
    if await _stove_works(
        session, constants, node, here, _class_names(PLANT) | _class_names(HEATER)
    ):
        return True
    #: The plant reaches one node further -- its own and every neighbour's.
    #: A neighbour is a neighbour by the graph, and heat travels along an edge
    #: like everything else in this world. Asked only now: a node warmed by its
    #: own stove is the common case, and it must cost one query, not three.
    neighbours = await _neighbours(session, node)
    if not neighbours:
        return False
    standing = await _standing(session, neighbours)
    for other in neighbours:
        if await _stove_works(
            session, constants, other, standing.get(other.id, frozenset()), _class_names(PLANT)
        ):
            return True
    return False


async def _stove_works(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    standing: frozenset[str],
    wanted: frozenset[str],
) -> bool:
    """Whether a stove of the wanted kind burns in this node, and on whose energy.

    Two purses, and they are not interchangeable (D-232): what the Forerunners
    left runs on their reactor while it lasts **or** on the city pool once it is
    gone; what people built runs on the pool and on nothing else. Without the
    split a reactor would be heating everything anybody carried into its city,
    free of charge, for a year.
    """
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    stoves = standing & wanted
    if not stoves:
        return False
    book = current_catalog().recipes
    if await _grid_alive(session, constants, node):
        return True
    if not any(book.is_relic(name) for name in stoves):
        return False
    return await energy.relic_power(session, constants, node) > 0


async def _standing(
    session: AsyncSession, nodes: Sequence[Node]
) -> dict[uuid.UUID, frozenset[str]]:
    """What stands in each of these nodes, by name, in one query.

    Read straight off the containers instead of through `world.thing_kinds`:
    that one creates the yard where a node has none, and warmth is asked for
    by `look` -- including about **neighbouring** nodes, where nobody stands
    and nothing should be brought into being by somebody glancing at the map.
    """
    if not nodes:  # pragma: no cover -- there is always at least the node itself
        return {}
    ids = tuple(sorted(node.id for node in nodes))

    async def read() -> dict[uuid.UUID, frozenset[str]]:
        rows = await session.execute(
            select(Container.owner_id, Item.type_key)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE, Container.owner_id.in_(ids))
            .distinct()
        )
        found: dict[uuid.UUID, set[str]] = {}
        for owner_id, type_key in rows:
            found.setdefault(owner_id, set()).add(type_key)
        return {owner: frozenset(names) for owner, names in found.items()}

    return await remember(session, ("frost_standing", ids), read)


async def _grid_alive(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Whether the **city pool** has anything for this node. A read: no pool is created.

    Only the pool: the Forerunners' reactor is another purse and pays only for
    their own things (`_stove_works`). Remembered by the city rather than by the
    node: every node of a city shares one pool, and `heated` asks about a whole
    ring of neighbours at once.
    """

    async def read() -> bool:
        from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

        pool = await energy.pool_of(session, constants, node, create=False)
        return pool is not None and float(pool.stored) > 0

    return await remember(session, ("frost_grid", node.parent_id, node.layer), read)


async def _neighbours(session: AsyncSession, node: Node) -> list[Node]:
    """The nodes one edge away. Read straight off the edge table: warmth is
    asked for by every look, and the road engine has nothing to add here."""

    async def read() -> list[Node]:
        edges = (
            (
                await session.execute(
                    select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
                )
            )
            .scalars()
            .all()
        )
        ids = {edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id for edge in edges}
        if not ids:
            return []
        return list((await session.execute(select(Node).where(Node.id.in_(ids)))).scalars().all())

    return await remember(session, ("frost_neighbours", node.id), read)


def _class_names(thing_class: str) -> frozenset[str]:
    """Every thing of the class, by name (D-215)."""
    return frozenset(world.station_names(thing_class))


# --- machines in the cold -----------------------------------------------------


def burns_own_fuel(type_key: str) -> bool:
    """Whether this machine keeps its own fire going.

    Not a flag but the fuel behaviour itself (D-231): the fuel station burns
    what is hauled to it, the brazier burns what is put in it, and both are the
    reason a frozen city can be lit at all.

    Answers to a class as readily as to a thing: the craft engine asks by class
    («Верстак»), the node scene by name («Угольная станция»), and the rule is
    one and the same rule.
    """
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    burning = (energy.FUEL_PLANT, BRAZIER)
    if type_key in burning:
        return True
    return any(type_key in _class_names(one) for one in burning)


async def works_here(
    session: AsyncSession, constants: Constants, node: Node, type_key: str
) -> bool:
    """Whether this machine works in this node. In the frost only what burns does."""
    if burns_own_fuel(type_key):
        return True
    return await is_warm(session, constants, node)


async def require_working(
    session: AsyncSession, constants: Constants, node: Node, type_key: str
) -> None:
    """Refuse work at a machine standing in a frozen node."""
    if await works_here(session, constants, node, type_key):
        return
    raise Frozen(
        f"«{node.name}» промёрз: «{type_key}» здесь не работает. "
        f"Тепло даёт «{PLANT}», «{HEATER}» или «{BRAZIER}» с топливом"
    )


def heat_draw(constants: Constants, standing: dict[str, float]) -> tuple[float, float]:
    """What the stoves of a node take an hour, as a pair: **theirs, ours**.

    `standing` is name -> how many stand there. Counted by `energy.produce` in
    the same pass that fills the pool: generation and heat are one balance, and
    two passes over the same city would sooner or later disagree.

    The pair matters because the two are paid from different purses (D-232):
    the Forerunners' plant runs on the Forerunners' reactor, and everything
    people built runs on the city pool. Were it one number, a reactor would be
    heating twenty heaters somebody carried in, and "a city on the permafrost
    pays for its own existence" would be off exactly where it must bite.
    """
    plants = _class_names(PLANT)
    heaters = _class_names(HEATER)
    book = current_catalog().recipes
    theirs = ours = 0.0
    for name, count in standing.items():
        if name in plants:
            draw = constants[R.FROST_PLANT_DRAW] * count
        elif name in heaters:
            draw = constants[R.FROST_HEATER_DRAW] * count
        else:
            continue
        if book.is_relic(name):
            theirs += draw
        else:
            ours += draw
    return theirs, ours


# --- the body's reserve -------------------------------------------------------


async def limit_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    """The reserve this body can hold, hours: the bare one times what is worn.

    Keyed by thing class the way the carry bonus is (`inventory.carry_bonus`):
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
    if warm:
        #: Coming back is as much faster than going as the vault says, and the
        #: suit speeds both: a big coat must not take half a day to warm up.
        gain = constants[R.FROST_WARM_RATE] * (ceiling / constants[R.FROST_RESERVE_MAX]) * hours
        left = min(ceiling, was + gain)
        uncovered = 0.0
    else:
        left = max(0.0, was - hours)
        uncovered = max(0.0, hours - was)

    locked.warmth = Decimal(str(left))
    locked.warmth_at = moment
    #: The stretch the reserve did not cover is **paid here**, in the same
    #: place it is counted, and by whoever settles first. Left to the tick, it
    #: would be paid only by a body that stands still: every command settles
    #: too, and each one would move the stamp out from under the tick -- an
    #: active frozen player would burn a sixth of what a sleeping one does,
    #: while D-231 charges for time and not for idleness.
    if uncovered > 0:
        burnt = constants[R.FROST_FROZEN_STAMINA] * uncovered
        locked.stamina = Decimal(str(max(0.0, float(locked.stamina) - burnt)))
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
        raise FrostError("мёртвое тело не греется")
    #: A sleeper does nothing by hand, a meal included (`food.eat`): the reserve
    #: melts in the sleep, and that is exactly the mistake the planet kills for.
    if body.sleeping_since is not None:
        raise FrostError("тело спит: сначала проснуться")
    if catalog.recipes.resolve(item.type_key) != WARMER:
        raise NotWarmer(f"«{item.type_key}» не греет: для этого есть «{WARMER}»")
    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise FrostError("грелку достают из рук")
    node = await session.get(Node, body.node_id)
    if node is None or await climate_of(session, node) is None:
        raise FrostError("здесь не мёрзнут: греться незачем, а грелка одноразовая")

    before = await settle(session, constants, catalog, body, now=moment)
    ceiling = await limit_of(session, constants, catalog, body)
    left = min(ceiling, before + constants[R.FROST_WARMER_HOURS])
    if left <= before:
        raise FrostError(
            f"теплозапас и так полон ({before:.1f} ч из {ceiling:.1f}): грелку берегут на холод"
        )
    #: The stack is locked before it is spent, like every other write-off in the
    #: world: the body's own lock is not a substitute for the thing's.
    await stock.lock_items(session, [item])
    body.warmth = Decimal(str(left))
    body.warmth_at = moment
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


# --- the world's own hours ----------------------------------------------------


async def tick_bodies(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> int:
    """Settle every body standing on a planet with a climate; kill the ones the
    cold has run out of. Returns how many died.

    The world does not wait for a login. A night in the frost is exactly the
    mistake Aurora kills for (10-world/05), and there is no offline mercy: one
    world for everybody, and hibernation restores less than the cold takes.
    """
    moment = now or datetime.now(UTC)
    weather = await _planet_marks(session)
    if not weather:
        return 0
    bodies = (
        (
            await session.execute(
                select(Body)
                .join(Node, Node.id == Body.node_id)
                .where(
                    Body.state == BodyState.ALIVE,
                    Node.planet.in_([planet.value for planet in weather]),
                )
            )
        )
        .scalars()
        .all()
    )
    #: Warmth is a property of the node, and bodies stand in the same few nodes:
    #: asked once per node for the whole pass, not once per body. Kept in a
    #: local rather than in `remember`, which every write throws away.
    warm_here: dict[uuid.UUID, bool] = {}
    dead = 0
    for found in bodies:
        if await _burn(session, constants, catalog, found, now=moment, warm_here=warm_here):
            dead += 1
    return dead


async def _burn(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    now: datetime,
    warm_here: dict[uuid.UUID, bool],
) -> bool:
    """One body's stretch of cold, and its end if it has come.

    The arithmetic and the stamina are `_advance`'s, on the locked row -- the
    tick brings the world's own hours to a body that is doing nothing, and it
    keeps one thing of its own: **the death**. Dying is not something to do to a
    body in the middle of somebody's command, and a minute's delay changes
    nothing for a body that has already spent its last strength in the frost.
    """
    locked = await _lock(session, body)
    node = await session.get(Node, locked.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        return False
    if node.id not in warm_here:
        warm_here[node.id] = await is_warm(session, constants, node)
    warm = warm_here[node.id] and not await _on_the_road(session, locked)
    ceiling = await limit_of(session, constants, catalog, locked)

    spell = await _advance(session, constants, catalog, locked, now=now, warm=warm, ceiling=ceiling)
    #: Death is the pair: no strength left, and still in the cold. Warm again
    #: and empty is a body that must eat and sleep, not a corpse.
    if spell.left > 0 or float(locked.stamina) > 0:
        return False

    from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

    await death.die(
        session,
        constants,
        locked,
        cause="жара" if await climate_of(session, node) == HEAT else "холод",
        now=now,
    )
    return True


async def tick_fires(session: AsyncSession, constants: Constants, *, hours: float) -> float:
    """Braziers burn what lies with them. Returns how much fuel went up.

    Counted by the tick's own period, the way wear and roads are: a fire is not
    a machine with a meter, and a brazier that burned a minute less because a
    tick was late is nobody's loss. No fuel in the node -- nothing burns, and
    the node goes cold by itself with no second rule for it.

    A brazier standing where a plant already heats burns all the same: there is
    no switch on a fire, and one left in a fuel store is the owner's mistake,
    not the world's arithmetic.
    """
    if hours <= 0:  # pragma: no cover -- the tick period is never zero
        return 0.0
    fuels: dict[str, float] = constants[R.ENERGY_FUEL_ENERGY]
    if not fuels:  # pragma: no cover -- the vault always names a fuel
        return 0.0
    weather = await _planet_marks(session)
    if not weather:
        return 0.0
    #: Only where a fire is a mechanic at all. A brazier standing in a Terran
    #: yard next to the coal pile must not quietly eat the city's fuel: on a
    #: planet without a climate nobody lights one, and nothing burns.
    yards = (
        select(Container.id)
        .join(Node, Node.id == Container.owner_id)
        .where(
            Container.kind == ContainerKind.NODE,
            Node.planet.in_([planet.value for planet in weather]),
        )
    )
    #: City by city and, inside a city, node by node -- exactly the way the
    #: energy step walks (`energy.tick_pools` by pool node, `produce` by node
    #: id). Both steps lock the fuel lying in a yard and run in the same tick,
    #: and two orders over one set of stacks are a deadlock waiting for a busy
    #: world. Yards outside any city come last: no pool reaches them, so the
    #: energy step never touches them at all.
    braziers = (
        await session.execute(
            select(Container.id, func.sum(Item.amount))
            .join(Item, Item.container_id == Container.id)
            .join(Node, Node.id == Container.owner_id)
            .where(
                Item.type_key.in_(tuple(_class_names(BRAZIER))),
                Container.id.in_(yards),
            )
            .group_by(Container.id, Node.id)
            .order_by(Node.parent_id.nulls_last(), Node.id)
        )
    ).all()
    burnt = 0.0
    for container_id, fires in braziers:
        stacks = await stock.locked_stacks(session, container_id, fuels)
        if not stacks:
            continue
        #: Every fire eats: two braziers in a yard burn twice the fuel, and
        #: they fold into one stack when nobody has touched them (D-214).
        need = constants[R.FROST_BRAZIER_FUEL_DRAW] * hours * amount_float(int(fires))
        burnt += amount_float(await stock.consume(session, stacks, amount(need)))
    return burnt
