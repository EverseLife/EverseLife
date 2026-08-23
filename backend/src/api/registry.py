# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The command registry: what the socket understands, declared where it lives.

`session.py` used to hold a dict of 165 names next to 168 handlers in one
file (review 2026-08-23). Now every domain module under `api/commands/`
registers its own with `@command("market.sell")`, and the socket loop looks
the name up here. The decorator carries what the loop needs to know about a
command without reading its body: whether it only reads (`readonly`), so a
future replica can serve it, and its docstring, which the AI agent's
reference is built from.

Handlers keep the signature `(state, db, message) -> dict`; the context
object that replaces the 139 hand-written prologues (`Ctx`, below) is the
next step and is introduced module by module.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine.errors import Refusal
from src.models.identity import Body, BodyState, Identity

Handler = Callable[[dict[str, Any], AsyncSession, dict[str, Any]], Awaitable[dict[str, Any]]]


class Refused(Refusal):
    """Command refused by the game rules. This is not a server error."""


@dataclass(frozen=True)
class Command:
    name: str
    handler: Handler
    readonly: bool
    doc: str
    #: Declared as `async def f(ctx: Ctx)` -- the new shape; the loop builds
    #: the context. The old `(state, db, message)` is still served.
    takes_ctx: bool
    #: Kept out of the reference the AI citizens are given (D-224). Their
    #: reference is generated from these declarations, so a command nobody
    #: but a developer may run would otherwise arrive in every agent's prompt
    #: as one more thing to try. Hiding it is not the access check -- the
    #: handler refuses on its own -- it is not putting the idea there.
    hidden: bool = False

    async def run(self, state: dict[str, Any], db: AsyncSession, message: dict[str, Any]) -> dict:
        if self.takes_ctx:
            return await self.handler(Ctx(state, db, message))  # type: ignore[arg-type]
        return await self.handler(state, db, message)


COMMANDS: dict[str, Command] = {}


def command(
    name: str, *, readonly: bool = False, hidden: bool = False
) -> Callable[[Handler], Handler]:
    """Register a socket command. One name, one handler; a second registration
    of the same name is a programming error and fails at import."""

    def wrap(handler: Handler) -> Handler:
        if name in COMMANDS:
            raise RuntimeError(f"command {name!r} registered twice")
        COMMANDS[name] = Command(
            name=name,
            handler=handler,
            readonly=readonly,
            doc=(handler.__doc__ or "").strip(),
            takes_ctx=len(inspect.signature(handler).parameters) == 1,
            hidden=hidden,
        )
        return handler

    return wrap


@dataclass
class Ctx:
    """What a command runs with: the transaction, the socket's state, the
    message -- and the player behind it, read once and locked when acted
    with. Replaces the prologues `identity = await _identity(state, db)` /
    `body = await _alive(state, db)` repeated in every handler."""

    state: dict[str, Any]
    db: AsyncSession
    message: dict[str, Any]
    _identity: Identity | None = field(default=None, repr=False)
    _body: Body | None = field(default=None, repr=False)

    @property
    def identity_id(self) -> uuid.UUID:
        found = self.state.get("identity_id")
        if found is None:
            raise Refused("сначала hello")
        return found

    async def identity(self) -> Identity:
        if self._identity is None:
            found = await self.db.get(Identity, self.identity_id)
            if found is None:
                raise Refused("личность не найдена")
            self._identity = found
        return self._identity

    async def body(self) -> Body | None:
        """The living body, if any -- a read, no lock."""
        stmt = select(Body).where(
            Body.identity_id == self.identity_id, Body.state == BodyState.ALIVE
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def alive(self) -> Body:
        """The body being acted with, **locked** for the command (D-211)."""
        if self._body is None:
            stmt = (
                select(Body)
                .where(Body.identity_id == self.identity_id, Body.state == BodyState.ALIVE)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            found = (await self.db.execute(stmt)).scalars().first()
            if found is None:
                raise Refused("нет живого тела")
            self._body = found
        return self._body

    def arg(self, key: str, default: Any = None) -> Any:
        return self.message.get(key, default)
