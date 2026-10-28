"""The post-analysis refinement recipe must mirror ``dara.jobs`` final refinement."""

import json
from copy import deepcopy
from functools import wraps
from inspect import signature
from pathlib import Path

from monty.json import MontyEncoder

from dara import selected_refinement
from dara.final_refinement_spec import build_final_refinement_spec, job_parity_inputs
from dara.refine import RefinementPhase, do_refinement
from dara.refinement_controls import (
    DEFAULT_PHASE_PARAMS,
    RefinementControls,
    apply_controls,
    override_phase_settings,
)
from dara.structure_quality import MP_LATTICE_WINDOW, apply_mp_lattice_window, widen_for_mp
from test_selected_refinement import _document, _refined, references  # noqa: F401


def test_plain_document_matches_selected_refinement_arguments(monkeypatch, tmp_path, references):
    document = _document(references)
    captured = {}

    @wraps(do_refinement)
    def fake_refine(**kwargs):
        captured.update(kwargs)
        return _refined(references)

    monkeypatch.setattr(selected_refinement, "do_refinement", fake_refine)
    monkeypatch.chdir(tmp_path)
    maker = selected_refinement.SelectedPhaseRefinementMaker(name="parity")
    maker.make.original(
        maker, document.xrd_data, references, document.search_kwargs,
        document.final_refinement_params, {},
    )
    spec = build_final_refinement_spec(
        document, {}, [Path(f"{cif.filename}.cif") for cif in references]
    )
    assert spec.phase_params == captured["phase_params"] == document.final_refinement_params
    skipped = {"pattern_path", "phases", "working_dir", "phase_params", "show_progress"}
    assert spec.refinement_kwargs == {k: v for k, v in captured.items() if k not in skipped}
    assert spec.effective_phase_params() == {
        cif.filename: document.final_refinement_params for cif in references
    }


def _jobs_recipe(paths, final_params, *, mp_enabled, uploaded, additional_params, overrides):
    """Literal copy of dara/jobs.py:476-482 and 540-553."""
    phases = list(paths)
    for i in range(len(phases)):
        if phases[i].stem in uploaded:
            phases[i] = RefinementPhase(path=phases[i], params=additional_params or {})
    phases, applied = apply_mp_lattice_window(
        phases, final_params, enabled=mp_enabled, window=MP_LATTICE_WINDOW
    )
    if overrides:
        phases = override_phase_settings(phases, overrides)
    return phases, applied


def _function_kwargs(cifs, **extra):
    return {"additional_cifs": json.loads(json.dumps(cifs, cls=MontyEncoder)), **extra}


def test_matches_the_jobs_recipe_for_uploaded_mp_and_overrides(references):
    uploaded = references[0]
    document = _document(references)
    document.candidate_summary = {
        "mp_lattice_window": {"enabled": True, "range": list(MP_LATTICE_WINDOW)}
    }
    kwargs = _function_kwargs(
        [uploaded],
        additional_cif_params={"lattice_range": 0.05},
        final_refinement_overrides={"gewicht": "0_0"},
    )
    paths = [
        Path(f"{uploaded.name}.cif"),
        Path("TiO2_136_(mp-2657)-0.cif"),
        Path("Fe2O3_167_(cod_1000)-3.cif"),
    ]
    expected, applied = _jobs_recipe(
        paths, document.final_refinement_params, mp_enabled=True, uploaded={uploaded.name},
        additional_params={"lattice_range": 0.05}, overrides={"gewicht": "0_0"},
    )
    spec = build_final_refinement_spec(document, kwargs, paths)
    assert spec.effective_phase_params() == {
        phase.path.stem: {**document.final_refinement_params, **phase.params}
        for phase in expected
    }
    assert spec.mp_applied == applied == ["TiO2_136_(mp-2657)-0"]
    effective = spec.effective_phase_params()
    assert tuple(effective["TiO2_136_(mp-2657)-0"]["lattice_range"]) == widen_for_mp(0.03)
    assert effective[uploaded.name]["lattice_range"] == 0.05
    assert effective["Fe2O3_167_(cod_1000)-3"]["lattice_range"] == 0.03  # global value of the fixture
    assert all(values["gewicht"] == "0_0" for values in effective.values())


def test_disabled_window_leaves_mp_phases_untouched(references):
    document = _document(references)
    document.candidate_summary = {"mp_lattice_window": {"enabled": False, "range": None}}
    spec = build_final_refinement_spec(document, {}, [Path("TiO2_136_(mp-2657)-0.cif")])
    assert spec.mp_applied == []
    assert spec.effective_phase_params()["TiO2_136_(mp-2657)-0"] == document.final_refinement_params


def test_missing_window_record_is_treated_as_disabled(references):
    document = _document(references)
    document.candidate_summary = {}
    spec = build_final_refinement_spec(document, None, [Path("TiO2_136_(mp-2657)-0.cif")])
    assert spec.mp_applied == []


