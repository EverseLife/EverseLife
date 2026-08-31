# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The surfaces of Pyroxis and Aurora: where a ship may land (D-230, D-232).

Split out of `seed.py` -- the starting world's module is long past the length
a file should have, and the other planets are content of their own.

A ship flies to a **spaceport** and nowhere else: the route list is the list of
nodes with a yard in them whose beacon shines (`ship.lit_ports`). So a planet
exists for a pilot only once something with a yard stands on it and something
keeps that yard warm and fed.

* **Pyroxis** -- a plateau and the black fields around it, and **no spaceport
  at all** (D-233). There cannot be one: nothing is built on Pyroxis (D-230),
  so there is nothing to put a yard into. A ship sets down in any surface node
  of the planet, by the same single edge connector-to-node, and the only
  infrastructure of the place is its own hull. The plateau is a `planet`-layer
  node like the capital, but it is no city: nobody founded it, nobody owns it,
  and its layer is a **camp**, which the client names so.

  The fields carry the planet's veins: what is rare on Terra lies here in
  plenty and what is ordinary lies poorly (`harvest.planet_weights`, D-233).
  The plateau carries none: it is the one place the eruptions leave alone
  (D-197), and a vein that never moved would be exactly the staked claim the
  eruptions exist against.
* **Aurora** -- **three** cities of the Forerunners, and not one of them is
  like another (D-232). Each is a `planet`-layer node with two locations under
  it: the central hall, where a «ТЭЦ Предтеч» and an «Изотопный реактор
  Предтеч» stand, and the spaceport one step away -- inside the plant's heat,
  because a port only works while its node is warm and its yard has power
  (D-231). Everything of the Forerunners' here is a **relic**: found, not
  assembled, and never taken down.

  There used to be six hundred and sixty-six identical ports here instead. They
  were a hedge against a race for a single berth, and they were the wrong
  answer: a planet of six hundred copies of one pier is not a planet. Three
  cities with faces, and the rest of Aurora found by walking (D-232).

The reactor's countdown is anchored **in the node where it stands**, at the
moment this seed lays the surface: the Forerunners did not wait for guests, and
a world that had been running for a year before Aurora existed would otherwise
receive the planet already dead.

Idempotent like the rest of the seed: a node found by key is left alone, and a
world laid out before the planets had surfaces catches up on its own.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current, current_catalog, display_name
from src.engine import energy, explore, oxygen, plates, props, ruins, ship, travel, world
from src.models.world import Layer, Node, Planet, Surface

#: The Anvil Plateau: the one stable ground of Pyroxis (10-world/04, D-197).
PYROXIS_PLATEAU = "pyroxis.anvil"
#: The spaceport the seed used to lay on the plateau (D-230). Kept as a name so
#: the migration that takes it away has something to name; nothing lays it now.
PYROXIS_PORT = "pyroxis.anvil.port"
#: The black fields: where the veins are, and where the map is redrawn. Enough
#: of them for an eruption to have somewhere to move a vein to, few enough that
#: the first expedition can walk the lot.
PYROXIS_FIELDS = 6
#: How rich a field's vein is and how much is in it. The same spans exploring
#: uses (`explore.vein_richness`, `explore.vein_stock`) would do, but the seed
#: has no scout: these are the planet's own, and generous -- Pyroxis is a shift
#: worth flying to (10-world/04).
PYROXIS_VEIN_RICHNESS = 70
PYROXIS_VEIN_STOCK = 4000
#: A walk across the black fields: they are neighbours of the plateau and of
#: each other, and the ground between them is no road.
PYROXIS_STEP_SECONDS = 900

#: A black field is open ground, and the whole of it is a working face.
FIELD_AREA_M2 = 5000
#: The plateau's mark: the one place on Pyroxis an eruption leaves alone
#: (D-197). The engine's name for it, so the seed and the planet's own weather
#: cannot drift apart over a spelling.
ANVIL = plates.ANVIL

#: The mark of the Forerunners on everything they left. The engine's, because
#: exploring reads it too: a planet with this mark holds their cities, and a
#: node with it is one of theirs (`engine.ruins`).
PRECURSOR = ruins.PRECURSOR


