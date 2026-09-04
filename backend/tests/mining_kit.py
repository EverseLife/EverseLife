# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the mining tests build a face out of.

Helpers and constants shared by `test_mining.py` and `test_mining_roof.py`,
which is why they are here and not beside one of them (the family's own
pattern, see `ship_kit.py`). Pytest does not collect this file: it holds no
tests and no fixtures -- a real `@pytest.fixture` must not live here, because
the import that puts its name in a signature reads as unused to ruff and
pytest never finds it.
"""

from __future__ import annotations

import random
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import mining, world
from src.engine.mining import SessionState

ORE = "iron_ore"


async def _face(
    session: AsyncSession,
    *,
    richness: float = 60,
    remaining: float = 100_000,
    tooled: bool = True,
):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.mine.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, ORE, richness=richness, remaining=remaining)
    identity = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, identity, node)
    if tooled:
        #: The vault requires a pickaxe (`Добыча requires: [Кирка, Жила]`),
        #: and since D-215 the engine checks it at the face.
        pocket = await world.body_container(session, body)
        await world.grant_item(
            session, pocket, "stone_pickaxe", quality=50, origin="сценарий теста"
        )
    return node, vein, body


async def _tool(session: AsyncSession, body):
    container = await world.body_container(session, body)
    return await world.grant_item(
        session, container, "iron_pickaxe", quality=50, origin="сценарий теста"
    )


async def _spend_the_grace(
    session: AsyncSession,
    constants: Constants,
    body,
    vein,
    rng: random.Random,
) -> None:
    """Drop the roof as many times as the vault spares this body (D-294)."""
    for _ in range(int(constants[R.MINE_COLLAPSES_SURVIVED])):
        sess = await mining.start(session, constants, body, vein)
        await _to_the_collapse(session, constants, sess, rng)


async def _to_the_collapse(
    session: AsyncSession,
    constants: Constants,
    sess,
    rng: random.Random,
) -> None:
    """Swing until the roof comes down: a face without timber is finite by design."""
    for _ in range(100):
        sight = await mining.swing(session, constants, sess, rng=rng)
        if sight.state is SessionState.COLLAPSED:
            return
    raise AssertionError("свод так и не обрушился")
