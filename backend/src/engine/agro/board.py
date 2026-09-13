# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton's board (D-339): setting its programme, its plots and
its storages, taking the programme off, and reading what the machines of a
yard are set to.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, travel, world
from src.engine.agro._base import (
    FIELD_AUTOMAT,
    AgroError,
    BadPlot,
    _machine_here,
    _store_here,
    of_item,
    parse,
)
from src.engine.agro.run import advance
from src.models.agro import FieldAutomat, FieldAutomatPlot
from src.models.event import EventKind
from src.models.farm import Plot
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node

log = logging.getLogger(__name__)


def _ids(raw: object) -> list[uuid.UUID]:
    if not isinstance(raw, list):
        raise BadPlot(key="agro-bad-plots")
    try:
        return [uuid.UUID(str(each)) for each in raw]
    except ValueError as wrong:
        raise BadPlot(key="agro-bad-plots") from wrong


async def _plots(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    machine: FieldAutomat | None,
    ids: Sequence[uuid.UUID],
) -> list[Plot]:
    """The plots the owner gives, checked one by one (D-339 p. 6).

    Their own, in this node, no smaller than the machine takes, at most as
    many as it serves, and not standing on another machine: two machines
    holding one bed's moisture would water it twice.
    """
    if len(set(ids)) != len(ids):
        raise BadPlot(key="agro-bad-plots")
    most = int(constants[R.AGRO_PLOTS_MAX])
    if len(ids) > most:
        raise BadPlot(key="agro-too-many-plots", most=most)
    smallest = constants[R.AGRO_PLOT_MIN_AREA]
    found: list[Plot] = []
    for plot_id in ids:
        plot = await session.get(Plot, plot_id)
        if plot is None or plot.node_id != node.id or plot.owner_identity_id != body.identity_id:
            raise BadPlot(key="agro-plot-not-yours")
        if float(plot.area_m2) < smallest:
            raise BadPlot(key="agro-plot-small", plot=plot.name, min=smallest)
        taken = await session.scalar(
            select(FieldAutomatPlot.automat_id).where(FieldAutomatPlot.plot_id == plot_id)
        )
        if taken is not None and (machine is None or taken != machine.id):
            raise BadPlot(key="agro-plot-taken", plot=plot.name)
        found.append(plot)
    return found


async def program(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    steps: object,
    plots: object,
    seeds: uuid.UUID | None,
    fertilizer: uuid.UUID | None,
    harvest: uuid.UUID | None,
    now: datetime | None = None,
) -> FieldAutomat:
    """Set the machine whole: programme, plots and storages, in one command.

    The old programme is worked up to now first -- the energy, lubricant and
    wear it owes are its own. A programme that changed starts from its first
    line; plots or storages changed alone leave the cursor where it stands.
    Idempotent: sending what the machine already holds changes nothing.
    """
    moment = now or datetime.now(UTC)
    node = await _machine_here(session, body, item)
    rows = parse(constants, catalog, steps)
    plot_ids = _ids(plots)
    stores = {
        "seeds": await _store_here(session, catalog, node, seeds),
        "fertilizer": await _store_here(session, catalog, node, fertilizer),
        "harvest": await _store_here(session, catalog, node, harvest),
    }

    row = await of_item(session, item)
    if row is not None:
        #: Locked before the savepoint: a lock taken inside one is let go
        #: when it rolls back, and the row is rewritten below.
        await session.refresh(row, with_for_update=True)
        await _settle_old(session, constants, catalog, row, item, moment)
        row = await of_item(session, item)
    #: The advance may have worn the machine to nothing: a programme is not
    #: loaded into a thing that is no more.
    if await session.get(Item, item.id) is None:
        raise AgroError(key="agro-machine-gone", goods=item.type_key)
    given = await _plots(session, constants, body, node, row, plot_ids)
    if row is None:
        row = FieldAutomat(
            item_id=item.id,
            node_id=node.id,
            owner_identity_id=body.identity_id,
            program=rows,
            cursor=0,
            step_since=moment,
            counted_at=moment,
        )
        session.add(row)
        await session.flush()
    elif row.program != rows:
        row.program = rows
        row.cursor = 0
        row.step_since = moment
        row.trouble = None
        row.told = None
    row.owner_identity_id = body.identity_id
    #: A machine taken down and put up in another yard works there: the row
    #: follows the machine (the automat's own rule, `automat.board.program`).
    row.node_id = node.id
    row.seeds_item_id = None if stores["seeds"] is None else stores["seeds"].id
    row.fertilizer_item_id = None if stores["fertilizer"] is None else stores["fertilizer"].id
    row.harvest_item_id = None if stores["harvest"] is None else stores["harvest"].id

    await session.execute(delete(FieldAutomatPlot).where(FieldAutomatPlot.automat_id == row.id))
    await session.flush()
    for rank, plot in enumerate(given):
        session.add(FieldAutomatPlot(automat_id=row.id, plot_id=plot.id, rank=rank))
    await session.flush()

    await events.record(
        session,
        EventKind.AGRO_PROGRAMMED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        machine=str(item.id),
        steps=len(rows),
        plots=len(given),
    )
    return row


