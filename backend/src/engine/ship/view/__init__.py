# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""ship: what the client shows before the attempt.

Split out of `engine/ship.py` along its sections (review 2026-08-23, wave 3).
"""

from src.engine.ship.view.card import (  # noqa: F401
    profile,
)
from src.engine.ship.view.sight import (  # noqa: F401
    in_sight,
)
from src.engine.ship.view.sky import (  # noqa: F401
    beacon_lit,
    landings,
    lands_anywhere,
    lit_ports,
    open_landings,
    passages,
    ports,
)
