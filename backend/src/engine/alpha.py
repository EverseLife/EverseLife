# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Alpha tools: matter out of nowhere, and work finished out of turn (D-229).

The alpha is tested by playing it, and playing it honestly means waiting out
every term the world sets: a survey is minutes, a road is hours, a batch is a
working day. Two levers exist for the alpha alone -- print a thing, and finish
what this player has already started.

Two rules keep them from becoming a hole in the world:

* **matter still arrives with a named ground.** The spawn goes through
  `world.grant_item` with `origin="alpha"`, so the journal says where the thing
  came from. Pillar P1 gets no exception here, only one more honest reason --
  `grant_item` already names a debugging script among them. `origin = 'alpha'`
  finds every thing the world did not earn, and that is the point of writing it;
* **hurrying does not do the work.** It moves the term to now and leaves the
  finishing to the ordinary journal handler the world runs anyway. There is no
  second code path where an arrival or a batch could end differently from the
  honest one. The handlers stamp their results by `job.run_at` -- the arrival
  time, the chat horizon, the batch's `finished_at` -- so a hurried result reads
  as a result of the moment it actually happened at, not of the term that was
  cancelled.

Who is allowed here is not this module's business: that depends on the copy
being run, and it is decided in `api/commands/alpha.py` from settings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, liquid, station, world
from src.engine.errors import Refusal
from src.models.craft import BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.travel import Travel, TravelState
from src.models.world import Node
from src.units import AMOUNT_MAX

#: The ground written into the journal for everything printed here.
ORIGIN = "alpha"

#: What "hurry" reaches: the waits a tester runs into constantly. All of them
#: are this body's own work, queued with its `body_id`.
#: Everything else in the journal is the world's own business -- the daily
#: tick, meters, spoilage, a vote counting down -- and pulling those forward
#: would not speed a session up, it would falsify it.
#:
#: A keel, a passage, the works on a building and the ploughing are here
#: because they are the longest of the lot (`ship.foundation_hours` is eight, a
#: passage is hours more, a house is a working day, and a field is not sown
#: until the furrow is turned): without them neither a ship, nor a house, nor a
#: harvest could be looked at in a session at all, which is exactly the kind of
#: wait this widget exists for (D-229). What makes reaching them safe is not
#: the list but the filter under it: every one is queued with its `body_id`,
#: and `hurry` selects by that, so no job but this body's can move.
#:
#: One of them moves more than this body all the same, and honestly:
#: `SHIP_FLIGHT` carries the pilot's `body_id`, but a ship arrives whole, so
#: everyone aboard lands early with it. That is the ship, not a hole in the
#: filter -- a passage belongs to the hull and never to one passenger.
HURRIED = (
    JobKind.EXPLORE_SURVEY.value,
    JobKind.TRAVEL_LEG.value,
    JobKind.CRAFT_BATCH.value,
    JobKind.SHIP_KEEL.value,
    JobKind.SHIP_FLIGHT.value,
    JobKind.BUILD_FINISH.value,
    JobKind.BUILD_DEMOLISH.value,
    JobKind.BUILD_REPAIR.value,
    JobKind.FARM_PLOW.value,
)

#: The one wait that belongs to nobody's hands: the body is what is being made,
#: so there is no `body_id` on the job to filter by, and the asker has no body
#: at all while it runs. It is reached by the identity written into the
#: payload -- and it has to be reachable, because at twelve hours at the
#: Forerunners' printer it is the longest term in the world, and a tester who
#: died was the one player the widget could not help.
BY_IDENTITY = (JobKind.BODY_PRINT.value,)


class AlphaError(Refusal):
    """The alpha tool refuses. As with any refusal, this is not a server error."""


class NoSuchThing(AlphaError):
    """No such thing in this world's catalog: there is nothing to print."""


def known(catalog: Catalog) -> tuple[str, ...]:
    """Every name a thing can exist under: catalog materials and recipe outputs.

    Sorted, so the client's list does not shuffle between reads, and built from
    the public fields of the book rather than from a new catalog method: the
    catalog describes the world, not what a debug widget wants to offer.
    """
    book = catalog.recipes
    names = {material.name for material in book.materials}
    names.update(recipe.name for recipe in book.recipes)
    return tuple(sorted(names))


