"""Compare two actual fits on the same measured grid; no confidence claims."""
import numpy as np
from fastapi import APIRouter, HTTPException, Query

from dara.analysis_range import analysis_mask
from dara.server.selected_refinement_router import _load

router = APIRouter(prefix="/api")


@router.get("/task/{task_id}/candidate-comparison")
async def compare_candidates(task_id: int, left: int = Query(..., ge=0), right: int = Query(..., ge=0)):
    _, document = _load(task_id)
    results = document.results or []
    if left == right or max(left, right) >= len(results):
        raise HTTPException(400, "Select two different available candidates.")
    a, b = results[left][1], results[right][1]
    x, other = np.asarray(a.plot_data.x), np.asarray(b.plot_data.x)
    if x.shape != other.shape or not np.allclose(x, other, rtol=0, atol=1e-7):
        raise HTTPException(409, "These fits use different angular grids; direct pointwise comparison is unavailable.")
    observed = np.asarray(a.plot_data.y_obs, dtype=float)
    if not np.allclose(observed, b.plot_data.y_obs, rtol=1e-7, atol=1e-7):
        raise HTTPException(409, "These fits use different observed intensities.")
    ya, yb = np.asarray(a.plot_data.y_calc, dtype=float), np.asarray(b.plot_data.y_calc, dtype=float)
    options = (document.search_kwargs or {}).get("refinement_params", {})
    valid = analysis_mask(x, **{k: options[k] for k in ("wmin", "wmax", "excluded_ranges") if k in options})
    valid &= np.isfinite(x) & np.isfinite(observed) & np.isfinite(ya) & np.isfinite(yb)
    # Display a few distinct regions of maximum calculated-intensity difference.
    regions = []
    for index in np.argsort(np.where(valid, abs(ya - yb), -1))[::-1]:
        if not valid[index] or abs(ya[index] - yb[index]) <= 0:
            continue
        if all(abs(float(x[index]) - p["two_theta"]) >= 0.5 for p in regions):
            regions.append({"two_theta": float(x[index]), "calculated_difference": float(abs(ya[index] - yb[index]))})
        if len(regions) == 5:
            break
    from dara.server.api_router import _search_result_key

    def identities(result):
        return {_search_result_key([name]): name for name in result.lst_data.phases_results}
    ai, bi = identities(a), identities(b)

    def values(array):
        return [float(value) if keep else None for value, keep in zip(array, valid)]

    return {"left": left, "right": right, "x": x.tolist(), "observed": values(observed),
            "left_calculated": values(ya), "right_calculated": values(yb),
            "left_residual": values(observed-ya), "right_residual": values(observed-yb),
            "left_only": [ai[key] for key in ai.keys()-bi.keys()],
            "right_only": [bi[key] for key in bi.keys()-ai.keys()],
            "common": [ai[key] for key in ai.keys() & bi.keys()],
            "regions": regions, "left_rwp": a.lst_data.rwp, "right_rwp": b.lst_data.rwp}
