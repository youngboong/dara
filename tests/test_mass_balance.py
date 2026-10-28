from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from dara.server import mass_balance as mb

DATA = Path(__file__).parent / "test_data"
FE2TIO5 = "Fe2TiO5_63_(cod_1)-0"
TIO2 = "TiO2_136_(cod_9007432)-10"


# --- parsing -----------------------------------------------------------------

def test_plain_formulas_are_unchanged_and_have_no_amounts():
    formulas, amounts = mb.split_precursor_entries(["In2O3", "La(OH)3", "TiO2"])
    assert formulas == ["In2O3", "La(OH)3", "TiO2"]
    assert amounts is None
    assert mb.split_precursor_entries([]) == ([], None)


@pytest.mark.parametrize("entry, formula, coefficient", [
    ("2 TiO2", "TiO2", 2.0), ("2TiO2", "TiO2", 2.0), ("2*TiO2", "TiO2", 2.0),
    ("0.5 Li2CO3", "Li2CO3", 0.5), ("3Y2O3", "Y2O3", 3.0), ("2FeC2O4(H2O)2", "FeC2O4(H2O)2", 2.0),
])
def test_leading_coefficients(entry, formula, coefficient):
    parsed = mb.parse_precursor_entry(entry)
    assert parsed["formula"] == formula and parsed["coefficient"] == coefficient


def test_coefficients_default_to_one_and_masses_convert_to_moles():
    formulas, amounts = mb.split_precursor_entries(["Fe2O3", "2 TiO2"])
    assert formulas == ["Fe2O3", "TiO2"]
    assert [a["moles"] for a in amounts] == [1.0, 2.0]

    formulas, amounts = mb.split_precursor_entries(["In2O3:0.3022g", "TiO2: 200 mg"])
    assert formulas == ["In2O3", "TiO2"]
    assert amounts[0]["moles"] == pytest.approx(0.3022 / 277.634, rel=1e-3)
    assert amounts[1]["moles"] == pytest.approx(0.2 / 79.866, rel=1e-3)
    _, amounts = mb.split_precursor_entries(["TiO2:2mol", "ZnO:500mmol"])
    assert [a["moles"] for a in amounts] == [2.0, 0.5]
    # a mass on one entry only leaves the others undefined -> unknown
    assert mb.split_precursor_entries(["In2O3:0.3g", "TiO2"])[1] is None
    assert mb.split_precursor_entries(["In2O3:0.3g", "2 TiO2"])[1] is None


@pytest.mark.parametrize("entry", ["TiO2:abc", "2 TiO2:0.3g", "0 TiO2", "TiO2:0g", ":0.3g"])
def test_malformed_amounts_raise(entry):
    with pytest.raises(ValueError):
        mb.parse_precursor_entry(entry)


# --- filename inference -----------------------------------------------------------

def test_filename_molar_coefficients():
    inferred = mb.amounts_from_filename("2Fe3O4-3Y2O3_1000C_60min.xrdml", ["Fe3O4", "Y2O3"])
    assert inferred["basis"] == "molar"
    assert [a["moles"] for a in inferred["amounts"]] == [2.0, 3.0]
    inferred = mb.amounts_from_filename("Fe2O3-2TiO2_1000C_60min", ["TiO2", "Fe2O3"])
    assert [a["moles"] for a in inferred["amounts"]] == [2.0, 1.0]


def test_filename_masses():
    inferred = mb.amounts_from_filename(
        "In2O3_0p3022-La(OH)3_0p5014-TiO2_0p2004--8min.xy", ["In2O3", "La(OH)3", "TiO2"]
    )
    assert inferred["basis"] == "mass"
    assert inferred["amounts"][1]["moles"] == pytest.approx(0.5014 / 189.93, rel=1e-3)