async def spawn(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    type_key: str,
    amount: float = 1,
    quality: float | None = None,
) -> Item:
    """Print a thing straight into the hands of this body.

    Into the hands and nowhere else: a chest, a machine and somebody else's
    node all have holders, doors and capacity, and a tool that walked past
    those would be testing a world other than the one being played. What is
    needed elsewhere is carried there the ordinary way.

    Quality is optional -- without it the thing has none, exactly as raw
    material out of a vein has none. With it, it is checked against the scale
    from the vault rather than a number written here: the scale is content
    (`quality.scale`), and a tool that knew better than the vault would be the
    second copy of it.

    **A liquid is poured, not handed over** (D-230): it goes into the vessels
    in the hands and then into those standing here, exactly as a batch's liquid
    output does. Printed into the bare hands it would be matter in a state this
    world has no place for -- unpourable, unusable, and heavy: `pour` moves a
    liquid between vessels, so a stack that started outside one could never
    reach a tank at all.
    """
    name = catalog.recipes.resolve(type_key)
    if name not in known(catalog):
        raise NoSuchThing(f"такой вещи в этом мире нет: {type_key}")
    if amount <= 0:
        raise AlphaError("количество должно быть больше нуля")
    if amount > AMOUNT_MAX:
        #: Not a balance ceiling -- the width of the amount column. Past it the
        #: insert overflows, and the player would get "the server failed"
        #: instead of a refusal in words.
        raise AlphaError(f"столько не бывает: не больше {AMOUNT_MAX}")
    if quality is not None:
        scale = constants[R.QUALITY_SCALE]
        if not scale.min <= quality <= scale.max:
            raise AlphaError(f"качество — от {scale.min:g} до {scale.max:g}")

    where = await world.body_container(session, body)
    item = await world.grant_item(
        session,
        where,
        name,
        amount=amount,
        quality=quality,
        origin=ORIGIN,
        #: Deliberately not stamped as made by anybody. A maker's mark is a
        #: claim about work that was not done, and it is read by the market
        #: and by reputation; the actor is named in the event below instead.
        maker_identity_id=None,
    )
    if liquid.is_liquid(catalog, name):
        item = await _poured(session, catalog, body, item, name)
    await events.record(
        session,
        EventKind.ALPHA_SPAWNED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        type_key=name,
        amount=amount,
        quality=quality,
    )
    return item


async def _poured(
    session: AsyncSession,
    catalog: Catalog,
    body: Body,
    item: Item,
    name: str,
) -> Item:
    """Send a printed liquid into the vessels within reach, or refuse.

    The vessels are the same two the workshop pours into (`craft.batch`): the
    hands first, then what stands in the node -- and of those, only the ones
    the printer may open, by the same question `pour` asks (D-181). Nothing
    fits -- the print is refused whole rather than spilled. Spilling is the
    honest end of work already done; here no work was done, so the tester is
    told to bring a canister instead of being charged for one.

    The stack itself survives the pouring: a fold keeps the arriving stack and
    deletes the twin it met (`world.stack_up`), and the one path that deletes
    this one -- a spill -- is refused above. So what comes back is the row that
    was printed, wherever it now stands.
    """
    within = [await world.body_container(session, body)]
    here = await session.get(Node, body.node_id)
    if here is not None and await station.may_build(session, body, here):
        within.append(await world.node_container(session, here))
    if await liquid.settle(session, catalog, item, within) > 0:
        raise AlphaError(
            f"«{name}» — жидкость, и налить её некуда: возьмите канистру в руки "
            "или встаньте там, где стоит бак. В ладонях жидкость не живёт"
        )
    return item


