"""Validated final-refinement controls. Unspecified fields preserve saved settings."""
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RefinementControls(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    lattice: Literal["inherit", "fixed", "refine"] = "inherit"
    lattice_percent: float = Field(default=2, gt=0, le=10)
    orientation: Literal["inherit", "isotropic", "SPHAR2", "SPHAR4"] = "inherit"
    peak_width: Literal["inherit", "instrument_only", "refine"] = "inherit"


DEFAULT_PHASE_PARAMS = {"gewicht": "SPHAR4", "lattice_range": 0.02,
                        "k1": "0_0^1", "k2": "0_0^0.001", "b1": "0_0^0.05"}


def apply_controls(saved, controls):
    params = deepcopy(saved if saved is not None else DEFAULT_PHASE_PARAMS)
    if controls is None:
        return params
    if controls.lattice != "inherit":
        params["lattice_range"] = "fixed" if controls.lattice == "fixed" else controls.lattice_percent / 100
    if controls.orientation != "inherit":
        params["gewicht"] = "0_0" if controls.orientation == "isotropic" else controls.orientation
    if controls.peak_width != "inherit":
        for key in ("k1", "k2", "b1"):
            params[key] = "fixed" if controls.peak_width == "instrument_only" else DEFAULT_PHASE_PARAMS[key]
    return params


def override_phase_settings(phases, overrides):
    """Explicit user choices take precedence over screening-only phase bounds."""
    from dara.refine import RefinementPhase

    return [RefinementPhase(path=(phase := RefinementPhase.make(value)).path,
                            params={**phase.params, **overrides}) for value in phases]
