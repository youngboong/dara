"""Jobflow jobs for DARA."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from inspect import signature
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING

from monty.serialization import dumpfn
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Composition

from dara.cif import Cif
from dara.prediction.core import PhasePredictor
from dara.refine import RefinementPhase, do_refinement, do_refinement_no_saving
from dara.schema import PhaseSearchDocument, RefinementDocument
from dara.search import search_phases
from dara.search.data_model import SearchResult
from dara.structure_db import CODDatabase, StructureDatabase, deduplicate_cif_paths
from dara.utils import get_logger

if TYPE_CHECKING:
    from dara.xrd import XRDData

try:
    from jobflow import Maker, job
except ImportError:
    raise ImportError(
        "Please install jobflow to use the dara jobs module! (pip install 'dara[jobflow]')"
    )


logger = get_logger(__name__)


def _phase_equivalence_ids(results: list[SearchResult]) -> dict[Path, str]:
    """Assign one identity to crystallographically equivalent CIF references.

    Formula and space-group labels alone are not sufficient: distinct ordered
    structures can share both. StructureMatcher is therefore the authority for
    merging references. If a CIF cannot be parsed, its file identity is kept so
    that a potentially distinct phase is never silently discarded.
    """
    matcher = StructureMatcher()
    identities: dict[Path, str] = {}
    representatives: dict[str, list[tuple[str, object]]] = {}

    unique_phases = {
        phase.path: phase
        for result in results
        for phases in result.phases
        for phase in phases
    }
    for path, phase in sorted(unique_phases.items(), key=lambda item: str(item[0])):
        try:
            structure = Cif.from_file(phase.path).to_structure()
            formula = structure.composition.reduced_formula
            matches = representatives.setdefault(formula, [])
            matched = next(
                (
                    identity
                    for identity, other in matches
                    if matcher.fit(structure, other)
                ),
                None,
            )
            if matched is None:
                matched = f"structure:{formula}:{len(matches)}"
                matches.append((matched, structure))
            identities[path] = matched
        except Exception as exc:
            logger.warning(
                "Could not compare phase %s structurally; preserving it as a distinct reference: %s",
                path,
                exc,
            )
            identities[path] = f"file:{path.stem}"
    return identities


def _distinct_phase_combinations(
    results: list[SearchResult], limit: int
) -> list[tuple[SearchResult, tuple[tuple[RefinementPhase, float, float], ...]]]:
    """Expand grouped references into distinct, ranked phase hypotheses.

    Search-tree paths that differ only by phase insertion order collapse to one
    hypothesis. Within each phase slot, only crystallographically equivalent
    database records collapse; distinct structures remain separate candidates.
    """
    hypotheses, _ = _collect_distinct_phase_combinations(results, limit)
    return hypotheses


def _collect_distinct_phase_combinations(
    results: list[SearchResult], limit: int
) -> tuple[
    list[tuple[SearchResult, tuple[tuple[RefinementPhase, float, float], ...]]],
    dict[str, int],
]:
    """Collect ranked structural hypotheses and report how grouping affected them."""
    phase_identities = _phase_equivalence_ids(results)
    hypotheses = []
    seen: set[tuple[str, ...]] = set()
    combinations_evaluated = 0
    equivalent_combinations_merged = 0
    for result in sorted(results, key=lambda item: item.refinement_result.lst_data.rwp):
        slot_choices = []
        for phases, foms, strains in zip(
            result.phases, result.foms, result.lattice_strains
        ):
            best_by_identity = {}
            for phase, fom, strain in zip(phases, foms, strains):
                identity = phase_identities[phase.path]
                current = best_by_identity.get(identity)
                if current is None or fom > current[1]:
                    best_by_identity[identity] = (phase, fom, strain)
            slot_choices.append(
                sorted(best_by_identity.values(), key=lambda item: item[1], reverse=True)[
                    :limit
                ]
            )

        combinations = sorted(
            product(*slot_choices),
            key=lambda combination: sum(item[1] for item in combination),
            reverse=True,
        )
        for combination in combinations:
            combinations_evaluated += 1
            key = tuple(sorted(phase_identities[item[0].path] for item in combination))
            if key in seen:
                equivalent_combinations_merged += 1
                continue
            seen.add(key)
            hypotheses.append((result, combination))
    summary = {
        "screening_results_retained": len(results),
        "combinations_evaluated": combinations_evaluated,
        "structurally_distinct_considered": len(hypotheses),
        "equivalent_combinations_merged": equivalent_combinations_merged,
        "selected_for_final_refinement": min(len(hypotheses), limit),
    }
    return hypotheses[:limit], summary


def _expanded_search_results(
    results: list[SearchResult], limit: int
) -> tuple[list[SearchResult], list[float], dict[str, int]]:
    """Create one SearchResult per distinct crystallographic phase combination."""
    expanded = []
    screening_rwps = []
    hypotheses, summary = _collect_distinct_phase_combinations(results, limit)
    for source, combination in hypotheses:
        expanded.append(
            source.model_copy(
                deep=True,
                update={
                    "phases": tuple((item[0],) for item in combination),
                    "foms": tuple((item[1],) for item in combination),
                    "lattice_strains": tuple((item[2],) for item in combination),
                },
            )
        )
        screening_rwps.append(source.refinement_result.lst_data.rwp)
    return expanded, screening_rwps, summary


@dataclass
class RefinementMaker(Maker):
    """Maker to create a job for performing Rietveld refinement using BGMN.

    Args:
        name: The name of the job. This is automatically created if not provided.
    """

    name: str = "refine"
    save: bool = True
    refinement_params: dict | None = None
    show_progress: bool = True

    @job(output_schema=RefinementDocument)
    def make(
        self,
        xrd_data: XRDData,
        cifs: list[Cif],
        phase_params: dict | None = None,
        instrument_name: str = "Aeris-fds-Pixcel1d-Medipix3",
    ):
        """Perform Rietveld refinement.

        Args:
            xrd_data: _description_
            cifs: _description_

        Returns
        -------
            _description_
        """
        pattern_path = Path("xrd_data.xy")
        xrd_data.to_xy_file(pattern_path)

        phase_paths = []
        for cif in cifs:
            cif_path = Path(f"{next(iter(cif.data.keys()))}.cif")
            phase_paths.append(cif_path)

            with open(cif_path, "w") as f:
                f.write(str(cif))

        args = {
            "pattern_path": pattern_path,
            "phase_paths": phase_paths,
            "instrument_name": instrument_name,
            "phase_params": phase_params,
            "refinement_params": self.refinement_params,
            "show_progress": self.show_progress,
        }

        result = do_refinement(**args) if self.save else do_refinement_no_saving(**args)

        return RefinementDocument(
            task_label=self.name,
            result=result,
            rwp=result.lst_data.rwp,
            xrd_data=xrd_data,
            input_cifs=cifs,
            refinement_params=self.refinement_params,
            phase_params=phase_params,
            instrument_name=instrument_name,
        )


@dataclass
class PhaseSearchMaker(Maker):
    """Maker to create a job for performing phase search using BGMN."""

    name: str = "phase_search"
    phase_predictor: PhasePredictor | None = field(default_factory=PhasePredictor)
    verbose: bool = True
    run_final_refinement: bool = True
    cifs_folder_name: str = "dara_cifs"
    max_num_results: int | None = 10

    @job(output_schema=PhaseSearchDocument)
    def make(
        self,
        xrd_data: XRDData,
        cifs: list[Cif] | None = None,
        cif_dbs: list[StructureDatabase] | None = None,
        additional_cifs: list[Cif] | None = None,
        additional_cif_params: dict | None = None,
        precursors: list[str] | None = None,
        elements: list[str] | None = None,
        analysis_mode: str = "reaction",
        database_e_hull_filter: float = 0.05,
        deduplicate_structures: bool = True,
        predict_kwargs: dict | None = None,
        search_kwargs: dict | None = None,
        final_refinement_params: dict | None = None,
        final_refinement_overrides: dict | None = None,
        computed_entries=None,
    ):
        """Perform phase search.

        Args:
            xrd_data: the XRD pattern
            cifs: A list of CIFs to search against. WARNING: If provided, phase
                prediction and CIF download from DBs will be skipped. If you want to
                provide additional CIFs to a database result, use "additional_cifs".
            cif_dbs: A list of CIF databases to search against; if not provided, will
                default to using the COD database.
            additional_cifs: Additional CIFs to include in the search. These are added
                to the CIFs from the database.
            additional_cif_params: Additional parameters for the CIFs, it will overwrite
                the default parameters in the refinement.
            precursors: A list of precursor formulas to predict phases for.
            elements: Elements allowed in an elemental phase-identification search.
            analysis_mode: One of reaction, elemental, or known_mixture.
            database_e_hull_filter: Maximum energy above hull for computed references.
            deduplicate_structures: Remove equivalent structures across databases.
            predict_kwargs: Keyword arguments for the phase predictor.
            search_kwargs: Keyword arguments for the search.
            final_refinement_params: Parameters for the final refinement.
            computed_entries: Computed entries to use for phase prediction.
        """
        directory = Path(os.getcwd()).resolve()

        if predict_kwargs is None:
            predict_kwargs = {}
        if search_kwargs is None:
            search_kwargs = {}

        # for backward compatibility
        # the instrument_name is now instrument_profile
        if "instrument_name" in search_kwargs:
            search_kwargs["instrument_profile"] = search_kwargs.pop("instrument_name")

        if final_refinement_params is None:
            final_refinement_params = {
                "gewicht": "SPHAR4",
                "lattice_range": 0.02,
                "k1": "0_0^1",
                "k2": "0_0^0.001",
                "b1": "0_0^0.05",
            }

        pattern_path = directory / "xrd_data.xy"
        cifs_path = directory / self.cifs_folder_name
        if cifs_path.is_dir():
            logger.info(f"Removing existing CIFs directory, located at: {cifs_path}")
            shutil.rmtree(cifs_path)

        cifs_path.mkdir()

        xrd_data.to_xy_file(pattern_path)

        if precursors is None:
            precursors = []
        if elements is None:
            elements = sorted(
                {str(elem) for formula in precursors for elem in Composition(formula).elements}
            )
        if analysis_mode not in {"reaction", "elemental", "known_mixture"}:
            raise ValueError(f"Unknown analysis mode: {analysis_mode}")

        from dara.task_progress import report_progress, checkpoint_candidate

        report_progress("Loading reference structures / reaction prediction")
        if cifs is not None:
            logger.info("CIFs provided. Skipping CIF download and phase prediction.")
            for cif in cifs + (additional_cifs or []):
                cp = cifs_path / f"{cif.name}.cif"

                with open(cp, "w") as f:
                    f.write(str(cif))
        else:
            if cif_dbs is None:
                cif_dbs = [CODDatabase()]

            if self.phase_predictor is None:
                if analysis_mode == "known_mixture":
                    if not precursors:
                        raise ValueError("Known-mixture mode requires phase formulas.")
                    logger.info("Known-mixture mode; loading exact formula references.")
                    for db in cif_dbs:
                        db.get_cifs_by_formulas(
                            precursors,
                            e_hull_filter=database_e_hull_filter,
                            copy_files=True,
                            dest_dir=cifs_path.as_posix(),
                        )
                else:
                    if not elements:
                        raise ValueError("Elemental mode requires at least one element.")
                    logger.info("Elemental mode; loading phases in %s.", "-".join(elements))
                    for db in cif_dbs:
                        db.get_cifs_by_chemsys(
                            elements,
                            e_hull_filter=database_e_hull_filter,
                            copy_files=True,
                            dest_dir=cifs_path.as_posix(),
                        )

                    # Known starting/expected phases should never be lost just because
                    # the broad database query was capped or filtered.
                    for db in cif_dbs:
                        db.get_cifs_by_formulas(
                            precursors,
                            e_hull_filter=database_e_hull_filter,
                            copy_files=True,
                            dest_dir=cifs_path.as_posix(),
                        )
            else:
                if not precursors:
                    raise ValueError("Reaction mode requires precursor formulas.")
                logger.info("Predicting phases...")
                self.phase_predictor.cif_dbs = (
                    cif_dbs  # ensure CIF databases match as provided in job
                )
                self._predict_folder(
                    precursors,
                    cifs_path=cifs_path,
                    computed_entries=computed_entries,
                    e_hull_filter=database_e_hull_filter,
                    **predict_kwargs,
                )
            if additional_cifs:
                for cif in additional_cifs:
                    cp = cifs_path / f"{cif.name}.cif"
                    with open(cp, "w") as f:
                        f.write(str(cif))

        cif_paths = list(cifs_path.glob("*.cif"))
        if deduplicate_structures:
            cif_paths, candidate_summary = deduplicate_cif_paths(cif_paths)
        else:
            candidate_summary = {
                "before": len(cif_paths),
                "after": len(cif_paths),
                "removed_duplicates": 0,
            }
        if not cif_paths:
            raise ValueError("No reference CIFs were found for this analysis.")
        if additional_cifs:
            additional_cifs_name_set = {cif.name for cif in additional_cifs}
            for i in range(len(cif_paths)):
                if cif_paths[i].stem in additional_cifs_name_set:
                    cif_paths[i] = RefinementPhase(
                        path=cif_paths[i], params=additional_cif_params or {}
                    )

        report_progress("Peak detection, reference screening and phase-combination search")
        results = search_phases(
            pattern_path=pattern_path, phases=cif_paths, **search_kwargs
        )
        result_limit = self.max_num_results or 10
        results, screening_rwps, search_summary = _expanded_search_results(
            results, result_limit
        )
        self._save_results(results)

        best_result = None
        if self.run_final_refinement and results:
            logger.info("Final-refining %d distinct candidate hypotheses...", len(results))
            refinement_kwargs = {
                key: value
                for key, value in search_kwargs.items()
                if key in signature(do_refinement).parameters
            }
            refined_candidates = []
            final_refinement_attempted = len(results)
            for candidate_index, (result, screening_rwp) in enumerate(
                zip(results, screening_rwps), start=1
            ):
                report_progress("Final candidate refinement", candidate_index - 1, len(results))
                candidate_dir = directory / f"_final_candidate_{candidate_index}"
                if candidate_dir.exists():
                    shutil.rmtree(candidate_dir)
                candidate_dir.mkdir()
                dumpfn(final_refinement_params, candidate_dir / "phase_params.json")
                selected_phases = [phase_group[0] for phase_group in result.phases]
                if final_refinement_overrides:
                    from dara.refinement_controls import override_phase_settings

                    selected_phases = override_phase_settings(selected_phases, final_refinement_overrides)
                try:
                    refined = do_refinement(
                        pattern_path=pattern_path,
                        phases=selected_phases,
                        working_dir=candidate_dir,
                        phase_params=final_refinement_params,
                        show_progress=True,
                        **refinement_kwargs,
                    )
                except Exception:
                    logger.exception(
                        "Final refinement failed for candidate %d; omitting it.",
                        candidate_index,
                    )
                    shutil.rmtree(candidate_dir, ignore_errors=True)
                    report_progress("Final candidate refinement", candidate_index, len(results))
                    continue

                checkpoint_candidate(candidate_index, refined, candidate_dir)
                report_progress("Final candidate refinement", candidate_index, len(results))
                metrics = refined.refinement_metrics
                refined_candidates.append(
                    (
                        result.model_copy(
                            deep=True,
                            update={
                                "refinement_result": refined,
                                "missing_peaks": (
                                    metrics.missing_peaks.tolist()
                                    if metrics and metrics.missing_peaks is not None
                                    else None
                                ),
                                "extra_peaks": (
                                    metrics.extra_peaks.tolist()
                                    if metrics and metrics.extra_peaks is not None
                                    else None
                                ),
                            },
                        ),
                        screening_rwp,
                        candidate_dir,
                    )
                )

            if not refined_candidates:
                raise ValueError("Final refinement failed for every candidate hypothesis.")

            refined_candidates.sort(
                key=lambda item: item[0].refinement_result.lst_data.rwp
            )
            results = [item[0] for item in refined_candidates]
            screening_rwps = [item[1] for item in refined_candidates]
            search_summary.update(
                {
                    "final_refinement_attempted": final_refinement_attempted,
                    "final_refinement_succeeded": len(refined_candidates),
                    "final_refinement_failed": (
                        final_refinement_attempted - len(refined_candidates)
                    ),
                    "displayed_hypotheses": len(refined_candidates),
                }
            )
            for rank, (result, _, source_dir) in enumerate(refined_candidates, start=1):
                prefix = "0_best" if rank == 1 else f"final_candidate_{rank}"
                target_dir = directory / (
                    f"{prefix}_rwp_{round(result.refinement_result.lst_data.rwp, 2)}"
                )
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                source_dir.rename(target_dir)
            best_result = results[0].refinement_result
        else:
            search_summary.update(
                {
                    "final_refinement_attempted": 0,
                    "final_refinement_succeeded": 0,
                    "final_refinement_failed": 0,
                    "displayed_hypotheses": len(results),
                }
            )

        parsed_results = [
            (
                [
                    [Cif.from_file(phase.path) for phase in phases]
                    for phases in result.phases
                ],
                result.refinement_result,
            )
            for result in results
        ]
        grouped_phases = [r.grouped_phases for r in results]

        # convert Path to str
        for i in range(len(grouped_phases)):
            for j in range(len(grouped_phases[i])):
                for k in range(len(grouped_phases[i][j])):
                    grouped_phases[i][j][k] = (
                        grouped_phases[i][j][k][0],
                        [p.stem for p in grouped_phases[i][j][k][1]],
                    )

        foms = [[list(f) for f in r.foms] for r in results]
        lattice_strains = [[list(ls) for ls in r.lattice_strains] for r in results]
        missing_peaks = [r.missing_peaks for r in results]
        extra_peaks = [r.extra_peaks for r in results]
        intensity_mismatch_peaks = []
        for result in results:
            metrics = result.refinement_result.refinement_metrics
            mismatch = metrics.intensity_mismatch_peaks if metrics else None
            intensity_mismatch_peaks.append(
                mismatch.tolist() if mismatch is not None else None
            )

        all_rwp = [i[1].lst_data.rwp for i in parsed_results]

        best_rwp = min(all_rwp, default=None)

        return PhaseSearchDocument(
            task_label=self.name,
            results=parsed_results,
            screening_rwps=screening_rwps,
            foms=foms,
            lattice_strains=lattice_strains,
            missing_peaks=missing_peaks,
            extra_peaks=extra_peaks,
            intensity_mismatch_peaks=intensity_mismatch_peaks,
            final_result=best_result,
            best_rwp=best_rwp,
            xrd_data=xrd_data,
            input_cifs=cifs,
            precursors=precursors,
            elements=elements,
            analysis_mode=analysis_mode,
            candidate_sources=[db.name for db in (cif_dbs or [])],
            candidate_summary=candidate_summary,
            search_summary=search_summary,
            phase_predictor=self.phase_predictor,
            predict_kwargs=predict_kwargs,
            search_kwargs=search_kwargs,
            final_refinement_params=final_refinement_params,
            run_final_refinement=self.run_final_refinement,
            cifs_folder_name=self.cifs_folder_name,
            grouped_phases=grouped_phases,
        )

    def _predict_folder(
        self, precursors, cifs_path, e_hull_filter: float = 0.05, **kwargs
    ) -> dict:
        if self.phase_predictor is None:
            raise ValueError("Phase predictor not provided!")

        prediction = self.phase_predictor.predict(precursors, **kwargs)
        dumpfn(prediction, "prediction.json")
        cost_cutoff = 0.1

        if any("CO3" in f for f in precursors):
            logger.info(
                "Carbonate compound detected, raising cost threshold to account for "
                "incorrectly modeled thermodynamics of CO2 production."
            )
            cost_cutoff = 0.5

        self.phase_predictor.write_cifs_from_formulas(
            prediction,
            dest_dir=cifs_path,
            cost_cutoff=cost_cutoff,
            e_hull_filter=e_hull_filter,
        )

        return prediction

    def _save_results(self, results: list[SearchResult]):
        results_sorted = sorted(results, key=lambda x: x.refinement_result.lst_data.rwp)
        for idx, search_result in enumerate(results_sorted):
            phases = search_result.phases
            main_folder = phases[0][0].path.parent.parent

            rwp = search_result.refinement_result.lst_data.rwp
            folder_path = main_folder / f"{idx+1}_result_rwp_{round(rwp,2)}"

            for fn in os.listdir(main_folder):
                if f"{idx+1}_result_rwp" in fn:
                    if os.path.isdir(main_folder / fn):
                        shutil.rmtree(main_folder / fn)
                    else:
                        os.remove(main_folder / fn)

                    logger.info(f"Removing old result: {fn}")

            os.mkdir(folder_path)

            for phase_i, phases_ in enumerate(phases):
                phase_folder = folder_path / f"phase_{phase_i+1}"
                phase_folder.mkdir(exist_ok=True)
                for phase in phases_:
                    shutil.copy(phase.path, phase_folder)

            logger.info(f"Successfully saved result {idx+1}")
