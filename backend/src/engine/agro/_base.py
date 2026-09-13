# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton's vocabulary and floor (D-339): the commands of a
programme and how one is read, the words the machine stands with, every
refusal a programme can meet, and the guards -- which machine stands here and
what storage of the yard it may name. Asks nobody above itself.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import station, storage, travel, world
from src.engine.errors import Refusal
from src.engine.farm import FERTILIZER, RIPE, STAGES
from src.models.agro import FieldAutomat
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node
from src.units import SCALE_MAX, SCALE_MIN

#: The field automaton thing class (D-339): its members come from the vault,
#: and its window opens by this class on the client.
FIELD_AUTOMAT = "field_automaton"

#: The closed list of commands (D-120, D-339). The four actions hold the
#: cursor until they are done on every plot; the four setpoints take a line
#: and no time, and hold their whole season, from harvest to harvest.
PLOW = "plow"
SOW = "sow"
MOISTURE = "moisture"
FEED = "feed"
WEED = "weed"
THIN = "thin"
HARVEST = "harvest"
FALLOW = "fallow"
ACTIONS: tuple[str, ...] = (PLOW, SOW, HARVEST, FALLOW)
SETPOINTS: tuple[str, ...] = (MOISTURE, FEED, WEED, THIN)
COMMANDS: tuple[str, ...] = (PLOW, SOW, MOISTURE, FEED, WEED, THIN, HARVEST, FALLOW)

#: Why a machine stands (D-339 p. 11): the words the window and the journal
#: translate. Stored as is on the row, so a rename here is a migration.
NO_PLOTS = "no_plots"
NO_POWER = "no_power"
NO_LUBE = "no_lube"
NO_WATER = "no_water"
NO_SEEDS = "no_seeds"
NO_FERTILIZER = "no_fertilizer"
STORE_FULL = "store_full"
NO_STORE = "no_store"
NOT_PLOWED = "not_plowed"
UNFIT = "unfit"
#: The owner lost the right to the node the machine stands in (land sold, D-339).
NOT_ENTITLED = "not_entitled"
#: The advance failed -- a programme the vault has since broken; logged once.
FAULT = "fault"
TROUBLES: tuple[str, ...] = (
    NO_PLOTS,
    NO_POWER,
    NO_LUBE,
    NO_WATER,
    NO_SEEDS,
    NO_FERTILIZER,
    STORE_FULL,
    NO_STORE,
    NOT_PLOWED,
    UNFIT,
    NOT_ENTITLED,
    FAULT,
)

#: The three storages a machine is told about, by the column that keeps each.
SEEDS = "seeds"
FERTILIZERS = "fertilizer"
HARVESTS = "harvest"


class AgroError(Refusal):
    pass


class NotAFieldAutomat(AgroError):
    """Not a field automaton: a programme of plots goes into nothing else."""


class BadProgram(AgroError):
    """The programme does not read: an unknown command, a missing or wrong parameter."""


class BadPlot(AgroError):
    """A plot the machine may not take: not yours, not here, too small, or on another machine."""


class BadStore(AgroError):
    """A storage the machine may not name: not a storage, a vessel, or not in this yard."""


# --- the programme ------------------------------------------------------------


def _number(raw: dict[str, Any], key: str, index: int) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise BadProgram(key="agro-bad-parameter", line=index + 1, parameter=key)
    return float(value)


