# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton's hands (D-339): one action on one plot, done the way
the farmer's hand does it and through the farm's own cores -- the same litres,
the same dose, the same effect on the bed -- but taken from the yard and the
storages its owner named instead of a pocket.

Each action runs under the plot's lock, judges the bed again under it (a hand
may have come while the plan was read free) and answers with the minutes it
took, or with the word the machine stands with, or with `None` when there is
nothing left to do after all.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import breed, events, farm, gear, stock, storage, world
from src.engine.agro._base import (
    NO_FERTILIZER,
    NO_SEEDS,
    NO_STORE,
    NO_WATER,
    STORE_FULL,
    UNFIT,
    is_store,
)
from src.models.agro import FieldAutomat
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.inventory import Item
from src.models.world import Node
from src.units import PERCENT, ROUND_QUALITY, SCALE_MIN, amount, amount_float, on_grid


@dataclass(frozen=True)
class Done:
    """An action done: the minutes it holds the machine for."""

    minutes: float


#: What an action answers: done, a trouble word, or nothing to do after all.
Outcome = Done | str | None


@dataclass
class Shift:
    """One advance of one machine: where it stands, what it holds, and when.

    `water` is the yard's water, locked by the advance in its one query with
    the lubricant (stock.py: one query, one lock order); by a river it is empty
    and never asked. `epoch` is the sky's, for reading a bed's clock.
    """

    session: AsyncSession
    constants: Constants
    catalog: Catalog
    row: FieldAutomat
    node: Node
    yard_id: uuid.UUID
    water: list[Item]
    epoch: datetime | None
    now: datetime

    # --- the actions ----------------------------------------------------------

    async def plow(self, plot_id: uuid.UUID) -> Outcome:
        """Turn a strip over at once; the machine is held for what the plough still owed.

        An idle strip, or one a hand paused under the plough and left (D-277);
        a plough still running is the hand's, and waited for.
        """
        plot = await self._locked(plot_id)
        if plot is None or not (plot.state is PlotState.IDLE or farm.plow_paused(plot)):
            return None
        left = farm.turn_over(self.constants, plot, self.now)
        await self.session.flush()
        await self._told(plot, "plow")
        return Done(left)

    async def sow(self, plot_id: uuid.UUID, culture: str) -> Outcome:
        """Sow a ploughed strip with a lot of the culture from the seed store.

        The place is asked first (D-261): a culture the place refuses is a
        trouble, not seeds thrown away. Then one lot with enough for the strip,
        the oldest first -- the machine does not pick the strong from the weak.
        """
        plot = await self._locked(plot_id)
        if plot is None or plot.state is not PlotState.PLOWED:
            return None
        plant = self.catalog.plants.by_id(culture)
        try:
            await farm.climate_gate(self.session, self.constants, self.node, plant, self.now)
        except farm.WrongClimate:
            return UNFIT
        store = await self._store(self.row.seeds_item_id)
        if isinstance(store, str):
            return store
        hold = await storage.inside(self.session, store, create=False)
        need = amount(self.constants[R.FARM_SEED_RATE] * float(plot.area_m2))
        found = (
            [] if hold is None else await stock.locked_stacks(self.session, hold.id, (plant.seed,))
        )
        lots = [lot for lot in found if lot.variety_id is not None and lot.amount >= need]
        if not lots:
            return NO_SEEDS
        lot = min(lots, key=lambda each: (each.created_at, str(each.id)))
        variety = await breed.variety_of(self.session, lot)
        strength = float(lot.vigor) if lot.vigor is not None else PERCENT
        lot.amount -= need
        if lot.amount <= 0:
            await self.session.delete(lot)
        farm.seed_bed(self.constants, plot, plant, variety, strength, self.now)
        await self.session.flush()
        await events.record(
            self.session,
            EventKind.PLOT_SOWN,
            actor_identity_id=self.row.owner_identity_id,
            node_id=plot.node_id,
            plot_id=str(plot.id),
            culture=plant.id,
            variety=str(variety.id),
            vigor=strength,
            seeds=amount_float(need),
            machine=str(self.row.item_id),
        )
        return self._care(plot)

    async def water_to(self, plot_id: uuid.UUID, target: float) -> Outcome:
        """Bring the bed back to the setpoint once it fell `agro.moisture_band` below it.

        The litres are the hand's formula (D-296); by a river they cost
        nothing, elsewhere they come out of the yard's vessels.
        """
        plot = await self._locked(plot_id)
        seen = None if plot is None else await self._seen(plot)
        if plot is None or seen is None:
            return None
        if seen.moisture > target - self.constants[R.AGRO_MOISTURE_BAND]:
            return None
        litres = farm.water_litres(self.constants, plot, seen.moisture, target)
        carried = not world.has_place(self.node, world.WATER)
        need = amount(litres)
        if carried and sum(stack.amount for stack in self.water) < need:
            #: Asked of the clock read, not of a settled bed: a dry yard must
            #: not rewrite every bed it cannot water, minute by minute.
            return NO_WATER
        if await self._growing(plot) is None:  # pragma: no cover -- the read and the settle agree
            return None
        if carried:
            await stock.consume(self.session, self.water, need)
        plot.moisture = on_grid(target, ROUND_QUALITY)
        await self.session.flush()
        await self._told(plot, "water")
        return self._care(plot)

    async def feed(self, plot_id: uuid.UUID, goods: str, stage: str) -> Outcome:
        """Give the dose in the named stage, if nothing fed the stage yet.

        What the dose does is the culture's table, the hand's effect exactly
        (D-296); a burn to nought kills the bed the moment it is given.
        """
        plot = await self._locked(plot_id)
        seen = None if plot is None else await self._seen(plot)
        if plot is None or seen is None or seen.ripe:
            return None
        if farm.stage_of(self.constants, seen.growth) != stage:
            return None
        #: A stage fed is closed, whoever fed it (D-339 p. 4).
        if (plot.fed or {}).get(stage):
            return None
        store = await self._store(self.row.fertilizer_item_id)
        if isinstance(store, str):
            return store
        hold = await storage.inside(self.session, store, create=False)
        dose = amount(self.constants[R.FARM_FERTILIZER_PER_M2] * float(plot.area_m2))
        stacks = (
            []
            if hold is None
            else await stock.locked_stacks(self.session, hold.id, (goods,), worst_first=True)
        )
        if sum(stack.amount for stack in stacks) < dose:
            return NO_FERTILIZER
        state = await self._growing(plot)
        if state is None:  # pragma: no cover -- the read and the settle agree
            return None
        await stock.consume(self.session, stacks, dose)
        plant, _ = await farm.sown_of(self.session, self.catalog, plot)
        farm.feed_effect(self.constants, plot, plant, state, stage, goods)
        await self.session.flush()
        if float(plot.health) <= SCALE_MIN:
            await farm.die(self.session, self.constants, plot, plant, self.now)
        await self._told(plot, "feed")
        return self._care(plot)

    async def weed(self, plot_id: uuid.UUID, every: timedelta) -> Outcome:
        """Pull the weeds the machine cannot see, because the calendar says so.

        The calendar is asked again under the lock: a hand that weeded between
        the plan's free read and now restarted the count (D-339 p. 4).
        """
        plot = await self._locked(plot_id)
        if plot is None:
            return None
        since = plot.weeded_at or plot.sown_at
        if since is None or self.now - since < every:
            return None
        state = await self._growing(plot)
        if state is None:
            return None
        farm.pull_weeds(plot, state, self.now)
        await self.session.flush()
        await self._told(plot, "weed")
        return self._care(plot)

    async def thin(self, plot_id: uuid.UUID) -> Outcome:
        """Thin the stand once, while the window is open (D-297)."""
        plot = await self._locked(plot_id)
        state = None if plot is None else await self._growing(plot)
        if plot is None or state is None:
            return None
        try:
            farm.thin_stand(self.constants, plot, state)
        except farm.WrongState:
            return None
        await self.session.flush()
        await self._told(plot, "thin")
        return self._care(plot)

    async def harvest(self, plot_id: uuid.UUID) -> Outcome:
        """Reap a ripe bed into the harvest store: the machine's share, its
        ceiling, no selection (D-120, D-339).

        What does not fit is not reaped at all -- the bed stands ripe and
        drinks on until the store is emptied; a harvest half in the chest and
        half on the ground would be a way round the store's capacity.
        """
        plot = await self._locked(plot_id)
        seen = None if plot is None else await self._seen(plot)
        if plot is None or seen is None or not seen.ripe:
            return None
        plant, found = await farm.sown_of(self.session, self.catalog, plot)
        variety = found or await breed.landrace(self.session, self.catalog, plant.id)
        signs = farm.signs_of(plant, variety)
        crop = farm.crop_of(self.constants, plot, plant, signs, seen).scaled(
            self.constants[R.AGRO_YIELD_SHARE] / PERCENT, self.constants[R.AGRO_QUALITY_CAP]
        )
        store = await self._store(self.row.harvest_item_id)
        if isinstance(store, str):
            return store
        weight = gear.mass_of(self.catalog, plant.gives, crop.goods) + gear.mass_of(
            self.catalog, plant.seed, crop.seeds
        )
        limit = storage.capacity(self.catalog, store.type_key) or 0.0
        if weight > limit - await storage.stored_mass(self.session, self.catalog, store):
            return STORE_FULL
        #: The bed is settled only now that it is reaped: the clock read and the
        #: settle walk the same steps from the same stamp (`farm.peek`).
        state = await self._growing(plot)
        if state is None:  # pragma: no cover -- the read and the settle agree
            return None
        hold = await storage.inside(self.session, store)
        assert hold is not None
        health = state.health
        await farm.reap(
            self.session,
            self.constants,
            self.catalog,
            plot,
            plant,
            variety,
            crop,
            hold.id,
            select_seed=False,
            moment=self.now,
        )
        await events.record(
            self.session,
            EventKind.PLOT_HARVESTED,
            actor_identity_id=self.row.owner_identity_id,
            node_id=plot.node_id,
            plot_id=str(plot.id),
            culture=plant.id,
            variety=str(variety.id),
            selected=False,
            got=crop.goods,
            seeds=crop.seeds,
            quality=crop.quality,
            health=health,
            fertility=float(plot.fertility),
            machine=str(self.row.item_id),
        )
        return self._care(plot)

    # --- the floor of every action --------------------------------------------

    async def _locked(self, plot_id: uuid.UUID) -> Plot | None:
        """The plot under the machine's hands -- or None when a hand or the plots'
        tick holds it right now: the machine does not wait on a bed, it comes
        back next minute. Waiting would hold the machine's yard and store while
        the tick of the plots, taking beds in its own order, waited on them."""
        return (
            await self.session.execute(
                select(Plot)
                .where(Plot.id == plot_id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _seen(self, plot: Plot) -> farm.Life | None:
        """The bed's life at now, read and not written, if it still grows."""
        if plot.state is not PlotState.SOWN or plot.culture_id is None:
            return None
        plant, variety = await farm.sown_of(self.session, self.catalog, plot)
        seen = farm.peek(
            self.constants,
            plant,
            farm.signs_of(plant, variety),
            self.node,
            self.epoch,
            plot,
            self.now,
        )
        return None if seen.dead else seen

    async def _growing(self, plot: Plot) -> farm.Life | None:
        """The bed settled to now, if it still grows."""
        if plot.state is not PlotState.SOWN or plot.culture_id is None:
            return None
        state = await farm.settle(
            self.session, self.constants, self.catalog, plot, now=self.now, node=self.node
        )
        if plot.state is not PlotState.SOWN or state.dead:
            return None
        return state

    async def _store(self, item_id: uuid.UUID | None) -> Item | str:
        """The named storage, locked and still standing in the yard -- or `no_store`.

        Locked and refreshed as the storage door locks it (D-181, D-313): a
        chest filled or emptied by a hand while the machine reads what room is
        left would walk round its capacity.
        """
        thing = None if item_id is None else await self.session.get(Item, item_id)
        if thing is None:
            return NO_STORE
        try:
            await self.session.refresh(thing, with_for_update=True)
        except InvalidRequestError:  # pragma: no cover -- gone between the read and the lock
            return NO_STORE
        if thing.container_id != self.yard_id or not is_store(self.catalog, thing):
            return NO_STORE
        return thing

    def _care(self, plot: Plot) -> Done:
        """An action of care holds the machine for a hand's minutes (D-296)."""
        return Done(farm.care_minutes(self.constants, float(plot.area_m2)))

    async def _told(self, plot: Plot, work: str) -> None:
        """Told, not journaled (D-227), like an automat's payout: the owner
        watching the garden sees the bed change without acting, and a machine's
        waterings stay out of the journal. The sowing and the harvest are
        journaled -- the ends of a season, told to the owner on return."""
        if self.row.owner_identity_id is None:
            return
        await events.announce(
            self.session,
            touches=("farm",),
            identity_id=self.row.owner_identity_id,
            event="agro.worked",
            work=work,
            plot=str(plot.id),
        )
