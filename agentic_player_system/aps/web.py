# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The agents panel: one admin from `.env`, a JSON API, a static page.

The panel is the only place the provider key is entered, and it never leaves
the server: the API reports whether a key is set, not the key.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import secrets as sealed
from . import settings as config
from .runner import Runner
from .store import AGENT_FIELDS, Store

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
COOKIE = "aps_session"

SETTINGS = config.load()
STORE = Store(SETTINGS.db_path)
RUNNER = Runner(SETTINGS, STORE)
SESSIONS: set[str] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=SETTINGS.log_level)
    if not SETTINGS.admin_password:
        log.warning("APS_ADMIN_PASSWORD is empty: the panel will refuse every login")
    task = asyncio.create_task(RUNNER.run())
    try:
        yield
    finally:
        RUNNER.stop()
        task.cancel()


app = FastAPI(title="Everse.Life agentic player system", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    """For the container's healthcheck: the process answers, the runner is up."""
    return {"ok": True, "stopping": RUNNER._stop.is_set()}


# --- auth ----------------------------------------------------------------------


def admin(request: Request) -> None:
    token = request.cookies.get(COOKIE)
    if not token or token not in SESSIONS:
        raise HTTPException(401, "войдите в панель")


class Login(BaseModel):
    login: str
    password: str


@app.post("/api/login")
async def login(body: Login, response: Response) -> dict[str, Any]:
    ok = (
        SETTINGS.admin_password
        and hmac.compare_digest(body.login, SETTINGS.admin_login)
        and hmac.compare_digest(body.password, SETTINGS.admin_password)
    )
    if not ok:
        raise HTTPException(401, "логин или пароль не подходят")
    token = secrets.token_urlsafe(32)
    SESSIONS.add(token)
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict")
    return {"ok": True}


@app.post("/api/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    SESSIONS.discard(request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE)
    return {"admin": bool(token and token in SESSIONS)}


# --- provider settings ---------------------------------------------------------


#: What the endpoints understand: OpenAI's ladder and Ollama's "none". An
#: unknown value is rejected by the provider itself, so a typo in the panel
#: would not cost one turn but every turn of every agent, with a 400 each.
REASONING_EFFORTS = ("", "none", "minimal", "low", "medium", "high")


class ProviderSettings(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    reasoning_effort: str = ""


@app.get("/api/settings", dependencies=[Depends(admin)])
async def get_settings() -> dict[str, Any]:
    provider = RUNNER.provider()
    return {
        "base_url": provider.base_url,
        "model": provider.model,
        "reasoning_effort": provider.reasoning_effort,
        "has_key": bool(provider.api_key),
        "key_from_env": not STORE.setting("llm.api_key") and bool(SETTINGS.llm_api_key),
        #: Whether passwords and the key are sealed at rest (APS_SECRET_KEY).
        "sealed": sealed.sealing(),
        "game_url": SETTINGS.game_url,
        "commands": len(RUNNER.reference),
    }


@app.put("/api/settings", dependencies=[Depends(admin)])
async def put_settings(body: ProviderSettings) -> dict[str, Any]:
    STORE.set_setting("llm.base_url", body.base_url.strip())
    STORE.set_setting("llm.model", body.model.strip())
    effort = body.reasoning_effort.strip().lower()
    if effort not in REASONING_EFFORTS:
        raise HTTPException(
            422, f"рассуждения: пусто или одно из {', '.join(REASONING_EFFORTS[1:])}"
        )
    STORE.set_setting("llm.reasoning_effort", effort)
    if body.api_key is not None and body.api_key.strip():
        STORE.set_setting("llm.api_key", body.api_key.strip())
    return await get_settings()


# --- agents --------------------------------------------------------------------


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)
    surname: str = ""
    age: int | None = None
    about: str = ""
    door: str = ""
    goal: str = ""
    persona: str = ""
    model: str = ""
    cadence_seconds: int = Field(default=300, ge=30)
    daily_token_budget: int = Field(default=300_000, ge=0)
    max_steps: int = Field(default=8, ge=1, le=40)
    history_limit: int = Field(default=20, ge=0, le=200)
    enabled: bool = False


class AgentPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None
    surname: str | None = None
    age: int | None = None
    about: str | None = None
    door: str | None = None
    goal: str | None = None
    persona: str | None = None
    model: str | None = None
    cadence_seconds: int | None = Field(default=None, ge=30)
    daily_token_budget: int | None = Field(default=None, ge=0)
    max_steps: int | None = Field(default=None, ge=1, le=40)
    history_limit: int | None = Field(default=None, ge=0, le=200)
    enabled: bool | None = None
    notes: str | None = None


def _public(agent: dict[str, Any]) -> dict[str, Any]:
    shown = {k: v for k, v in agent.items() if k not in ("password", "token")}
    shown["usage_today"] = STORE.usage_today(agent["id"])
    shown["busy"] = agent["id"] in RUNNER.busy
    return shown


@app.get("/api/agents", dependencies=[Depends(admin)])
async def list_agents() -> list[dict[str, Any]]:
    return [_public(a) for a in STORE.agents()]


@app.post("/api/agents", dependencies=[Depends(admin)])
async def create_agent(body: AgentIn) -> dict[str, Any]:
    data = body.model_dump()
    data["enabled"] = int(data["enabled"])
    return _public(STORE.create_agent(data))


@app.get("/api/agents/{agent_id}", dependencies=[Depends(admin)])
async def get_agent(agent_id: str) -> dict[str, Any]:
    agent = STORE.agent(agent_id)
    if agent is None:
        raise HTTPException(404, "нет такого агента")
    return _public(agent)


@app.patch("/api/agents/{agent_id}", dependencies=[Depends(admin)])
async def patch_agent(agent_id: str, body: AgentPatch) -> dict[str, Any]:
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    if "enabled" in data:
        data["enabled"] = int(data["enabled"])
    if "email" in data or "password" in data:
        #: New credentials: the old token belongs to the old account.
        data["token"] = None
        data["account_id"] = None
    if data.get("enabled"):
        data["paused_until"] = None
        data["pause_reason"] = ""
    agent = STORE.update_agent(agent_id, data)
    if agent is None:
        raise HTTPException(404, "нет такого агента")
    return _public(agent)


@app.delete("/api/agents/{agent_id}", dependencies=[Depends(admin)])
async def delete_agent(agent_id: str) -> dict[str, Any]:
    STORE.delete_agent(agent_id)
    return {"ok": True}


@app.post("/api/agents/{agent_id}/turn", dependencies=[Depends(admin)])
async def turn_now(agent_id: str) -> dict[str, Any]:
    if STORE.agent(agent_id) is None:
        raise HTTPException(404, "нет такого агента")
    RUNNER.request_turn(agent_id)
    return {"ok": True}


@app.get("/api/agents/{agent_id}/events", dependencies=[Depends(admin)])
async def agent_events(
    agent_id: str,
    before: int | None = None,
    after: int | None = None,
    limit: int = 50,
    kinds: str = "",
) -> list[dict[str, Any]]:
    wanted = tuple(k for k in kinds.split(",") if k)
    return STORE.events(
        agent_id, limit=max(1, min(limit, 500)), before=before, after=after, kinds=wanted
    )


# --- reports and usage ---------------------------------------------------------


@app.get("/api/reports", dependencies=[Depends(admin)])
async def reports(all: bool = False) -> list[dict[str, Any]]:
    return STORE.reports(include_resolved=all)


@app.post("/api/reports/{report_id}/resolve", dependencies=[Depends(admin)])
async def resolve_report(report_id: int, resolved: bool = True) -> dict[str, Any]:
    STORE.resolve_report(report_id, resolved)
    return {"ok": True}


@app.get("/api/usage", dependencies=[Depends(admin)])
async def usage() -> list[dict[str, Any]]:
    return STORE.usage_all()


@app.get("/api/commands", dependencies=[Depends(admin)])
async def command_reference() -> dict[str, Any]:
    return RUNNER.reference


# --- the page ------------------------------------------------------------------


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


__all__ = ["AGENT_FIELDS", "app"]
