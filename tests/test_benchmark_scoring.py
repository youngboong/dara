"""Unit tests for benchmark scoring (pure functions, no BGMN or network)."""

import pytest

from dara.benchmark import scoring
from dara.benchmark.ground_truth import GTAlias, GTSample

ALIASES = {"Li4CO5": GTAlias(formula="Li2CO3", note="mislabelled")}


def gt_sample(phases, *, sid="s1", scan=None, allowed_extra=(), displacement=None):
    return GTSample.model_validate({
        "id": sid,
        "pattern": "dataset/x.xrdml",
        "submission": {
            "analysis_mode": "elemental", "precursors": [], "elements": [],
            "instrument_profile": "Aeris-fds-Pixcel1d-Medipix3",
        },
        "phases": [
            {"formula": f, "spacegroup": sg, "wt_percent": wt, **extra}
            for f, sg, wt, extra in phases
        ],
        "allowed_extra": list(allowed_extra),
        "unknown_phase_present": False, "fully_indexed": True,
        "scan": scan, "source": "test",
        "reference_fit": {"sample_displacement_mm": displacement} if displacement is not None else None,
    })


def pred(name, sg, wt, lattice=None, level=None):
    return {"name": name, "sg": sg, "wt": wt, "lattice": lattice or {}, "level": level}


def summary(phases, *, all_results=(), rwp=7.0, overall=None, displacement=None, runtime=100.0):
    def block(items):
        names = [p["name"] for p in items]
        out = {
            "phases": names,
            "highlighted_phases": names,
            "phase_metadata": {p["name"]: {"spacegroup_number": p["sg"]} for p in items},
            "phase_details": {
                p["name"]: {
                    "weight_percent": p["wt"],
                    "weight_esd_percent": 0.1,
                    "lattice_parameters": {
                        k: {"value": v, "esd": 0.001} for k, v in p["lattice"].items()
                    },
                }
                for p in items
            },
        }
        if any(p["level"] for p in items):
            out["confidence"] = {"phases": {p["name"]: {"level": p["level"]} for p in items}}
        return out

    final = block(phases)
    final["fit_quality"] = {"rwp": rwp, "gof": 2.0}
    final["rwp"] = rwp
    if overall or displacement is not None:
        final.setdefault("confidence", {})
        if overall:
            final["confidence"]["overall"] = {"level": overall}
        if displacement is not None:
            final["confidence"]["sample_displacement"] = {"displacement_mm": displacement}
    return {
        "final_result": final,
        "all_results": [block(items) for items in all_results],
        "runtime": runtime,
    }


Y2O3 = "Y2O3_206_(cod_1)-0"


def test_exact_match():
    gt = gt_sample([("Y2O3", 206, 40.0, {}), ("Fe2O3", 167, 60.0, {})])
    s = summary([pred(Y2O3, 206, 41.0, level="confident"), pred("Fe2O3_167_(cod_2)-0", 167, 59.0)])
    score = scoring.score_sample(gt, s, aliases=ALIASES, runtime_s=12.0)
    assert (score["tp"], score["fp"], score["fn"], score["sg_mismatch"]) == (2, 0, 0, 0)
    assert score["exact_set"] and score["strict_exact"]
    assert score["precision"] == 1.0 and score["recall"] == 1.0
    assert score["wt_mae"] == pytest.approx(1.0)
    assert score["wt_max_abs"] == pytest.approx(1.0)
    assert score["runtime_s"] == 12.0 and score["rwp"] == 7.0 and score["status"] == "scored"
    assert score["matches"][0]["verdict"] == "confident"
    assert score["group"] == "pairwise"


def test_missing_phase():
    gt = gt_sample([("Y2O3", 206, 40.0, {}), ("Fe2O3", 167, 60.0, {})])
    score = scoring.score_sample(gt, summary([pred(Y2O3, 206, 100.0)]), aliases={})
    assert (score["tp"], score["fp"], score["fn"]) == (1, 0, 1)
    assert score["false_negatives"] == ["Fe2O3"]
    assert not score["exact_set"]
    assert score["recall"] == 0.5 and score["precision"] == 1.0


