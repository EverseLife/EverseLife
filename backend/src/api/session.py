# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Client session -- the only place where a player acts.

Anti-cheat rests not on protecting the client but on the fact that **there is
no action API** (60-meta/01-anti-cheat). An in-person action goes only from
here and only after the device fee (D-110).

The protocol is deliberately boring: JSON over WebSocket, one command -- one
reply. The reply to any mining command is a `Sight`, i.e. exactly what the
player sees. Roof stability is not there: it is not "hidden in the UI", it
simply does not exist in the reply.

Craft lives here for the same reason, even though the batch runs offline:
**starting** is an in-person action and there will never be a convenient REST
for it. The forecast (`craft.plan`) and the start (`craft.start`) parse the
request with the same code -- otherwise the player would see one number and
get another (D-092).

**Account identification is email and password** (D-187): `hello` accepts
either those or a token issued by a previous login. The subscription (E7,
D-027) will bind to the same account.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.api import commands as _commands  # noqa: F401 -- registers every command
from src.api import push
from src.api.commands.common import _body
from src.api.commands.views import _money
from src.api.registry import COMMANDS, Refused
from src.constants import current
from src.db.base import session_factory
from src.engine import (
    account as accounts,
)
from src.engine import (
    world,
)
from src.engine.errors import Refusal
from src.models.identity import Body, Identity
from src.settings import is_admin

log = logging.getLogger(__name__)


router = APIRouter(tags=["session"])


@router.websocket("/session/ws")
async def play(socket: WebSocket) -> None:
    """One connection: commands in, answers and events out (D-226).

    A command may carry `id`; the answer carries it back, so the client
    matches them by number and not by order. Events -- what the server says
    on its own -- have `event` instead and start after `hello` with `since`;
    a client that never asks for them reads answers by order as before.
    """
    await socket.accept()

    async def send_raw(message: dict[str, Any]) -> None:
        await socket.send_json(_without_nulls(message))

    sink = push.Sink(send_raw=send_raw, cut=socket.close)
    #: The language this session is answered in (D-251 wave III). The default
    #: holds until `hello` says whose account this is: before that there is
    #: nobody whose choice to honour.
    state: dict[str, Any] = {
        "identity_id": None,
        "sink": sink,
        "locale": i18n.DEFAULT_LOCALE,
    }
    push.hub.attach(sink)

    try:
        while True:
            message = await socket.receive_json()
            try:
                answer = await _dispatch(state, message)
            #: The rules said no (`engine/errors.Refusal`): every refusal of
            #: every engine module descends from it, and the player reads it
            #: in their own words. Anything else below is a bug.
            except Refusal as refusal:
                answer = _refused(refusal, state["locale"])
            #: A malformed command -- a missing argument, a string where a
            #: number or an id was expected -- is the client's mistake, and it
            #: is answered, not dropped: an exception here used to close the
            #: socket without a close frame, and the player saw "connection
            #: lost" instead of what was wrong.
            except KeyError as missing:
                #: The name of the field, not the repr of the exception: the
                #: player (and the AI citizen, D-224) must read which argument
                #: the command wanted, not `KeyError('output')`.
                log.warning("command %r without %s", message.get("cmd"), missing)
                answer = _said("session-field-missing", state, field=missing.args[0])
            except (ValueError, TypeError) as bad:
                log.warning("command %r not understood: %r", message.get("cmd"), bad)
                answer = _said("session-not-understood", state, why=str(bad))
            #: Anything else is our bug. It goes to the log whole, and the
            #: session survives it: the transaction was rolled back by
            #: `_dispatch`, so nothing half-done is left behind.
            except Exception:
                log.exception("command %r crashed", message.get("cmd"))
                answer = _said("session-server-failed", state)
            if isinstance(message, dict) and isinstance(message.get("cmd"), str):
                push.hub.tally.answered(message["cmd"])
            ticket = message.get("id") if isinstance(message, dict) else None
            if ticket is not None:
                answer = {"id": ticket, **answer}
            await sink.send(answer)
            #: Catching up goes after the answer to `hello`, never before it:
            #: the client wants to know who it is before it hears what happened.
            if state.pop("listen", False):
                sink.listening = True
                push.hub.dirty = True
            since = state.pop("replay", None)
            if since:
                await push.hub.replay(sink, since)
    except WebSocketDisconnect:
        #: The player leaving does not close the mining session: it lives until
        #: "leave" or until a collapse. What was mined lies in the face and
        #: waits for a decision.
        log.info("session disconnected, identity %s", state.get("identity_id"))
    finally:
        push.hub.detach(sink)


