"""Reconstruct the inputs of the normal final refinement for a stored analysis.

The automatic workflow (``dara.jobs``) refines every displayed candidate with a
specific recipe: per-phase parameters for user-supplied CIFs, the Materials Project
lattice window and explicit user overrides, on top of the global final-refinement
parameters and the ``do_refinement`` options found in ``search_kwargs``.  Re-refinements
done after the analysis must follow the very same recipe so their numbers are
comparable.  This module is pure (no server imports) and never touches a store.
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from typing import Any

from dara.refine import RefinementPhase, do_refinement
from dara.refinement_controls import (
    DEFAULT_PHASE_PARAMS,
    apply_controls,
    override_phase_settings,
)

#: Keys of ``do_refinement`` that are never taken from ``search_kwargs``.
_EXCLUDED_KWARGS = frozenset(
    {
        "bgmn_timeout",
        "pattern_path",
        "phases",
        "working_dir",
        "phase_params",
        "show_progress",
        "observed_peaks",
        "cancel_event",
    }
)

SELECTED_WORKFLOW = "selected_phase_refinement"

#: Tasks finished before this moment were refined by older final-refinement code.
#: Bump when final-refinement behaviour changes.
REFINEMENT_CODE_DATE = "2026-10-28T00:00:00+00:00"


@dataclass(frozen=True)
class FinalRefinementSpec:
    """Everything needed to call ``do_refinement`` the way the final refinement does."""

    phases: list[RefinementPhase]
    phase_params: dict
    refinement_kwargs: dict
    mp_applied: list[str] = field(default_factory=list)
    overrides: dict = field(default_factory=dict)  # final per-phase overrides that were applied

    def effective_phase_params(self) -> dict[str, dict]:
        """``stem -> {**global phase params, **per-phase params}`` as ``do_refinement`` merges them."""
        return {
            phase.path.stem: {**self.phase_params, **phase.params} for phase in self.phases
        }


def _decoded_additional_cifs(function_kwargs: dict | None) -> list:
    raw = (function_kwargs or {}).get("additional_cifs") or (function_kwargs or {}).get("cifs")
    if not raw:
        return []
    try:
        from monty.json import MontyDecoder

        decoded = MontyDecoder().process_decoded(deepcopy(raw))
    except Exception:
        return []
    return list(decoded) if isinstance(decoded, (list, tuple)) else []


def job_parity_inputs(function_kwargs: dict | None) -> dict:
    """Run-time inputs of the final refinement that are not stored in the document.

    Returns ``{"additional_names", "additional_cif_params", "final_refinement_overrides"}``.
    Never raises; unreadable data yields empty values.
    """
    names: set[str] = set()
    for cif in _decoded_additional_cifs(function_kwargs):
        try:
            names.add(str(cif.name))
        except Exception:  # noqa: BLE001 - a broken CIF must not break the spec
            pass
        filename = getattr(cif, "filename", None)
        if filename:
            names.add(Path(str(filename)).stem)
    kwargs = function_kwargs or {}
    params = kwargs.get("additional_cif_params")
    overrides = kwargs.get("final_refinement_overrides")
    return {
        "additional_names": names,
        "additional_cif_params": deepcopy(params) if isinstance(params, dict) else None,
        "final_refinement_overrides": deepcopy(overrides) if isinstance(overrides, dict) else None,
    }


def _refinement_kwargs(document, refinement_params: dict | None) -> dict:
    accepted = signature(do_refinement).parameters
    kwargs = {
        key: deepcopy(value)
        for key, value in (getattr(document, "search_kwargs", None) or {}).items()
        if key in accepted and key not in _EXCLUDED_KWARGS
    }
    if refinement_params is not None:
        if refinement_params:
            kwargs["refinement_params"] = deepcopy(refinement_params)
        else:
            kwargs.pop("refinement_params", None)
    return kwargs


def build_final_refinement_spec(
    document,
    function_kwargs: dict | None,
    cif_paths: list[Path],
    *,
    controls=None,
    phase_params: dict | None = None,
    overrides: dict | None = None,
    extra_additional_stems: Iterable[str] = (),
    refinement_params: dict | None = None,
) -> FinalRefinementSpec:
    """Mirror ``dara.jobs`` final refinement (steps a, b, c) for ``cif_paths``.

    Args:
        document: the stored ``PhaseSearchDocument``.
        function_kwargs: ``record["job"]["function_kwargs"]`` of the worker record.
        cif_paths: phase files (``.cif`` or ``.str``) to refine.
        controls: optional ``RefinementControls`` applied on top of the saved params.
        phase_params: base override of the global phase parameters (e.g. from a version).
        overrides: base override of ``final_refinement_overrides`` (e.g. from a version).
        extra_additional_stems: stems treated like uploaded CIFs (reference library).
        refinement_params: replaces ``search_kwargs["refinement_params"]`` (``{}`` drops it).
    """
    from dara.structure_quality import MP_LATTICE_WINDOW, apply_mp_lattice_window

    kwargs = _refinement_kwargs(document, refinement_params)
    base = phase_params or getattr(document, "final_refinement_params", None) or DEFAULT_PHASE_PARAMS
    params = deepcopy(base)
    if controls is not None:
        params = apply_controls(params, controls)

    parity = job_parity_inputs(function_kwargs)
    paths = [Path(path) for path in cif_paths]
    summary = getattr(document, "search_summary", None)
    selected_workflow = isinstance(summary, dict) and summary.get("workflow") == SELECTED_WORKFLOW

    mp_applied: list[str] = []
    if selected_workflow:
        # Selected-phase tasks were refined without steps a-c.
        phases = [RefinementPhase(path=path) for path in paths]
    else:
        uploaded = set(parity["additional_names"]) | set(extra_additional_stems)
        phases = [
            RefinementPhase(path=path, params=deepcopy(parity["additional_cif_params"] or {}))
            if path.stem in uploaded
            else RefinementPhase(path=path)
            for path in paths
        ]
        candidate_summary = getattr(document, "candidate_summary", None)
        window = (
            ((candidate_summary or {}).get("mp_lattice_window") or {}).get("range")
            if isinstance(candidate_summary, dict)
            else None
        )
        phases, mp_applied = apply_mp_lattice_window(
            phases,
            params,
            enabled=window is not None,
            window=tuple(window) if window else MP_LATTICE_WINDOW,
        )

    # Selected-phase tasks never used the job-level overrides (see the module notes above).
    job_overrides = None if selected_workflow else parity["final_refinement_overrides"]
    base_overrides = overrides if overrides is not None else job_overrides
    merged_overrides: dict[str, Any] = dict(base_overrides or {})
    if controls is not None:
        merged_overrides.update(apply_controls({}, controls))
    if merged_overrides:
        phases = override_phase_settings(phases, merged_overrides)

    return FinalRefinementSpec(
        phases=phases,
        phase_params=params,
        refinement_kwargs=kwargs,
        mp_applied=mp_applied,
        overrides=merged_overrides,
    )
