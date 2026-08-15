"""Checks of the constants layer.

The point of these tests is not that the loader can read JSON but that **a
missing or corrupted constant breaks startup**, not gameplay (D-065).
"""

from __future__ import annotations

import json

import pytest

from src.constants import Constants, load_constants
from src.constants import registry as R
from src.constants.spec import ConstantError, Num, Span


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
    with pytest.raises(ConstantError, match="ожидалось"):
        snapshot[Span("body.drain_rate")]


def test_range_with_min_above_max_rejected() -> None:
    snapshot = Constants({"x": {"min": 10, "max": 1}}, source="тест")
    with pytest.raises(ConstantError):
        snapshot[Span("x")]


def test_edit_of_missing_key_rejected(constants: Constants) -> None:
    """An edit changes a value rather than introducing a new quantity: a new one is created in
    the vault."""
    with pytest.raises(ConstantError, match="несуществующие"):
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
    """"roof dry" -> "dust trickles" -> "roof creaks" -> "cracks" (D-143)."""
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
        load_constants(tmp_path)


def test_loader_reads_file_whole(tmp_path) -> None:
    path = tmp_path / "constants.json"
    path.write_text(json.dumps({"time.tick": 1}), encoding="utf-8")
    assert load_constants(tmp_path)[Num("time.tick")] == 1
