# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A language model behind an OpenAI-compatible chat endpoint (DeepSeek and the like).

Only `chat/completions` with tools is used -- it is the common denominator of
every cheap provider, so the agent does not depend on any one of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


class ModelError(Exception):
    pass


@dataclass
class Reply:
    content: str
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_message: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Provider:
    base_url: str
    api_key: str
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


async def chat(
    provider: Provider,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 1200,
) -> Reply:
    body = {
        "model": model or provider.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    url = provider.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {provider.api_key}"}
    async with httpx.AsyncClient(timeout=180) as http:
        try:
            response = await http.post(url, json=body, headers=headers)
        except httpx.HTTPError as trouble:
            raise ModelError(f"провайдер недоступен: {trouble}") from trouble
    if response.status_code >= 400:
        raise ModelError(f"провайдер ответил {response.status_code}: {response.text[:500]}")
    data = response.json()
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError) as trouble:
        raise ModelError(f"странный ответ провайдера: {str(data)[:500]}") from trouble
    usage = data.get("usage") or {}
    return Reply(
        content=message.get("content") or "",
        #: DeepSeek reasoner and friends: the thinking before the answer.
        reasoning=message.get("reasoning_content") or "",
        tool_calls=message.get("tool_calls") or [],
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        raw_message=message,
    )
