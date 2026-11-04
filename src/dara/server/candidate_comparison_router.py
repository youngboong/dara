"""Compare two actual fits on the same measured grid; no confidence claims."""
import numpy as np
from fastapi import APIRouter, HTTPException, Query

from dara.analysis_range import analysis_mask
from dara.server.selected_refinement_router import _load

router = APIRouter(prefix="/api")


def compare_fits(a, b, *range_params) -> dict:
    """Pointwise comparison of two fits on one grid (shared with ``/fit-compare``).

    ``range_params`` are refinement-parameter dicts whose ``wmin``/``wmax``/``excluded_ranges``
    all mask the comparison. Returns the grid, the observed and calculated arrays, the
    validity mask, up to five distinct regions of largest calculated difference and the
    phases only in ``a``, only in ``b`` and common to both.
    """
    x, other = np.asarray(a.plot_data.x, dtype=float), np.asarray(b.plot_data.x, dtype=float)
    if x.shape != other.shape or not np.allclose(x, other, rtol=0, atol=1e-7):
        raise HTTPException(409, "These fits use different angular grids; direct pointwise comparison is unavailable.")
    observed = np.asarray(a.plot_data.y_obs, dtype=float)
    if not np.allclose(observed, b.plot_data.y_obs, rtol=1e-7, atol=1e-7):
        raise HTTPException(409, "These fits use different observed intensities.")
    ya, yb = np.asarray(a.plot_data.y_calc, dtype=float), np.asarray(b.plot_data.y_calc, dtype=float)
    valid = np.isfinite(x)
    for params in range_params:
        params = params or {}
        valid &= analysis_mask(
            x, **{k: params[k] for k in ("wmin", "wmax", "excluded_ranges") if params.get(k) is not None}
        )
    valid &= np.isfinite(observed) & np.isfinite(ya) & np.isfinite(yb)
    # Display a few distinct regions of maximum calculated-intensity difference.
    regions = []
    difference = np.where(valid, np.abs(ya - yb), -1)
    for index in np.argsort(difference)[::-1]:
        if not valid[index] or difference[index] <= 0:
            continue
        if all(abs(float(x[index]) - p["two_theta"]) >= 0.5 for p in regions):
            regions.append({"two_theta": float(x[index]), "calculated_difference": float(difference[index])})
        if len(regions) == 5:
            break
    from dara.server.api_router import _search_result_key

    def identities(result):
        return {_search_result_key([name]): name for name in result.lst_data.phases_results}
    ai, bi = identities(a), identities(b)
    return {
        "x": x, "observed": observed, "a_calculated": ya, "b_calculated": yb, "valid": valid,
        "regions": regions,
        "a_only": [ai[key] for key in ai.keys() - bi.keys()],
        "b_only": [bi[key] for key in bi.keys() - ai.keys()],
        "common": [ai[key] for key in ai.keys() & bi.keys()],
    }


def masked(array, valid) -> list:
    return [float(value) if keep else None for value, keep in zip(array, valid)]


@router.get("/task/{task_id}/candidate-comparison")
async def compare_candidates(task_id: int, left: int = Query(..., ge=0), right: int = Query(..., ge=0)):
    _, document = await _load(task_id)
    results = document.results or []
    if left == right or max(left, right) >= len(results):
        raise HTTPException(400, "Select two different available candidates.")
    a, b = results[left][1], results[right][1]
    fits = compare_fits(a, b, (document.search_kwargs or {}).get("refinement_params"))
    valid, observed = fits["valid"], fits["observed"]
    ya, yb = fits["a_calculated"], fits["b_calculated"]
    return {"left": left, "right": right, "x": fits["x"].tolist(), "observed": masked(observed, valid),
            "left_calculated": masked(ya, valid), "right_calculated": masked(yb, valid),
            "left_residual": masked(observed - ya, valid), "right_residual": masked(observed - yb, valid),
            "left_only": fits["a_only"], "right_only": fits["b_only"], "common": fits["common"],
            "regions": fits["regions"], "left_rwp": a.lst_data.rwp, "right_rwp": b.lst_data.rwp,
            "wavelength_angstrom": _wavelength_angstrom((document.search_kwargs or {}).get("wavelength"))}


def _wavelength_angstrom(spec):
    """Kα1 wavelength (Å) for hover d-spacings; None when unknown."""
    try:
        from dara.search.candidate_cost import wavelength_angstrom

        return round(wavelength_angstrom(spec if spec is not None else "Cu"), 6)
    except Exception:
        return None
