"""Tests for the candidate cost estimates and the runtime-option resolvers."""

from pathlib import Path

import pytest

from dara.search import candidate_cost as cc

TEST_DATA = Path(__file__).parent / "test_data"
ENV_NAMES = ("DARA_CANDIDATE_PREFILTER", "DARA_SCREENING_TIMEOUT_S", "DARA_MAX_RUNTIME_S")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_wavelength_angstrom():
    assert cc.wavelength_angstrom("Cu") == pytest.approx(1.540598)
    assert cc.wavelength_angstrom("Mo") == pytest.approx(0.709319)
    assert cc.wavelength_angstrom(0.15406) == pytest.approx(1.5406)
    with pytest.raises(ValueError):
        cc.wavelength_angstrom("Unobtainium")


def test_reflections_grow_with_volume_and_angle():
    base = cc.reflections_from_cell(200.0, 14, "Cu", 60.0)
    assert cc.reflections_from_cell(400.0, 14, "Cu", 60.0) > base
    assert cc.reflections_from_cell(200.0, 14, "Cu", 90.0) > base
    assert isinstance(base, int) and base >= 1


def test_reflections_symmetry_divisors():
    # P1 merges Friedel pairs (divisor 2) exactly like P-1 (order 2): same count.
    assert cc.reflections_from_cell(500.0, 1, "Cu", 90.0) == cc.reflections_from_cell(500.0, 2, "Cu", 90.0)
    # P2 (no inversion) has divisor 2 * 2 = 4, twice the P1 divisor.
    p1 = cc.reflections_from_cell(500.0, 1, "Cu", 90.0)
    p2 = cc.reflections_from_cell(500.0, 3, "Cu", 90.0)
    assert p1 / p2 == pytest.approx(2.0, rel=0.02)
    # Fm-3m (192) vs Pm-3m (48) for the same conventional cell.
    fm3m = cc.reflections_from_cell(20000.0, 225, "Cu", 90.0)
    pm3m = cc.reflections_from_cell(20000.0, 221, "Cu", 90.0)
    assert pm3m / fm3m == pytest.approx(4.0, rel=0.05)
    # primitive cells divide by the point-group order only
    assert cc.reflections_from_cell(100.0, 225, "Cu", 90.0, primitive=True) > cc.reflections_from_cell(
        100.0, 225, "Cu", 90.0
    )


def test_reflections_invalid_input():
    with pytest.raises(ValueError):
        cc.reflections_from_cell(0.0, 14, "Cu", 90.0)
    with pytest.raises(ValueError):
        cc.reflections_from_cell(100.0, 0, "Cu", 90.0)
    with pytest.raises(ValueError):
        cc.reflections_from_cell(100.0, 231, "Cu", 90.0)


def test_estimate_reflections_from_file(tmp_path):
    cif = TEST_DATA / "BiFeO3.cif"
    n = cc.estimate_reflections(cif, "Cu", 90.0)
    assert isinstance(n, int) and n > 0
    assert cc.estimate_reflections(cif, "Cu", 120.0) > n
    bad = tmp_path / "broken.cif"
    bad.write_text("this is not a cif")
    assert cc.estimate_reflections(bad, "Cu", 90.0) is None
    assert cc.estimate_reflections(tmp_path / "missing.cif", "Cu", 90.0) is None


def test_estimate_reflections_uses_stem_space_group(tmp_path):
    source = (TEST_DATA / "BiFeO3.cif").read_text()
    (tmp_path / "BiFeO3_161_(cod_1)-0.cif").write_text(source)
    (tmp_path / "BiFeO3_1_(cod_2)-0.cif").write_text(source)
    high = cc.estimate_reflections(tmp_path / "BiFeO3_161_(cod_1)-0.cif", "Cu", 90.0)
    low = cc.estimate_reflections(tmp_path / "BiFeO3_1_(cod_2)-0.cif", "Cu", 90.0)
    assert low > high


@pytest.mark.parametrize(
    ("n", "label"),
    [(None, "unknown"), (1, "light"), (399, "light"), (400, "heavy"), (1199, "heavy"), (1200, "very_heavy")],
)
def test_classify_weight(n, label):
    assert cc.classify_weight(n) == label


