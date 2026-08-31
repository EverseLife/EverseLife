# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Russian names for the id-speaking wire (D-251, wave II).

Since wave II the server names goods, stations, tiers, slots, operations and
node properties by stable snake_case ids (`iron_ore`, `good`, `logging`). The
agents play in Russian on a local model, so observations interpolate the
Russian spellings back -- from the `names_ru` bundle of `GET /public/renames`,
fetched once per process and cached here.

The rendering format is `Имя [id]`: the model reads the name and quotes the
id from the brackets in command arguments -- the same convention the digest
already uses for nodes (`Рынок [terra.capital.market]`). A server without the
endpoint, or one not reached yet, leaves the ids raw: the fetch is retried on
the next turn, and nothing downstream depends on it succeeding.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: `names_ru` as served: domain -> {id: Russian name}. Empty until loaded.
_TABLE: dict[str, dict[str, str]] = {}
_LOADED = False


class _Reads(Protocol):
    async def public(self, path: str) -> Any: ...


def install(names_ru: dict[str, dict[str, str]] | None) -> None:
    """Put a table in place (also the seam the tests use)."""
    global _LOADED
    _TABLE.clear()
    for domain, names in (names_ru or {}).items():
        if isinstance(names, dict):
            _TABLE[domain] = {str(k): str(v) for k, v in names.items()}
    _LOADED = True


def reset() -> None:
    """Forget the table: the next `ensure` fetches again. For the tests."""
    global _LOADED
    _TABLE.clear()
    _LOADED = False


async def ensure(game: _Reads) -> None:
    """Fetch the table once per process; failure means raw ids, not a crash."""
    if _LOADED:
        return
    try:
        answer = await game.public("renames")
    except Exception as trouble:  # noqa: BLE001 -- any transport trouble: ids stay raw
        log.warning("renames unread, ids stay raw this turn: %s", trouble)
        return
    names_ru = answer.get("names_ru") if isinstance(answer, dict) else None
    install(names_ru if isinstance(names_ru, dict) else None)


def label(domain: str, value: Any) -> str:
    """`Имя [id]` when the table knows the id; the value itself otherwise.

    Goods fall back to `virtual_stations`: a station standing in a place is a
    thing, and the two domains share the goods namespace on the wire.
    """
    key = str(value)
    name = _TABLE.get(domain, {}).get(key)
    if name is None and domain == "goods":
        name = _TABLE.get("virtual_stations", {}).get(key)
    return f"{name} [{key}]" if name else key
