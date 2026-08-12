"""Сессия клиента — единственное место, где игрок действует.

Античит держится не на защите клиента, а на том, что **API действий не
существует** (60-meta/01-anti-cheat). Присутственное действие идёт только
отсюда и только после платы устройства (D-110).

Протокол намеренно скучный: JSON поверх WebSocket, одна команда — один ответ.
Ответ на любую команду добычи — `Sight`, то есть ровно то, что игрок видит.
Устойчивости свода там нет: она не «скрыта в интерфейсе», её не существует
в ответе вовсе.

**Опознание аккаунта — заглушка разработки.** Настоящая аутентификация
приезжает вместе с подпиской (Э7, D-027), и притворяться, что она уже есть,
хуже, чем честно назвать заглушку заглушкой.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octoverse.constants import current
from octoverse.db.base import session_factory
from octoverse.engine import mining
from octoverse.engine import pow as device
from octoverse.models.identity import Body, BodyState, Identity
from octoverse.models.mining import MiningSession, Pace, PowChallenge, SessionState
from octoverse.models.world import Vein

log = logging.getLogger(__name__)

router = APIRouter(tags=["сессия"])


class Refused(Exception):
    """Команда отклонена по правилам игры. Это не ошибка сервера."""


@router.websocket("/session/ws")
async def play(socket: WebSocket) -> None:
    await socket.accept()
    state: dict[str, Any] = {"identity_id": None}

    try:
        while True:
            message = await socket.receive_json()
            try:
                answer = await _dispatch(state, message)
            except Refused as refusal:
                answer = {"refused": str(refusal)}
            except mining.MiningError as refusal:
                answer = {"refused": str(refusal)}
            except device.PowError as refusal:
                answer = {"refused": str(refusal)}
            await socket.send_json(answer)
    except WebSocketDisconnect:
        #: Уход игрока не закрывает сессию добычи: она живёт до «уйти» либо
        #: до обрушения. Добытое лежит в забое и ждёт решения.
        log.info("сессия отключилась, личность %s", state.get("identity_id"))


async def _dispatch(state: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    command = message.get("cmd")
    if command is None:
        raise Refused("команда не названа")

    async with session_factory()() as db, db.begin():
        if command == "hello":
            return await _hello(state, db, message)

        identity_id = state.get("identity_id")
        if identity_id is None:
            raise Refused("сначала hello")

        handler = _COMMANDS.get(command)
        if handler is None:
            raise Refused(f"нет такой команды: {command}")
        return await handler(state, db, message)


async def _hello(state: dict, db: AsyncSession, message: dict) -> dict:
    """Заглушка опознания: клиент называет личность по имени."""
    name = message.get("name")
    identity = (
        await db.execute(select(Identity).where(Identity.name == name))
    ).scalar_one_or_none()
    if identity is None:
        raise Refused(f"нет личности {name!r}")

    state["identity_id"] = identity.id
    body = await _body(db, identity.id)
    return {
        "hello": identity.name,
        "body": None if body is None else str(body.id),
        "node": None if body is None else str(body.node_id),
        "constants": current().digest,
    }


async def _challenge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Выдать задачу платы устройства. Клиент считает её в Web Worker."""
    identity = await db.get(Identity, state["identity_id"])
    if identity is None:  # pragma: no cover
        raise Refused("личность исчезла")
    task = await device.issue(db, current(), identity.account_id)
    return {"challenge": str(task.id), "nonce": task.nonce.hex()}


async def _mine_start(state: dict, db: AsyncSession, message: dict) -> dict:
    """Открыть забой. Без оплаченной задачи сессия не начинается."""
    constants = current()
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused("нет живого тела")

    task = await db.get(PowChallenge, uuid.UUID(message["challenge"]))
    if task is None or task.account_id != (await db.get(Identity, body.identity_id)).account_id:
        raise Refused("задача не ваша")
    await device.verify(db, constants, task, bytes.fromhex(message["answer"]))

    vein = await db.get(Vein, uuid.UUID(message["vein"]))
    if vein is None:
        raise Refused("нет такой жилы")

    session = await mining.start(
        db,
        constants,
        body,
        vein,
        tool_item_id=_optional_uuid(message.get("tool")),
        pace=Pace(message.get("pace", Pace.STEADY.value)),
    )
    task.spent_on_session_id = session.id
    state["session_id"] = session.id
    return _sight(session, await mining.sight(db, constants, session))


async def _mine_swing(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    return _sight(session, await mining.swing(db, current(), session))


async def _mine_timber(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    return _sight(session, await mining.timber(db, current(), session))


async def _mine_pace(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    pace = Pace(message["pace"])
    return _sight(session, await mining.set_pace(db, current(), session, pace))


async def _mine_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    session = await _active(state, db)
    haul = await mining.leave(db, current(), session)
    state.pop("session_id", None)
    return {"left": True, "haul": haul}


_COMMANDS = {
    "pow.challenge": _challenge,
    "mine.start": _mine_start,
    "mine.swing": _mine_swing,
    "mine.timber": _mine_timber,
    "mine.pace": _mine_pace,
    "mine.leave": _mine_leave,
}


def _sight(session: MiningSession, sight: mining.Sight) -> dict[str, Any]:
    """Наружу уходит только то, что видит игрок.

    Собирается из `Sight`, а не из модели сессии, — чтобы скрытое число
    физически не могло попасть в ответ по недосмотру.
    """
    payload = asdict(sight)
    payload["pace"] = sight.pace.value
    payload["state"] = sight.state.value
    payload["session"] = str(session.id)
    return payload


async def _body(db: AsyncSession, identity_id: uuid.UUID) -> Body | None:
    stmt = select(Body).where(Body.identity_id == identity_id, Body.state == BodyState.ALIVE)
    return (await db.execute(stmt)).scalars().first()


async def _active(state: dict, db: AsyncSession) -> MiningSession:
    session_id = state.get("session_id")
    if session_id is None:
        #: Клиент мог переподключиться — ищем открытую сессию тела.
        body = await _body(db, state["identity_id"])
        if body is None:
            raise Refused("нет живого тела")
        found = (
            await db.execute(
                select(MiningSession).where(
                    MiningSession.body_id == body.id,
                    MiningSession.state == SessionState.ACTIVE,
                )
            )
        ).scalars().first()
        if found is None:
            raise Refused("сессия не открыта")
        state["session_id"] = found.id
        return found

    session = await db.get(MiningSession, session_id)
    if session is None:  # pragma: no cover
        raise Refused("сессия исчезла")
    return session


def _optional_uuid(value: str | None) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(value)