async def hurry(
    session: AsyncSession,
    identity_id: uuid.UUID,
    body: Body | None = None,
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Bring this player's running terms up to now. Returns the kinds moved.

    The work itself is not done here -- the term is moved and the journal
    handler finishes it on its next pass, within `WORKER_IDLE_SLEEP`. So a
    hurried survey rolls its find the same way a waited-out one does, and a
    hurried batch spoils on bad inputs exactly as it would have.

    Two ways of owning a term, and both are asked for by name rather than
    trusted to the kind alone: the works of a pair of hands, found by the
    `body_id` they were queued with, and the printing of a body, found by the
    identity in its payload. Without a body only the second is possible, and
    that is exactly the state the second exists for.

    A job the worker already holds is skipped rather than waited for: it is
    being finished as we ask, and there is nothing left to hurry. That is what
    `skip_locked` says here -- not "ignore contention" but "already running".
    """
    if body is not None and body.identity_id != identity_id:
        #: The whole guarantee of this lever is "your own terms and nobody
        #: else's", and it would otherwise rest on every caller passing a
        #: matching pair. One layer up is where the pair is assembled; this is
        #: where the invariant belongs.
        raise AlphaError("это тело не этой личности")
    moment = now or datetime.now(UTC)
    owned = [
        and_(
            Job.kind.in_(BY_IDENTITY),
            Job.payload["identity"].astext == str(identity_id),
        )
    ]
    if body is not None:
        owned.append(and_(Job.body_id == body.id, Job.kind.in_(HURRIED)))
    stmt = (
        select(Job)
        .where(
            or_(*owned),
            Job.state == JobState.PENDING,
            Job.run_at > moment,
        )
        #: The term is a deadline the worker races us for: it selects pending
        #: jobs by `run_at` under `FOR UPDATE SKIP LOCKED` and claims them.
        #: Moving `run_at` without the same lock would write into a row the
        #: worker is already carrying out.
        .with_for_update(skip_locked=True)
        #: A lock alone does not refresh an object already in the identity
        #: map, and the checks below read `state` off these rows.
        .execution_options(populate_existing=True)
    )
    jobs = (await session.execute(stmt)).scalars().all()

    moved: list[str] = []
    for job in jobs:
        if job.kind == JobKind.TRAVEL_LEG.value and not await _arrive_now(session, job, moment):
            continue
        if job.kind == JobKind.CRAFT_BATCH.value and not await _finish_now(session, job, moment):
            continue
        job.run_at = moment
        moved.append(job.kind)
    if not moved:
        return ()

    await session.flush()
    await events.record(
        session,
        EventKind.ALPHA_HURRIED,
        actor_identity_id=identity_id,
        #: In the cloud there is no node to stamp: the asker is nowhere, which
        #: is the whole of what "no body" means.
        node_id=None if body is None else body.node_id,
        kinds=moved,
    )
    return tuple(moved)


async def _arrive_now(session: AsyncSession, job: Job, moment: datetime) -> bool:
    """Pull the passage's own term up with the job's.

    The term lives in two places -- the job fires by `run_at`, the client draws
    the bar by `Travel.arrives_at` -- and moving one alone leaves the traveller
    watching a countdown for a road already walked.
    """
    travel = await session.get(
        Travel,
        uuid.UUID(job.payload["travel"]),
        with_for_update=True,
        populate_existing=True,
    )
    if travel is None or travel.state is not TravelState.GOING:
        #: The passage was turned back or already arrived; the job left over
        #: from it is the handler's business, not ours.
        return False
    travel.arrives_at = moment
    return True


async def _finish_now(session: AsyncSession, job: Job, moment: datetime) -> bool:
    """Pull the batch's own term up with the job's.

    A batch frozen while the master was away has no term at all -- it holds
    the work left instead (D-209), and its own job is queued when it resumes.
    Each run has its own job, and the handler refuses one whose run number is
    not the current one; hurrying such a leftover would move a term nobody is
    waiting on.
    """
    batch = await session.get(
        CraftBatch,
        uuid.UUID(job.payload["batch"]),
        with_for_update=True,
        populate_existing=True,
    )
    if batch is None or batch.state is not BatchState.RUNNING:
        return False
    if job.payload.get("run", batch.runs) != batch.runs:
        return False
    batch.ready_at = moment
    #: The machine's booking is deliberately left alone. Pulling `busy_until`
    #: up with the term would read as tidiness and is a hole: `_pick_station`
    #: counts a machine free once the stamp has passed (D-150), so for the
    #: second until the handler runs, another master can take this bench --
    #: and then our handler's `_release` wipes their fresh booking, putting
    #: two batches on one machine. The bench reads as taken until an hour that
    #: no longer means anything, for one second, and that is the cheaper of
    #: the two prices.
    return True
