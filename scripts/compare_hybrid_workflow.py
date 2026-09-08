"""Compare COD-only and COD+Materials Project DARA searches on paper data."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from jobflow.managers.local import run_locally

from dara.jobs import PhaseSearchMaker
from dara.structure_db import CODDatabase, MaterialsProjectDatabase
from dara.xrd import load_pattern


CASES = {
    "in_la_ti_o": {
        "pattern": "In2O3_0p3022-La(OH)3_0p5014-TiO2_0p2004--8min.xrdml",
        "formulas": ["In2O3", "La(OH)3", "TiO2"],
        "elements": ["In", "La", "Ti", "O"],
    },
    "ti_zn_o": {
        "pattern": "TiO2_0p4009-ZnO_0p6010--8min.xrdml",
        "formulas": ["TiO2", "ZnO"],
        "elements": ["Ti", "Zn", "O"],
    },
}


def run_search(case_name: str, config_name: str, output_root: Path):
    case = CASES[case_name]
    data_root = Path(__file__).resolve().parents[2] / "data" / "precursor_mixture_patterns"
    pattern_path = data_root / case["pattern"]
    databases = [CODDatabase()]
    if config_name == "hybrid":
        databases.append(MaterialsProjectDatabase(max_structures=100, e_hull_filter=0.05))

    pattern = load_pattern(pattern_path)
    job = PhaseSearchMaker(
        name=f"{case_name}_{config_name}",
        phase_predictor=None,
        verbose=False,
        max_num_results=3,
    ).make(
        pattern,
        precursors=case["formulas"],
        elements=case["elements"],
        analysis_mode="known_mixture",
        database_e_hull_filter=0.05,
        cif_dbs=databases,
        search_kwargs={
            "instrument_profile": "Aeris-fds-Pixcel1d-Medipix3",
            "wavelength": "Cu",
            "express_mode": True,
            "max_phases": len(case["formulas"]),
        },
    )

    run_root = output_root / case_name / config_name
    run_root.mkdir(parents=True, exist_ok=False)
    responses = run_locally(
        job,
        root_dir=run_root,
        create_folders=True,
        ensure_success=True,
        raise_immediately=True,
    )
    response = next(iter(next(iter(responses.values())).values()))
    document = response.output
    return {
        "case": case_name,
        "configuration": config_name,
        "pattern": case["pattern"],
        "expected_formulas": case["formulas"],
        "best_rwp": document.best_rwp,
        "candidate_sources": document.candidate_sources,
        "candidate_summary": document.candidate_summary,
        "final_phases": (
            list(document.final_result.lst_data.phases_results)
            if document.final_result is not None
            else []
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=[*CASES, "all"], default="all")
    parser.add_argument("--configuration", choices=["cod", "hybrid", "both"], default="both")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path(__file__).resolve().parents[1] / "comparison_results" / stamp
    selected_cases = CASES if args.case == "all" else {args.case: CASES[args.case]}
    configurations = ["cod", "hybrid"] if args.configuration == "both" else [args.configuration]
    results = [
        run_search(case_name, config_name, output_root)
        for case_name in selected_cases
        for config_name in configurations
    ]

    report_path = output_root / "comparison.json"
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"Saved report: {report_path}")


if __name__ == "__main__":
    main()
