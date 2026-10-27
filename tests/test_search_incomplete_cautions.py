"""Cautions, confidence reasons and report sentences for time-limited / pre-filtered searches."""

from types import SimpleNamespace

from dara.server import confidence
from dara.server.fit_cautions import search_cautions
from dara.server.report.builder import _runtime_text


def _overall(search_summary=None):
    return confidence._overall(
        SimpleNamespace(gof=None, rwp=5.0),
        {}, {}, {"level": "ok"}, {},
        counts_like=None, missing_peaks=0, search_summary=search_summary,
    )


# --- search_cautions ----------------------------------------------------------------

def test_old_documents_have_no_search_cautions():
    assert search_cautions(None, None) == []
    assert search_cautions({}, {}) == []
    assert search_cautions({"rejected": []}, {"final_refinement_attempted": 3}) == []
    assert search_cautions("garbage", 5) == []
    assert search_cautions({"prefilter": {"enabled": True, "applied": False, "excluded": 4}}, {}) == []


def test_screening_timeout_caution_lists_files():
    summary = {"screening": {"timed_out_candidates": [
        {"file": "Bi2O3_14_(cod_1)-0.cif"}, {"file": "BiVO4_15_(mp_2)-3.cif"}]}}
    [caution] = search_cautions(summary, {})
    assert caution["code"] == "screening_timed_out" and caution["level"] == "warning"
    assert caution["phase"] is None
    assert caution["message"].startswith(
        "2 reference structure(s) exceeded the screening time limit and were not considered: ")
    assert "Bi2O3" in caution["message"] and "BiVO4" in caution["message"]
    assert caution["message"].endswith(
        ". If one of them is expected, refine it with Known-phase refinement.")


def test_screening_timeout_names_are_truncated_after_five():
    files = [{"file": f"Phase{i}_1_(cod_{i})-0.cif"} for i in range(7)]
    [caution] = search_cautions({"screening": {"timed_out_candidates": files}}, {})
    message = caution["message"]
    assert message.startswith("7 reference structure(s)")
    assert "Phase4" in message and "Phase5" not in message and ", …:" not in message
    assert ", …. If one of them" in message


def test_search_incomplete_stages():
    base = {"stopped_early": True, "runtime_budget_s": 1800}
    cases = [
        ({"stopped_at_stage": "screening", "screening_not_screened": 12},
         "The analysis stopped at its time limit (30 min) during reference screening "
         "(12 references not screened); the result is the best found by then and may be incomplete."),
        ({"stopped_at_stage": "search"},
         "The analysis stopped at its time limit (30 min) during the phase-combination search; "
         "the result is the best found by then and may be incomplete."),
        ({"stopped_at_stage": "final_refinement", "final_refinement_skipped": 2},
         "The analysis stopped at its time limit (30 min) during final refinement "
         "(2 candidates not refined); the result is the best found by then and may be incomplete."),
    ]
    for extra, message in cases:
        [caution] = search_cautions({}, {**base, **extra})
        assert caution["code"] == "search_incomplete" and caution["level"] == "warning"
        assert caution["message"] == message


def test_search_incomplete_without_budget_omits_parenthesis():
    [caution] = search_cautions({}, {"stopped_early": True, "stopped_at_stage": "search"})
    assert caution["message"].startswith("The analysis stopped at its time limit during the phase")
    assert search_cautions({}, {"stopped_early": False, "stopped_at_stage": "search"}) == []


def test_prefilter_caution_is_informational():
    summary = {"prefilter": {"applied": True, "excluded": 31, "before": 146}}
    [caution] = search_cautions(summary, {})
    assert caution == {
        "code": "prefilter_applied", "level": "info", "phase": None,
        "message": "31 of 146 candidate references were skipped by the pre-filter because their "
                   "strongest calculated peaks were not found in the pattern.",
    }
    assert search_cautions({"prefilter": {"applied": True, "excluded": 0, "before": 9}}, {}) == []


def test_caution_order():
    codes = [c["code"] for c in search_cautions(
        {"screening": {"timed_out_candidates": [{"file": "A_1_(cod_1)-0.cif"}]},
         "prefilter": {"applied": True, "excluded": 1, "before": 5}},
        {"stopped_early": True, "stopped_at_stage": "search"},
    )]
    assert codes == ["screening_timed_out", "search_incomplete", "prefilter_applied"]


# --- confidence ---------------------------------------------------------------------

def test_confidence_unchanged_for_legacy_summaries():
    for summary in (None, {}, {"final_refinement_attempted": 4, "final_refinement_failed": 0}):
        assert _overall(summary) == {
            "level": "check", "label": confidence.OVERALL_LABELS["check"],
            "reasons": ["No refined phases were found."],
        }
    assert _overall({"stopped_early": False, "screening_timed_out": 0,
                     "screening_not_screened": 0, "candidates_screened_total": 100})["level"] == "check"


def test_single_timeout_is_a_check_reason():
    result = _overall({"screening_timed_out": 1, "candidates_screened_total": 100})
    assert result["level"] == "check"
    assert "1 reference structure(s) timed out during screening." in result["reasons"]


def test_quarter_missing_is_an_expert_reason():
    result = _overall({"screening_timed_out": 5, "screening_not_screened": 20,
                       "candidates_screened_total": 100})
    assert result["level"] == "expert"
    assert result["reasons"][0] == "25 of 100 reference structures were not screened (time limit)."
    below = _overall({"screening_timed_out": 5, "screening_not_screened": 19,
                      "candidates_screened_total": 100})
    assert below["level"] == "check"
    assert "5 reference structure(s) timed out during screening." in below["reasons"]


def test_stopped_early_is_a_check_reason():
    result = _overall({"stopped_early": True})
    assert result["level"] == "check"
    assert "The search stopped at its time limit; the result may be incomplete." in result["reasons"]


# --- report sentences ---------------------------------------------------------------

def test_runtime_text_is_empty_for_old_documents():
    assert _runtime_text(None, None) == ""
    assert _runtime_text({}, {}) == ""
    assert _runtime_text({"prefilter": {"applied": False, "excluded": 3}}, {"stopped_early": False}) == ""


def test_runtime_text_sentences():
    candidates = {
        "prefilter": {"applied": True, "excluded": 31, "before": 146},
        "screening": {"timeout_s": {"default": 300, "very_heavy": 600},
                      "timed_out_candidates": [{"file": "A_1_(cod_1)-0.cif"}, {"file": "B_1_(cod_2)-0.cif"}]},
    }
    search = {"stopped_early": True, "stopped_at_stage": "final_refinement", "runtime_budget_s": 5400}
    text = _runtime_text(candidates, search)
    assert text == (
        "Before screening, 31 of 146 candidate structures whose strongest calculated reflections "
        "were not found in the measured pattern were skipped (presence pre-filter). "
        "2 candidate structure(s) exceeded the per-reference screening limit of 300 s and were not considered. "
        "The analysis stopped at the user-set time limit of 90 min during final refinement; "
        "the reported result is the best found by then and may be incomplete."
    )


def test_runtime_text_is_escaped_and_partial():
    text = _runtime_text({}, {"stopped_early": True, "stopped_at_stage": "screening"})
    assert text == (
        "The analysis stopped at the user-set time limit during reference screening; "
        "the reported result is the best found by then and may be incomplete."
    )
    only_timeout = _runtime_text({"screening": {"timed_out_candidates": [{"file": "A.cif"}]}}, {})
    assert only_timeout == "1 candidate structure(s) exceeded the per-reference screening limit and were not considered."
