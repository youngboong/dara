from types import SimpleNamespace

from dara.server.api_router import (
    _fit_quality,
    _phase_refinement_details,
)


def _refinement():
    phases = {
        "A": SimpleNamespace(
            gewicht=(2.0, 0.1), a=(0.4, 0.001), b=None, c=None,
            alpha=None, beta=None, gamma=None, xray_density=4.2,
            rphase=3.1, model_extra={"GrainSize(1,1,1)": (52.0, 3.0)},
        ),
        "B": SimpleNamespace(
            gewicht=(1.0, 0.1), a=0.5, b=None, c=None,
            alpha=None, beta=None, gamma=None, xray_density=5.1,
            rphase=4.2, model_extra={},
        ),
    }
    lst = SimpleNamespace(
        rp=3.0, rpb=4.0, r=5.0, rwp=6.0, rexp=3.0,
        d=1.8, rho=7.0, num_steps=12, phases_results=phases,
    )
    return SimpleNamespace(
        lst_data=lst,
        plot_data=SimpleNamespace(x=[10.0, 20.0, 30.0]),
    )


def test_fit_quality_and_phase_details_use_reported_values():
    result = _refinement()
    quality = _fit_quality(result)
    assert quality["gof"] == 2.0
    assert quality["chi_squared"] == 4.0
    assert quality["two_theta_min"] == 10.0
    assert quality["data_points"] == 3

    details = _phase_refinement_details(result)
    assert round(details["A"]["weight_percent"], 6) == round(200 / 3, 6)
    assert details["A"]["weight_esd_percent"] is not None
    assert details["A"]["lattice_parameters"]["a"]["value"] == 4.0
    assert details["A"]["lattice_parameters"]["a"]["esd"] == 0.01
    assert details["A"]["grain_sizes"][0]["value"] == 52.0


def test_unrefined_start_value_grain_size_is_not_reported():
    result = _refinement()
    # BGMN prints the same start-value GrainSize (no esd) for phases whose B1 stayed fixed
    result.lst_data.phases_results["B"].model_extra = {"GrainSize(1,1,1)": 84.1811, "B1": 0.005}
    assert _phase_refinement_details(result)["B"]["grain_sizes"] == []
    # a refined B1 without an esd on the size still counts as a result
    result.lst_data.phases_results["B"].model_extra = {"GrainSize(1,1,1)": 84.1811, "B1": (0.01, 0.002)}
    assert _phase_refinement_details(result)["B"]["grain_sizes"][0]["value"] == 84.1811