def _refused(refusal: Refusal, locale: str) -> dict[str, Any]:
    """A refusal on the wire (D-251 wave III).

    Three fields where there used to be one: the sentence for the player, the
    `code` for whoever acts on it -- the client drawing its own window, the AI
    citizen deciding what to try next (D-224) -- and the `args` the sentence
    was built from, so that a reader may compose their own. A call site still
    writing its own Russian travels as a bare string, exactly as before.
    """
    if refusal.key is None:
        return {"refused": str(refusal)}
    #: A quoted refusal is rendered first and put in as an argument (wave IV):
    #: "тело занято: идёт разведка" is two messages, and only the outer one
    #: knows where the inner goes.
    quoted = {name: i18n.join(said, locale=locale) for name, said in refusal.inner.items()}
    return {
        "refused": i18n.render(refusal.key, {**refusal.params, **quoted}, locale=locale),
        "code": refusal.key,
        #: Empty rather than `{}`: `_without_nulls` drops it, and a refusal
        #: with no numbers in it stays two fields wide. The quoted halves go
        #: out as keys, not as the words they became: a client redrawing the
        #: refusal renders them in its own language, like everything else.
        "args": {**refusal.params, **_inner_args(refusal)} or None,
    }


def _inner_args(refusal: Refusal) -> dict[str, Any]:
    """The quoted messages as the wire carries them: keys and their arguments."""
    return {
        name: [
            {"code": said.key, **({"args": said.params} if said.params else {})} for said in says
        ]
        for name, says in refusal.inner.items()
    }


def _said(key: str, state: dict[str, Any], **params: Any) -> dict[str, Any]:
    """The socket's own refusal -- the ones no engine module raises."""
    return _refused(Refused(key=key, **params), state["locale"])


def _without_nulls(value: Any) -> Any:
    """The reply without keys whose value is null (D-225, widened to every
    answer): no value -- no key. A body in the cloud has no `body`, a plot of
    nobody has no `owner`, a thing of no quality has no `quality`. The client
    reads absence the same way it read null, and the payload loses the
    fields that said nothing. Lists keep their nulls: a position in a list is
    a fact, dropping one would shift the rest.
    """
    if isinstance(value, dict):
        return {key: _without_nulls(inner) for key, inner in value.items() if inner is not None}
    if isinstance(value, list):
        return [_without_nulls(inner) for inner in value]
    return value


