# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The alpha's starting world: Terra's capital as it greets the first player.

Run from `backend/`: `python -m src.seed`. Running again breaks nothing -- the
world is created once and lives its own life from then on.

**This is a development scenario, not part of the game.** Everything it puts
into hands goes through `world.grant_item` with an explicit ground: matter does
not appear in the world anonymously (pillar P1), and such an arrival must be
visible in telemetry.

## The layout is data, this file is rules (D-243)

Which nodes the capital consists of, what edges join them and for how many
seconds, what machines stand where, what lies in whose pocket -- all of that
is the vault's (`data/world.yaml`, edited by the vault editor's «Мир» tab or
by hand) and reaches the engine as `build/world.json`. `src/seed_world.py`
interprets it: lays missing nodes, ensures machines, and assembles every one
of them by recipe (D-216) -- a recipe without a composition, an input nobody
makes, a circle in the ladder each stop the world from being created, loudly.

What remains is what data cannot be, and it is split in three by the one seam
this file has -- a world is either **laid** for the first time or **caught up**
to today:

* here -- the first laying: the capital, its city as an institution (D-154),
  the two founders and what is in their pockets;
* `seed_parts` -- the pieces both ends need, each laid only if missing: the
  solar system and its orbits, the Forerunners' printer, the treasury, the base
  shelf, a building under a machine placed before buildings existed;
* `seed_catchup` -- the one-off repairs an old world is brought up to today by.

And the two honest assumptions of development time:

* **money is given to the city, not the player** -- there is no bank or credit
  before E4, and without money in the treasury there is nothing to pay the
  settlement grant from. Issue goes through the `genesis` account, i.e. it is
  visible in the invariant check. Players print with zero and get the grant by
  the city's decision (D-153);
* **coal and refined metal are given to the founders** (in the scenario's
  `pockets`) -- the mine is a twenty-minute walk, and one wants to see
  smelting and minting today.

## The shape of the world

A city is not a place but **a group of locations connected by short edges**
(D-045, D-089). A step inside the city is seconds (`travel.city_step`),
leaving the walls is `distance 1`, i.e. `travel.frontier_step` by **road**,
not offroad. Beyond that every ring of distance is pricier than the previous
(D-180) -- that is all the geography: going for a machine is cheap, going for
coal is a trip, reaching the frontier means fitting out an expedition.

The capital is created as an **institutional city** (D-154): it has a charter,
code-laws and a treasury, and the first player becomes its president.
Everything the authority changes afterwards it changes itself -- the seed only
sets the initial position.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_catchup, seed_world
from src import seed_parts as parts
from src.constants import bootstrap, current, current_catalog
from src.db.base import dispose, session_factory
from src.engine import breed, death, energy, market, ruins, tick, utility, world
from src.engine import city as town
from src.models.world import Node
from src.seed_surfaces import surfaces
from src.settings import settings
from src.units import PERCENT, money

#: The node the world grows from: the capital's core, and the one key the
#: whole of the seeding is decided by -- it stands, or the world is new.
CORE = parts.CORE

log = logging.getLogger("everselife.seed")


