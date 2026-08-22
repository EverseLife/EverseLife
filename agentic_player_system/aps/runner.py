# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The runner: every agent gets a turn on its own cadence, within its own budget.

Protection from the two ways an agent wastes money lives here and not in the
game (D-224): a daily token budget, and a pause when the same action is
refused over and over.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from . import brain, commands, llm
from .game import Game, GameError, Refused
from .settings import Settings
from .store import Store

log = logging.getLogger(__name__)

#: The same refused action this many times in a row in one turn -> a pause.
STUCK_REPEATS = 4
STUCK_PAUSE_TURNS = 5
ERROR_BACKOFF = timedelta(minutes=5)
#: Woken a little after the body is free: the world's job must have run.
BUSY_MARGIN = timedelta(seconds=20)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class Runner:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.reference: dict[str, dict[str, Any]] = {}
        self.busy: set[str] = set()
        self.wanted: set[str] = set()
        self._stop = asyncio.Event()

    def provider(self) -> llm.Provider:
        return llm.Provider(
            base_url=self.store.setting("llm.base_url") or self.settings.llm_base_url,
            api_key=self.store.setting("llm.api_key") or self.settings.llm_api_key,
            model=self.store.setting("llm.model") or self.settings.llm_model,
        )

    def load_reference(self) -> None:
        cached = self.store.setting("commands.cache", "")
        self.reference = commands.load(self.settings.session_source, cached)
        if self.settings.session_source.exists():
            self.store.set_setting("commands.cache", json.dumps(self.reference, ensure_ascii=False))
        log.info("protocol reference: %d commands", len(self.reference))

    def request_turn(self, agent_id: str) -> None:
        self.wanted.add(agent_id)

    async def run(self) -> None:
        self.load_reference()
        while not self._stop.is_set():
            try:
                self._sweep()
            except Exception:
                log.exception("sweep failed")
            await asyncio.sleep(2)

    def stop(self) -> None:
        self._stop.set()

    def _sweep(self) -> None:
        now = datetime.now(UTC)
        for agent in self.store.agents():
            agent_id = agent["id"]
            if agent_id in self.busy:
                continue
            forced = agent_id in self.wanted
            if not forced:
                if not agent["enabled"]:
                    continue
                paused = _parse(agent["paused_until"])
                if paused is not None and paused > now:
                    continue
                due = _parse(agent["next_run_at"])
                if due is not None and due > now:
                    continue
            self.wanted.discard(agent_id)
            self.busy.add(agent_id)
            asyncio.create_task(self._turn(agent_id, forced=forced))

    async def _turn(self, agent_id: str, *, forced: bool) -> None:
        store = self.store
        try:
            agent = store.agent(agent_id)
            if agent is None:
                return
            cadence = max(30, int(agent["cadence_seconds"] or 300))
            next_run = datetime.now(UTC) + timedelta(seconds=cadence)

            budget = int(agent["daily_token_budget"] or 0)
            spent = store.usage_today(agent_id)["total"]
            if budget and spent >= budget and not forced:
                tomorrow = datetime.now(UTC).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
                store.update_agent(
                    agent_id,
                    {"next_run_at": _iso(tomorrow), "pause_reason": "дневной бюджет исчерпан"},
                )
                store.event(
                    agent_id, "system", text=f"дневной бюджет исчерпан: {spent} из {budget} токенов"
                )
                return

            provider = self.provider()
            if not provider.configured:
                store.update_agent(
                    agent_id,
                    {"next_run_at": _iso(next_run), "last_error": "провайдер модели не настроен"},
                )
                return

            game = Game(self.settings.game_url, self.settings.ws_url)
            try:
                await game.connect()
                await self._identify(agent, game)
                turn = await brain.run_turn(
                    agent=agent, game=game, store=store, provider=provider, reference=self.reference
                )
            finally:
                await game.close()

            #: Wake-up: the cadence, unless the world or the agent says later.
            #: A busy body cannot act, and a model woken up to say "still
            #: walking" is tokens for nothing (D-224).
            now = datetime.now(UTC)
            reason = "по расписанию"
            if turn.busy_until is not None and turn.busy_until + BUSY_MARGIN > next_run:
                next_run = turn.busy_until + BUSY_MARGIN
                reason = "тело занято"
            if turn.wait_seconds and now + timedelta(seconds=turn.wait_seconds) > next_run:
                next_run = now + timedelta(seconds=turn.wait_seconds)
                reason = "агент попросил подождать"
            update: dict[str, Any] = {
                "next_run_at": _iso(next_run),
                "last_error": "",
                "pause_reason": "",
                "token": game.token,
                "account_id": game.account,
            }
            if self._stuck(turn):
                until = datetime.now(UTC) + timedelta(seconds=cadence * STUCK_PAUSE_TURNS)
                update |= {
                    "paused_until": _iso(until),
                    "pause_reason": "повторяет отказанное действие",
                }
                store.event(
                    agent_id,
                    "system",
                    text=f"пауза до {_iso(until)}: одно и то же действие отказано {STUCK_REPEATS} раз подряд",
                )
            store.update_agent(agent_id, update)
            store.event(
                agent_id,
                "turn",
                text=(
                    f"ход: {turn.steps} шагов, {turn.prompt_tokens}+{turn.completion_tokens} "
                    f"токенов; следующий {_iso(next_run)} ({reason})"
                ),
            )
        except (GameError, Refused, llm.ModelError) as trouble:
            log.warning("agent %s: %s", agent_id, trouble)
            store.event(agent_id, "error", text=str(trouble))
            store.update_agent(
                agent_id,
                {
                    "next_run_at": _iso(datetime.now(UTC) + ERROR_BACKOFF),
                    "last_error": str(trouble),
                },
            )
        except Exception as trouble:
            log.exception("agent %s crashed", agent_id)
            store.event(agent_id, "error", text=f"внутренняя ошибка: {trouble!r}")
            store.update_agent(
                agent_id,
                {
                    "next_run_at": _iso(datetime.now(UTC) + ERROR_BACKOFF),
                    "last_error": repr(trouble),
                },
            )
        finally:
            self.busy.discard(agent_id)

    async def _identify(self, agent: dict[str, Any], game: Game) -> None:
        """Log in; a fresh account registers itself at the chosen door first."""
        try:
            await game.hello(token=agent["token"], email=agent["email"], password=agent["password"])
            return
        except Refused as refusal:
            first = str(refusal)
        try:
            answer = await game.join(
                email=agent["email"],
                password=agent["password"],
                name=agent["name"],
                door=agent["door"] or "",
                surname=agent["surname"] or "",
                age=agent["age"],
                about=agent["about"] or "",
            )
        except Refused as refusal:
            raise Refused(f"вход не удался ({first}); регистрация тоже: {refusal}") from refusal
        self.store.event(
            agent["id"],
            "system",
            text=f"зарегистрирован: {answer.get('hello')} у двери {answer.get('node')}, на руках {answer.get('money')}",
        )

    @staticmethod
    def _stuck(turn: brain.Turn) -> bool:
        tail = turn.actions[-STUCK_REPEATS:]
        if len(tail) < STUCK_REPEATS:
            return False
        return all(not ok for _, _, ok in tail) and len({(c, a) for c, a, _ in tail}) == 1
