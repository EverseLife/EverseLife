"""Account: email, password, session token, account panel (D-187).

The account is payment and device, the identity is the game (05-domain-model).
There is not a single game rule here: only identification and the character's
self-description. Everything the engine decides -- name, reputation,
citizenship -- lives in `world` and `city`.

The password is stored as an Argon2id hash by the same library that computes
the device fee (`engine/pow.py`): the project needs no second cryptography.
The session token is issued at login and stored **as a hash**: a leaked table
does not let anyone in.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.identity import Account, Identity, Line, LoginToken
from src.runtime import (
    CHARACTER_ABOUT_LIMIT,
    CHARACTER_AGE_MAX,
    CHARACTER_AGE_MIN,
    CHARACTER_NAME_LIMIT,
    CHARACTER_SURNAME_LIMIT,
    LOGIN_TOKEN_BYTES,
    LOGIN_TOKEN_TTL,
    PASSWORD_MIN_LENGTH,
)


class AccountError(Exception):
    """A refusal of identification or the account panel. Not a server error."""


#: Enough to cut off typos; the real check will be done by a letter once mail
#: delivery exists. Stricter would mean refusing live addresses.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hasher = PasswordHasher()

#: Character lines for the selection screen: name, how it differs, whether
#: playable. The text is from the world setting (10-world/00, 10-world/03), not a lore secret.
LINES: list[dict[str, Any]] = [
    {
        "id": Line.HUMAN.value,
        "name": "Человек-киборг",
        "world": "Терра",
        "playable": True,
        "summary": (
            "Изделие машины Предтеч: тело печатает биопринтер, личность хранит "
            "Сеть. Смерть отнимает вещи, но не вас."
        ),
        "traits": [
            "Стартовый мир — Терра: столица, шахты, поймы, дороги, которых ещё нет",
            "Ремесло — знание: рецепты берутся в Библиотеке и торгуются",
            "Прогресс — в имуществе, репутации и связях, а не в уровне",
            "Печать нового тела стоит энергии и железа города — или ждёт у Принтера Предтеч",
        ],
    },
    {
        "id": Line.NYMPH.value,
        "name": "Нимфа",
        "world": "Акватика",
        "playable": False,
        "summary": (
            "Вторая линия и вторая планета: океан, ярусы глубины и иная "
            "производственная традиция. Ещё в разработке."
        ),
        "traits": [
            "Планета-океан Акватика: острова и глубина вместо шахт и пашен",
            "Живая, а не печатная традиция: своя экономика и свои институты",
            "Появится, когда механики обкатаны на людях (D-104)",
        ],
    },
]


# --- email and password ------------------------------------------------------


def normalize_email(raw: Any) -> str:
    """Email is lower-cased: one address -- one account."""
    email = str(raw or "").strip().lower()
    if not email or not _EMAIL.match(email):
        raise AccountError("почта выглядит неправильно")
    return email


def check_password(raw: Any) -> str:
    password = str(raw or "")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AccountError(f"пароль короче {PASSWORD_MIN_LENGTH} знаков")
    return password


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(account: Account, password: str) -> bool:
    if not account.password_hash:
        return False
    try:
        return _hasher.verify(account.password_hash, password)
    except VerifyMismatchError:
        return False


async def by_email(session: AsyncSession, email: str) -> Account | None:
    return (
        await session.execute(select(Account).where(Account.email == email))
    ).scalar_one_or_none()


async def set_credentials(
    session: AsyncSession, account: Account, email: str, password: str
) -> None:
    """Set email and password. An email taken by another account -- refusal."""
    email = normalize_email(email)
    other = await by_email(session, email)
    if other is not None and other.id != account.id:
        raise AccountError("эта почта уже занята")
    account.email = email
    account.password_hash = hash_password(check_password(password))
    await session.flush()


async def login(session: AsyncSession, email: Any, password: Any) -> Account:
    """Identify by email and password. The same refusal for both cases: a
    foreign address and a wrong password need not be told apart from outside."""
    try:
        address = normalize_email(email)
    except AccountError:
        raise AccountError("почта или пароль не подходят") from None
    account = await by_email(session, address)
    if account is None or account.disabled_at is not None:
        raise AccountError("почта или пароль не подходят")
    if not verify_password(account, str(password or "")):
        raise AccountError("почта или пароль не подходят")
    return account


# --- session token -----------------------------------------------------------


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(session: AsyncSession, account: Account) -> str:
    """A new token. The token itself goes out, its hash stays in the database."""
    token = secrets.token_urlsafe(LOGIN_TOKEN_BYTES)
    session.add(
        LoginToken(
            account_id=account.id,
            token_hash=_digest(token),
            expires_at=datetime.now(UTC) + LOGIN_TOKEN_TTL,
        )
    )
    await session.flush()
    return token


async def by_token(session: AsyncSession, token: Any) -> Account:
    """The account by token. Expired and revoked -- the same refusal."""
    raw = str(token or "")
    if not raw:
        raise AccountError("жетон пуст")
    found = (
        await session.execute(
            select(LoginToken).where(LoginToken.token_hash == _digest(raw))
        )
    ).scalar_one_or_none()
    if (
        found is None
        or found.revoked_at is not None
        or found.expires_at <= datetime.now(UTC)
    ):
        raise AccountError("сессия истекла: войдите заново")
    account = await session.get(Account, found.account_id)
    if account is None or account.disabled_at is not None:
        raise AccountError("сессия истекла: войдите заново")
    return account


async def revoke_token(session: AsyncSession, token: Any) -> None:
    """Logout: the token no longer identifies. Somebody else's or unknown -- silently."""
    found = (
        await session.execute(
            select(LoginToken).where(LoginToken.token_hash == _digest(str(token or "")))
        )
    ).scalar_one_or_none()
    if found is not None and found.revoked_at is None:
        found.revoked_at = datetime.now(UTC)
        await session.flush()


