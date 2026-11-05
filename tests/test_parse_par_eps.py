import numpy as np

from dara.result import fixed_eps_from_control, parse_par

HEADER = "PEAKZAHL=1 TITEL=x LAMBDA=CU POL=1.00000 EPS1=0.000000{eps2}"
REFLECTION = "2 1000.0 0.50000 PHASE=Phase1 1 0 0"


def _two_theta(tmp_path, eps2_header="", fixed=None):
    par = tmp_path / "x.par"
    par.write_text(HEADER.format(eps2=eps2_header) + "\n" + REFLECTION + "\n")
    return float(parse_par(par, ["Phase1"], fixed_eps=fixed)["2theta"].iloc[0])


def test_fixed_eps2_from_the_control_file_shifts_like_a_refined_one(tmp_path):
    # A fixed EPS2 is absent from the .par header; the control-file value must be used.
    refined = _two_theta(tmp_path, " EPS2=-0.002700")
    fixed = _two_theta(tmp_path, "", {"EPS2": -0.0027})
    uncorrected = _two_theta(tmp_path)
    assert np.isclose(fixed, refined)
    assert not np.isclose(fixed, uncorrected)


def test_exponent_notation_is_read_whole(tmp_path):
    assert np.isclose(_two_theta(tmp_path, " EPS2=-2.7e-03"), _two_theta(tmp_path, " EPS2=-0.0027"))


def test_fixed_eps_only_reads_plain_assignments():
    sav = "EPS1=0.0\nEPS2=-0.0027\n"
    assert fixed_eps_from_control(sav) == {"EPS1": 0.0, "EPS2": -0.0027}
    assert fixed_eps_from_control("PARAM[1]=EPS2=0_-0.05^0.05\n") == {}
