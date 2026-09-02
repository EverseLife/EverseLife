# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Representation units -- not balance numbers.

Only quantities defining **how we store and compute** live here, not **how
much something costs in the game**. Any number affecting balance must lie in
`build/constants.json` (D-065) and come through `everselife.constants`.

That is exactly why the module is excluded from the magic-number check
(`tests/test_no_magic_numbers.py`): numbers from here cannot be "balanced".
"""

from __future__ import annotations

from decimal import Decimal

#: Money is stored as integer minor units: 1 TC = 10 000 units. Integers rule
#: out rounding errors in double entry -- no posting can "lose a cent"
#: (see 01-tech-notes, pattern 2).
MONEY_SCALE = 10_000

#: The percent scale. Constants like `craft.waste_share` are given in percent.
PERCENT = 100.0

#: Hours per Terran day the engine takes from the vault (`time.day_terra`),
#: but the tariff unit is set by the vault itself as "TC per 100 energy": that
#: is how the price is written, not its magnitude.
ENERGY_PER_TARIFF_UNIT = 100.0

#: Per mille: coin fineness is given in thousandths (`coin.default_fineness`
#: = 900). This is the unit's definition, not balance -- the balance value of
#: the fineness itself lies in `coin.*`.
PERMILLE = 1000.0

#: The item quality and condition scale: 0..100 (20-systems/15-quality). The
#: scale bounds are representation, not balance: balance values inside it
#: (tier thresholds, repair losses) lie in `quality.*`.
SCALE_MIN = 0.0
SCALE_MAX = 100.0

#: The daylight scale: 0..3 (D-261). Catalog demands (`requires.light`) are
#: written on 1-3, and 3 is an open clearing at noon. The ceiling is how the
#: scale is written, not balance: what takes a step off it lies in the catalog
#: and in `farm.shade_built_share`.
LIGHT_MAX = 3

#: The hardiness scale the catalog writes traits on: 1..5 (plants.yaml,
#: D-261). The bound defines the scale; what 5/5 buys is balance and lies in
#: `farm.hardiness_relief`.
HARDINESS_SCALE = 5.0

#: Where the diurnal cosine crosses the mean: the lit half of the planetary
#: day is phase [0.25, 0.75), exactly the hours the temperature runs above
#: the node's mean (D-261). Geometry of the day model, not balance -- moving
#: these alone would tear "day" away from the temperature curve.
DAY_PHASE_DAWN = 0.25
DAY_PHASE_DUSK = 0.75

#: Minutes per hour. Vault constants are given in hours (`mining.iron_per_hour`,
#: `wound.recovery_hours`), while sessions live in minutes.
MINUTES_PER_HOUR = 60.0

#: Seconds per minute: deadlines are stored in seconds and told in minutes.
SECONDS_PER_MINUTE = 60.0

#: Seconds per hour: map edges and sleep live in seconds, vault rates in hours.
SECONDS_PER_HOUR = 3600.0

#: Hours per **real** day. Ship passage tables are given in real days
#: ("2-8 суток реального времени", 10-world/06), and this is that conversion --
#: not the length of a Terran day, which is balance and lives in `time.day_terra`.
HOURS_PER_DAY = 24.0

#: Kilograms per tonne: item mass is in kilograms, ship fuel is charged per
#: tonne of mass. The definition of the unit, not a property of the game.
KG_PER_TON = 1000.0

#: How many decimals a summary keeps: masses and fuel to a tenth, hours to a
#: hundredth, dimensionless ratios to a thousandth. Presentation, not balance --
#: there is nothing here to tune.
ROUND_MASS = 1
ROUND_HOURS = 2
ROUND_RATIO = 3
#: Delta-v of a passage, map units per day (D-271), and the points of
#: the arc it flies, map units: a tenth is finer than the map draws.
ROUND_DV = 2
ROUND_TRACE = 1
#: How the sky is stored and remembered (D-271), not how it is priced:
#: points along a drawn arc, the memo's buckets of a day, and how many
#: curves and calendars a process keeps.
TRACE_POINTS = 24
SKY_MEMO_PER_DAY = 144
SKY_CURVE_MEMO = 512
SKY_CALENDAR_MEMO = 64
#: Column scales, not presentation: the two below say how wide the row is, not
#: how a summary reads. Changing one alone leaves the code rounding coarser or
#: finer than the column it writes to, and nothing objects -- so a scale tied
#: to a single column is pinned to it by a test.
#: Quality is stored to a hundredth of a point (`Numeric(6, 2)` columns).
ROUND_QUALITY = 2
#: Work banked in minutes is stored to a hundredth (`Plot.plow_done_minutes`
#: is `Numeric(10, 2)`): a plough is paused and taken up again (D-277), so the
#: remainder has to survive the round trip through the row unchanged. The
#: scale of the column, not a property of the game -- how long the ploughing
#: takes lies in `farm.plow_time_per_m2`.
ROUND_MINUTES = 2

#: Argon2id takes memory in KiB, while `pow.memory_per_session` is given in MB.
KIB_PER_MIB = 1024

#: Raw material amounts are stored as integer thousandths of a unit -- ore can
#: be fractional, and floating point in vein remainders is unacceptable.
AMOUNT_SCALE = 1_000

#: The widest amount the column can hold, in pieces: `Item.amount` is a signed
#: bigint of internal units. Not a balance number -- the width of the row, and
#: therefore here rather than in the vault. Whoever takes an amount straight
#: from a player checks against it: past this `amount()` overflows on insert,
#: and an integrity error reaches the player as "the server failed" instead of
#: as a refusal in words.
AMOUNT_MAX = ((1 << 63) - 1) // AMOUNT_SCALE


def money(value: Decimal | int | float | str) -> int:
    """TC -> minor units. Banker's rounding, to the nearest even."""
    return int((Decimal(str(value)) * MONEY_SCALE).to_integral_value())


def money_str(minor: int) -> str:
    """Minor units -> a string for showing the player."""
    return f"{Decimal(minor) / MONEY_SCALE:.4f}".rstrip("0").rstrip(".")


def amount(value: Decimal | int | float | str) -> int:
    """Pieces/kilograms -> internal integer units."""
    return int((Decimal(str(value)) * AMOUNT_SCALE).to_integral_value())


def amount_float(internal: int) -> float:
    return internal / AMOUNT_SCALE