async def _settle_old(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    row: FieldAutomat,
    machine: Item,
    now: datetime,
) -> None:
    """Work the old programme up to now -- in a savepoint of its own.

    A programme the vault has since broken (a culture dropped, a lot without
    its cultivar) must not lock its owner out of the one door that fixes it:
    what fails is logged and the owner's command goes on.
    """
    machine_id = machine.id
    try:
        async with session.begin_nested():
            await advance(session, constants, row, catalog=catalog, now=now)
    except Exception:  # noqa: BLE001 -- the owner's command must reach a broken machine
        log.exception("field automat %s: the old programme failed to settle", machine_id)
        #: The rolled-back savepoint expired what it touched -- the machine wore
        #: in it -- and the command reads it again below.
        #: A machine gone meanwhile is the caller's to say.
        with contextlib.suppress(InvalidRequestError):
            await session.refresh(machine)


async def stop(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> bool:
    """Take the programme off: the machine stays, the beds go on without it.

    What it owes up to now is worked first. Returns whether there was a
    programme to take off.
    """
    moment = now or datetime.now(UTC)
    node = await _machine_here(session, body, item)
    row = await of_item(session, item)
    if row is None:
        return False
    await session.refresh(row, with_for_update=True)
    await _settle_old(session, constants, catalog, row, item, moment)
    row = await of_item(session, item)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    await events.record(
        session,
        EventKind.AGRO_STOPPED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        machine=str(item.id),
    )
    return True


async def view(session: AsyncSession, body: Body) -> dict[str, Any]:
    """The field automatons set in this yard: programme, cursor, plots, storages, trouble.

    A read (D-225): the machines themselves and what lies in the bunker come
    with the node view; the beds with the farm's survey.
    """
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    #: `node_yard`, not `node_container`: a read does not make the yard it
    #: finds missing (db/readonly.py), and a node with no yard has no machine.
    yard = None if node is None else await world.node_yard(session, node)
    if node is None or yard is None:
        return {"machines": []}
    rows = (
        (
            await session.execute(
                select(FieldAutomat)
                .join(Item, Item.id == FieldAutomat.item_id)
                .where(FieldAutomat.node_id == node.id, Item.container_id == yard.id)
                .where(Item.type_key.in_(world.station_names(FIELD_AUTOMAT)))
                .order_by(FieldAutomat.created_at)
            )
        )
        .scalars()
        .all()
    )
    links = (
        (
            await session.execute(
                select(FieldAutomatPlot)
                .where(FieldAutomatPlot.automat_id.in_([row.id for row in rows]))
                .order_by(FieldAutomatPlot.rank)
            )
        )
        .scalars()
        .all()
        if rows
        else []
    )
    plots: dict[uuid.UUID, list[str]] = {}
    for link in links:
        plots.setdefault(link.automat_id, []).append(str(link.plot_id))
    return {
        "machines": [
            {
                "item": str(row.item_id),
                "program": row.program,
                "cursor": row.cursor,
                "plots": plots.get(row.id, []),
                "seeds": None if row.seeds_item_id is None else str(row.seeds_item_id),
                "fertilizer": None
                if row.fertilizer_item_id is None
                else str(row.fertilizer_item_id),
                "harvest": None if row.harvest_item_id is None else str(row.harvest_item_id),
                "trouble": row.trouble,
            }
            for row in rows
        ]
    }
