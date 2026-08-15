"""Аккаунт: почта, пароль, жетон сессии, кабинет (D-187).

Аккаунт — оплата и устройство, личность — игра (05-domain-model). Здесь нет
ни одного игрового правила: только опознание и самоописание персонажа. Всё,
что решает движок — имя, репутация, гражданство, — живёт в `world` и `city`.

Пароль хранится хэшем Argon2id той же библиотекой, что считает плату
устройства (`engine/pow.py`): второй криптографии проекту не нужно. Жетон
сессии выдаётся при входе и хранится **хэшем**: утёкшая таблица не даёт войти.
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
    """Отказ опознания или кабинета. Не ошибка сервера."""


#: Достаточно, чтобы отсечь опечатки; настоящую проверку сделает письмо, когда
#: появится рассылка. Строже — значит отказывать живым адресам.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hasher = PasswordHasher()

#: Линии персонажа для экрана выбора: имя, чем отличается, играбельна ли.
#: Текст — из установки мира (10-world/00, 10-world/03), не тайна лора.
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


# --- почта и пароль ---------------------------------------------------------


def normalize_email(raw: Any) -> str:
    """Почта приводится к нижнему регистру: один адрес — один аккаунт."""
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
    """Назначить почту и пароль. Занятая чужим аккаунтом почта — отказ."""
    email = normalize_email(email)
    other = await by_email(session, email)
    if other is not None and other.id != account.id:
        raise AccountError("эта почта уже занята")
    account.email = email
    account.password_hash = hash_password(check_password(password))
    await session.flush()


async def login(session: AsyncSession, email: Any, password: Any) -> Account:
    """Опознать по почте и паролю. Отказ один и тот же на оба случая: чужой
    адрес и чужой пароль отличать снаружи не нужно."""
    try:
        адрес = normalize_email(email)
    except AccountError:
        raise AccountError("почта или пароль не подходят") from None
    account = await by_email(session, адрес)
    if account is None or account.disabled_at is not None:
        raise AccountError("почта или пароль не подходят")
    if not verify_password(account, str(password or "")):
        raise AccountError("почта или пароль не подходят")
    return account


# --- жетон сессии -------------------------------------------------------------


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(session: AsyncSession, account: Account) -> str:
    """Новый жетон. Наружу уходит сам жетон, в базе остаётся его хэш."""
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
    """Аккаунт по жетону. Просроченный и отозванный — тот же отказ."""
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
    """Выход: жетон больше не опознаёт. Чужой или неизвестный — молча."""
    found = (
        await session.execute(
            select(LoginToken).where(LoginToken.token_hash == _digest(str(token or "")))
        )
    ).scalar_one_or_none()
    if found is not None and found.revoked_at is None:
        found.revoked_at = datetime.now(UTC)
        await session.flush()


async def revoke_all(session: AsyncSession, account: Account) -> None:
    """Смена пароля отзывает все жетоны: старые сессии не переживают её."""
    now = datetime.now(UTC)
    for жетон in (
        await session.execute(
            select(LoginToken).where(
                LoginToken.account_id == account.id, LoginToken.revoked_at.is_(None)
            )
        )
    ).scalars():
        жетон.revoked_at = now
    await session.flush()


# --- персонаж ---------------------------------------------------------------


def check_name(raw: Any) -> str:
    """Имя — единственное, что движок считает своим (D-011): уникально и
    несменяемо. Проверка длины и непустоты — здесь, уникальности — в `world.spawn`."""
    name = " ".join(str(raw or "").split())
    if not name:
        raise AccountError("имя не названо")
    if len(name) > CHARACTER_NAME_LIMIT:
        raise AccountError(f"имя длиннее {CHARACTER_NAME_LIMIT} знаков")
    return name


def check_profile(message: dict[str, Any]) -> dict[str, Any]:
    """Фамилия, возраст, описание — самоописание, но в пределах."""
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
    """Линия: в альфе играбельна одна (D-104). Нимфы — «ещё в разработке»."""
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
    """Кабинет: что игрок видит о себе. Игрового здесь нет."""
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
    """Линии с числом играющих: экран выбора обязан показывать живой мир."""
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
    if account is None:  # pragma: no cover — личность без аккаунта это баг
        raise RuntimeError(f"у личности {identity.id} нет аккаунта")
    return account
