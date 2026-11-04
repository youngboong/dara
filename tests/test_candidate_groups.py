"""Candidates that differ only in database record are shown once (lowest Rwp)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from dara.server import api_router, table_export, versions
from dara.server import confidence as cf
from dara.server import fit_cautions as fc

A1 = "Bi2O3_14_(cod_9012546)-0"
A2 = "Bi2O3_14_(cod_1010004)-0"
POLY = "Bi2O3_15_(cod_5000001)-0"
B = "Nb16.8O42_55_(cod_2106534)-None"
C = "CaF2_225_(cod_2300449)-0"


def _refinement(rwp, names):
    def phase(name):
        return SimpleNamespace(
            spacegroup_no=int(name.split("_")[1]), hermann_mauguin="P1", gewicht=(0.5, 0.01),
            a=(0.546, 0.0001), b=None, c=None, alpha=None, beta=None, gamma=None,
            xray_density=3.2, rphase=4.0, model_extra={}, unit="NM",
        )

    return SimpleNamespace(
        lst_data=SimpleNamespace(
            rwp=rwp, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0, num_steps=3,
            phases_results={name: phase(name) for name in names},
        ),
        plot_data=SimpleNamespace(x=np.array([20.0, 21.0, 22.0, 30.0])),
        get_phase_weights=lambda: {name: 1.0 / len(names) for name in names},
    )


JOB = {
    "index": 4, "uuid": "u-groups", "status": "COMPLETED",
    "job": {"name": "x.xy", "function_kwargs": {}, "metadata": {}},
    "submitted_time": datetime(2026, 1, 1, 0, 0), "start_time": datetime(2026, 1, 1, 0, 1),
    "end_time": datetime(2026, 1, 1, 0, 3),
}


def _document(specs, final=None):
    results = [
        ([[SimpleNamespace(filename=f"{name}.cif")] for name in names], _refinement(rwp, names))
        for rwp, names in specs
    ]
    return SimpleNamespace(
        results=results, grouped_phases=None, missing_peaks=None, extra_peaks=None,
        screening_rwps=None, final_result=final or results[0][1], best_rwp=6.0, precursors=[],
        phase_predictor=None, predict_kwargs=None, search_kwargs={},
        final_refinement_params=None, input_cifs=[],
        xrd_data=SimpleNamespace(
            angles=np.array([20.0, 21.0]), intensities=np.array([1.5, 2.5]), errors=None,
            intensity_unit="cps",
        ),
    )


@pytest.fixture
def document():
    fc.clear_lattice_flag_cache()
    yield _document([
        (7.35, [A1, B]),  # 0: other record of result 1
        (6.87, [B, A2]),  # 1: lower Rwp -> representative
        (7.00, [POLY, B]),  # 2: polymorph -> separate
        (6.50, [A1, B, C]),  # 3: three phases -> separate
    ])
    fc.clear_lattice_flag_cache()


def test_groups_merge_records_but_keep_polymorphs(document):
    groups = api_router.candidate_groups(document)
    assert [(g.representative, g.members) for g in groups] == [(1, [1, 0]), (2, [2]), (3, [3])]
    assert api_router.displayed_number_of(document, 0) == 1
    assert api_router.displayed_number_of(document, 3) == 3
    assert api_router.displayed_number_of(document, 9) is None
    assert versions.candidate_label(document, 1) == "Candidate 1"
    assert versions.candidate_label(document, 0) == "Candidate 1 · other reference (cod_9012546)"
    assert versions.candidate_label(document, 2) == "Candidate 2"


def test_rwp_tie_and_pinned_representative():
    tie = _document([(7.0, [A1, B]), (7.0, [A2, B])])
    assert api_router.candidate_groups(tie)[0].members == [0, 1]
    view = _document([(7.0, [A1, B]), (6.0, [A2, B])])
    assert api_router.candidate_groups(view)[0].representative == 1
    view.pinned_result_index = 0  # a version view keeps the version as its group's entry
    assert api_router.candidate_groups(view)[0].members == [0, 1]


def test_summary_payload_lists_merged_results(document):
    payload = api_router.summarize_document(JOB, document, persist_summary=False)
    rows = payload["all_results"]
    assert [row["source_index"] for row in rows] == [1, 2, 3]
    assert payload["duplicate_results_removed"] == 1
    assert rows[0]["equivalent_result_count"] == 2
    (member,) = rows[0]["merged_results"]
    assert member["source_index"] == 0 and member["rwp"] == 7.35
    assert member["differing_phases"] == [A1]
    assert member["highlighted_phases"] == [A1, B]
    assert set(member["weight_percent"]) == {A1, B} and member["fit_quality"]["rwp"] == 7.35
    assert rows[1]["merged_results"] == [] and rows[2]["merged_results"] == []
    numbers = {1, 2, 3}
    for row in rows:
        for verdict in (row["confidence"]["phases"] or {}).values():
            evidence = verdict.get("evidence") or {}
            for key in ("alternative", "structure_model_alternative"):
                if evidence.get(key):
                    assert evidence[key]["candidate_number"] in numbers
                    assert evidence[key]["source_index"] in {1, 2, 3}


def test_single_displayed_candidate_has_no_presence_fraction():
    doc = _document([(6.87, [A1, B]), (7.35, [A2, B])])
    fc.clear_lattice_flag_cache()
    payload = api_router.summarize_document(JOB, doc, persist_summary=False)
    assert len(payload["all_results"]) == 1
    phases = payload["final_result"]["confidence"]["phases"]
    assert all(v["evidence"]["presence_fraction"] is None for v in phases.values())
    # confidence of the merged-away result is judged against the same displayed set
    assert cf.confidence_for_selection(doc, 1)["schema"] == 1
    fc.clear_lattice_flag_cache()


def test_final_result_matches_its_own_records_first(document):
    document.final_result = _refinement(6.0, [A1, B])
    assert api_router._matching_result_index(document, document.final_result) == 0
    document.final_result = _refinement(6.0, [B, A2])
    assert api_router._matching_result_index(document, document.final_result) == 1
    other = _refinement(6.0, ["Bi2O3_14_(cod_7)-0", B])  # same identities, unknown record
    assert api_router._matching_result_index(document, other) == 0


def test_archive_of_a_merged_result(document, monkeypatch):
    written = []
    monkeypatch.setattr(
        api_router, "_write_refinement_files",
        lambda output, refinement, folder, *args: written.append(folder),
    )
    summary = api_router.summarize_document(JOB, document, persist_summary=False)
    archive = api_router._make_result_archive(4, document, summary, result_index=0)
    with zipfile.ZipFile(io.BytesIO(archive.getvalue())) as output:
        exported = json.loads(output.read("summary.json"))
        readme = output.read("README.txt").decode()
    assert exported["search_result_number"] == 1
    assert exported["reference_variant_of"] == 1
    assert "Selected search hypothesis: Candidate 1 · other reference (cod_9012546)" in readme
    assert written == [""]

    written.clear()
    monkeypatch.setattr(api_router, "task_table_rows", lambda *a, **k: [])
    api_router._make_result_archive(4, document, summary)
    assert "search_results/result_1/alternative_references/ref_1" in written
    assert written.count("search_results/result_1") == 1


def test_table_marks_the_representative_row_for_an_adopted_merged_result(document):
    summary = api_router.summarize_document(JOB, document, persist_summary=False)
    rows = table_export.task_table_rows(4, {"review": {"selection": 0}}, summary)
    adopted = {row["candidate"] for row in rows if row["adopted"] == "yes"}
    assert adopted == {"1 · other reference (cod_9012546)"}
    rows = table_export.task_table_rows(4, {"review": {"selection": 1}}, summary)
    assert {row["candidate"] for row in rows if row["adopted"] == "yes"} == {"1"}