def test_estimate_runtime_shape_and_monotonicity():
    result = cc.estimate_runtime_s(100, [300] * 20, 8)
    assert set(result) >= {"low_s", "expected_s", "high_s", "assumptions"}
    assert 0 < result["low_s"] <= result["expected_s"] <= result["high_s"]
    assert all(isinstance(item, str) for item in result["assumptions"])
    more = cc.estimate_runtime_s(200, [300] * 20, 8)
    assert more["expected_s"] > result["expected_s"]
    faster = cc.estimate_runtime_s(100, [300] * 20, 32)
    assert faster["expected_s"] < result["expected_s"]
    heavier = cc.estimate_runtime_s(100, [2000] * 100, 8)
    assert heavier["expected_s"] > result["expected_s"]


def test_estimate_runtime_edge_cases():
    zero = cc.estimate_runtime_s(0, [], 8)
    assert (zero["low_s"], zero["expected_s"], zero["high_s"]) == (0, 0, 0)
    no_counts = cc.estimate_runtime_s(10, [], 0)
    assert no_counts["expected_s"] > 0
    long_pattern = cc.estimate_runtime_s(10, [], 4, data_points=60000)
    short_pattern = cc.estimate_runtime_s(10, [], 4, data_points=3000)
    assert long_pattern["expected_s"] > short_pattern["expected_s"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("0", "off"),
        ("OFF", "off"),
        (" False ", "off"),
        ("no", "off"),
        ("Legacy", "off"),
        ("1", "on"),
        ("On", "on"),
        ("TRUE", "on"),
        ("yes", "on"),
        ("300", 300.0),
        (" 12.5 ", 12.5),
        ("-5", None),
        ("banana", None),
    ],
)
def test_env_setting_parsing(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("DARA_SCREENING_TIMEOUT_S", raw)
    assert cc.env_setting("DARA_SCREENING_TIMEOUT_S") == expected


def test_resolve_screening_timeouts(monkeypatch):
    legacy = {"default": 1200.0, "very_heavy": 1200.0, "source": "legacy"}
    assert cc.resolve_screening_timeouts(None) == legacy
    assert cc.resolve_screening_timeouts(300) == {"default": 300.0, "very_heavy": 600.0, "source": "submission"}
    assert cc.resolve_screening_timeouts(10)["default"] == 60.0
    assert cc.resolve_screening_timeouts(5000) == {"default": 1200.0, "very_heavy": 1200.0, "source": "submission"}
    monkeypatch.setenv("DARA_SCREENING_TIMEOUT_S", "400")
    assert cc.resolve_screening_timeouts(None) == {"default": 400.0, "very_heavy": 800.0, "source": "env"}
    assert cc.resolve_screening_timeouts(300)["source"] == "submission"
    monkeypatch.setenv("DARA_SCREENING_TIMEOUT_S", "off")
    assert cc.resolve_screening_timeouts(300) == legacy
    monkeypatch.setenv("DARA_SCREENING_TIMEOUT_S", "on")
    assert cc.resolve_screening_timeouts(None) == legacy


def test_resolve_budget(monkeypatch):
    assert cc.resolve_budget_s(None) is None
    assert cc.resolve_budget_s(1800) == 1800.0
    assert cc.resolve_budget_s(60) == 300.0
    assert cc.resolve_budget_s(0) is None
    monkeypatch.setenv("DARA_MAX_RUNTIME_S", "900")
    assert cc.resolve_budget_s(None) == 900.0
    assert cc.resolve_budget_s(1800) == 1800.0
    monkeypatch.setenv("DARA_MAX_RUNTIME_S", "10")
    assert cc.resolve_budget_s(None) == 300.0
    monkeypatch.setenv("DARA_MAX_RUNTIME_S", "off")
    assert cc.resolve_budget_s(1800) is None


def test_prefilter_env(monkeypatch):
    assert cc.prefilter_env() is None
    monkeypatch.setenv("DARA_CANDIDATE_PREFILTER", "on")
    assert cc.prefilter_env() == "on"
    monkeypatch.setenv("DARA_CANDIDATE_PREFILTER", "off")
    assert cc.prefilter_env() == "off"
    monkeypatch.setenv("DARA_CANDIDATE_PREFILTER", "250")
    assert cc.prefilter_env() is None