@pytest.mark.parametrize("filename, precursors", [
    ("Fe2O3-2TiO2_1000C", ["Fe2O3"]),                    # fewer than two precursors
    ("Fe2O3-2TiO2_1000C", ["Fe2O3", "ZnO"]),             # precursor missing in the name
    ("Fe2O3-2TiO2-ZnO_1000C", ["Fe2O3", "TiO2"]),        # extra formula in the name
    ("sample_A_scan3", ["Fe2O3", "TiO2"]),               # no formulas at all
    ("In2O3_0p3022-TiO2--8min", ["In2O3", "TiO2"]),      # mass for one entry only
    ("TiO2-2TiO2_1000C", ["TiO2", "Fe2O3"]),             # duplicate chunk
    (None, ["Fe2O3", "TiO2"]),
])
def test_filename_ambiguous_is_skipped(filename, precursors):
    assert mb.amounts_from_filename(filename, precursors) is None


def test_explicit_amounts_win_over_filename():
    explicit = [{"formula": "Fe2O3", "moles": 1.0}, {"formula": "TiO2", "moles": 1.0}]
    assert mb.resolve_amounts(explicit, ["Fe2O3", "TiO2"], "Fe2O3-2TiO2_1000C") == (explicit, "input")
    amounts, source = mb.resolve_amounts(None, ["Fe2O3", "TiO2"], "Fe2O3-2TiO2_1000C")
    assert source == "filename" and amounts[1]["moles"] == 2.0
    assert mb.resolve_amounts(None, ["Fe2O3", "TiO2"], "unknown.xy") == (None, None)


# --- computation -------------------------------------------------------------------

AMOUNTS = mb.split_precursor_entries(["Fe2O3", "2 TiO2"])[1]


def test_fe2o3_2tio2_mass_balance_example():
    ok = mb.mass_balance(AMOUNTS, {FE2TIO5: 75.0, TIO2: 25.0})
    assert ok["status"] == "evaluated" and ok["level"] == "ok"
    assert ok["max_deviation"] == pytest.approx(0.0, abs=2e-3)
    assert ok["expected_ratio"] == "Fe:Ti = 0.50:0.50"
    assert ok["excluded_elements"] == ["C", "H", "N", "O"]

    dara = mb.mass_balance(AMOUNTS, {FE2TIO5: 72.4, TIO2: 27.6})
    assert dara["level"] == "ok" and dara["observed_ratio"] == "Fe:Ti = 0.48:0.52"

    wrong = mb.mass_balance(AMOUNTS, {FE2TIO5: 26.8, TIO2: 73.2})
    assert wrong["level"] == "warning"
    assert wrong["max_deviation"] == pytest.approx(0.32, abs=0.01)
    assert wrong["observed_ratio"] == "Fe:Ti = 0.18:0.82"
    message = mb.mass_balance_message(wrong)
    assert message.startswith("Phase fractions imply Fe:Ti = 0.18:0.82, but the precursors imply 0.50:0.50")
    assert "largest deviation 0.32" in message
    assert mb.mass_balance_message(ok) is None


def test_caution_band_and_esd_tolerance():
    caution = mb.mass_balance(AMOUNTS, {FE2TIO5: 55.0, TIO2: 45.0})
    assert caution["level"] == "caution"
    assert mb.OK_MAX < caution["max_deviation"] <= mb.WARNING_MIN
    # a huge esd makes the same deviation statistically insignificant
    tolerant = mb.mass_balance(
        AMOUNTS, {FE2TIO5: 55.0, TIO2: 45.0}, weight_esd={FE2TIO5: 20.0, TIO2: 20.0}
    )
    assert tolerant["level"] == "ok" and tolerant["deviation_sigma"] > 0


def test_volatile_elements_are_excluded():
    # carbonate decomposition: CO2 loss must not count as a deviation
    amounts = mb.split_precursor_entries(["1 CaCO3", "1 TiO2"])[1]
    block = mb.mass_balance(amounts, {"CaTiO3_221_(cod_1)-0": 100.0})
    assert [row["element"] for row in block["elements"]] == ["Ca", "Ti"]
    assert block["level"] == "ok"
    # the open element of reaction mode is excluded as well
    block = mb.mass_balance(
        [{"formula": "Li2S", "moles": 1.0}, {"formula": "P2S5", "moles": 1.0}],
        {"Li3PS4_62_(cod_1)-0": 100.0}, open_element="S",
    )
    assert "S" in block["excluded_elements"]