def test_extra_phase_and_weight_renormalisation():
    gt = gt_sample([("Y2O3", 206, 40.0, {}), ("Fe2O3", 167, 60.0, {})])
    s = summary([
        pred(Y2O3, 206, 36.0), pred("Fe2O3_167_(cod_2)-0", 167, 54.0),
        pred("NiO_225_(cod_3)-0", 225, 10.0, level="weak"),
    ])
    score = scoring.score_sample(gt, s, aliases={})
    assert (score["tp"], score["fp"], score["fn"]) == (2, 1, 0)
    assert score["false_positives"] == [{"pred": "NiO_225_(cod_3)-0", "wt_pred": 10.0, "verdict": "weak"}]
    # predicted wt% renormalised over matched phases: 36/90 -> 40, 54/90 -> 60
    assert score["wt_mae"] == pytest.approx(0.0, abs=1e-6)
    assert score["precision"] == pytest.approx(2 / 3)


def test_polymorph_is_sg_mismatch_but_counts_as_tp():
    gt = gt_sample([("Y2O3", 206, 100.0, {})])
    score = scoring.score_sample(gt, summary([pred("Y2O3_199_(cod_9)-0", 199, 100.0)]), aliases={})
    assert (score["tp"], score["fp"], score["fn"], score["sg_mismatch"]) == (1, 0, 0, 1)
    assert score["exact_set"] and not score["strict_exact"]
    assert score["matches"][0]["sg_ok"] is False


def test_sg_compatible_match_is_preferred():
    gt = gt_sample([("Y2O3", 206, 50.0, {})])
    s = summary([pred("Y2O3_199_(cod_9)-0", 199, 50.0), pred(Y2O3, 206, 10.0)])
    score = scoring.score_sample(gt, s, aliases={})
    assert score["matches"][0]["pred"] == Y2O3
    assert score["sg_mismatch"] == 0 and score["fp"] == 1


def test_accept_any_of_and_composition_tolerance():
    gt = gt_sample([("ZnO", 186, 50.0, {}), ("ZnCo2O4", 227, 50.0, {"accept_any_of": ["Co3O4"]})])
    s = summary([pred("ZnO_186_(cod_1)-0", 186, 50.0), pred("Co3O4_227_(cod_2)-0", 227, 50.0)])
    assert scoring.score_sample(gt, s, aliases={})["exact_set"]

    nb = gt_sample([("Nb2O5", None, 100.0, {"accept_any_of": ["Nb16.8O42", "Nb12O29"]})])
    s = summary([pred("Nb12O29_12_(cod_5)-0", 12, 100.0)])
    score = scoring.score_sample(nb, s, aliases={})
    assert score["exact_set"] and score["strict_exact"]  # spacegroup None -> always compatible


def test_formula_matches_rules():
    assert scoring.formula_matches("Nb2O5", ["Nb12O29"], "Nb12O29", {})
    assert scoring.formula_matches("Nb2O5", [], "Nb12O29", {})  # within 3 % atomic fraction
    assert not scoring.formula_matches("Y2O3", [], "Fe2O3", {})
    assert not scoring.formula_matches("NiO", [], None, {})
    assert scoring.formula_matches("TiO2", [], "Ti2O4", {})


def test_alias_li4co5():
    gt = gt_sample([("Li2CO3", 15, 100.0, {})])
    score = scoring.score_sample(gt, summary([pred("Li4CO5_5_(icsd_181261)-0", 5, 100.0)]), aliases=ALIASES)
    assert (score["tp"], score["fp"], score["fn"]) == (1, 0, 0)
    without = scoring.score_sample(gt, summary([pred("Li4CO5_5_(icsd_181261)-0", 5, 100.0)]), aliases={})
    assert (without["tp"], without["fp"], without["fn"]) == (0, 1, 1)


def test_duplicate_nio_merged():
    gt = gt_sample([("NiO", 225, 100.0, {})])
    s = summary([pred("NiO_225_(cod_1)-0", 225, 60.0), pred("NiO_225_(cod_2)-0", 225, 40.0)])
    score = scoring.score_sample(gt, s, aliases={})
    assert (score["tp"], score["fp"], score["fn"]) == (1, 0, 0)
    assert score["matches"][0]["wt_pred_raw"] == pytest.approx(100.0)


def test_allowed_extra_not_false_positive():
    gt = gt_sample([("Bi2O3", 14, 50.0, {}), ("Li2CO3", 15, 50.0, {})], allowed_extra=["Bi2CO5"])
    s = summary([
        pred("Bi2O3_14_(cod_1)-0", 14, 48.0), pred("Li2CO3_15_(cod_2)-0", 15, 47.0),
        pred("Bi2CO5_129_(cod_3)-0", 129, 5.0),
    ])
    score = scoring.score_sample(gt, s, aliases={})
    assert score["fp"] == 0 and score["exact_set"]
    assert score["allowed_extra_found"] == ["Bi2CO5_129_(cod_3)-0"]


