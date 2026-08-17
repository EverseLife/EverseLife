"""Representation units -- not balance numbers.

Only quantities defining **how we store and compute** live here, not **how
much something costs in the game**. Any number affecting balance must lie in
`build/constants.json` (D-065) and come through `octoverse.constants`.

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

#: Minutes per hour. Vault constants are given in hours (`mining.iron_per_hour`,
#: `wound.recovery_hours`), while sessions live in minutes.
MINUTES_PER_HOUR = 60.0

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

#: Argon2id takes memory in KiB, while `pow.memory_per_session` is given in MB.
KIB_PER_MIB = 1024

#: Raw material amounts are stored as integer thousandths of a unit -- ore can
#: be fractional, and floating point in vein remainders is unacceptable.
AMOUNT_SCALE = 1_000


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
