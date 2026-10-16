"""Refine an explicitly chosen phase set without another database/tree search."""

from copy import deepcopy
from dataclasses import dataclass
from inspect import signature
from pathlib import Path, PureWindowsPath

from jobflow import Maker, job

from dara.cif import Cif
from dara.refine import do_refinement
from dara.schema import PhaseSearchDocument
from dara.xrd import XRDData


def valid_reference_filename(name):
    return (
        isinstance(name, str) and bool(name) and len(name) <= 200
        and not name.endswith((' ', '.'))
        and not any(char in name for char in '<>:"/\\|?*')
        and not any(ord(char) < 32 for char in name)
        and not PureWindowsPath(f"{name}.cif").is_reserved()
    )


@dataclass
class SelectedPhaseRefinementMaker(Maker):
    name: str = "Selected-phase refinement"

    @job(output_schema=PhaseSearchDocument)
    def make(
        self,
        xrd_data: XRDData,
        cifs: list[Cif],
        search_kwargs: dict,
        final_refinement_params: dict | None = None,
        context: dict | None = None,
    ):
        """Create a separate, auditable result; never modify the parent result."""
        context = context or {}
        if not cifs:
            raise ValueError("Select at least one phase for refinement.")
        phases = []
        for cif in cifs:
            # Filename is a stored reference identity, never an arbitrary user path.
            name = cif.filename
            if not valid_reference_filename(name):
                raise ValueError("Invalid reference CIF filename.")
            path = Path(f"{name}.cif")
            if path in phases:
                raise ValueError("Duplicate reference CIF filename.")
            cif.to_file(path)
            phases.append(path)

        pattern_path = Path("xrd_data.xy")
        xrd_data.to_xy_file(pattern_path)
        options = {
            key: deepcopy(value)
            for key, value in search_kwargs.items()
            if key in signature(do_refinement).parameters
            and key not in {"pattern_path", "phases", "working_dir", "phase_params", "show_progress"}
        }
        phase_params = final_refinement_params if final_refinement_params is not None else {
            "gewicht": "SPHAR4", "lattice_range": 0.02,
            "k1": "0_0^1", "k2": "0_0^0.001", "b1": "0_0^0.05",
        }
        from dara.task_progress import report_progress, checkpoint_candidate

        report_progress("Refining user-selected phases", 0, 1)
        refined = do_refinement(
            pattern_path=pattern_path, phases=phases,
            working_dir=Path("selected_phase_refinement"),
            phase_params=phase_params, show_progress=True, **options,
        )
        checkpoint_candidate(1, refined, Path("selected_phase_refinement"))
        report_progress("Refining user-selected phases", 1, 1)
        metrics = refined.refinement_metrics

        def peaks(name):
            value = getattr(metrics, name, None) if metrics is not None else None
            return [value.tolist() if value is not None else None]

        return PhaseSearchDocument(
            task_label=self.name, xrd_data=xrd_data, input_cifs=cifs,
            results=[([[cif] for cif in cifs], refined)],
            final_result=refined, best_rwp=refined.lst_data.rwp,
            screening_rwps=[refined.lst_data.rwp],
            missing_peaks=peaks("missing_peaks"), extra_peaks=peaks("extra_peaks"),
            intensity_mismatch_peaks=peaks("intensity_mismatch_peaks"),
            search_kwargs=deepcopy(search_kwargs), final_refinement_params=phase_params,
            analysis_mode="selected_phases", precursors=context.get("precursors"),
            elements=context.get("elements"),
            candidate_sources=context.get("candidate_sources", []),
            candidate_summary={"before": len(cifs), "after": len(cifs), "removed_duplicates": 0},
            search_summary={
                "workflow": "selected_phase_refinement",
                "parent_task_id": context.get("parent_task_id"),
                "parent_selection": context.get("parent_selection"),
                "selected_references": [cif.filename for cif in cifs],
                "final_refinement_attempted": 1, "final_refinement_succeeded": 1,
                "final_refinement_failed": 0, "displayed_hypotheses": 1,
            },
            phase_predictor=None, predict_kwargs=None,
            run_final_refinement=True, cifs_folder_name="selected_phase_refinement",
        )