def test_not_required_phase_is_not_a_false_negative():
    gt = gt_sample([("Y2O3", 206, 90.0, {}), ("Fe2O3", 167, 10.0, {"required": False})])
    score = scoring.score_sample(gt, summary([pred(Y2O3, 206, 100.0)]), aliases={})
    assert score["fn"] == 0 and score["exact_set"]


def test_lattice_error_and_permutation():
    # Pbnm -> Pnma setting of MoO3: lengths are permuted, still a perfect match
    gt = gt_sample([("MoO3", 62, 100.0, {"lattice": {"a": 3.9628, "b": 13.855, "c": 3.6964}})])
    s = summary([pred("MoO3_62_(cod_1)-0", 62, 100.0, {"a": 13.855, "b": 3.6964, "c": 3.9628})])
    score = scoring.score_sample(gt, s, aliases={})
    assert score["matches"][0]["lattice_rel_error_pct"] == pytest.approx(0.0, abs=1e-6)
    shifted = summary([pred("MoO3_62_(cod_1)-0", 62, 100.0, {"a": 13.855 * 1.01, "b": 3.6964, "c": 3.9628})])
    score = scoring.score_sample(gt, shifted, aliases={})
    assert score["matches"][0]["lattice_rel_error_pct"] == pytest.approx(1.0, abs=0.01)


def test_lattice_symmetry_filled_for_cubic_and_setting_mismatch_skipped():
    gt = gt_sample([("NiO", 225, 100.0, {"lattice": {"a": 4.177}})])
    ok = scoring.score_sample(gt, summary([pred("NiO_225_(cod_1)-0", 225, 100.0, {"a": 4.18})]), aliases={})
    assert ok["matches"][0]["lattice_rel_error_pct"] == pytest.approx(0.0718, abs=0.001)
    bad = scoring.score_sample(gt, summary([pred("NiO_225_(cod_1)-0", 225, 100.0, {"a": 8.354})]), aliases={})
    assert bad["matches"][0]["lattice_rel_error_pct"] is None
    assert bad["matches"][0]["lattice_note"] == "setting_mismatch"


def test_failed_sample():
    gt = gt_sample([("Y2O3", 206, 40.0, {}), ("Fe2O3", 167, 60.0, {})])
    score = scoring.score_sample(gt, None, aliases={}, error="boom", runtime_s=3.0)
    assert score["status"] == "failed" and score["error"] == "boom"
    assert score["tp"] is None and score["n_required_phases"] == 2
    assert score["runtime_s"] == 3.0


def test_top5_contains_truth():
    gt = gt_sample([("Y2O3", 206, 100.0, {})])
    wrong = [pred("NiO_225_(cod_3)-0", 225, 100.0)]
    right = [pred(Y2O3, 206, 100.0)]
    s = summary(wrong, all_results=[wrong, wrong, right])
    score = scoring.score_sample(gt, s, aliases={})
    assert score["exact_set"] is False and score["top5_contains_truth"] is True
    s = summary(wrong, all_results=[wrong, wrong])
    assert scoring.score_sample(gt, s, aliases={})["top5_contains_truth"] is False
    # candidates beyond the fifth do not count
    s = summary(wrong, all_results=[wrong] * 5 + [right])
    assert scoring.score_sample(gt, s, aliases={})["top5_contains_truth"] is False


def test_confidence_and_displacement():
    gt = gt_sample([("Y2O3", 206, 100.0, {})], displacement=-0.4)
    s = summary([pred(Y2O3, 206, 100.0)], overall="check", displacement=-0.2)
    score = scoring.score_sample(gt, s, aliases={})
    assert score["confidence_overall"] == "check"
    assert score["displacement_mm_pred"] == -0.2 and score["displacement_mm_ref"] == -0.4
    assert score["displacement_ratio"] == pytest.approx(0.5)
    assert score["displacement_diff_mm"] == pytest.approx(0.2)


def test_predicted_phases_candidate_and_defensive():
    s = summary([pred(Y2O3, 206, 100.0)], all_results=[[pred("NiO_225_(cod_3)-0", 225, 100.0)]])
    assert [p["name"] for p in scoring.predicted_phases(s)] == [Y2O3]
    only = scoring.predicted_phases(s, candidate=0)
    assert only[0]["formula"] == "NiO" and only[0]["spacegroup"] == 225
    assert scoring.predicted_phases(s, candidate=7) == []
    assert scoring.predicted_phases({}) == []
    odd = {"final_result": {"phases": ["Weird name"], "phase_details": {}, "phase_metadata": {}}}
    assert scoring.predicted_phases(odd)[0]["formula"] is None