def test_insufficient_elements_or_data_are_skipped():
    single = mb.mass_balance([{"formula": "Fe2O3", "moles": 1.0}, {"formula": "Fe3O4", "moles": 1.0}],
                             {"Fe2O3_167_(cod_1)-0": 100.0})
    assert single["status"] == "skipped" and "two non-volatile" in single["reason"]
    assert mb.mass_balance(None, {FE2TIO5: 100.0})["reason"].startswith("Add precursor amounts")
    assert mb.mass_balance(AMOUNTS, {})["status"] == "skipped"
    unreadable = mb.mass_balance(AMOUNTS, {"my phase!!": 100.0})
    assert unreadable["status"] == "skipped" and "could not be read" in unreadable["reason"]
    assert mb.mass_balance_message(unreadable) is None


def test_foreign_element_counts_as_deviation():
    block = mb.mass_balance(
        mb.split_precursor_entries(["ZnO", "2 TiO2"])[1],
        {"Zn2TiO4_227_(cod_1)-0": 40.0, "Al2O3_167_(cod_1)-0": 60.0},
    )
    assert "Al" in [row["element"] for row in block["elements"]]
    assert block["level"] == "warning"


def test_apply_to_confidence_levels():
    warning = mb.mass_balance(AMOUNTS, {FE2TIO5: 26.8, TIO2: 73.2})
    high = {"overall": {"level": "high", "label": "High confidence", "reasons": ["All fine."]}}
    mb.apply_to_confidence(high, warning)
    assert high["overall"]["level"] == "check"
    assert high["overall"]["reasons"][0].startswith("Mass balance: Phase fractions imply")
    expert = {"overall": {"level": "expert", "label": "x", "reasons": ["a", "b", "c", "d"]}}
    mb.apply_to_confidence(expert, warning)
    assert expert["overall"]["level"] == "expert" and len(expert["overall"]["reasons"]) == 4
    assert expert["overall"]["reasons"][-1].startswith("Mass balance:")
    caution = mb.mass_balance(AMOUNTS, {FE2TIO5: 55.0, TIO2: 45.0})
    high = {"overall": {"level": "high", "label": "High confidence", "reasons": ["All fine."]}}
    mb.apply_to_confidence(high, caution)
    assert high["overall"]["level"] == "high" and len(high["overall"]["reasons"]) == 2
    assert mb.apply_to_confidence(None, warning) is None


# --- submission ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pattern():
    from dara.xrd import load_pattern

    return load_pattern(DATA / "BiFeO3.xy")


def test_build_submission_strips_coefficients(pattern):
    from dara.server import api_router

    job = api_router.build_submission_job(
        pattern, "BiFeO3.xy", analysis_mode="known_mixture",
        precursor_formulas="Bi2O3, 2 Fe2O3", use_mp=False,
    )
    assert job.function_kwargs["precursors"] == ["Bi2O3", "Fe2O3"]
    assert job.function_kwargs["elements"] == ["Bi", "Fe", "O"]
    settings = job.metadata["submission_settings"]
    assert settings["precursor_input"] == ["Bi2O3", "2 Fe2O3"]
    assert [a["moles"] for a in settings["precursor_amounts"]] == [1.0, 2.0]

    plain = api_router.build_submission_job(
        pattern, "BiFeO3.xy", analysis_mode="known_mixture",
        precursor_formulas='["Bi2O3", "Fe2O3"]', use_mp=False,
    )
    assert plain.function_kwargs["precursors"] == ["Bi2O3", "Fe2O3"]
    assert "precursor_amounts" not in plain.metadata["submission_settings"]

    with pytest.raises(HTTPException) as info:
        api_router.build_submission_job(
            pattern, "BiFeO3.xy", analysis_mode="known_mixture",
            precursor_formulas="Bi2O3:abc", use_mp=False,
        )
    assert info.value.status_code == 400


# --- end to end -----------------------------------------------------------------------