#: The keys the vault's layout gives the three cities of Aurora (D-232, D-243).
#: Named here rather than derived, because the engine has exactly one thing left
#: to do to them -- anchor the reactor's countdown -- and it must know where.
AURORA_HALLS = (
    "aurora.merid.hall",
    "aurora.caldar.hall",
    "aurora.veyr.hall",
)


async def surfaces(session: AsyncSession) -> None:
    """Lay the surfaces of both planets.

    Neither of them gets a yard built by recipe any more: Aurora's piers are
    relics the Forerunners left (D-232), and Pyroxis has no port at all and
    cannot have one (D-233).
    """
    await _pyroxis(session)
    await _aurora(session)


async def _pyroxis(session: AsyncSession) -> None:
    """The plateau, the fields, and the planet's own veins (D-233).

    No yard anywhere: the planet takes a landing in any of its surface nodes,
    and that property is written on the planet itself. A world seeded before
    D-233 has a spaceport on the plateau; the migration takes it away, and this
    lays nothing in its place -- there is nothing to lay.
    """
    sphere = await _sphere(session, "pyroxis")
    #: The planet's own properties, like its climate (D-231): a ship aims at
    #: ground here rather than at a pier, and there is nothing to breathe when
    #: it gets there (D-233, D-234). Both are facts of the world, so both are
    #: written on the world -- on the planet's own node, where `ship` and
    #: `oxygen` read them. Written only when missing, so a deploy does not
    #: lock the planet's row for nothing.
    marks = {ship.OPEN_LANDING: True, oxygen.AIRLESS: True}
    if any(not (sphere.properties or {}).get(key) for key in marks):
        await props.stamp(session, sphere, marks)
    plateau = (
        await _ensure(
            session,
            PYROXIS_PLATEAU,
            "Плато Наковальни",
            planet=Planet.PYROXIS,
            layer=Layer.PLANET,
            parent=sphere,
            area=1,
            properties={ANVIL: True},
        )
    ).node
    #: And the mark is set **every** time, not only when the node is made.
    #: `_ensure` leaves a node it found alone, and the plateau of a world laid
    #: before D-233 was made without properties at all -- so it would come out
    #: of the catch-up unmarked, and unmarked it is not the plateau at all:
    #: `_exempt` would return nothing, the planet would shake its own anvil,
    #: burn what stands on it, tear its ways and move a vein onto it, where
    #: nothing could ever move it off again (D-197).
    #:
    #: Not player state and not a roll: the mark is a fact of the world's
    #: structure, and the seed owns it. Written only when it is missing, so a
    #: deploy does not touch the row for nothing.
    if not (plateau.properties or {}).get(ANVIL):
        await props.stamp(session, plateau, {ANVIL: True})
    dice = random.Random(PYROXIS_PLATEAU)
    for number in range(1, PYROXIS_FIELDS + 1):
        #: Laid **once**, and the whole field with it: the way to it, its vein,
        #: its name. An eruption moves veins and redraws ways on purpose
        #: (D-197), and a seed that ran again on every deploy would put them
        #: back -- endless ore on a release schedule, and the one mechanic the
        #: planet exists for undone. The roll is spent either way, so the
        #: fields of a world caught up late are the fields of a fresh one.
        laid = await _ensure(
            session,
            pyroxis_field_key(number),
            f"Чёрное поле №{number}",
            planet=Planet.PYROXIS,
            layer=Layer.PLANET,
            parent=sphere,
            area=FIELD_AREA_M2,
            anchor=plateau,
        )
        species = await explore.species_of(
            session, current(), current_catalog(), dice, planet=Planet.PYROXIS
        )
        if laid.created:
            await travel.connect(
                session,
                plateau,
                laid.node,
                base_seconds=PYROXIS_STEP_SECONDS,
                surface=Surface.TRAIL,
            )
            await world.create_vein(
                session,
                laid.node,
                species,
                richness=PYROXIS_VEIN_RICHNESS,
                remaining=PYROXIS_VEIN_STOCK,
            )
            #: The species is a D-251 id, and the field's name is what a
            #: player reads off the map: the word goes in, not the key.
            #: The same seam as the vein an explorer finds
            #: (`explore/run.py`), and the same wave-IV debt -- a Russian
            #: name frozen into a row that no language can undo.
            laid.node.name = f"{laid.node.name}: {display_name(species).lower()}"
            await session.flush()
            #: And a way to the field before it. A star -- every field hanging
            #: on the plateau alone -- would switch the planet's main mechanic
            #: off on the day it is deployed: the plateau is never shaken and
            #: is not a destination either, so a vein in a star has nowhere to
            #: move and `_move_veins` returns nought on every eruption until a
            #: bridge happens to fall between two fields. A ring of fields is
            #: also the honest map: they lie next to each other on one plateau,
            #: and walking from one to the next round the rim is shorter than
            #: going back over the top every time.
            #:
            #: Inside the `created` guard with everything else, and for the same
            #: reason: an eruption tears ways on purpose, and a seed that laid
            #: this one again on every deploy would put back exactly what the
            #: planet had just decided to take away. A world that already has
            #: its fields keeps whatever shape the eruptions left it in.
            before = (
                await session.execute(select(Node).where(Node.key == pyroxis_field_key(number - 1)))
            ).scalar_one_or_none()
            if before is not None:
                await travel.connect(
                    session,
                    before,
                    laid.node,
                    base_seconds=PYROXIS_STEP_SECONDS,
                    surface=Surface.TRAIL,
                )


