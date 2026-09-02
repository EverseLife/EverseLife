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

from src.api.commands.common import goods_key
from src.api.registry import Ctx, Refused, command
from src.constants import current, current_catalog
from src.engine import alpha, death, liquid
from src.models.identity import Identity
from src.settings import is_admin
from src.units import amount_float


async def _admin(ctx: Ctx) -> Identity:
    identity = await ctx.identity()
    if not is_admin(identity.name):
        #: Deliberately the same words as an unknown command would get: a
        #: player who guessed the name learns nothing about who does have it.
        raise Refused(key="cmd-unknown-command", cmd=ctx.message.get("cmd"))
    return identity


@command("alpha.spawn", hidden=True)
async def _alpha_spawn(ctx: Ctx) -> dict:
    """Print a thing into your own hands, or onto the floor underfoot (alpha only).

    `goods` -- the name from the catalog, `amount` -- how many, `quality` --
    optional, on the vault's scale; `where` -- `hands` (the default) or
    `floor`. The thing arrives with `origin = "alpha"` in the journal: matter
    that the world did not earn is still matter that can be found afterwards.
    """
    await _admin(ctx)
    body = await ctx.alive()
    asked = float(ctx.arg("amount", 1))
    item = await alpha.spawn(
        ctx.db,
        current(),
        current_catalog(),
        body,
        type_key=goods_key(ctx.arg("goods")),
        amount=asked,
        quality=None if ctx.arg("quality") is None else float(ctx.arg("quality")),
        where=str(ctx.arg("where", alpha.HANDS)),
    )
    #: `Item.amount` is the internal integer (`units.AMOUNT_SCALE`); the player
    #: asked for pieces and is answered in pieces. And the answer is the item's
    #: own amount rather than the one asked for: a stack folds into a stack
    #: already lying there (D-214), and a counted thing is whole.
    #:
    #: A liquid is the one thing that has no single stack to point at: it is
    #: poured into the vessels within reach (D-230) and may end up split between
    #: a canister and a tank. Nothing of it is lost -- a print that would spill
    #: is refused whole -- so the honest answer there is what was asked for.
    poured = liquid.is_liquid(current_catalog(), item.type_key)
    return {
        "spawned": item.type_key,
        "amount": asked if poured else amount_float(item.amount),
    }


@command("alpha.hurry", hidden=True)
async def _alpha_hurry(ctx: Ctx) -> dict:
    """Finish what this player is waiting on: survey, passage, batch, print
    (alpha only).

    A live body is **not** required. Without one the identity is in the cloud
    with a single term running -- the printing of the next body -- and at
    twelve hours at the Forerunners' printer that is the longest wait in the
    world. Asking for a body here would have shut the widget off in the one
    state that most needs it.

    The answer is a confirmation of what was moved, not the result of the work
    (D-226): the result comes as an event when the journal handler runs it, the
    same handler and the same way as a term waited out honestly.
    """
    identity = await _admin(ctx)
    moved = await alpha.hurry(ctx.db, identity.id, await death.alive_body(ctx.db, identity.id))
    return {"hurried": list(moved)}


@command("alpha.energize", hidden=True)
async def _alpha_energize(ctx: Ctx) -> dict:
    """Put energy into the pool of the city you stand in (alpha only).

    `amount` -- how much. A test world's pool runs dry, and a dry pool hides
    every door that needs it; the widget fills it where the world's own coal
    would have. The answer is the pool's new level -- a confirmation the
    asker cannot derive, not an echo of the request.
    """
    await _admin(ctx)
    body = await ctx.alive()
    stored = await alpha.energize(ctx.db, current(), body, float(ctx.arg("amount", 0)))
    return {"stored": stored}
