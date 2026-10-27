import tempfile
import unittest
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest
from monty.json import MontyDecoder

from dara.xrd import (
    SUPPORTED_PATTERN_EXTENSIONS,
    BRMLFile,
    PatternFormatError,
    RASXFile,
    XYFile,
    convert_pattern_to_xy,
    get_xrdml_data,
    get_xrdml_metadata,
    load_pattern,
    load_raw,
    load_xrdml,
    rasx2xy,
    read_ascii_pattern,
    recommend_xrdml_instrument_profile,
)

FORMATS = Path(__file__).parent / "test_data" / "formats"


def test_xrdml_accepts_legacy_intensities_tag():
    xrdml = {
        "xrdMeasurements": {
            "xrdMeasurement": {
                "scan": {
                    "dataPoints": {
                        "positions": [
                            {"startPosition": "20.0", "endPosition": "21.0"}
                        ],
                        "intensities": {"@unit": "counts", "#text": "10 20 30"},
                    }
                }
            }
        }
    }

    angles, intensities = get_xrdml_data(xrdml)

    np.testing.assert_allclose(angles, [20.0, 20.5, 21.0])
    np.testing.assert_allclose(intensities, [10.0, 20.0, 30.0])


def test_xrdml_metadata_recommends_aeris_profile():
    path = (
        Path(__file__).parents[1]
        / "dataset"
        / "binary_reactions"
        / "NH4H2PO4-Na2CO3_200C_60min.xrdml"
    )

    metadata = get_xrdml_metadata(load_xrdml(path))
    suggestion = recommend_xrdml_instrument_profile(metadata)

    assert metadata["instrument_control_software"] == "AERIS"
    assert metadata["detector"] == "PIXcel1D-Medipix3 detector"
    assert metadata["radiation"] == "Cu"
    assert metadata["data_points"] > 100
    assert suggestion == {
        "profile": "Aeris-fds-Pixcel1d-Medipix3",
        "confidence": "high",
        "reason": "Aeris and PIXcel1D/Medipix3 metadata match the bundled profile.",
    }


def test_legacy_xpert_xrdml_recommends_xcelerator_profile():
    document = {
        "xrdMeasurements": {
            "comment": {"entry": "Diffractometer system=XPERT-PRO"},
            "xrdMeasurement": {
                "usedWavelength": {"kAlpha1": "1.54056"},
                "incidentBeamPath": {
                    "xRayTube": {"anodeMaterial": "Cu", "@name": "PW3373/10 Cu LFF"},
                },
                "diffractedBeamPath": {
                    "detector": {"@name": "X'Celerator"},
                },
                "scan": {
                    "header": {"source": {"instrumentControlSoftware": "XPERT-PRO"}},
                    "dataPoints": {
                        "positions": [{"startPosition": "20", "endPosition": "80"}],
                        "intensities": "1 2 3",
                    },
                },
            },
        },
    }

    metadata = get_xrdml_metadata(document)
    suggestion = recommend_xrdml_instrument_profile(metadata)

    assert metadata["instrument_system"] == "XPERT-PRO"
    assert metadata["detector"] == "X'Celerator"
    assert metadata["radiation"] == "Cu"
    assert suggestion["profile"] == "PW3040-FDS-ADS-Xcelerator"
    assert suggestion["confidence"] == "medium"


def test_bruker_raw_v2_load(tmp_path):
    content = bytearray(256 + 60 + 3 * 4)
    content[:4] = b"RAW2"
    struct.pack_into("<H", content, 4, 1)
    struct.pack_into("<HH", content, 256, 60, 3)
    struct.pack_into("<fff", content, 264, 2.0, 0.5, 10.0)
    struct.pack_into("<fff", content, 316, 100.0, 200.0, 300.0)
    raw_path = tmp_path / "test.RAW"
    raw_path.write_bytes(content)

    (angles, intensities), binary_data = load_raw(raw_path)

    np.testing.assert_allclose(angles, [10.0, 10.5, 11.0])
    np.testing.assert_allclose(intensities, [100.0, 200.0, 300.0])
    assert binary_data == content