def pyroxis_field_key(number: int) -> str:
    return f"{PYROXIS_PLATEAU}.field.{number:02d}"


async def _aurora(session: AsyncSession) -> None:
    """What is left of Aurora once its layout became data (D-243).

    The three cities, their halls and piers, their relics and the walk between
    them are the vault's now (`data/world.yaml`) -- they are a layout like the
    capital's, and they are edited in the editor's «Мир» tab like one.

    Two things could not go with them, and both are rules rather than places:

    * **the planet's own mark.** From here on a search for city ground on
      Aurora finds a city that already stands, not an empty place. It belongs
      to the planet, and the planet is laid by the engine (orbits are a rule);
    * **the anchor of the reactor's fading.** It is written at the moment the
      surface appears, not at a moment somebody typed into a file: the
      Forerunners did not wait for guests, and a world that had been running
      for a year before Aurora existed would otherwise receive the planet
      already dead. A timestamp in the vault would be a lie the day after it
      was written.
    """
    sphere = await _sphere(session, "aurora")
    #: The planet itself is marked: from here on, a search for city ground on
    #: Aurora finds a city that already stands, not an empty place (D-232).
    if not (sphere.properties or {}).get(PRECURSOR):
        await props.stamp(session, sphere, {PRECURSOR: True})
    now = datetime.now(UTC).isoformat()
    for key in AURORA_HALLS:
        hall = (await session.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
        #: The layout lays the hall; this only marks it. A world whose vault
        #: has not got the city yet simply has nothing to mark.
        if hall is None or (hall.properties or {}).get(energy.REACTOR_SINCE):
            continue
        await props.stamp(session, hall, {energy.REACTOR_SINCE: now})


async def _sphere(session: AsyncSession, key: str) -> Node:
    """The planet's node on the space layer: `seed_parts.system` lays it first."""
    return (await session.execute(select(Node).where(Node.key == key))).scalar_one()


@dataclass(frozen=True, slots=True)
class Laid:
    """A node the seed asked for, and whether this run is what made it.

    The difference matters wherever the world may have moved on since: a field
    of Pyroxis gets its way and its vein **only** when it is new, because an
    eruption is allowed to take both away and a deploy is not allowed to give
    them back (D-197).
    """

    node: Node
    created: bool


async def _ensure(
    session: AsyncSession,
    key: str,
    name: str,
    *,
    planet: Planet,
    layer: Layer,
    parent: Node,
    area: float,
    properties: dict[str, object] | None = None,
    anchor: Node | None = None,
) -> Laid:
    """The node by key, created if the world has none yet.

    `anchor` is the node this one is laid beside on the map (D-237): the
    plateau a field is walked to from, the hall a pier opens off. Without it a
    surface would come out as a ring round its planet's origin instead of
    following its own ways.
    """
    found = (await session.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if found is not None:
        return Laid(node=found, created=False)
    made = await world.create_node(
        session,
        key,
        name,
        planet=planet,
        area_m2=area,
        layer=layer,
        parent=parent,
        anchor=anchor,
        properties=dict(properties or {}),
    )
    return Laid(node=made, created=True)
