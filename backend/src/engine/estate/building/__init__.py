# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""estate: building (D-106, D-125).

Split out of `engine/estate.py` along its sections (review 2026-08-23, wave 3).
"""

from src.engine.estate.building.build import (  # noqa: F401
    bill,
    build_minutes,
    close_storeys,
    composition,
    construct,
    estimate,
    floor_growth,
    kinds,
    open_storeys,
)
from src.engine.estate.building.frame import (  # noqa: F401
    _equipment,
    buildings_of,
    built_area,
    floor_mass,
    free_ground,
    height_of,
    hold_ground,
    marked_ground,
    planned_footprint,
    slots,
    space,
    spare_ground,
    spare_storeys,
    split,
    storey_area,
    storey_area_for,
    storeys_of,
    under_construction,
    yard,
    yard_mass,
)
