import pytest

from dara.series import parse_series_variables, parse_variable_token, series_order


@pytest.mark.parametrize(
    "token, value, unit",
    [
        ("300C", 300.0, "°C"),
        ("300degC", 300.0, "°C"),
        ("300°C", 300.0, "°C"),
        ("1.5K", 1.5, "K"),
        ("60min", 60.0, "min"),
        ("2h", 2.0, "h"),
        ("0p5wt%", 0.5, "wt%"),
        ("10MPa", 10.0, "MPa"),
        ("5%", 5.0, "%"),
        ("3.3V", 3.3, "V"),
    ],
)
def test_parse_variable_token_accepts_number_and_unit(token, value, unit):
    assert parse_variable_token(token) == {"raw": token, "value": value, "unit": unit}


@pytest.mark.parametrize(
    "token", ["Bi2O3-2MoO3", "ZnO", "300", "C", "300c", "300k", "x0.1", "a300C", "300Cx", "", "1p"]
)
def test_parse_variable_token_rejects_non_variables(token):
    assert parse_variable_token(token) is None


def test_varying_slot_is_chosen_and_constant_slot_ignored():
    names = ["ZnO_300C_60min.xy", "ZnO_500C_60min.xy", "ZnO_700C_60min.xy"]
    parsed = parse_series_variables(names)
    assert parsed["unit"] == "°C"
    assert [item["value"] for item in parsed["variables"]] == [300, 500, 700]
    assert parsed["slot"] == 1


def test_formula_token_never_parses_and_time_is_the_variable():
    names = ["Bi2O3-2MoO3_400C_60min", "Bi2O3-2MoO3_400C_120min"]
    parsed = parse_series_variables(names)
    assert parsed["unit"] == "min"
    assert [item["value"] for item in parsed["variables"]] == [60, 120]


def test_decimal_with_p_separator_and_no_extension():
    parsed = parse_series_variables(["s_0p5wt%", "s_1p0wt%"])
    assert parsed["unit"] == "wt%"
    assert [item["value"] for item in parsed["variables"]] == [0.5, 1.0]


def test_decimal_point_is_not_mistaken_for_an_extension():
    parsed = parse_series_variables(["s_0.5wt%", "s_1.0wt%"])
    assert [item["value"] for item in parsed["variables"]] == [0.5, 1.0]


def test_no_variable_falls_back_to_natural_order():
    parsed = parse_series_variables(["a.xy", "b.xy"])
    assert parsed == {"unit": None, "slot": None, "variables": [None, None]}
    assert series_order(["sample10.xy", "sample2.xy", "sample1.xy"], [None] * 3) == [2, 1, 0]


def test_single_file_or_constant_values_have_no_variable():
    assert parse_series_variables(["ZnO_300C.xy"])["variables"] == [None]
    assert parse_series_variables(["a_300C.xy", "b_300C.xy"])["variables"] == [None, None]


def test_slot_must_exist_in_every_file():
    parsed = parse_series_variables(["a_300C.xy", "a_500C.xy", "a_none.xy"])
    assert parsed["variables"] == [None, None, None]


def test_series_order_by_value_then_name():
    names = ["z_700C.xy", "z_300C.xy", "z_500C.xy"]
    parsed = parse_series_variables(names)
    assert series_order(names, parsed["variables"]) == [2, 0, 1]
    # A missing variable for one file switches to natural file-name order.
    assert series_order(["b.xy", "a.xy"], [parsed["variables"][0], None]) == [1, 0]


def test_hyphen_separated_tokens_are_parsed():
    parsed = parse_series_variables(["ZnO-500C-2h.xy", "ZnO-600C-2h.xy", "ZnO-700C-2h.xy"])
    assert parsed["unit"] == "°C"
    assert [item["value"] for item in parsed["variables"]] == [500, 600, 700]
    parsed = parse_series_variables(["ZnO-500C-1h", "ZnO-500C-2h"])
    assert parsed["unit"] == "h" and [item["value"] for item in parsed["variables"]] == [1, 2]


def test_hyphenated_formulas_still_never_parse():
    for names in (
        ["Bi2O3-2MoO3-a", "Bi2O3-2MoO3-b"],
        ["Fe-2O3_x", "Fe-2O3_y"],
    ):
        assert parse_series_variables(names)["variables"] == [None, None]