def parse(constants: Constants, catalog: Catalog, raw: object) -> list[dict[str, Any]]:
    """Read a programme as the owner sent it into the rows the row keeps.

    Every parameter the command takes and nothing else; a culture the catalog
    knows; a fertilizer by its class (D-291) in a stage it can be given in --
    ripe is not one, the hand is refused it too; days and moisture as numbers
    on their scales. A programme with no action has nothing to move its cursor
    and is refused whole (D-339 p. 2).
    """
    if not isinstance(raw, list) or not raw:
        raise BadProgram(key="agro-program-empty")
    steps = int(constants[R.AGRO_PROGRAM_STEPS])
    if len(raw) > steps:
        raise BadProgram(key="agro-program-long", steps=steps)
    rows: list[dict[str, Any]] = []
    book = catalog.recipes
    for index, line in enumerate(raw):
        kind = line.get("do") if isinstance(line, dict) else None
        if kind not in COMMANDS:
            raise BadProgram(key="agro-bad-command", line=index + 1)
        assert isinstance(line, dict)
        row: dict[str, Any] = {"do": kind}
        if kind == SOW:
            culture = line.get("culture")
            if not isinstance(culture, str) or culture not in {p.id for p in catalog.plants.plants}:
                raise BadProgram(key="agro-bad-parameter", line=index + 1, parameter="culture")
            row["culture"] = culture
        elif kind == MOISTURE:
            target = _number(line, "target", index)
            if not SCALE_MIN < target <= SCALE_MAX:
                raise BadProgram(key="agro-bad-parameter", line=index + 1, parameter="target")
            row["target"] = target
        elif kind == FEED:
            goods = line.get("goods")
            stage = line.get("stage")
            if not isinstance(goods, str) or book.class_of(book.resolve(goods)) != FERTILIZER:
                raise BadProgram(key="agro-bad-parameter", line=index + 1, parameter="goods")
            if stage not in STAGES or stage == RIPE:
                raise BadProgram(key="agro-bad-parameter", line=index + 1, parameter="stage")
            row["goods"] = book.resolve(goods)
            row["stage"] = stage
        elif kind in (WEED, FALLOW):
            days = _number(line, "days", index)
            #: Bounded above as well: a date is finite, and a fallow of ten
            #: million days would overflow the machine's clock (D-339).
            if not 0 < days <= constants[R.AGRO_DAYS_MAX]:
                raise BadProgram(
                    key="agro-bad-days", line=index + 1, most=int(constants[R.AGRO_DAYS_MAX])
                )
            row["days"] = days
        rows.append(row)
    if not any(row["do"] in ACTIONS for row in rows):
        raise BadProgram(key="agro-program-idle")
    return rows


def in_force(program: Sequence[dict[str, Any]], cursor: int) -> list[dict[str, Any]]:
    """The setpoints holding while the cursor stands on `cursor` (D-339 p. 2).

    A setpoint holds its whole season: the lines between the harvest before
    the cursor and the harvest at or after it, wherever in between the cursor
    stands. So a sowing that stalls on its last plot -- seeds for seven beds of
    eight -- does not leave the seven it sowed without the moisture written
    after it. Returned in the season's order, from the line after its harvest:
    of two setpoints of one kind the later line is the one kept.
    """
    size = len(program)
    #: With no harvest at all the season is the whole programme, read from its
    #: first line -- not from wherever the cursor happens to stand.
    start = 0
    for back in range(1, size + 1):
        if program[(cursor - back) % size]["do"] == HARVEST:
            start = (cursor - back + 1) % size
            break
    held: list[dict[str, Any]] = []
    for step in range(size):
        row = program[(start + step) % size]
        if row["do"] == HARVEST:
            break
        if row["do"] in SETPOINTS:
            held.append(row)
    return held


# --- guards -------------------------------------------------------------------


async def of_item(session: AsyncSession, item: Item) -> FieldAutomat | None:
    """The row of this machine, if it was ever programmed."""
    return (
        await session.execute(select(FieldAutomat).where(FieldAutomat.item_id == item.id))
    ).scalar_one_or_none()


async def _machine_here(session: AsyncSession, body: Body, item: Item) -> Node:
    """Alive, here, entitled, and this thing is a field automaton standing in this node.

    The door of the automats (D-253) and of a chest (D-181): whoever may
    dispose of the node sets its machines, standing in it (D-339 p. 10).
    """
    if body.state is not BodyState.ALIVE:
        raise AgroError(key="agro-dead-works")
    await travel.require_here(session, body)
    if item.type_key not in world.station_names(FIELD_AUTOMAT):
        raise NotAFieldAutomat(key="agro-not-a-field-automat", goods=item.type_key)
    if not item.installed:
        raise NotAFieldAutomat(key="agro-not-installed", goods=item.type_key)
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise AgroError(key="agro-body-off-node")
    yard = await world.node_container(session, node)
    if item.container_id != yard.id:
        raise AgroError(key="agro-not-here")
    if not await station.may_build(session, body, node):
        raise AgroError(key="agro-not-entitled")
    return node


def is_store(catalog: Catalog, item: Item) -> bool:
    """A storage for dry goods: a chest, a rack, the machine's own bunker.

    A vessel holds liquids and neither seeds nor grain go in (D-230).
    """
    return storage.is_storage(catalog, item.type_key) and not storage.is_vessel(
        catalog, item.type_key
    )


async def _store_here(
    session: AsyncSession, catalog: Catalog, node: Node, item_id: uuid.UUID | None
) -> Item | None:
    """The named storage, if it is one and stands in this yard. Refuses otherwise."""
    if item_id is None:
        return None
    thing = await session.get(Item, item_id)
    yard = await world.node_container(session, node)
    if thing is None or thing.container_id != yard.id:
        raise BadStore(key="agro-store-not-here")
    if not is_store(catalog, thing):
        raise BadStore(key="agro-not-a-store", goods=thing.type_key)
    return thing