async def revoke_all(session: AsyncSession, account: Account) -> None:
    """A password change revokes all tokens: old sessions do not survive it."""
    now = datetime.now(UTC)
    for token in (
        await session.execute(
            select(LoginToken).where(
                LoginToken.account_id == account.id, LoginToken.revoked_at.is_(None)
            )
        )
    ).scalars():
        token.revoked_at = now
    await session.flush()


# --- character ---------------------------------------------------------------


def check_name(raw: Any) -> str:
    """The name is the only thing the engine considers its own (D-011): unique and
    unchangeable. Length and non-emptiness are checked here, uniqueness in `world.spawn`."""
    name = " ".join(str(raw or "").split())
    if not name:
        raise AccountError("имя не названо")
    if len(name) > CHARACTER_NAME_LIMIT:
        raise AccountError(f"имя длиннее {CHARACTER_NAME_LIMIT} знаков")
    return name


def check_profile(message: dict[str, Any]) -> dict[str, Any]:
    """Surname, age, description -- self-description, but within limits."""
    surname = " ".join(str(message.get("surname") or "").split())
    if len(surname) > CHARACTER_SURNAME_LIMIT:
        raise AccountError(f"фамилия длиннее {CHARACTER_SURNAME_LIMIT} знаков")
    about = str(message.get("about") or "").strip()
    if len(about) > CHARACTER_ABOUT_LIMIT:
        raise AccountError(f"описание длиннее {CHARACTER_ABOUT_LIMIT} знаков")
    age_raw = message.get("age")
    age: int | None = None
    if age_raw not in (None, ""):
        try:
            age = int(age_raw)
        except (TypeError, ValueError):
            raise AccountError("возраст — число") from None
        if not CHARACTER_AGE_MIN <= age <= CHARACTER_AGE_MAX:
            raise AccountError(f"возраст от {CHARACTER_AGE_MIN} до {CHARACTER_AGE_MAX}")
    return {"surname": surname, "age": age, "about": about}


def check_line(raw: Any) -> Line:
    """Line: one is playable in the alpha (D-104). Nymphs are "still in development"."""
    try:
        line = Line(str(raw or Line.HUMAN.value))
    except ValueError:
        raise AccountError("такой линии нет") from None
    playable = {entry["id"] for entry in LINES if entry["playable"]}
    if line.value not in playable:
        raise AccountError("эта линия ещё в разработке")
    return line


def apply_profile(identity: Identity, profile: dict[str, Any]) -> None:
    identity.surname = profile["surname"]
    identity.age = profile["age"]
    identity.about = profile["about"]


def profile(account: Account, identity: Identity) -> dict[str, Any]:
    """Account panel: what the player sees about themselves. Nothing game-related here."""
    return {
        "email": account.email,
        "name": identity.name,
        "surname": identity.surname,
        "age": identity.age,
        "about": identity.about,
        "line": identity.line.value,
        "since": identity.created_at.isoformat(),
    }


async def lines(session: AsyncSession) -> list[dict[str, Any]]:
    """Lines with the number of players: the selection screen must show a living world."""
    counts = dict(
        (
            await session.execute(
                select(Identity.line, func.count()).group_by(Identity.line)
            )
        ).all()
    )
    return [
        entry | {"players": int(counts.get(Line(entry["id"]), 0))} for entry in LINES
    ]


async def account_of(session: AsyncSession, identity: Identity) -> Account:
    account = await session.get(Account, identity.account_id)
    if account is None:  # pragma: no cover -- an identity without an account is a bug
        raise RuntimeError(f"у личности {identity.id} нет аккаунта")
    return account
