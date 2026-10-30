from dara.cif2str import _distinct_axes


def test_equal_independent_axes_are_moved_apart_by_one_digit():
    # COD 9007583 (NH4H2PO4, P2_12_12_1) has a = b; BGMN rejects such a cell.
    out = _distinct_axes({"A": 0.74997, "B": 0.74997, "C": 0.75493})
    assert round(out["A"], 5) == 0.74997
    assert round(out["B"], 5) == 0.74998
    assert out["C"] == 0.75493


def test_three_equal_axes_and_untouched_cells():
    out = _distinct_axes({"A": 0.5, "B": 0.5, "C": 0.5, "ALPHA": 90.0})
    assert len({round(out[k], 5) for k in "ABC"}) == 3
    assert out["ALPHA"] == 90.0
    distinct = {"A": 0.5, "B": 0.6, "C": 0.7, "BETA": 100.0}
    assert _distinct_axes(distinct) == distinct
    assert _distinct_axes({"A": 0.4}) == {"A": 0.4}
