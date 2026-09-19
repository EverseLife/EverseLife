# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""How the journal names a node (`facet.told_of`, D-251, D-321).

What is pinned:

* a line names a node by its name, and a nameless find by the keys of its
  ground, face and biome -- never by the vault's word, which is Russian only
  and was printed as it was to an English reader of the return digest;
* no line of the journal is written with the vault's word in it: the rule is
  read off the source, so a new event that reaches for `word_of` fails here
  rather than in front of a player;
* a run's line keeps the find's biome whether or not it has a name, and the
  digest hands the keys on as the journal wrote them;
* an arrival at a find, which the digest names itself, is named by the same
  rule: it used to be named by `Node.name`, and a find's is empty.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from explore_kit import _camp, _reach, _step
from src.api.commands.world import _world_summary
from src.constants import Catalog, Constants
from src.engine import biome, events, explore, facet, jobs, places, world
from src.models.event import Event, EventKind
from src.models.world import Node, Planet

SRC = Path(__file__).resolve().parent.parent / "src"

#: The call that gives the vault's own word for a node, by whatever module
#: it is reached through (`biome`, or its re-export in `explore`).
VAULT_WORDS = {"word_of"}


def test_a_line_names_a_node_by_its_name_or_the_keys_of_its_ground(
    constants: Constants, catalog: Catalog
) -> None:
    named = Node(key="terra.yard", name="Yard", planet=Planet.TERRA, properties={})
    assert facet.told_of(constants, named) == {"node": "Yard"}
    #: A find: no name, its biome and its face written on it by the run.
    face = catalog.facets.of_biome(biome.FOREST)[0].id
    found = Node(
        key="terra.find",
        name="",
        planet=Planet.TERRA,
        properties={biome.BIOME: biome.FOREST, facet.FACET: face},
    )
    assert facet.told_of(constants, found) == {"biome": biome.FOREST, "facet": face}
    #: The vault's word is not in it, in any language.
    assert biome.name(constants, biome.FOREST) not in facet.told_of(constants, found).values()
    #: A find with no face written on it: the biome alone.
    bare = Node(
        key="terra.bare", name="", planet=Planet.TERRA, properties={biome.BIOME: biome.TAIGA}
    )
    assert facet.told_of(constants, bare) == {"biome": biome.TAIGA}
    #: No name and no ground under it: nothing, rather than the key -- a key
    #: in a line is a broken line.
    inside = Node(key="terra.room", name="", planet=Planet.TERRA, properties={})
    assert facet.told_of(constants, inside) == {}


def _vault_word_in(call: ast.Call) -> bool:
    for part in ast.walk(call):
        if not isinstance(part, ast.Call):
            continue
        func = part.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if called in VAULT_WORDS:
            return True
        #: `biome.name(constants, here)`: the word `word_of` is made of.
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "name"
            and isinstance(func.value, ast.Name)
            and func.value.id == "biome"
        ):
            return True
    return False


def test_no_line_of_the_journal_is_written_with_the_vault_s_word() -> None:
    """The journal is read in the reader's language, the vault's word is in one.

    It sees the word written straight into the call -- the form all three
    lines had, and the form a new line copied from them would have. A word
    carried in through a variable or a helper of one's own passes it; the
    tests of `told_of` and of each line are what hold those.
    """
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "record"
                and isinstance(func.value, ast.Name)
                and func.value.id == "events"
            ):
                continue
            if _vault_word_in(node):
                offenders.append(f"{path.relative_to(SRC).as_posix()}:{node.lineno}")
    assert not offenders, (
        f"a line of the journal names a node by the vault's word; use facet.told_of: {offenders}"
    )


async def test_a_run_s_line_names_the_find_by_its_kind_and_the_digest_hands_it_on(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, catalog, camp)
        target = _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.3)
        job = await explore.survey(session, constants, scout, target)
        term, reader = job.run_at, scout.identity_id
    assert await jobs.run_one(factory, now=term) is not None
    async with factory() as session, session.begin():
        line = await session.scalar(
            select(Event).where(
                Event.kind == EventKind.EXPLORE_FOUND.value, Event.actor_identity_id == reader
            )
        )
        assert line is not None
        find = await session.get(Node, line.node_id)
        assert find is not None and not find.name
        assert line.payload["biome"] == find.properties[biome.BIOME]
        assert line.payload.get("facet") == facet.of_node_id(find)
        assert "node" not in line.payload, "the vault's word stays out of the journal"
        digest = await _world_summary({"identity_id": reader}, session, {})
        said = [row for row in digest["happened"] if row["kind"] == EventKind.EXPLORE_FOUND.value]
        assert len(said) == 1
        assert said[0]["payload"]["biome"] == find.properties[biome.BIOME]
        assert said[0]["payload"].get("facet") == facet.of_node_id(find)


async def test_an_arrival_at_a_find_is_named_by_the_keys_of_its_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    stamp = uuid.uuid4().hex[:6]
    face = catalog.facets.of_biome(biome.TAIGA)[0].id
    find = await world.create_node(
        session,
        f"terra.find.{stamp}",
        "",
        area_m2=100,
        properties={biome.BIOME: biome.TAIGA, facet.FACET: face},
    )
    walker = await world.create_identity(session, f"Walker-{stamp}")
    await world.print_body(session, walker, find)
    recorded = await events.record(
        session,
        EventKind.TRAVEL_ARRIVED,
        actor_identity_id=walker.id,
        node_id=find.id,
        travel_id=str(uuid.uuid4()),
    )

    digest = await _world_summary({"identity_id": walker.id}, session, {})
    said = [row for row in digest["happened"] if row["kind"] == EventKind.TRAVEL_ARRIVED.value]
    assert len(said) == 1
    assert said[0]["payload"]["biome"] == biome.TAIGA
    assert said[0]["payload"]["facet"] == face
    #: Not an empty name, which the digest skipped: the arrival said nowhere.
    assert "node" not in said[0]["payload"]
    #: The wire copy was named, the journal row was not: a read writes nothing.
    #: Asked of the object in memory, which is the row the digest read: a
    #: refresh would reread it and throw away the very write looked for.
    assert "biome" not in recorded.payload and "facet" not in recorded.payload
    assert not session.dirty and not session.new