class TestRASXFile(unittest.TestCase):
    """Test RASX file loading and conversion."""

    def setUp(self):
        """Set up the test. This test file is taken from https://github.com/thomasgredig/rigakuXRD/blob/master/inst/extdata/RM20220915Si01.rasx"""
        self.rasx_path = Path(__file__).parent / "test_data" / "RM20220915Si01.rasx"

    def test_rasx_file_load(self):
        """Test loading a RASX file with specific values from RM20220915Si01.rasx."""
        rasx_file = RASXFile.from_file(self.rasx_path)

        # Check that angles and intensities are loaded
        self.assertIsNotNone(rasx_file.angles)
        self.assertIsNotNone(rasx_file.intensities)

        # Check that they are numpy arrays
        self.assertIsInstance(rasx_file.angles, np.ndarray)
        self.assertIsInstance(rasx_file.intensities, np.ndarray)

        # Check specific values from the file
        self.assertEqual(len(rasx_file.angles), 2151)
        self.assertEqual(len(rasx_file.intensities), 2151)

        # Check first angle and intensity values
        np.testing.assert_almost_equal(rasx_file.angles[0], 8.5, decimal=2)
        np.testing.assert_almost_equal(rasx_file.intensities[0], 591.0, decimal=0)

        # Check last angle and intensity values
        np.testing.assert_almost_equal(rasx_file.angles[-1], 30.0, decimal=2)
        np.testing.assert_almost_equal(rasx_file.intensities[-1], 524.0, decimal=0)

        # Check max intensity
        np.testing.assert_almost_equal(np.max(rasx_file.intensities), 639.0, decimal=0)

        # Check intermediate values
        np.testing.assert_almost_equal(rasx_file.angles[100], 9.5, decimal=2)
        np.testing.assert_almost_equal(rasx_file.intensities[100], 565.0, decimal=0)
        np.testing.assert_almost_equal(rasx_file.angles[500], 13.5, decimal=2)
        np.testing.assert_almost_equal(rasx_file.intensities[500], 526.0, decimal=0)
        np.testing.assert_almost_equal(rasx_file.angles[1000], 18.5, decimal=2)
        np.testing.assert_almost_equal(rasx_file.intensities[1000], 560.0, decimal=0)

        # Check that intensities are non-negative
        self.assertGreaterEqual(np.min(rasx_file.intensities), 0)

    def test_rasx_file_properties(self):
        """Test RASXFile properties."""
        rasx_file = RASXFile.from_file(self.rasx_path)

        # Check that binary_data is stored
        self.assertIsNotNone(rasx_file.binary_data)
        self.assertIsInstance(rasx_file.binary_data, bytes)
        self.assertGreater(len(rasx_file.binary_data), 0)

    def test_rasx_to_xy_conversion(self):
        """Test converting RASX to XY format with specific values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            xy_path = rasx2xy(self.rasx_path, tmpdir)

            # Check that XY file was created
            self.assertTrue(xy_path.exists())
            self.assertTrue(xy_path.is_file())
            self.assertEqual(xy_path.suffix, ".xy")

            # Check that the XY file contains valid data with correct values
            xy_data = np.loadtxt(xy_path, unpack=False)
            self.assertEqual(xy_data.shape[0], 2151)  # Should have 2151 data points
            self.assertEqual(xy_data.shape[1], 2)  # angle, intensity columns

            # Check first and last values match
            np.testing.assert_almost_equal(xy_data[0, 0], 8.5, decimal=2)  # First angle
            np.testing.assert_almost_equal(xy_data[0, 1], 591.0, decimal=0)  # First intensity
            np.testing.assert_almost_equal(xy_data[-1, 0], 30.0, decimal=2)  # Last angle
            np.testing.assert_almost_equal(xy_data[-1, 1], 524.0, decimal=0)  # Last intensity

    def test_rasx_file_round_trip(self):
        """Test that RASX file can be loaded and saved back."""
        rasx_file = RASXFile.from_file(self.rasx_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            output_path = tmpdir / "test_output.rasx"

            # Save the file
            rasx_file.to_rasx_file(output_path)

            # Check that file was created
            self.assertTrue(output_path.exists())

            # Load it back and compare
            loaded_file = RASXFile.from_file(output_path)

            # Check that angles and intensities match
            np.testing.assert_array_almost_equal(
                rasx_file.angles, loaded_file.angles, decimal=6
            )
            np.testing.assert_array_almost_equal(
                rasx_file.intensities, loaded_file.intensities, decimal=6
            )

    def test_rasx_file_plot(self):
        """Test that RASX file can be plotted."""
        rasx_file = RASXFile.from_file(self.rasx_path)

        # Test plotting (should not raise an exception)
        ax = rasx_file.plot()
        self.assertIsNotNone(ax)

        # Test with different styles
        ax = rasx_file.plot(style="points")
        self.assertIsNotNone(ax)

        ax = rasx_file.plot(style="line")
        self.assertIsNotNone(ax)



# ---------------------------------------------------------------------------
# Input-format robustness
# ---------------------------------------------------------------------------


def _assert_ascending(angles):
    assert np.all(np.diff(angles) > 0)


def _warnings_text(pattern) -> str:
    return " | ".join(pattern.load_warnings)


def test_supported_extensions_include_new_formats():
    for ext in (".brml", ".ras", ".csv", ".dat", ".asc", ".xy", ".xrdml", ".rasx"):
        assert ext in SUPPORTED_PATTERN_EXTENSIONS
    assert list(SUPPORTED_PATTERN_EXTENSIONS) == sorted(SUPPORTED_PATTERN_EXTENSIONS)


def test_rigaku_ras_header_cp949_and_attenuator():
    pattern = load_pattern(FORMATS / "rigaku_header.ras")
    text = (FORMATS / "rigaku_header.ras").read_bytes().decode("cp949")
    rows = [line.split() for line in text.splitlines() if line[:1].isdigit()]
    assert len(rows) == 40

    assert len(pattern.angles) == 40
    _assert_ascending(pattern.angles)
    assert pattern.angles[0] == pytest.approx(20.0)
    assert pattern.angles[-1] == pytest.approx(39.5)
    assert pattern.errors is None
    for i, (_angle, intensity, coef) in enumerate(rows):
        factor = float(coef)
        assert factor == (10.0 if i in (5, 6) else 1.0)
        assert pattern.intensities[i] == pytest.approx(float(intensity) * factor)
    assert "Applied Rigaku attenuator coefficients to 2 point(s)." in pattern.load_warnings


def test_semicolon_decimal_comma_csv():
    pattern = load_pattern(FORMATS / "semicolon_decimal_comma.csv")
    assert len(pattern.angles) == 30
    assert pattern.angles[0] == pytest.approx(10.0)  # not 1000
    assert pattern.angles[1] == pytest.approx(10.5)
    assert pattern.angles[-1] == pytest.approx(24.5)
    assert pattern.intensities[0] == pytest.approx(100.0)
    assert pattern.load_warnings == []


def test_comma_csv_with_bom_and_header():
    pattern = load_pattern(FORMATS / "comma_header.csv")
    assert len(pattern.angles) == 30
    _assert_ascending(pattern.angles)
    assert pattern.angles[0] == pytest.approx(10.0)
    assert pattern.angles[-1] == pytest.approx(24.5)
    assert pattern.intensities[0] == pytest.approx(100.0)


def test_text_header_with_numeric_metadata_line():
    pattern = load_pattern(FORMATS / "header_text.txt")
    assert len(pattern.angles) == 30
    assert pattern.angles[0] == pytest.approx(10.0)  # not the 1.5406 wavelength
    assert pattern.angles[-1] == pytest.approx(24.5)
    assert "Ignored 1 numeric line(s) outside the main data block" in _warnings_text(pattern)


def test_descending_xy_is_reversed():
    pattern = load_pattern(FORMATS / "descending.xy")
    assert len(pattern.angles) == 31
    _assert_ascending(pattern.angles)
    assert pattern.angles[0] == pytest.approx(20.0)
    assert pattern.angles[-1] == pytest.approx(50.0)
    assert "descending" in _warnings_text(pattern)
    # intensities were reversed together with the angles
    raw = np.loadtxt(FORMATS / "descending.xy")
    np.testing.assert_allclose(pattern.intensities, raw[::-1, 1])


def test_xye_and_three_column_handling(tmp_path):
    xye = tmp_path / "pattern.xye"
    xye.write_text("# title\n10 100 10\n11 120 11\n12 110 10.5\n")
    pattern = load_pattern(xye)
    np.testing.assert_allclose(pattern.errors, [10, 11, 10.5])
    assert pattern.load_warnings == []

    csv = tmp_path / "three.csv"
    csv.write_text("a,b,c\n10,100,7\n11,120,8\n12,110,9\n")
    pattern = load_pattern(csv)
    assert pattern.errors is None
    assert "Using columns 1–2 of 3" in _warnings_text(pattern)


def test_unsorted_and_duplicate_rows_are_cleaned(tmp_path):
    path = tmp_path / "messy.dat"
    path.write_text("12 3\n10 1\n11 2\n11 2\n13 nan\n14 5\n")
    pattern = load_pattern(path)
    np.testing.assert_allclose(pattern.angles, [10, 11, 12, 14])
    np.testing.assert_allclose(pattern.intensities, [1, 2, 3, 5])
    text = _warnings_text(pattern)
    assert "sorted" in text
    assert "duplicate" in text
    assert "Dropped 1 row(s)" in text


def test_utf16_and_latin1_text(tmp_path):
    utf16 = tmp_path / "wide.txt"
    utf16.write_bytes("2theta intensity\n10 1\n11 2\n12 3\n".encode("utf-16"))
    assert len(load_pattern(utf16).angles) == 3
    latin = tmp_path / "latin.txt"
    latin.write_bytes(b"Sample \xe9\xe8 \xff\n10 1\n11 2\n12 3\n")
    assert len(load_pattern(latin).angles) == 3


def test_garbage_and_too_short_files_raise_pattern_format_error():
    with pytest.raises(PatternFormatError) as excinfo:
        load_pattern(FORMATS / "garbage.xy")
    assert isinstance(excinfo.value, ValueError)
    message = str(excinfo.value)
    assert message.startswith("garbage.xy")
    assert "line 1" in message
    assert "Traceback" not in message

    with pytest.raises(PatternFormatError) as excinfo:
        load_pattern(FORMATS / "two_rows.xy")
    assert "two_rows.xy" in str(excinfo.value)
    assert "at least 3" in str(excinfo.value)


def test_empty_and_out_of_range_ascii(tmp_path):
    empty = tmp_path / "empty.xy"
    empty.write_text("\n  \n")
    with pytest.raises(PatternFormatError, match="the file is empty"):
        load_pattern(empty)

    wrong = tmp_path / "wrong_columns.xy"
    wrong.write_text("100 10\n200 20\n300 30\n400 40\n")
    with pytest.raises(PatternFormatError, match=r"between 0 and 180.*line 2"):
        load_pattern(wrong)

    zeros = tmp_path / "zeros.xy"
    zeros.write_text("10 0\n11 0\n12 0\n")
    assert len(load_pattern(zeros).angles) == 3  # quality is reported elsewhere


def test_unsupported_extension_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="Unsupported pattern file format"):
        load_pattern(tmp_path / "pattern.pdf")


def test_ras_second_scan_warns(tmp_path):
    path = tmp_path / "two.ras"
    path.write_text(
        "*RAS_INT_START\n10 1 1\n11 2 1\n12 3 1\n*RAS_INT_END\n"
        "*RAS_INT_START\n20 1 1\n21 2 1\n22 3 1\n*RAS_INT_END\n"
    )
    pattern = load_pattern(path)
    np.testing.assert_allclose(pattern.angles, [10, 11, 12])
    assert "RAS file contains 2 scans; using scan 1." in pattern.load_warnings


def test_read_ascii_pattern_fmt_override(tmp_path):
    path = tmp_path / "noext"
    path.write_text("10 1 0.5\n11 2 0.5\n12 3 0.5\n")
    _, _, errors, _ = read_ascii_pattern(path, fmt=".xye")
    np.testing.assert_allclose(errors, [0.5, 0.5, 0.5])


# --- XRDML ---------------------------------------------------------------


def test_multiscan_xrdml_uses_first_scan_two_theta():
    pattern = load_pattern(FORMATS / "multiscan.xrdml")
    assert len(pattern.angles) == 20
    assert pattern.angles[0] == pytest.approx(10.0)  # 2Theta, not Omega (5.0)
    assert pattern.angles[-1] == pytest.approx(29.0)
    assert pattern.intensity_unit == "counts"
    assert any(
        w.startswith("XRDML contains 2 scans; using scan 1 (2Theta 10.00–29.00°)")
        for w in pattern.load_warnings
    )


def test_single_positions_xrdml_loads():
    pattern = load_pattern(FORMATS / "single_positions.xrdml")
    assert len(pattern.angles) == 20
    assert pattern.angles[0] == pytest.approx(10.0)
    assert pattern.angles[-1] == pytest.approx(29.0)
    assert pattern.load_warnings == []


def test_listpositions_xrdml_in_counts_per_second():
    pattern = load_pattern(FORMATS / "listpositions_cps.xrdml")
    expected = [10.0 + 0.5 * i + (0.05 if i % 2 else 0.0) for i in range(30)]
    np.testing.assert_allclose(pattern.angles, expected)
    assert pattern.intensity_unit == "cps"
    assert len(pattern.intensities) == 30


def test_real_xrdml_dataset_reports_counts():
    path = (
        Path(__file__).parents[1]
        / "dataset"
        / "binary_reactions"
        / "CoO-ZnO_1100C_60min.xrdml"
    )
    if not path.exists():
        pytest.skip("dataset file not available")
    pattern = load_pattern(path)
    assert pattern.intensity_unit == "counts"
    # 2Theta is listed before Omega in this file; point count must be unchanged
    angles, intensities = get_xrdml_data(load_xrdml(path))
    assert len(angles) == len(intensities) == len(pattern.angles) > 100
    assert pattern.angles[0] < pattern.angles[-1]


def test_xrdml_with_utf8_bom_bytes(tmp_path):
    path = tmp_path / "bom.xrdml"
    path.write_bytes(b"\xef\xbb\xbf" + (FORMATS / "single_positions.xrdml").read_bytes())
    pattern = load_pattern(path)
    assert len(pattern.angles) == 20


def test_malformed_xrdml_raises_pattern_format_error(tmp_path):
    path = tmp_path / "broken.xrdml"
    path.write_text("<xrdMeasurements><oops></xrdMeasurements>")
    with pytest.raises(PatternFormatError) as excinfo:
        load_pattern(path)
    assert str(excinfo.value).startswith("broken.xrdml: not a valid XRDML/XML file")

    no_scan = tmp_path / "noscan.xrdml"
    no_scan.write_text("<xrdMeasurements><xrdMeasurement/></xrdMeasurements>")
    with pytest.raises(PatternFormatError) as excinfo:
        load_pattern(no_scan)
    assert str(excinfo.value).startswith("noscan.xrdml: ")


def test_xrdml_list_positions_length_mismatch_is_rejected():
    document = {
        "xrdMeasurements": {
            "xrdMeasurement": {
                "scan": {
                    "dataPoints": {
                        "positions": {"@axis": "2Theta", "listPositions": "10 11"},
                        "counts": "1 2 3",
                    }
                }
            }
        }
    }
    with pytest.raises(PatternFormatError, match="2 positions but 3 intensity"):
        get_xrdml_data(document)


def test_xrdml_beam_attenuation_factors_warn(tmp_path):
    text = (FORMATS / "single_positions.xrdml").read_text(encoding="utf-8")
    text = text.replace(
        "</dataPoints>",
        "<beamAttenuationFactors>1 1 5</beamAttenuationFactors></dataPoints>",
    )
    path = tmp_path / "atten.xrdml"
    path.write_text(text, encoding="utf-8")
    pattern = load_pattern(path)
    assert "XRDML lists beam attenuation factors; intensities are used as recorded." in (
        pattern.load_warnings
    )


# --- BRML ----------------------------------------------------------------


def _write_brml(path, datums, route_extra="", member="Experiment0/RawData0.xml"):
    body = "".join(f"<Datum>{d}</Datum>" for d in datums)
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><RawData><DataRoutes>'
        f"<DataRoute>{route_extra}{body}</DataRoute></DataRoutes></RawData>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, xml)
    return path


def test_brml_default_column_layout(tmp_path):
    datums = [f"1,1,{10.0 + 0.5 * i:.3f},{5.0 + 0.25 * i:.3f},{100 + i}" for i in range(20)]
    pattern = load_pattern(_write_brml(tmp_path / "a.brml", datums))
    assert isinstance(pattern, BRMLFile)
    assert len(pattern.angles) == 20
    assert pattern.angles[0] == pytest.approx(10.0)
    assert pattern.angles[-1] == pytest.approx(19.5)
    assert pattern.intensities[0] == pytest.approx(100.0)
    assert pattern.intensities[-1] == pytest.approx(119.0)
    assert pattern.intensity_unit is None


def test_brml_uses_raw_data_views(tmp_path):
    # columns: time, 2theta, theta, counter -> no default layout applies
    views = (
        "<DataViews>"
        '<RawDataView LogicName="Time" Start="0" Length="1"/>'
        '<RawDataView LogicName="TwoTheta" Start="1" Length="1"/>'
        '<RawDataView LogicName="Theta" Start="2" Length="1"/>'
        '<RawDataView LogicName="Counter1D" Start="3" Length="1"/>'
        "</DataViews>"
    )
    datums = [f"0.5,{20.0 + 0.1 * i:.3f},{10.0 + 0.05 * i:.3f},{500 + i}" for i in range(15)]
    pattern = load_pattern(_write_brml(tmp_path / "views.brml", datums, views))
    assert pattern.angles[0] == pytest.approx(20.0)
    assert pattern.angles[-1] == pytest.approx(21.4)
    assert pattern.intensities[0] == pytest.approx(500.0)
    assert pattern.intensities[-1] == pytest.approx(514.0)


def test_brml_finds_two_theta_column_by_search(tmp_path):
    # Only 3 columns, so the default layout does not apply; the first strictly
    # increasing column inside (0, 180] is used for 2theta, the last for counts.
    datums = [f"{10.0 + 0.5 * i:.3f},{20.0 + i},{300 + i}" for i in range(10)]
    pattern = load_pattern(_write_brml(tmp_path / "search.brml", datums))
    assert pattern.angles[0] == pytest.approx(10.0)
    assert pattern.intensities[0] == pytest.approx(300.0)


def test_brml_errors(tmp_path):
    bad = tmp_path / "notzip.brml"
    bad.write_bytes(b"this is not a zip archive")
    with pytest.raises(PatternFormatError, match="notzip.brml: not a valid BRML"):
        load_pattern(bad)

    empty = tmp_path / "nodata.brml"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("other.xml", "<x/>")
    with pytest.raises(PatternFormatError, match="no Experiment"):
        load_pattern(empty)

    nocolumn = _write_brml(tmp_path / "nocol.brml", [f"{500 - i},{i}" for i in range(6)])
    with pytest.raises(PatternFormatError, match="could not identify the 2θ column"):
        load_pattern(nocolumn)


def test_brml_multiple_routes_and_files_warn(tmp_path):
    xml = (
        "<RawData><DataRoutes>"
        + "".join(
            "<DataRoute>"
            + "".join(f"<Datum>1,1,{10 + i + r * 20},0,{i}</Datum>" for i in range(5))
            + "</DataRoute>"
            for r in range(2)
        )
        + "</DataRoutes></RawData>"
    )
    path = tmp_path / "multi.brml"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Experiment0/RawData0.xml", xml)
        archive.writestr("Experiment1/RawData0.xml", xml)
    pattern = load_pattern(path)
    text = _warnings_text(pattern)
    assert "BRML contains 2 scan ranges; using range 1." in text
    assert "BRML contains 2 data files; using Experiment0/RawData0.xml." in text
    assert pattern.angles[0] == pytest.approx(10.0)


# --- conversion ----------------------------------------------------------


def test_convert_comma_csv_to_plain_xy(tmp_path):
    out = convert_pattern_to_xy(FORMATS / "comma_header.csv", tmp_path)
    assert out == tmp_path / "comma_header.xy"
    data = np.loadtxt(out)  # plain whitespace loader must work
    pattern = load_pattern(FORMATS / "comma_header.csv")
    np.testing.assert_allclose(data[:, 0], pattern.angles, atol=1e-6)
    np.testing.assert_allclose(data[:, 1], pattern.intensities, atol=1e-6)


def test_convert_xy_in_place_rewrites_clean(tmp_path):
    source = tmp_path / "pattern.xy"
    source.write_text("# my header\n10 1\n11 2\n12 3\n13 4\n")
    out = convert_pattern_to_xy(source)
    assert out == source
    data = np.loadtxt(out)
    np.testing.assert_allclose(data, [[10, 1], [11, 2], [12, 3], [13, 4]])


def test_convert_descending_input_is_written_ascending(tmp_path):
    out = convert_pattern_to_xy(FORMATS / "descending.xy", tmp_path)
    data = np.loadtxt(out)
    _assert_ascending(data[:, 0])
    assert data[0, 0] == pytest.approx(20.0)


def test_convert_other_folder_leaves_source_untouched(tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = source_dir / "p.csv"
    source.write_text("a;b\n10,5;1\n11,5;2\n12,5;3\n")
    before = source.read_bytes()
    out = convert_pattern_to_xy(source, tmp_path)
    assert source.read_bytes() == before
    np.testing.assert_allclose(np.loadtxt(out)[:, 0], [10.5, 11.5, 12.5])


# --- serialization -------------------------------------------------------


def test_xyfile_round_trips_through_monty():
    pattern = load_pattern(FORMATS / "header_text.txt")
    assert isinstance(pattern, XYFile)
    decoded = MontyDecoder().process_decoded(pattern.as_dict())
    assert isinstance(decoded, XYFile)
    np.testing.assert_allclose(decoded.angles, pattern.angles)
    np.testing.assert_allclose(decoded.intensities, pattern.intensities)
    assert decoded.load_warnings == []  # warnings are not serialized
    assert decoded.intensity_unit is None


def test_brmlfile_round_trips_through_monty(tmp_path):
    datums = [f"1,1,{10.0 + 0.5 * i:.3f},5.0,{100 + i}" for i in range(10)]
    pattern = load_pattern(_write_brml(tmp_path / "rt.brml", datums))
    decoded = MontyDecoder().process_decoded(pattern.as_dict())
    assert isinstance(decoded, BRMLFile)
    np.testing.assert_allclose(decoded.angles, pattern.angles)
    np.testing.assert_allclose(decoded.intensities, pattern.intensities)
