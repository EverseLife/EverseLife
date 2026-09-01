# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: quality.

Split out of `engine/craft.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

from src.constants import Constants
from src.constants import registry as R
from src.engine.craft._base import Procedure
from src.units import (
    PERCENT,
)


def optimal_amounts(
    constants: Constants, proc: Procedure, units: float, base_quality: float
) -> dict[str, float]:
    """The optimal proportion for **this** raw material.

    An assembly has none: a workbench is a log and a rope, nothing in between.
    """
    nominal = {name: value * units for name, value in proc.per_unit.items()}
    if not proc.mix or not proc.inputs:
        return nominal

    scale = constants[R.QUALITY_SCALE]
    correction = 1 + (scale.mid - base_quality) / scale.max
    base = proc.inputs[0]
    return {name: value if name == base else value * correction for name, value in nominal.items()}


def ratio_accuracy(actual: dict[str, float], optimal: dict[str, float]) -> float:
    """How well the proportion was hit: 1 -- exactly, 0 -- missed entirely."""
    errors = [
        abs(actual.get(name, 0.0) - want) / want for name, want in optimal.items() if want > 0
    ]
    if not errors:
        return 1.0
    return max(0.0, 1 - sum(errors) / len(errors))


def waste_share(constants: Constants, accuracy: float) -> float:
    """Losses for waste and scrap, percent of inputs.

    There is no "hit / miss" threshold in the vault, so losses follow accuracy
    continuously: from `craft.waste_share` for correct work to
    `craft.waste_bad_ratio` for a complete miss.
    """
    good = constants[R.CRAFT_WASTE_SHARE]
    bad = constants[R.CRAFT_WASTE_BAD_RATIO]
    return good + (bad - good) * (1 - accuracy)


def spread_of(constants: Constants, accuracy: float) -> float:
    """Result spread: narrow with correct proportions, wide on a miss."""
    good = constants[R.QUALITY_SPREAD_GOOD_RATIO]
    bad = constants[R.QUALITY_SPREAD_BAD_RATIO]
    return good + (bad - good) * (1 - accuracy)


def forecast_quality(
    constants: Constants,
    proc: Procedure,
    *,
    ceiling: float,
    material: float,
    accuracy: float,
) -> float:
    """Quality forecast: the ceiling and how close to it we came."""
    scale = constants[R.QUALITY_SCALE]
    if proc.mix:
        closeness = (
            constants[R.QUALITY_MATERIAL_WEIGHT] * material
            + constants[R.QUALITY_RATIO_WEIGHT] * accuracy * scale.max
        ) / PERCENT
    else:
        closeness = material
    value = ceiling * closeness / scale.max
    #: Craft premium: the master sees today's ore is worse than usual and
    #: adjusts proportions for it (15-quality). The unattended machine has its
    #: own ceiling instead (`auto.quality_cap`, D-253) -- that is the whole
    #: difference between hand and factory.
    if proc.mix:
        value += constants[R.QUALITY_HAND_CRAFT_BONUS] * accuracy
    return scale.clamp(min(value, quality_cap(constants, proc, ceiling)))


def quality_cap(constants: Constants, proc: Procedure, ceiling: float) -> float:
    """Only the craft premium rises above the ceiling, and only for a mix."""
    scale = constants[R.QUALITY_SCALE]
    bonus = constants[R.QUALITY_HAND_CRAFT_BONUS] if proc.mix else 0.0
    return min(scale.max, ceiling + bonus)