def _score(sid, tp, fp, fn, *, group="pairwise", mae=1.0, runtime=10.0, level="check", exact=None, verdicts=()):
    exact = (fp == 0 and fn == 0) if exact is None else exact
    return {
        "id": sid, "group": group, "status": "scored", "n_required_phases": tp + fn,
        "tp": tp, "fp": fp, "fn": fn, "sg_mismatch": 0, "exact_set": exact, "strict_exact": exact,
        "matches": [
            {"wt_abs_error": mae, "lattice_rel_error_pct": 0.1 * (i + 1), "verdict": verdicts[i] if i < len(verdicts) else None}
            for i in range(tp)
        ],
        "false_positives": [{"pred": "x", "wt_pred": 1.0, "verdict": "weak"}] * fp,
        "wt_mae": mae if tp else None, "runtime_s": runtime, "top5_contains_truth": exact,
        "confidence_overall": level, "displacement_ratio": 0.5, "displacement_diff_mm": -0.1,
    }


def test_aggregate_micro_macro():
    scores = [
        _score("a", 3, 0, 0, runtime=10.0, level="high"),
        _score("b", 1, 1, 1, group="precursor-2min", mae=3.0, runtime=20.0, level="check"),
        _score("c", 2, 0, 0, group="precursor-8min", mae=2.0, runtime=30.0, level="high"),
    ]
    agg = scoring.aggregate(scores)
    assert agg["n_samples"] == 3 and agg["n_scored"] == 3 and agg["n_failed"] == 0
    assert agg["phase_recall"] == pytest.approx(6 / 7)
    assert agg["phase_precision"] == pytest.approx(6 / 7)
    assert agg["macro_recall"] == pytest.approx((1 + 0.5 + 1) / 3)
    assert agg["fraction_exact_set"] == pytest.approx(2 / 3)
    assert agg["wt_mae_median"] == pytest.approx(2.0)
    assert agg["wt_abs_error_median"] == pytest.approx(1.5)
    assert agg["runtime_median_s"] == 20.0 and agg["runtime_total_s"] == 60.0
    assert agg["top5_contains_truth_fraction"] == pytest.approx(2 / 3)
    assert set(agg["by_group"]) == {"pairwise", "precursor-2min", "precursor-8min"}
    assert agg["by_group"]["precursor-2min"]["phase_recall"] == 0.5
    assert agg["displacement"] == {"n": 3, "median_ratio": 0.5, "median_abs_diff_mm": 0.1}


def test_aggregate_counts_failures_against_recall():
    scores = [_score("a", 2, 0, 0)]
    failed = scoring.score_sample(
        gt_sample([("Y2O3", 206, 50.0, {}), ("Fe2O3", 167, 50.0, {})], sid="f"), None, aliases={}, error="x"
    )
    agg = scoring.aggregate(scores + [failed])
    assert agg["n_failed"] == 1 and agg["n_scored"] == 1
    assert agg["phase_recall"] == pytest.approx(2 / 4)
    assert agg["fraction_exact_set"] == pytest.approx(0.5)
    assert agg["macro_recall"] == pytest.approx(0.5)


def test_calibration_table():
    scores = [
        _score("a", 2, 0, 0, level="high", verdicts=("confident", "ambiguous")),
        _score("b", 1, 1, 0, level="check", verdicts=("confident",)),
        _score("c", 1, 0, 1, level=None, verdicts=()),
    ]
    scores[2]["confidence_overall"] = None
    cal = scoring.aggregate(scores)["calibration"]
    assert cal["overall"]["high"] == {"n": 1, "exact_set_fraction": 1.0}
    assert cal["overall"]["check"] == {"n": 1, "exact_set_fraction": 0.0}
    assert cal["overall"]["none"]["n"] == 1
    assert cal["phase"]["confident"] == {"n": 2, "tp_fraction": 1.0}
    assert cal["phase"]["weak"] == {"n": 1, "tp_fraction": 0.0}
    assert cal["phase"]["ambiguous"]["n"] == 1
    assert cal["phase"]["none"]["n"] == 1


def test_aggregate_empty():
    agg = scoring.aggregate([])
    assert agg["n_samples"] == 0 and agg["phase_recall"] is None
