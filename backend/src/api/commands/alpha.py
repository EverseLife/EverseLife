# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The alpha's debug widget: printing a thing and finishing work early (D-229).

The gate lives here rather than in the engine on purpose. Who is allowed is
not a rule of the world -- it is a property of the copy being run, and the
engine has no business reading settings (the layers go api -> engine ->
models -> constants). `engine/alpha.py` knows how to print and how to hurry;
this module knows who may ask.

The commands are `hidden`: the reference the AI citizens are given is built
from these declarations (D-224), and a command they may not run has no place
in their prompt. The refusal below is the actual guard -- hiding is only about
not handing everyone the idea.
"""

from __future__ import annotations

from src.api.registry import Ctx, Refused, command
from src.constants import current, current_catalog
from src.engine import alpha
from src.models.identity import Identity
from src.settings import is_admin
from src.units import amount_float


async def _admin(ctx: Ctx) -> Identity:
    identity = await ctx.identity()
    if not is_admin(identity.name):
        #: Deliberately the same words as an unknown command would get: a
        #: player who guessed the name learns nothing about who does have it.
        raise Refused(f"нет такой команды: {ctx.message.get('cmd')}")
    return identity


@command("alpha.spawn", hidden=True)
async def _alpha_spawn(ctx: Ctx) -> dict:
    """Print a thing into your own hands (alpha only).

    `goods` -- the name from the catalog, `amount` -- how many, `quality` --
    optional, on the vault's scale. The thing arrives with `origin = "alpha"`
    in the journal: matter that the world did not earn is still matter that
    can be found afterwards.
    """
    await _admin(ctx)
    body = await ctx.alive()
    item = await alpha.spawn(
        ctx.db,
        current(),
        current_catalog(),
        body,
        type_key=str(ctx.arg("goods")),
        amount=float(ctx.arg("amount", 1)),
        quality=None if ctx.arg("quality") is None else float(ctx.arg("quality")),
    )
    #: `Item.amount` is the internal integer (`units.AMOUNT_SCALE`); the player
    #: asked for pieces and is answered in pieces. And the answer is the item's
    #: own amount rather than the one asked for: a stack folds into a stack
    #: already lying there (D-214), and a counted thing is whole.
    return {"spawned": item.type_key, "amount": amount_float(item.amount)}


@command("alpha.hurry", hidden=True)
async def _alpha_hurry(ctx: Ctx) -> dict:
    """Finish what this body is doing now: survey, passage, batch (alpha only).

    The answer is a confirmation of what was moved, not the result of the work
    (D-226): the result comes as an event when the journal handler runs it, the
    same handler and the same way as a term waited out honestly.
    """
    await _admin(ctx)
    body = await ctx.alive()
    moved = await alpha.hurry(ctx.db, body)
    return {"hurried": list(moved)}
