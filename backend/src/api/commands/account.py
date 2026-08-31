# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Account, profile, cards of people.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.api import push
from src.api.commands.common import _alive, _identity
from src.api.commands.views import _identity_by_name
from src.api.registry import Refused, command
from src.engine import (
    account as accounts,
)
from src.engine import (
    bank,
    events,
)
from src.engine import city as town
from src.models.identity import Body, BodyState, Identity


@command("account.profile")
async def _account_profile(state: dict, db: AsyncSession, message: dict) -> dict:
    """Account panel: what the account knows about itself (D-187)."""
    identity = await _identity(state, db)
    account = await accounts.account_of(db, identity)
    return {"profile": accounts.profile(account, identity)}


@command("account.update")
async def _account_update(state: dict, db: AsyncSession, message: dict) -> dict:
    """Change surname, age, description. The name does not change (D-011)."""
    identity = await _identity(state, db)
    accounts.apply_profile(identity, accounts.check_profile(message))
    await db.flush()
    account = await accounts.account_of(db, identity)
    #: The profile is cached by the client (D-226); no journal event says it moved.
    await events.announce(
        db, touches=("profile",), identity_id=identity.id, event="account.updated"
    )
    return {"profile": accounts.profile(account, identity)}


@command("account.password")
async def _account_password(state: dict, db: AsyncSession, message: dict) -> dict:
    """Change password: the old one is required, all previous sessions are
    revoked, and this one gets a new token."""
    identity = await _identity(state, db)
    account = await accounts.account_of(db, identity)
    if not accounts.verify_password(account, str(message.get("old") or "")):
        raise Refused(key="cmd-old-password-wrong")
    new = accounts.check_password(message.get("new"))
    if message.get("new_again") is not None and message["new_again"] != new:
        raise Refused(key="cmd-passwords-differ")
    account.password_hash = accounts.hash_password(new)
    await accounts.revoke_all(db, account)
    issued = await accounts.issue_token(db, account)
    state["token"] = issued
    return {"token": issued}


@command("account.email")
async def _account_email(state: dict, db: AsyncSession, message: dict) -> dict:
    """Change email: confirmed by password."""
    identity = await _identity(state, db)
    account = await accounts.account_of(db, identity)
    password = str(message.get("password") or "")
    if not accounts.verify_password(account, password):
        raise Refused(key="cmd-password-wrong")
    await accounts.set_credentials(db, account, str(message.get("email") or ""), password)
    #: The profile is cached by the client (D-226); no journal event says it moved.
    await events.announce(
        db, touches=("profile",), identity_id=identity.id, event="account.updated"
    )
    return {"profile": accounts.profile(account, identity)}


@command("account.locale")
async def _account_locale(state: dict, db: AsyncSession, message: dict) -> dict:
    """Choose the language the world is read in (D-249, D-251 wave III).

    Takes effect at once and for good: the session starts answering in it, and
    the next login reads it back off the account. No password: a language is
    not a credential, and asking for one would make changing it a chore.
    """
    identity = await _identity(state, db)
    account = await accounts.account_of(db, identity)
    said = str(message.get("locale") or "")
    #: Chosen, not read: an unknown language is refused rather than quietly
    #: replaced by the default. `ru-RU` is accepted and stored as `ru` -- the
    #: same spelling `hello` would have made of it.
    asked = i18n.spoken(said)
    if asked is None:
        raise Refused(key="session-locale-unknown", locale=said)
    account.locale = asked
    state["locale"] = asked
    await events.announce(
        db, touches=("profile",), identity_id=identity.id, event="account.updated"
    )
    return {"locale": asked}


@command("account.logout")
async def _account_logout(state: dict, db: AsyncSession, message: dict) -> dict:
    """Logout: this session's token is revoked, the socket forgets the identity."""
    await accounts.revoke_token(db, message.get("token") or state.get("token"))
    state["identity_id"] = None
    state["token"] = None
    sink: push.Sink | None = state.get("sink")
    if sink is not None:
        sink.listening = False
        sink.identity_id = None
        sink.node_id = None
        push.hub.dirty = True
    return {"bye": True}


@command("people.here")
async def _people_here(state: dict, db: AsyncSession, message: dict) -> dict:
    """Who is standing in this location.

    Needed to hand a thing to somebody: a name typed by hand would be a way to
    give things to anyone anywhere, and the point of handing over is that both
    people are in the same room. Those passing through are not in it -- the query
    asks for bodies in the node, and a body in transit is nowhere.
    """
    body = await _alive(state, db)
    rows = (
        await db.execute(
            select(Body, Identity)
            .join(Identity, Identity.id == Body.identity_id)
            .where(
                Body.node_id == body.node_id,
                Body.state == BodyState.ALIVE,
                Body.id != body.id,
            )
        )
    ).all()
    return {
        "people": sorted(
            ({"body": str(who.id), "name": person.name} for who, person in rows),
            key=lambda row: row["name"],
        )
    }


@command("identity.profile")
async def _identity_profile(state: dict, db: AsyncSession, message: dict) -> dict:
    """Somebody's card: what a person shows of themselves, and where they belong.

    Self-description only (D-187): no stamina, no pocket, no whereabouts.
    Names are public (D-058), and so is citizenship -- it is a record about
    the person, not the place.
    """
    await _identity(state, db)
    who = await _identity_by_name(db, str(message.get("name", "")))
    own_ = await town.citizenship(db, who.id)
    native = None if own_ is None else await town.by_id(db, own_.city_id)
    return {
        "profile": {
            "name": who.name,
            "surname": who.surname,
            "age": who.age,
            "about": who.about,
            "line": who.line.value,
            "since": who.created_at.isoformat(),
            "city": None if native is None else native.name,
        }
    }


@command("person.report")
async def _person_report(state: dict, db: AsyncSession, message: dict) -> dict:
    """Point at a defective print (D-173). Lowers trust, does not kill."""
    identity = await _identity(state, db)
    whom = await _identity_by_name(db, str(message["who"]))
    await bank.report_defect(db, identity, whom)
    return {"reported": whom.name}


@command("person.unreport")
async def _person_unreport(state: dict, db: AsyncSession, message: dict) -> dict:
    """Withdraw your report: one may err, and one must be able to correct it."""
    identity = await _identity(state, db)
    whom = await _identity_by_name(db, str(message["who"]))
    return {"withdrawn": await bank.withdraw_report(db, identity, whom)}
