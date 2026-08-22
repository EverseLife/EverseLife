# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""`python -m aps`: the panel and the runner in one process."""

from __future__ import annotations

import uvicorn

from . import settings


def main() -> None:
    current = settings.load()
    uvicorn.run(
        "aps.web:app", host=current.host, port=current.port, log_level=current.log_level.lower()
    )


if __name__ == "__main__":
    main()
