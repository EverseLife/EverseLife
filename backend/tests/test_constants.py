# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Checks of the constants layer.

The point of these tests is not that the loader can read JSON but that **a
missing or corrupted constant breaks startup**, not gameplay (D-065).
"""

from __future__ import annotations

import json

import pytest

from src.constants import Constants, RenameTable, load_constants
from src.constants import registry as R
from src.constants.spec import Bands, ConstantError, Num, Span, Table, Words


def test_all_declared_constants_exist_in_vault(constants: Constants) -> None:
    constants.validate(R.declared())


def test_missing_constant_breaks_check() -> None:
    snapshot = Constants({"mine.roof_start": 100}, source="тест")
    with pytest.raises(ConstantError) as exc:
        snapshot.validate([Num("mine.roof_start"), Num("нет.такого"), Num("тоже.нет")])
    #: All problems at once -- fixing the set one per restart is unbearable.
    assert "нет.такого" in str(exc.value)
    assert "тоже.нет" in str(exc.value)


def test_wrong_shape_breaks_check() -> None:
    snapshot = Constants({"body.drain_rate": 5}, source="тест")
    with pytest.raises(ConstantError, match="expected"):
        snapshot[Span("body.drain_rate")]


def test_a_table_missing_a_key_the_engine_reads_breaks_the_boot() -> None:
    """A table declares the keys the engine reads by name (`Table.keys`), and
    a vault that dropped one fails at startup like any missing constant.
    Without that the shape alone is checked and the table passes whole: the
    hole shows as a `KeyError` deep in play, on the machine of whoever ran
    the half of the pair that arrived first."""
    axes = Table("biome.facet_axes", keys=("wave_m", "favour_k"))
    whole = Constants({"biome.facet_axes": {"wave_m": 200, "favour_k": 2.5}}, source="тест")
    assert whole[axes] == {"wave_m": 200.0, "favour_k": 2.5}
    half = Constants({"biome.facet_axes": {"wave_m": 200}}, source="тест")
    with pytest.raises(ConstantError, match="favour_k"):
        half.validate([axes])
    #: A table that names no keys is still checked for its shape alone.
    assert half[Table("biome.facet_axes")] == {"wave_m": 200.0}


def test_a_band_and_a_word_declare_their_keys_too() -> None:
    """`Bands` and `Words` name the entries the engine reads, as `Table` does.

    Both learnt it when the planets were told apart (D-329): every world got
    its own `terrain.temp_range` and its own `terrain.fluid`, and without the
    keys a vault that dropped a planet passed the boot whole. The word table
    also names the words it knows -- a misspelt `Lava` compares equal to
    nothing, so the planet would have quietly turned watery with no reader
    raising anywhere along the way.
    """
    ends = Bands("terrain.temp_range", keys=("terra", "aurora"))
    both = {"terra": {"min": -15, "max": 35}, "aurora": {"min": -75, "max": -25}}
    whole = Constants({"terrain.temp_range": both}, source="тест")
    assert whole[ends]["aurora"] == {"min": -75.0, "max": -25.0}
    half = Constants({"terrain.temp_range": {"terra": {"min": -15, "max": 35}}}, source="тест")
    with pytest.raises(ConstantError, match="aurora"):
        half.validate([ends])

    fluid = Words("terrain.fluid", keys=("terra", "pyroxis"), allowed=("water", "lava"))
    good = Constants({"terrain.fluid": {"terra": "water", "pyroxis": "lava"}}, source="тест")
    assert good[fluid]["pyroxis"] == "lava"
    typo = Constants({"terrain.fluid": {"terra": "water", "pyroxis": "Lava"}}, source="тест")
    with pytest.raises(ConstantError, match="water, lava"):
        typo.validate([fluid])
    short = Constants({"terrain.fluid": {"terra": "water"}}, source="тест")
    with pytest.raises(ConstantError, match="pyroxis"):
        short.validate([fluid])
    #: A word table that names neither is still checked for its shape alone.
    assert typo[Words("terrain.fluid")]["pyroxis"] == "Lava"


def test_range_with_min_above_max_rejected() -> None:
    snapshot = Constants({"x": {"min": 10, "max": 1}}, source="тест")
    with pytest.raises(ConstantError):
        snapshot[Span("x")]


def test_edit_of_missing_key_rejected(constants: Constants) -> None:
    """An edit changes a value rather than introducing a new quantity: a new one is created in
    the vault."""
    with pytest.raises(ConstantError, match="do not exist"):
        constants.with_overrides({"выдуманная.константа": 1})


def test_edit_gives_new_snapshot_and_fingerprint(constants: Constants) -> None:
    before = constants[R.MINING_IRON_PER_HOUR]
    patched = constants.with_overrides({"mining.iron_per_hour": before + 10})

    assert patched[R.MINING_IRON_PER_HOUR] == before + 10
    #: The original snapshot is unchanged -- a reader never sees a half-edit.
    assert constants[R.MINING_IRON_PER_HOUR] == before
    #: The fingerprint differs: by it the journal shows which numbers an episode ran on.
    assert patched.digest != constants.digest


def test_roof_sign_bands_cover_scale(constants: Constants) -> None:
    """ "roof dry" -> "dust trickles" -> "roof creaks" -> "cracks" (D-143)."""
    bands = constants[R.MINE_SIGN_BANDS]
    assert min(bands.values()) == 0, "нижняя полоса обязана доходить до нуля"
    assert len(set(bands.values())) == len(bands), "полосы не должны совпадать"


def test_fingerprint_independent_of_key_order(tmp_path) -> None:
    raw = {"a": 1, "b": {"min": 0, "max": 2}}
    first = Constants(raw, source="тест")
    second = Constants(dict(reversed(list(raw.items()))), source="тест")
    assert first.digest == second.digest


def test_clear_error_if_vault_not_built(tmp_path) -> None:
    with pytest.raises(ConstantError, match="build.py"):
        load_constants(tmp_path, RenameTable())


def test_loader_reads_file_whole(tmp_path) -> None:
    path = tmp_path / "constants.json"
    path.write_text(json.dumps({"time.tick": 1}), encoding="utf-8")
    assert load_constants(tmp_path, RenameTable())[Num("time.tick")] == 1
