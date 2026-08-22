# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Storage: agents, their journal, bug reports, provider settings -- one SQLite file.

The system is low-traffic (an agent acts once in minutes), so the standard
library's sqlite3 is enough and the runner does not need one more service.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    password TEXT NOT NULL,
    surname TEXT NOT NULL DEFAULT '',
    age INTEGER,
    about TEXT NOT NULL DEFAULT '',
    door TEXT NOT NULL DEFAULT '',
    goal TEXT NOT NULL DEFAULT '',
    persona TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    cadence_seconds INTEGER NOT NULL DEFAULT 300,
    daily_token_budget INTEGER NOT NULL DEFAULT 300000,
    max_steps INTEGER NOT NULL DEFAULT 8,
    enabled INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    token TEXT,
    account_id TEXT,
    next_run_at TEXT,
    paused_until TEXT,
    pause_reason TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    cmd TEXT NOT NULL DEFAULT '',
    request TEXT NOT NULL DEFAULT '',
    reply TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_agent_at ON events (agent_id, id);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    at TEXT NOT NULL,
    text TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
    agent_id TEXT NOT NULL,
    day TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, day)
);
"""

AGENT_FIELDS = (
    "name",
    "email",
    "password",
    "surname",
    "age",
    "about",
    "door",
    "goal",
    "persona",
    "model",
    "cadence_seconds",
    "daily_token_budget",
    "max_steps",
    "enabled",
)

RUNTIME_FIELDS = (
    "notes",
    "token",
    "account_id",
    "next_run_at",
    "paused_until",
    "pause_reason",
    "last_error",
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def _json(value: Any, limit: int = 20000) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False)[:limit]


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- settings -------------------------------------------------------------

    def setting(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def set_setting(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # --- agents ---------------------------------------------------------------

    def agents(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM agents ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return None if row is None else dict(row)

    def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        agent_id = uuid.uuid4().hex
        fields = {k: data.get(k) for k in AGENT_FIELDS if data.get(k) is not None}
        fields["id"] = agent_id
        fields["created_at"] = now_iso()
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self.db:
            self.db.execute(
                f"INSERT INTO agents ({columns}) VALUES ({marks})", tuple(fields.values())
            )
        result = self.agent(agent_id)
        assert result is not None
        return result

    def update_agent(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        allowed = set(AGENT_FIELDS) | set(RUNTIME_FIELDS)
        fields = {k: v for k, v in data.items() if k in allowed}
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            with self.db:
                self.db.execute(
                    f"UPDATE agents SET {sets} WHERE id = ?", (*fields.values(), agent_id)
                )
        return self.agent(agent_id)

    def delete_agent(self, agent_id: str) -> None:
        with self.db:
            for table in ("agents", "events", "reports", "usage"):
                column = "id" if table == "agents" else "agent_id"
                self.db.execute(f"DELETE FROM {table} WHERE {column} = ?", (agent_id,))

    # --- journal --------------------------------------------------------------

    def event(
        self,
        agent_id: str,
        kind: str,
        *,
        cmd: str = "",
        request: Any = None,
        reply: Any = None,
        text: str = "",
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO events (agent_id, at, kind, cmd, request, reply, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (agent_id, now_iso(), kind, cmd, _json(request), _json(reply), text),
            )

    def events(
        self, agent_id: str, *, limit: int = 100, before: int | None = None
    ) -> list[dict[str, Any]]:
        if before is None:
            rows = self.db.execute(
                "SELECT * FROM events WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM events WHERE agent_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
                (agent_id, before, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def recent(self, agent_id: str, kinds: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in kinds)
        rows = self.db.execute(
            f"SELECT * FROM events WHERE agent_id = ? AND kind IN ({marks}) "
            "ORDER BY id DESC LIMIT ?",
            (agent_id, *kinds, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def report(self, agent_id: str, text: str, context: Any = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO reports (agent_id, at, text, context) VALUES (?, ?, ?, ?)",
                (agent_id, now_iso(), text, _json(context)),
            )

    def reports(self, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        query = (
            "SELECT r.*, a.name AS agent_name FROM reports r "
            "LEFT JOIN agents a ON a.id = r.agent_id"
        )
        if not include_resolved:
            query += " WHERE r.resolved = 0"
        rows = self.db.execute(query + " ORDER BY r.id DESC LIMIT 500").fetchall()
        return [dict(r) for r in rows]

    def resolve_report(self, report_id: int, resolved: bool) -> None:
        with self.db:
            self.db.execute(
                "UPDATE reports SET resolved = ? WHERE id = ?", (int(resolved), report_id)
            )

    # --- usage ----------------------------------------------------------------

    def add_usage(self, agent_id: str, prompt_tokens: int, completion_tokens: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO usage (agent_id, day, prompt_tokens, completion_tokens, calls) "
                "VALUES (?, ?, ?, ?, 1) ON CONFLICT(agent_id, day) DO UPDATE SET "
                "prompt_tokens = prompt_tokens + excluded.prompt_tokens, "
                "completion_tokens = completion_tokens + excluded.completion_tokens, "
                "calls = calls + 1",
                (agent_id, today(), prompt_tokens, completion_tokens),
            )

    def usage_today(self, agent_id: str) -> dict[str, int]:
        row = self.db.execute(
            "SELECT prompt_tokens, completion_tokens, calls FROM usage "
            "WHERE agent_id = ? AND day = ?",
            (agent_id, today()),
        ).fetchone()
        if row is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "total": 0}
        usage = dict(row)
        usage["total"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return usage

    def usage_all(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT agent_id, day, prompt_tokens, completion_tokens, calls FROM usage "
            "ORDER BY day DESC LIMIT 1000"
        ).fetchall()
        return [dict(r) for r in rows]
