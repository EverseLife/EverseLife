# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The words of care (D-293): a crop's norms said as a text a person reads in
the Library, remembers into the "knowledge" tab and retells.

Assembled from the data -- the moisture band, the feeding table, the
hardiness -- in the language of whoever asked, and never written by hand: a
number retuned in the vault would otherwise leave a stale sentence behind. A
bred cultivar gets its text the same way from its own traits, so the author
reads what their line actually asks for (D-057).

Knowledge gates nothing here: the survey shows the same signs to everybody,
and what to do about them is what this text says. The row in the identity is
a bookmark, not a key (D-293).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import breed, travel, world
from src.engine.farm.life import norms
from src.models.identity import Body, Identity, Knowledge, KnowledgeKind
from src.models.plant import Variety
from src.models.world import Node


def care_text(constants: Constants, plant: Plant, signs: Mapping[str, Any], *, locale: str) -> str:
    """The care text of a crop, or of a cultivar through its `signs`."""
    norm = norms(constants, plant, signs)
    said = [
        i18n.render(
            "care-band",
            {
                "culture": plant.id,
                "min": round(norm.band_min),
                "max": round(norm.band_max),
                #: A number, not its spelling: Fluent matches a numeric variant
                #: key against a number, and "3" as a string falls to the default.
                "need": int(signs.get("water", plant.requires.water)),
            },
            locale=locale,
        )
    ]
    if plant.feeding:
        #: The rows are one list in one sentence, so the separator is the
        #: language's own (`LIST_OUT`), the same one `NAMES()` uses.
        rows = i18n.LIST_OUT.join(
            i18n.render(
                "care-feeding-row", {"goods": row.fertilizer, "stage": row.stage}, locale=locale
            )
            for row in plant.feeding
        )
        said.append(i18n.render("care-feeding", {"rows": rows}, locale=locale))
    else:
        said.append(i18n.render("care-feeding-none", locale=locale))
    said.append(i18n.render("care-hardiness", {"hardiness": int(norm.hardiness)}, locale=locale))
    #: Wave 2 (D-295): how much the crowd costs this crop, and when thinning
    #: still works; the weeds are the same for every crop and said once.
    said.append(
        i18n.render(
            "care-crowd",
            {
                "risk": int(signs.get("density_risk", plant.traits.density_risk)),
                "until": str(constants[R.FARM_THIN_UNTIL]),
            },
            locale=locale,
        )
    )
    said.append(i18n.render("care-weeds", locale=locale))
    return " ".join(said)


async def _in_library(session: AsyncSession, body: Body) -> None:
    """The text is read where it stands: the library is a machine (D-176)."""
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    if node is None or not await world.is_library(session, node):
        raise breed.BreedError(key="breed-library-in-person")


async def read_care(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    culture_id: str,
    *,
    locale: str,
) -> str:
    """A base crop's care text, read in the Library (D-053, D-293)."""
    await _in_library(session, body)
    plant = catalog.plants.by_id(culture_id)
    return care_text(constants, plant, breed.traits_of_plant(plant), locale=locale)


async def remember_care(
    session: AsyncSession, catalog: Catalog, body: Body, culture_id: str
) -> Knowledge | None:
    """Remember a base crop's care text: free, for good, on foot (D-053).

    The eight base ones lie in the Library for everyone; a bred cultivar's
    text goes to its author at creation and to nobody else (D-057) -- what
    the author tells is the author's business, not the engine's.
    """
    await _in_library(session, body)
    plant = catalog.plants.by_id(culture_id)
    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise breed.BreedError(key="breed-body-without-identity")
    return await world.learn(session, identity, plant.id, kind=KnowledgeKind.AGROTECH)


async def remembered(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    identity_id: uuid.UUID,
    *,
    locale: str,
) -> list[dict[str, Any]]:
    """The care texts this identity remembered, said in its language.

    A key is a crop's id -- the Library's eight -- or a cultivar's id, the
    author's own line (`breed.agrotech_key`). A cultivar whose row is gone
    is skipped: a bookmark to nothing is not shown as nothing.
    """
    rows = (
        await session.execute(
            select(Knowledge.key)
            .where(Knowledge.identity_id == identity_id, Knowledge.kind == KnowledgeKind.AGROTECH)
            .order_by(Knowledge.acquired_at)
        )
    ).scalars()
    known = {plant.id: plant for plant in catalog.plants.plants}
    out: list[dict[str, Any]] = []
    for key in rows:
        plant = known.get(key)
        if plant is not None:
            text = care_text(constants, plant, breed.traits_of_plant(plant), locale=locale)
            out.append({"key": key, "culture": plant.id, "text": text})
            continue
        try:
            variety = await session.get(Variety, uuid.UUID(key))
        except ValueError:
            variety = None
        if variety is None or variety.culture_id not in known:
            continue
        plant = known[variety.culture_id]
        signs = variety.traits or breed.traits_of_plant(plant)
        out.append(
            {
                "key": key,
                "culture": plant.id,
                "variety": breed.shown_as(catalog, variety),
                "text": care_text(constants, plant, signs, locale=locale),
            }
        )
    return out
