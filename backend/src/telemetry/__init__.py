# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Observing the world, not the world's rules.

What **measures** lives here: daily aggregates and invariant checks. Separate
from `engine/` deliberately and for two reasons.

The first is semantic: the engine decides what happens, telemetry only
watches. A module that computes a median and a Gini coefficient changes
nothing in the world.

The second is practical: `engine/` is checked for the absence of numeric
literals (D-065), because a number in the rules is a balance decision hidden
from the vault. In measurement numbers are of another nature: halving for a
median and converting to percent is arithmetic, not balance. Keeping them
under the same ban would mean either weakening the rules check or hiding
statistics behind vault constants that are not there and must not be.

**Telemetry's balance thresholds are still the vault's business.** That is
exactly why there is no threshold "stock grows more than N% a week" here: the
vault did not set it, and inventing it in code is not allowed, neither in
rules nor in observation.
"""

from __future__ import annotations

from src.telemetry import metrics

__all__ = ["metrics"]
