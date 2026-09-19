# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city the catch-up founded on a planet's sphere, taken down (D-356, item 10).

Since D-330 a city stands on its own printer, so the capital's node is the
core, and the core hangs on Terra's sphere. The catch-up went on reading the
core's parent as the capital, and at the first deploy of a world laid after
D-330 it found no city on the sphere and founded one there: a city named
after the planet, a treasury filled from `genesis`, the capital's flag -- and
at every deploy after, every unowned node hanging on the sphere written to it
as its built-up area. The alpha lived with it from 2026-09-17 (the vault's
D-356 says what it held there).

`seed_catchup` no longer reads it so; this takes down what it left. Split out
of `seed_catchup` because that file is at the size bar, and because the step
is its own thing: a city is not dissolved anywhere in the game, and nothing
here is a rule a player could meet.

What goes, and why each is safe to take:

* **its land**: `owner_city_id` off every node it holds, and the distance to
  a printer the tick measured for it (`estate.measure_cities`) -- wild ground
  carries none, and ground a real city takes later is measured by its tick;
* **its members**: offices, council seats, grants paid, citizenships and
  requests for one, and the term jobs of its offices. A citizen of it is left
  with no city -- the state the defect took them from; a grant it paid stays
  in the pocket it was paid into;
* **its official channel**, and with it the posts and the subscriptions
  (`ON DELETE CASCADE`);
* **its daily figures** on the panel (`city.<id>.*`);
* **its treasury**: whatever is left goes back to `genesis`, the one lawful
  sink of the money supply, by a posting of its own. The account stays, empty:
  the book only grows (`db.ddl`), and its entries name it;
* **the row itself**.

What stays: the journal. `event` only grows as well, so `city.founded` and the
`customs.crossed` lines naming it keep a city id that no longer answers --
readers of those ids already live with that (a deleted city is not new to
`customs.traffic` or to a term's end).

What it will not settle by itself is land somebody holds from it and the
business of a city -- a loan, a print, a case, a sanction, a vote, a public
work. None of it is possible without an office, and a city the catch-up
founded has none; if one is there all the same, the step says so in the log,
takes nothing down, and asks again at the next deploy.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_once
from src.db.base import forget
from src.engine import city as town
from src.engine import ledger
from src.models.bank import Loan
from src.models.city import Citizen, CitizenshipRequest, City, CityGrant, CouncilSeat, Office
from src.models.emission import EmissionProposal
from src.models.job import Job
from src.models.justice import Case, Sanction
from src.models.ledger import AccountKind, PostingReason
from src.models.metrics import DailyMetric
from src.models.net import NetChannel
from src.models.vote import Vote
from src.models.works import WorkOrder
from src.models.world import Layer, Node

log = logging.getLogger("everselife.seed")

#: A city's business: rows that carry money or a process the step cannot undo
#: without deciding for somebody. Any of them stops it.
BUSINESS = (Loan, EmissionProposal, Case, Sanction, Vote, WorkOrder)

#: Who belongs to the city: rows that mean membership and nothing more, and go
#: with it. Deleted in this order; none of them points at another.
MEMBERS = (Office, CouncilSeat, CityGrant, Citizen, CitizenshipRequest)


async def take_down(session: AsyncSession) -> list[dict[str, Any]] | None:
    """Take down every city that does not stand on a planet's surface.

    Returns what was taken, city by city -- an empty list when there was none,
    which is every world laid fresh or before D-330 -- or `None` when one of
    them has land held from it or business of its own: then nothing is taken
    and the caller does not mark the step done.

    A city off the surface is the defect and only the defect: a player founds
    on the surface only (`city.establish`), and the seed founds the cities
    its layout names, on nodes of the surface.
    """
    standing = (
        await session.execute(
            select(City, Node)
            .join(Node, Node.id == City.node_id)
            .where(Node.layer != Layer.PLANET)
            .order_by(City.id)
            .with_for_update(of=City)
        )
    ).all()

    waiting = False
    for city, home in standing:
        held = (
            (
                await session.execute(
                    select(Node.key)
                    .where(Node.owner_city_id == city.id, Node.owner_identity_id.is_not(None))
                    .order_by(Node.key)
                )
            )
            .scalars()
            .all()
        )
        business = {
            model.__tablename__: count
            for model in BUSINESS
            if (
                count := await session.scalar(
                    select(func.count()).select_from(model).where(model.city_id == city.id)
                )
            )
        }
        if held or business:
            waiting = True
            log.error(
                "city %r on %s (layer %s) is not taken down: land held from it %s, "
                "business %s. Nothing of it was touched; the step runs again at the "
                "next deploy, and the owner decides what becomes of these first",
                city.name,
                home.key,
                home.layer,
                list(held),
                business,
            )
    if waiting:
        return None
    return [await _take_down(session, city, home) for city, home in standing]


async def _take_down(session: AsyncSession, city: City, home: Node) -> dict[str, Any]:
    """Take one city down; what was taken, for the step's row."""
    #: The land first, in id order -- the order the tick's measuring writes the
    #: same rows in (the unit of work flushes updates by primary key), so the
    #: deploy and a live world's tick queue on them rather than cross.
    await session.execute(
        select(Node.id).where(Node.owner_city_id == city.id).order_by(Node.id).with_for_update()
    )
    land = (
        (
            await session.execute(
                update(Node)
                .where(Node.owner_city_id == city.id)
                .values(owner_city_id=None, center_node_id=None, center_steps=None)
                .returning(Node.key)
            )
        )
        .scalars()
        .all()
    )
    #: Its own node was measured with its land (`estate.price._owned`).
    home.center_node_id = None
    home.center_steps = None

    members = {}
    for model in MEMBERS:
        gone = await session.execute(delete(model).where(model.city_id == city.id))
        members[model.__tablename__] = gone.rowcount or 0
    #: An office's term is a job keyed by the city (`city.succession`); with
    #: no office left it would end a term nobody serves.
    jobs = await session.execute(delete(Job).where(Job.payload["city"].astext == str(city.id)))
    channel = await session.execute(delete(NetChannel).where(NetChannel.city_id == city.id))
    figures = await session.execute(
        delete(DailyMetric).where(DailyMetric.key.startswith(f"city.{city.id}."))
    )

    #: The treasury last: accounts close every lock order (`ledger.lock_accounts`).
    #: Locked before its balance is read, so a payout from it queues behind the
    #: return rather than both spending the same money. A credit does not queue
    #: -- none does -- and one in flight at this very second, from a command
    #: that found the city before it was gone, lands on the emptied account;
    #: the book shows it there.
    treasury = await town.treasury(session, city)
    await ledger.lock_accounts(session, [treasury.id])
    returned = await ledger.balance(session, treasury.id)
    if returned > 0:
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=treasury.id,
            credit=genesis.id,
            amount=returned,
            memo={"step": seed_once.SPHERE_CITY_TAKEN_DOWN, "city": city.name, "node": home.key},
        )

    taken = {
        "city": str(city.id),
        "name": city.name,
        "node": home.key,
        "capital": city.capital,
        "land": sorted(land),
        "members": members,
        "jobs": jobs.rowcount or 0,
        "channel": channel.rowcount or 0,
        "figures": figures.rowcount or 0,
        "treasury": str(treasury.id),
        "returned": returned,
    }
    await session.delete(city)
    await session.flush()
    #: `world.is_city_node` remembers its answer for the command; the city it
    #: remembered is gone.
    forget(session)
    log.info(
        "city %r on %s taken down: %d nodes let go, %d returned to genesis",
        taken["name"],
        home.key,
        len(land),
        returned,
    )
    return taken
