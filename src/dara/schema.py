"""Pydantic document schemas for jobflow jobs."""

from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field
from pymatgen.core import Composition

from dara.cif import Cif
from dara.prediction.core import PhasePredictor
from dara.result import RefinementResult
from dara.utils import datetime_str
from dara.xrd import XRDData


class RefinementDocument(BaseModel):
    """Refinement document schema. This is used to store the results of a refinement
    job.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_label: Optional[str] = Field("refine", description="The name of the task.")
    last_updated: str = Field(
        default_factory=datetime_str,
        description="Timestamp of when the document was last updated.",
    )
    result: RefinementResult = Field(description="The result of the refinement.")
    rwp: Optional[float] = Field(None, description="The Rwp of the refinement.")
    xrd_data: XRDData = Field(description="The input XRD data.")
    # input_cifs: list[Cif] = Field(description="The input CIFs.")
    refinement_params: Optional[dict] = Field(
        None, description="The refinement parameters."
    )
    phase_params: Optional[dict] = Field(None, description="The phase parameters.")
    instrument_name: Optional[str] = Field(None, description="The instrument name.")


class PhaseSearchDocument(BaseModel):
    """Phase search document schema. This is used to store the results of a phase search
    job.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_label: Optional[str] = Field(
        "phase_search", description="The name of the task."
    )
    last_updated: str = Field(
        default_factory=datetime_str,
        description="Timestamp of when the document was last updated.",
    )
    results: Optional[
        list[tuple[Union[list[Cif], list[list[Cif]]], RefinementResult]]
    ] = Field(None, description="The result of the refinement.")
    screening_rwps: Optional[list[float]] = Field(
        None,
        description="Fast search-stage Rwp values before candidate final refinement.",
    )

    foms: Optional[list[list[list[float]]]] = Field(
        None, description="The FOMs of the results."
    )
    lattice_strains: Optional[list[list[list[float]]]] = Field(
        None, description="The lattice strains of the results."
    )
    missing_peaks: Optional[list[Optional[list[list[float]]]]] = Field(
        None, description="The missing peaks of the results."
    )
    extra_peaks: Optional[list[Optional[list[list[float]]]]] = Field(
        None, description="The extra peaks of the results."
    )
    intensity_mismatch_peaks: Optional[
        list[Optional[list[list[float]]]]
    ] = Field(None, description="The intensity-mismatch peaks of the results.")

    final_result: Optional[RefinementResult] = Field(
        None, description="The result of the re-refinement of the best result."
    )
    best_rwp: Optional[float] = Field(None, description="The best (lowest) Rwp.")
    xrd_data: XRDData = Field(description="The input XRD data.")
    input_cifs: Optional[list[Cif]] = Field(None, description="The input CIFs.")
    precursors: Optional[list[str]] = Field(None, description="The precursor formulas.")
    elements: Optional[list[str]] = Field(None, description="The allowed elements.")
    analysis_mode: str = Field("reaction", description="The analysis workflow.")
    candidate_sources: list[str] = Field(
        default_factory=list, description="Structure databases used for candidates."
    )
    candidate_summary: dict = Field(
        default_factory=dict, description="Candidate counts before and after deduplication."
    )
    search_summary: dict = Field(
        default_factory=dict,
        description="Counts describing hypothesis grouping and final refinement.",
    )
    phase_predictor: Optional[PhasePredictor] = Field(
        None, description="The phase predictor."
    )
    predict_kwargs: Optional[dict] = Field(
        None, description="The kwargs for the phase predictor."
    )
    search_kwargs: Optional[dict] = Field(
        None, description="The kwargs for the search."
    )
    final_refinement_params: Optional[dict] = Field(
        None, description="The final refinement parameters."
    )
    run_final_refinement: bool = Field(
        None, description="Whether to run final refinement."
    )
    cifs_folder_name: str = Field(
        None, description="The name of the folder containing the CIFs."
    )

    grouped_phases: Optional[list[list[list[tuple[Composition, list[str]]]]]] = Field(
        None, description="The groupped phases."
    )