def _refinement(weights: dict[str, float]):
    phases = {
        name: SimpleNamespace(
            spacegroup_no=136, hermann_mauguin="P4_2/mnm", gewicht=(fraction, 0.002),
            a=(0.46, 0.0001), b=None, c=None, alpha=None, beta=None, gamma=None,
            xray_density=4.2, rphase=4.0, model_extra={}, unit="NM",
        )
        for name, fraction in weights.items()
    }
    return SimpleNamespace(
        lst_data=SimpleNamespace(
            rwp=4.0, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0, num_steps=3,
            phases_results=phases,
        ),
        plot_data=SimpleNamespace(x=np.array([20.0, 21.0, 22.0, 30.0])),
        get_phase_weights=lambda: dict(weights),
    )


def _summarize(name: str, metadata: dict, weights: dict[str, float]):
    from dara.server import api_router
    from dara.server import fit_cautions as fc

    fc.clear_lattice_flag_cache()
    job = {
        "index": 9, "uuid": "u-mass", "status": "COMPLETED",
        "job": {"name": name, "function_kwargs": {}, "metadata": metadata},
        "submitted_time": datetime(2026, 1, 1, 0, 0), "start_time": datetime(2026, 1, 1, 0, 1),
        "end_time": datetime(2026, 1, 1, 0, 3),
    }
    document = SimpleNamespace(
        results=[([[SimpleNamespace(filename=n) for n in weights]], _refinement(weights))],
        grouped_phases=None, missing_peaks=None, extra_peaks=None, screening_rwps=None,
        final_result=_refinement(weights), best_rwp=4.0, precursors=["Fe2O3", "TiO2"],
        phase_predictor=None, predict_kwargs=None, search_kwargs={},
        final_refinement_params=None, input_cifs=[],
        xrd_data=SimpleNamespace(intensities=np.array([1.5, 2.5, 3.5]), intensity_unit="cps"),
    )
    payload = api_router.summarize_document(job, document, persist_summary=False)
    fc.clear_lattice_flag_cache()
    return payload


def test_summarize_document_flags_impossible_fractions_from_filename():
    payload = _summarize("Fe2O3-2TiO2_1000C_60min.xrdml", {}, {FE2TIO5: 0.268, TIO2: 0.732})
    final = payload["final_result"]
    block = final["mass_balance"]
    assert block["status"] == "evaluated" and block["level"] == "warning"
    assert block["source"] == "filename"
    caution = next(c for c in final["cautions"] if c["code"] == "mass_balance")
    assert caution["level"] == "warning" and "Fe:Ti = 0.18:0.82" in caution["message"]
    assert "amounts read from the file name" in caution["message"]
    overall = final["confidence"]["overall"]
    assert overall["level"] in {"check", "expert"}
    assert any(reason.startswith("Mass balance:") for reason in overall["reasons"])
    candidate = payload["all_results"][0]
    assert candidate["mass_balance"]["level"] == "warning"
    assert any(c["code"] == "mass_balance" for c in candidate["cautions"])


def test_summarize_document_uses_submitted_amounts_and_stays_quiet_when_ok():
    metadata = {"submission_settings": {
        "filename": "sample.xy",
        "precursor_amounts": [{"formula": "Fe2O3", "moles": 1.0}, {"formula": "TiO2", "moles": 2.0}],
    }}
    payload = _summarize("sample.xy", metadata, {FE2TIO5: 0.75, TIO2: 0.25})
    final = payload["final_result"]
    assert final["mass_balance"]["level"] == "ok" and final["mass_balance"]["source"] == "input"
    assert not any(c["code"] == "mass_balance" for c in final["cautions"])

    skipped = _summarize("sample.xy", {}, {FE2TIO5: 0.75, TIO2: 0.25})
    assert skipped["final_result"]["mass_balance"]["status"] == "skipped"
    assert not any(c["code"] == "mass_balance" for c in skipped["final_result"]["cautions"])


def test_report_section_and_table_column():
    from dara.server.report.builder import _mass_balance_section
    from dara.server.table_export import TABLE_COLUMNS

    block = mb.mass_balance(AMOUNTS, {FE2TIO5: 26.8, TIO2: 73.2}, source="filename")
    html = "".join(_mass_balance_section(block))
    assert "Fe:Ti = 0.18:0.82" in html and "read from the pattern file name" in html
    assert "differ strongly" in html and "<table" in html
    assert _mass_balance_section(mb.mass_balance(None, {})) == []
    assert TABLE_COLUMNS[-1] == "mass_balance_deviation"