async def seed(session: AsyncSession) -> Node:
    """Create the starting world if it does not exist yet, otherwise bring it up to date."""
    standing = await session.execute(select(Node).where(Node.key == CORE))
    existing = standing.scalar_one_or_none()
    if existing is not None:
        log.info("the starting world already exists: %s", existing.key)
        await seed_catchup.catch_up(session, existing)
        return existing

    constants = current()

    #: Layers are a display abstraction over one graph (D-045): Terra is seen
    #: from space, the capital from the planet, the built-up area in the city.
    #: One walks on the leaves.
    await parts.system(session)

    #: The layout itself -- nodes, edges, veins, machines, stocks -- is the
    #: vault's scenario (D-243), and a fresh world is simply the case where all
    #: of it is missing.
    scenario, applied = await seed_world.apply(session, constants)
    core = applied.nodes[CORE]

    #: The Forerunners' Printer: free and twelve hours (D-028). It is also the
    #: only door into the world that never closes, hence it stands in the core.
    #: A **relic** (D-232): found, never made, never taken down -- and it is the
    #: thing itself that prints for free, not the ground under it. Laid by
    #: rules rather than the scenario: there is exactly one in the world.
    await ruins.grant_relic(
        session, core, death.PRINTER, origin="наследие Предтеч: принтер столицы"
    )
    #: A genesis library holds the base set (D-068, D-209): the capital's shelf
    #: is what the Forerunners left -- today the whole catalog; a library a
    #: city builds starts empty and fills as people bring carriers.
    await parts.shelves(session, scenario, applied)

    #: The capital is an institutional city (D-154): a charter from vault
    #: defaults, a treasury and code-laws. Everything set here the authority
    #: changes itself later. The prison and the spaceport are city land like
    #: the gate (D-176, D-206): a state location is never a "free plot".
    city = None
    for delegate in applied.city_nodes(scenario):
        founded = await town.found(session, current_catalog(), delegate, delegate.name)
        city = city or founded
        for node in applied.descendants(scenario, delegate.key):
            if node.owner_identity_id is None:
                node.owner_city_id = founded.id
        await session.flush()
        await parts.treasury(session, founded)
        #: The city's first decision: pay newcomers a settlement grant. Written
        #: by the seed for lack of a live president in the world's first second
        #: -- from then on it is an ordinary code-law, changed from the
        #: administration (D-153).
        founded.laws = {"newcomer_grant": parts.NEWCOMER_GRANT}
        await session.flush()
    if city is None:  # pragma: no cover -- a scenario without a city is a defect
        raise RuntimeError("сценарий мира не основал ни одного города")
    #: The world's one mint (D-175, D-270): the first city the scenario names.
    #: The seed's word, not a player's -- a city of players cannot become it.
    city.capital = True
    await session.flush()

    #: The city pool is created at once: a city has one by construction (D-071).
    await energy.ensure_pools(session, constants)

    tern, tern_body = await world.spawn(session, "Тэрн", core, **parts.account_of("Тэрн"))
    #: The first player is the founder: authority in the city appears with the
    #: first person, not by a separate script (D-154).
    await town.install_founder(session, city, tern)
    pocket = await world.body_container(session, tern_body)
    await seed_world.outfit(session, pocket, scenario.pockets.get("Тэрн", ()))
    #: The newcomer's seed fund: seeds are an item separate from the harvest
    #: (D-057). The cultivar is a base one, nobody's: everyone starts from it,
    #: and then the farmer either selects or watches the fund degrade.
    for crop, qty in (("spelt", 300), ("turnip", 200)):
        cultivar = await breed.landrace(session, current_catalog(), crop)
        await breed.seed_lot(session, current_catalog(), pocket.id, cultivar, qty, PERCENT)

    marketplace = applied.nodes["terra.capital.market"]
    hyom, hyom_body = await world.spawn(session, "Хём", marketplace, **parts.account_of("Хём"))
    hyom_pocket = await world.body_container(session, hyom_body)
    await seed_world.outfit(session, hyom_pocket, scenario.pockets.get("Хём", ()))
    #: So that there is something to look at in the book from the first minute.
    await market.load(session, constants, hyom_body, parts.IRON, 30)
    await market.sell(
        session,
        constants,
        current_catalog(),
        hyom,
        marketplace,
        type_key=parts.IRON,
        tier=market.tier_of(constants, 64),
        price=money(3),
        quantity=30,
    )

    #: The other planets' surfaces (D-230): a spaceport on Pyroxis, the ports
    #: of the abandoned city on Aurora. Laid before the buildings, so the yards
    #: there get theirs by the same rule as the capital's.
    await surfaces(session)

    #: Buildings of city nodes: a machine is placed in a building and takes area
    #: (D-106), and the seed must let the building stand before the machine.
    #: The city's built-up area counts as fully built.
    await parts.buildings(session)

    await tick.ensure_scheduled(session)
    #: The household meter ticks with the world clock: maintenance runs by
    #: time and without players (D-149).
    await utility.ensure_scheduled(session)
    log.info(
        "starting world created: Terra's capital with administration, mine, players Tern and Hyom"
    )
    return core


async def main() -> None:
    conf = settings()
    logging.basicConfig(level=conf.log_level, format="%(levelname)s %(name)s %(message)s")
    bootstrap(conf.vault_build_path)

    factory = session_factory()
    async with factory() as session, session.begin():
        await seed(session)
    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