def test_bgmn_timeout_and_non_search_options_are_dropped(references):
    document = _document(references)
    document.search_kwargs = {
        **document.search_kwargs, "bgmn_timeout": 99, "observed_peaks": [1], "cancel_event": 1,
        "use_residual": False,
    }
    spec = build_final_refinement_spec(document, {}, [Path("a_(cod_1)-0.cif")])
    assert "bgmn_timeout" not in spec.refinement_kwargs
    assert "observed_peaks" not in spec.refinement_kwargs
    assert "cancel_event" not in spec.refinement_kwargs
    assert spec.refinement_kwargs["use_residual"] is False
    assert set(spec.refinement_kwargs) <= set(signature(do_refinement).parameters)
    assert document.search_kwargs["bgmn_timeout"] == 99  # the document is not mutated


def test_selected_phase_documents_get_plain_phases(references):
    document = _document(references)
    document.search_summary = {"workflow": "selected_phase_refinement"}
    document.candidate_summary = {"mp_lattice_window": {"range": list(MP_LATTICE_WINDOW)}}
    kwargs = _function_kwargs(
        [references[0]],
        additional_cif_params={"lattice_range": 0.05},
        final_refinement_overrides={"gewicht": "0_0"},
    )
    paths = [Path(f"{references[0].name}.cif"), Path("TiO2_136_(mp-2657)-0.cif")]
    spec = build_final_refinement_spec(document, kwargs, paths)
    assert all(phase.params == {} for phase in spec.phases)
    assert spec.mp_applied == []
    assert spec.effective_phase_params() == {p.stem: document.final_refinement_params for p in paths}


def test_all_inherit_controls_add_no_overrides():
    assert apply_controls({}, RefinementControls()) == {}


def test_controls_change_global_params_and_overrides(references):
    document = _document(references)
    controls = RefinementControls(lattice="refine", lattice_percent=3, orientation="isotropic")
    paths = [Path("a_(cod_1)-0.cif")]
    spec = build_final_refinement_spec(document, {}, paths, controls=controls)
    assert spec.phase_params["lattice_range"] == 0.03
    assert spec.phase_params["gewicht"] == "0_0"
    assert spec.overrides == {"lattice_range": 0.03, "gewicht": "0_0"}
    assert spec.effective_phase_params()["a_(cod_1)-0"]["lattice_range"] == 0.03
    base = build_final_refinement_spec(document, {}, paths, controls=RefinementControls())
    assert base.overrides == {}
    assert base.phase_params == document.final_refinement_params


def test_base_overrides_from_a_version_replace_job_overrides(references):
    document = _document(references)
    kwargs = {"final_refinement_overrides": {"gewicht": "0_0"}}
    paths = [Path("a_(cod_1)-0.cif")]
    from_job = build_final_refinement_spec(document, kwargs, paths)
    from_version = build_final_refinement_spec(document, kwargs, paths, overrides={"k1": "fixed"})
    assert from_job.effective_phase_params()["a_(cod_1)-0"]["gewicht"] == "0_0"
    assert from_version.effective_phase_params()["a_(cod_1)-0"]["gewicht"] == "SPHAR4"
    assert from_version.effective_phase_params()["a_(cod_1)-0"]["k1"] == "fixed"


def test_library_stems_are_treated_like_uploads(references):
    document = _document(references)
    kwargs = {"additional_cif_params": {"lattice_range": 0.05}}
    spec = build_final_refinement_spec(
        document, kwargs, [Path("lib_phase.cif"), Path("other_(cod_1)-0.cif")],
        extra_additional_stems=["lib_phase"],
    )
    effective = spec.effective_phase_params()
    assert effective["lib_phase"]["lattice_range"] == 0.05
    assert effective["other_(cod_1)-0"]["lattice_range"] == 0.03


def test_refinement_params_argument_replaces_the_stored_ones(references):
    document = _document(references)
    path = [Path("a_(cod_1)-0.cif")]
    kept = build_final_refinement_spec(document, {}, path)
    assert kept.refinement_kwargs["refinement_params"]["wmin"] == 12.0
    replaced = build_final_refinement_spec(
        document, {}, path, refinement_params={"background_degree": 8}
    )
    assert replaced.refinement_kwargs["refinement_params"] == {"background_degree": 8}
    dropped = build_final_refinement_spec(document, {}, path, refinement_params={})
    assert "refinement_params" not in dropped.refinement_kwargs


def test_defaults_when_the_document_has_no_params(references):
    document = _document(references)
    document.final_refinement_params = None
    spec = build_final_refinement_spec(document, None, [Path("a_(cod_1)-0.cif")])
    assert spec.phase_params == DEFAULT_PHASE_PARAMS
    assert spec.phase_params is not DEFAULT_PHASE_PARAMS


def test_job_parity_inputs_never_raise():
    assert job_parity_inputs(None)["additional_names"] == set()
    assert job_parity_inputs({"additional_cifs": "garbage"})["additional_names"] == set()
    broken = job_parity_inputs({"additional_cifs": [{"@module": "nope", "@class": "X"}]})
    assert broken["additional_names"] == set()


def test_job_parity_inputs_names_cover_name_and_filename(references):
    cif = deepcopy(references[0])
    cif.filename = "my_upload"
    inputs = job_parity_inputs(_function_kwargs([cif]))
    assert {cif.name, "my_upload"} <= inputs["additional_names"]
