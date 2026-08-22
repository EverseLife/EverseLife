# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The game through a player's eyes: one WebSocket, one command -- one reply.

This is the browser client rewritten in forty lines, on purpose. The agent
gets nothing the browser does not get: `hello`/`join` for identification,
`/public/*` for the catalogs, and the device fee (D-110) computed by the same
Argon2id call the client's Web Worker makes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Self

import httpx
import websockets
from argon2.low_level import Type, hash_secret_raw

KIB_PER_MIB = 1024
POW_HASH_BYTES = 32
POW_PARALLELISM = 1


class GameError(Exception):
    """Transport or protocol trouble -- not a refusal by the rules."""


class Refused(Exception):
    """The server said no, with the same words a player would read."""


def solve_fee(account: str, nonce_hex: str, values: dict[str, Any]) -> str:
    return hash_secret_raw(
        secret=uuid.UUID(account).bytes,
        salt=bytes.fromhex(nonce_hex),
        time_cost=int(values["pow.argon_iterations"]),
        memory_cost=int(float(values["pow.memory_per_session"]) * KIB_PER_MIB),
        parallelism=POW_PARALLELISM,
        hash_len=POW_HASH_BYTES,
        type=Type.ID,
    ).hex()


class Game:
    def __init__(self, base_url: str, ws_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url
        self.socket: Any = None
        self.account: str | None = None
        self.token: str | None = None
        self.name: str | None = None
        self._lock = asyncio.Lock()
        self._constants: dict[str, Any] | None = None
        self._credentials: tuple[str, str] | None = None
        #: Two-way socket (D-226): commands are numbered, and between answers
        #: the server speaks on its own. What it said waits here for the turn.
        self._ticket = 0
        self.events: list[dict[str, Any]] = []
        #: The last journal row heard: `since` of the next hello.
        self.seq = 0

    # --- public reads -----------------------------------------------------------

    async def public(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=30) as http:
            response = await http.get(f"{self.base_url}/public/{path.lstrip('/')}")
            response.raise_for_status()
            return response.json()

    async def constants(self) -> dict[str, Any]:
        if self._constants is None:
            self._constants = (await self.public("constants"))["values"]
        return self._constants

    # --- session ----------------------------------------------------------------

    async def connect(self) -> None:
        try:
            self.socket = await websockets.connect(self.ws_url, max_size=16 * 1024 * 1024)
        except OSError as trouble:
            raise GameError(f"сервер недоступен: {trouble}") from trouble

    async def close(self) -> None:
        if self.socket is not None:
            await self.socket.close()
            self.socket = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def send(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """One command, one reply -- matched by number, not by order: events
        the server sends in between are kept in `events`. A `refused` reply
        becomes `Refused`."""
        if self.socket is None:
            raise GameError("нет соединения")
        self._ticket += 1
        ticket = self._ticket
        payload = {"id": ticket, "cmd": cmd, **(args or {})}
        async with self._lock:
            try:
                await self.socket.send(json.dumps(payload, ensure_ascii=False))
                while True:
                    raw = await asyncio.wait_for(self.socket.recv(), timeout=120)
                    answer = json.loads(raw)
                    if not isinstance(answer, dict):
                        continue
                    if "event" in answer:
                        self._heard(answer)
                        continue
                    if answer.get("id") == ticket:
                        break
            except (websockets.ConnectionClosed, OSError, TimeoutError) as trouble:
                raise GameError(f"соединение оборвалось: {trouble}") from trouble
        answer.pop("id", None)
        if "refused" in answer:
            raise Refused(str(answer["refused"]))
        return answer

    def _heard(self, happening: dict[str, Any]) -> None:
        seq = happening.get("seq")
        if isinstance(seq, int) and seq > self.seq:
            self.seq = seq
        self.events.append(happening)

    async def drain(self) -> None:
        """Pull whatever the server said while nobody was reading the socket."""
        if self.socket is None:
            return
        async with self._lock:
            while True:
                try:
                    raw = await asyncio.wait_for(self.socket.recv(), timeout=0.05)
                except TimeoutError:
                    return
                except (websockets.ConnectionClosed, OSError):
                    return
                answer = json.loads(raw)
                if isinstance(answer, dict) and "event" in answer:
                    self._heard(answer)

    def take_events(self) -> list[dict[str, Any]]:
        """What happened since the last turn, once."""
        taken, self.events = self.events, []
        return taken

    async def hello(self, *, token: str | None, email: str, password: str) -> dict[str, Any]:
        self._credentials = (email, password)
        #: `since` turns the stream on and replays what was missed since the
        #: last row heard; 0 means from now on.
        if token:
            try:
                return self._identified(
                    await self.send("hello", {"token": token, "since": self.seq})
                )
            except Refused:
                pass
        return self._identified(
            await self.send("hello", {"email": email, "password": password, "since": self.seq})
        )

    async def reconnect(self) -> None:
        """The socket dropped mid-turn: open a new one and identify again."""
        await self.close()
        await self.connect()
        if self._credentials is None:
            raise GameError("переподключиться нельзя: личность не известна")
        email, password = self._credentials
        await self.hello(token=self.token, email=email, password=password)

    async def join(
        self,
        *,
        email: str,
        password: str,
        name: str,
        door: str = "",
        surname: str = "",
        age: int | None = None,
        about: str = "",
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "email": email,
            "password": password,
            "name": name,
            "line": "human",
            "surname": surname,
            "about": about,
        }
        if age is not None:
            args["age"] = age
        if door:
            args["node"] = door
        self._credentials = (email, password)
        return self._identified(await self.send("join", {**args, "since": 0}))

    def _identified(self, answer: dict[str, Any]) -> dict[str, Any]:
        self.account = answer.get("account")
        self.token = answer.get("token")
        self.name = answer.get("hello")
        return answer

    # --- actions ----------------------------------------------------------------

    async def act(self, cmd: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a command; pay the device fee for `mine.start` the way the client does."""
        args = dict(args or {})
        if cmd == "mine.start" and "challenge" not in args:
            if not self.account:
                raise GameError("аккаунт не известен: нет hello")
            issued = await self.send("pow.challenge")
            values = await self.constants()
            answer = await asyncio.to_thread(solve_fee, self.account, issued["nonce"], values)
            args |= {"challenge": issued["challenge"], "answer": answer}
        return await self.send(cmd, args)
