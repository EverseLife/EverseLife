# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The herald: the world chronicle out to Discord.

The bridge is one-way, and that is not an omission. There is no action API
(60-meta/01-anti-cheat), so Discord can do exactly one thing -- learn. Not a
single inbound command will appear from here: the player acts only in person
and only through the client session.

What goes out and why exactly that -- `chronicle.py`; how it goes -- `webhook.py`;
when -- `job.py`. One setting: `EVERSELIFE_DISCORD_WEBHOOK`. The enabling
procedure is described in `community/discord-bridge.md`.
"""

from __future__ import annotations

#: Importing the job also registers its handler: `require_handlers()` must
#: find the herald at startup, not in a tick in the middle of the night.
from src.herald import chronicle, webhook
from src.herald.job import ensure_scheduled, post, run_once

__all__ = [
    "chronicle",
    "ensure_scheduled",
    "post",
    "run_once",
    "webhook",
]