async def _dispatch(state: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    command = message.get("cmd")
    if command is None:
        raise Refused(key="session-command-unnamed")

    async with session_factory()() as db, db.begin():
        if command == "hello":
            return await _hello(state, db, message)
        #: New player -- before identification: nobody to identify yet.
        if command == "join":
            return await _join(state, db, message)

        identity_id = state.get("identity_id")
        if identity_id is None:
            raise Refused(key="session-need-hello")

        known = COMMANDS.get(command)
        if known is None:
            raise Refused(key="session-command-unknown", cmd=command)
        return await known.run(state, db, message)


async def _hello(state: dict, db: AsyncSession, message: dict) -> dict:
    """Identification: email and password, or the token of a previous login (D-187).

    The password is entered once: a token goes back, and reconnecting the socket
    or refreshing the page is identified by it. The token lives `LOGIN_TOKEN_TTL`
    and is revoked by logging out of the account panel.
    """
    token = message.get("token")
    if token:
        account = await accounts.by_token(db, token)
        issued = str(token)
    else:
        account = await accounts.login(db, message.get("email"), message.get("password"))
        issued = await accounts.issue_token(db, account)

    identity = (
        await db.execute(select(Identity).where(Identity.account_id == account.id))
    ).scalar_one_or_none()
    if identity is None:
        raise Refused(key="cmd-account-without-identity")

    state["identity_id"] = identity.id
    state["token"] = issued
    #: From here on this session is answered in the account's own language
    #: (D-249): refusals, and in wave IV the events too.
    state["locale"] = i18n.normalize(account.locale)
    body = await _body(db, identity.id)
    _listen(state, message, identity, body)
    return {
        "hello": identity.name,
        "token": issued,
        #: What language the world is being read in. The client needs it to
        #: ask `/public/i18n` for the matching words and to format its own
        #: dates and sorting -- it is not derivable from anything else sent.
        "locale": state["locale"],
        #: The client computes the device fee itself, and its account is part
        #: of the estimate (D-112).
        "account": str(identity.account_id),
        "body": None if body is None else str(body.id),
        "node": None if body is None else str(body.node_id),
        "constants": current().digest,
        #: The alpha's debug widget, if this copy opens it for this name
        #: (D-229). Said at the greeting and not in `look`: it cannot be
        #: derived from anything already sent (D-225), and it does not change
        #: while the session lasts -- repeating it on every look would be a
        #: constant travelling as state.
        **({"admin": True} if is_admin(identity.name) else {}),
    }


def _listen(state: dict, message: dict, identity: Identity, body: Body | None) -> None:
    """Turn the stream of events on, if the client asked (D-226): `since` is
    the last `seq` it saw, 0 for "from now on" with nothing replayed. Without
    it the connection stays answer-by-order, as the old client and the tests
    expect."""
    sink: push.Sink | None = state.get("sink")
    if sink is None:
        return
    sink.identity_id = identity.id
    sink.node_id = None if body is None else body.node_id
    push.hub.dirty = True
    since = message.get("since")
    if since is None:
        return
    try:
        state["replay"] = max(0, int(since))
    except (TypeError, ValueError) as bad:
        raise Refused(key="cmd-since-not-a-number") from bad
    #: Turned on by the socket loop after the answer is queued: the client
    #: must hear who it is before it hears what happened.
    state["listen"] = True


async def _join(state: dict, db: AsyncSession, message: dict) -> dict:
    """Registration: account, identity and first body at the chosen door (D-187).

    The client walks the player through four steps -- email and password, line,
    character, door -- but the server receives them as one command: there is no
    half-account. Everything is checked before the first write, and a refusal on
    any field leaves the database untouched.

    **Where to print is the player's decision** (D-013, D-182): the door is named
    by a node key from `/public/doors`. Without it we print at the first printer
    we find -- that is how old clients enter, not how entry is meant to work.

    The balance is **zero**: the world hands out no money (D-153). If the city
    where the bioprinter stands decided to pay a settlement grant, it comes from
    its treasury -- and that is visible in the reply. A zero in the reply is
    honest too: the city is poor or does not pay.
    """
    email = accounts.normalize_email(message.get("email"))
    password = accounts.check_password(message.get("password"))
    if message.get("password_again") is not None and message["password_again"] != password:
        raise Refused(key="cmd-passwords-differ")
    if await accounts.by_email(db, email) is not None:
        raise Refused(key="cmd-email-taken")
    line = accounts.check_line(message.get("line"))
    name = accounts.check_name(message.get("name"))
    if is_admin(name):
        #: A name on the admin list is reserved, whether or not an identity
        #: already wears it (D-229). Otherwise, on a copy where the seed has
        #: not made that identity yet, the first comer to type the name would
        #: register straight into the debug widget -- the list lives in a
        #: compose file, so the name is public and guessable. The words are the
        #: ones a taken name gets: guessing right must teach nothing.
        raise Refused(key="land-name-taken", name=name)
    profile = accounts.check_profile(message)

    key = str(message.get("node") or "").strip()
    if key:
        where = await world.door(db, key)
        if where is None:
            raise Refused(key="cmd-door-does-not-print", node=key)
    else:
        where = await world.spawn_point(db)
    if where is None:
        raise Refused(key="cmd-world-not-created")
    identity, body = await world.spawn(
        db, name, where, email=email, password=password, line=line, profile=profile
    )

    account = await accounts.account_of(db, identity)
    issued = await accounts.issue_token(db, account)
    state["identity_id"] = identity.id
    state["token"] = issued
    state["locale"] = i18n.normalize(account.locale)
    _listen(state, message, identity, body)
    return {
        "hello": identity.name,
        "token": issued,
        #: Said here as in `hello`: a client that has just registered needs the
        #: language for the same reasons one that has just logged in does, and
        #: it cannot derive it from anything else in this answer (D-225).
        "locale": state["locale"],
        "account": str(identity.account_id),
        "body": str(body.id),
        "node": str(body.node_id),
        "money": await _money(db, identity.id),
        "constants": current().digest,
        #: No `admin` key here, and it is not an oversight: a name on that list
        #: cannot be registered at all (above), so a fresh identity never has
        #: the widget. It arrives on the next `hello`, if ever.
    }
