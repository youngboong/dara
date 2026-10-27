"""Load and process XRD data files.

Supported formats (see ``SUPPORTED_PATTERN_EXTENSIONS``): ASCII patterns
(.xy, .xye, .txt, .scn, .csv, .dat, .asc, .ras), PANalytical/Malvern .xrdml,
Bruker .raw (RAW2) and .brml, and Rigaku .rasx.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
from pathlib import Path
from xml.parsers.expat import ExpatError

import matplotlib.pyplot as plt
import numpy as np
import xmltodict
from dict2xml import dict2xml
from monty.json import MSONable

from dara.utils import get_logger

logger = get_logger(__name__)


class PatternFormatError(ValueError):
    """Unreadable/invalid pattern file; message is user-facing and names the line when relevant."""


class XRDData(MSONable):
    """General XRD data class; this is the base class for XRDMLFile, XYFile and other
    XRD data formats. This class ensures that all XRD data can be serialized.
    """

    def __init__(
        self,
        angles: list | np.ndarray,
        intensities: list | np.ndarray,
        errors: list | np.ndarray | None = None,
    ):
        """Initialize XRD data from angles (2-theta values) and intensities/counts."""
        self._angles = np.array(angles)
        self._intensities = np.array(intensities)
        self._errors = np.array(errors) if errors is not None else None
        # Set by ``from_file`` after construction; deliberately not constructor
        # arguments so that stored (serialized) patterns keep decoding.
        self._load_warnings: list[str] = []
        self._intensity_unit: str | None = None

    @property
    def load_warnings(self) -> list[str]:
        """Short English notes about how the file was interpreted (may be empty)."""
        return list(getattr(self, "_load_warnings", []) or [])

    @property
    def intensity_unit(self) -> str | None:
        """``"counts"``, ``"cps"``, or ``None`` when the file does not say."""
        return getattr(self, "_intensity_unit", None)

    @property
    def angles(self) -> np.ndarray:
        """2-theta values."""
        return self._angles

    @property
    def intensities(self) -> np.ndarray:
        """Intensity values (counts)."""
        return self._intensities

    @property
    def errors(self) -> np.ndarray | None:
        """Errors in intensity values."""
        return self._errors

    def plot(self, style="line", ax=None, **kwargs):
        """Plot XRD data.

        Args:
            ax: existing matplotlib axis to plot on
            style: either "points" or "line"
            kwargs: keyword arguments to pass to matplotlib.pyplot.plot

        Returns
        -------
            matplotlib axis
        """
        if ax is None:
            _, ax = plt.subplots(figsize=(8, 5))

        if style == "points":
            ax.plot(self.angles, self.intensities, "+", ls="", ms=2, **kwargs)
        elif style == "line":
            ax.plot(self.angles, self.intensities, lw=1, **kwargs)
        else:
            raise ValueError(f"Invalid style {style}")

        ax.set_xlabel(r"$2\theta$ (deg)")
        ax.set_ylabel("Intensity (counts)")
        return ax

    @classmethod
    def from_file(cls, path: str | Path):
        """Load data from file. To be implemented in subclasses."""
        raise NotImplementedError

    def to_xy_file(self, fn: str | Path = "xrd_data.xy") -> None:
        """Save as a .xy file.

        Args:
            fn: filename to save to. Defaults to "xrd_data.xy".

        Returns
        -------
            filename of saved file
        """
        if self.errors is not None:
            np.savetxt(
                Path(fn).as_posix(),
                np.column_stack((self.angles, self.intensities, self.errors)),
                fmt="%f",
            )
        else:
            np.savetxt(
                Path(fn).as_posix(),
                np.column_stack((self.angles, self.intensities)),
                fmt="%f",
            )


class RawFile(XRDData):
    """Load supported binary RAW powder-diffraction data formats."""

    def __init__(self, angles, intensities, binary_data: bytes | None = None):
        super().__init__(angles, intensities)
        self._binary_data = binary_data

    @classmethod
    def from_file(cls, path: str | Path) -> RawFile:
        """Load data from a raw file."""
        path = Path(path)
        (angles, intensities), binary_data = load_raw(path)
        return cls(angles, intensities, binary_data)

    @property
    def binary_data(self) -> bytes | None:
        """Binary data."""
        return self._binary_data

    def to_raw_file(self, fn: str | Path = "xrd_data.raw") -> None:
        """Save as a raw file.

        Args:
            fn: filename to save to. Defaults to "xrd_data.raw".
        """
        with open(Path(fn), "wb") as f:
            f.write(self.binary_data)


class XRDMLFile(XRDData):
    """XRDML file class, useful for loading .xrdml data. This is the file type used by
    the Aeris instrument.
    """

    def __init__(self, angles, intensities, xrdml_dict: dict | None = None):
        """Initialize an XRDMLFile object; providing dictionary allows one to serialize
        and deserialize the XRDML file.
        """
        super().__init__(angles, intensities)
        self._xrdml_dict = xrdml_dict

    @property
    def xrdml_dict(self) -> dict | None:
        """Dictionary representation of the XRDML file."""
        return self._xrdml_dict

    @classmethod
    def from_file(cls, path: str | Path) -> XRDMLFile:
        """Load data from an XRDML file."""
        path = Path(path)
        xrdml_dict = load_xrdml(path)
        try:
            angles, intensities, unit, warnings = _xrdml_extract(xrdml_dict)
        except PatternFormatError as exc:
            raise PatternFormatError(f"{path.name}: {exc}") from None
        obj = cls(angles=angles, intensities=intensities, xrdml_dict=xrdml_dict)
        obj._load_warnings = warnings
        obj._intensity_unit = unit
        return obj

    def to_xrdml_file(self, fn: str | Path = "xrd_data.xrdml") -> None:
        """Save as an XRDML file.

        Args:
            fn: filename to save to. Defaults to "xrd_data.xrdml".
        """
        with open(Path(fn), "w") as f:
            f.write(dict2xml(self.xrdml_dict))


class XYFile(XRDData):
    """XY file class, useful for loading .xy data."""

    def __init__(self, angles, intensities, errors: list | np.ndarray | None = None):
        super().__init__(angles, intensities, errors)

    @classmethod
    def from_file(cls, path: str | Path) -> XYFile:
        """Load data from a text pattern (.xy, .xye, .txt, .scn, .csv, .dat, .asc, .ras).

        Header lines, ``;``/tab/comma/whitespace delimiters, decimal commas and
        non-UTF-8 text are tolerated; see :func:`read_ascii_pattern`.
        """
        path = Path(path)
        angles, intensities, errors, warnings = read_ascii_pattern(path)
        obj = cls(angles, intensities, errors)
        obj._load_warnings = warnings
        obj._intensity_unit = None
        return obj


class BRMLFile(XRDData):
    """Load Bruker .brml (ZIP of XML) data. No binary payload is stored."""

    def __init__(self, angles, intensities):
        super().__init__(angles, intensities)

    @classmethod
    def from_file(cls, path: str | Path) -> BRMLFile:
        """Load data from a BRML file."""
        angles, intensities, warnings = load_brml(path)
        obj = cls(angles, intensities)
        obj._load_warnings = warnings
        obj._intensity_unit = None
        return obj


class RASXFile(XRDData):
    """Load Rigaku RASX file data format."""

    def __init__(self, angles, intensities, binary_data: bytes | None = None):
        super().__init__(angles, intensities)
        self._binary_data = binary_data

    @classmethod
    def from_file(cls, path: str | Path) -> RASXFile:
        """Load data from a RASX file."""
        path = Path(path)
        angles, intensities, binary_data, warnings = _load_rasx(path)
        obj = cls(angles, intensities, binary_data)
        obj._load_warnings = warnings
        obj._intensity_unit = None
        return obj

    @property
    def binary_data(self) -> bytes | None:
        """Binary data."""
        return self._binary_data

    def to_rasx_file(self, fn: str | Path = "xrd_data.rasx") -> None:
        """Save as a RASX file.

        Args:
            fn: filename to save to. Defaults to "xrd_data.rasx".
        """
        with open(Path(fn), "wb") as f:
            f.write(self.binary_data)


# ---------------------------------------------------------------------------
# Shared cleaning / validation
# ---------------------------------------------------------------------------


def _line_span_text(span: tuple[int, int] | None) -> str:
    if span is None:
        return ""
    first, last = span
    if first == last:
        return f" (line {first})"
    return f" (lines {first}–{last})"


def _clean_pattern(
    angles,
    intensities,
    errors,
    name: str,
    line_numbers: np.ndarray | None = None,
    line_span: tuple[int, int] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, list[str]]:
    """Drop bad rows, order by 2θ, de-duplicate and validate a pattern.

    Returns ``(angles, intensities, errors, warnings)`` or raises
    :class:`PatternFormatError`. ``line_numbers`` (1-based source line of every
    input row) and ``line_span`` only improve the error messages.
    """
    angles = np.asarray(angles, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    errors = None if errors is None else np.asarray(errors, dtype=float)
    src = np.arange(len(angles))
    warnings: list[str] = []

    def select(index) -> None:
        nonlocal angles, intensities, errors, src
        angles = angles[index]
        intensities = intensities[index]
        if errors is not None:
            errors = errors[index]
        src = src[index]

    finite = np.isfinite(angles) & np.isfinite(intensities)
    if not finite.all():
        warnings.append(
            f"Dropped {int((~finite).sum())} row(s) with missing or non-finite values."
        )
        select(finite)

    if len(angles) > 1:
        steps = np.diff(angles)
        if (steps < 0).all():
            select(slice(None, None, -1))
            warnings.append("2θ values were in descending order and have been reversed.")
        elif (steps < 0).any():
            select(np.argsort(angles, kind="stable"))
            warnings.append("2θ values were not sorted; rows were sorted by 2θ.")

    if len(angles) > 1:
        keep = np.concatenate(([True], np.diff(angles) > 0))
        if not keep.all():
            warnings.append(f"Removed {int((~keep).sum())} duplicate 2θ row(s).")
            select(keep)

    if len(angles) < 3:
        raise PatternFormatError(
            f"{name}: only {len(angles)} data row(s) found{_line_span_text(line_span)}; "
            "at least 3 are required."
        )

    out_of_range = (angles <= 0) | (angles > 180)
    if out_of_range.any():
        where = ""
        if line_numbers is not None:
            first_bad = int(np.argmax(out_of_range))
            where = f", line {int(line_numbers[src[first_bad]])}"
        raise PatternFormatError(
            f"{name}: 2θ values must be between 0 and 180° "
            f"(found {angles.min():g}–{angles.max():g}{where}). Check the column order."
        )

    return angles, intensities, errors, warnings


# ---------------------------------------------------------------------------
# Tolerant ASCII reader
# ---------------------------------------------------------------------------

_FLOAT_RE = re.compile(
    r"^[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|nan|inf|infinity)$", re.IGNORECASE
)
_DECIMAL_COMMA_RE = re.compile(r"^[+-]?\d+,\d+(?:[eE][+-]?\d+)?$")


def _decode_text(raw: bytes) -> str:
    """Decode pattern text; only the digits matter, so this never fails."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _to_float(token: str, *, decimal_comma: bool) -> float | None:
    token = token.strip().strip("\"'").strip()
    if not token:
        return None
    if _FLOAT_RE.match(token):
        return float(token)
    if decimal_comma and _DECIMAL_COMMA_RE.match(token):
        return float(token.replace(",", "."))
    return None


def _leading_floats(tokens: list[str], *, decimal_comma: bool) -> list[float]:
    values: list[float] = []
    for token in tokens:
        value = _to_float(token, decimal_comma=decimal_comma)
        if value is None:
            break
        values.append(value)
    return values


def _numeric_tokens(line: str) -> list[float]:
    """Return the leading numbers of ``line`` (>= 2 means a numeric data line)."""
    values: list[float] = []
    if ";" in line:
        values = _leading_floats(line.split(";"), decimal_comma=True)
    elif "\t" in line:
        values = _leading_floats(
            [part for part in line.split("\t") if part.strip()], decimal_comma=True
        )
    elif "," in line:
        values = _leading_floats(line.split(","), decimal_comma=False)
    if len(values) < 2:
        values = _leading_floats(line.split(), decimal_comma=True)
    return values


def read_ascii_pattern(
    path: str | Path, *, fmt: str | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, list[str]]:
    """Read a text powder pattern, tolerating headers, delimiters and encodings.

    Args:
        path: the text file.
        fmt: the format extension (e.g. ``".xye"``); defaults to the file suffix.

    Returns
    -------
        ``(angles, intensities, errors, warnings)``; ``errors`` is ``None`` unless
        the format carries a usable third column. ``warnings`` are short English
        sentences describing how the file was interpreted.

    Raises
    ------
        PatternFormatError: if no valid pattern can be read.
    """
    path = Path(path)
    name = path.name
    fmt = (fmt or path.suffix).lower()
    text = _decode_text(path.read_bytes())

    entries = [
        (number, line.strip())
        for number, line in enumerate(re.split(r"\r\n|\r|\n", text), start=1)
        if line.strip()
    ]
    if not entries:
        raise PatternFormatError(f"{name}: the file is empty.")
    first_number, first_line = entries[0]
    warnings: list[str] = []

    if fmt == ".ras":
        starts = [i for i, (_, line) in enumerate(entries) if "*RAS_INT_START" in line]
        if starts:
            end = next(
                (
                    i
                    for i in range(starts[0] + 1, len(entries))
                    if "*RAS_INT_END" in entries[i][1]
                ),
                len(entries),
            )
            entries = entries[starts[0] + 1 : end]
            if len(starts) > 1:
                warnings.append(f"RAS file contains {len(starts)} scans; using scan 1.")

    blocks: list[list[tuple[int, list[float]]]] = []
    current: list[tuple[int, list[float]]] | None = None
    numeric_total = 0
    for number, line in entries:
        values = _numeric_tokens(line)
        if len(values) >= 2:
            numeric_total += 1
            if current is None:
                current = []
                blocks.append(current)
            current.append((number, values))
        else:
            current = None

    if not blocks:
        raise PatternFormatError(
            f"{name}: no numeric 2θ/intensity data found. Expected lines with at least "
            f"two numbers (2θ and intensity); line {first_number} reads "
            f"'{first_line[:60]}'."
        )

    block = max(blocks, key=len)  # max() keeps the first of equally long blocks
    line_span = (block[0][0], block[-1][0])
    if numeric_total > len(block):
        warnings.append(
            f"Ignored {numeric_total - len(block)} numeric line(s) outside the main "
            f"data block (lines {line_span[0]}–{line_span[1]} used)."
        )

    n_columns = min(len(values) for _, values in block)
    table = np.array([values[: min(n_columns, 3)] for _, values in block], dtype=float)
    line_numbers = np.array([number for number, _ in block])
    angles = table[:, 0]
    intensities = table[:, 1]
    errors = None

    if n_columns >= 3:
        third = table[:, 2]
        if fmt == ".ras":
            # Rigaku .ras: third column is the attenuator coefficient, not an error.
            if np.all(np.isfinite(third) & (third > 0)) and np.any(third != 1):
                intensities = intensities * third
                warnings.append(
                    "Applied Rigaku attenuator coefficients to "
                    f"{int(np.count_nonzero(third != 1))} point(s)."
                )
        else:
            if fmt in (".xy", ".xye"):
                valid = bool(np.all(np.isfinite(third)))
                if fmt == ".xy":
                    valid = valid and bool(np.all(third > 0))
                if valid:
                    errors = third
            if errors is None:
                warnings.append(f"Using columns 1–2 of {n_columns} (2θ, intensity).")

    angles, intensities, errors, clean_warnings = _clean_pattern(
        angles, intensities, errors, name, line_numbers, line_span
    )
    return angles, intensities, errors, warnings + clean_warnings


# ---------------------------------------------------------------------------
# XRDML
# ---------------------------------------------------------------------------


def load_xrdml(file: Path) -> dict:
    """Load an XRDML file and returns a dictionary using xmltodict."""
    file = Path(file)
    # Parse bytes so expat honours the XML declaration's encoding and any BOM.
    try:
        return xmltodict.parse(file.read_bytes())
    except ExpatError as exc:
        raise PatternFormatError(
            f"{file.name}: not a valid XRDML/XML file ({exc})."
        ) from None


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _xrdml_points(data_points: dict) -> tuple[np.ndarray, np.ndarray, str | None]:
    """Extract (angles, intensities, unit) from one ``dataPoints`` mapping."""
    positions = [node for node in _as_list(data_points.get("positions")) if isinstance(node, dict)]
    if not positions:
        raise PatternFormatError("XRDML dataPoints has no positions element")

    def axis_of(node: dict) -> str:
        return str(node.get("@axis") or "").strip().lower()

    node = next((p for p in positions if axis_of(p) == "2theta"), None)
    if node is None:
        node = next((p for p in positions if "2theta" in axis_of(p)), positions[0])

    # XRDML producers use either <counts> (newer Data Collector exports) or
    # <intensities> (including older X'Pert Data Collector files). Both contain
    # the same whitespace-separated observed intensity series.
    tag = "counts" if data_points.get("counts") is not None else "intensities"
    intensity_data = data_points.get(tag)
    if intensity_data is None:
        raise PatternFormatError("XRDML dataPoints contains neither counts nor intensities")
    intensity_text = _xml_text(intensity_data)
    if not intensity_text:
        raise PatternFormatError("XRDML intensity data is empty")
    try:
        intensities = np.array([float(val) for val in intensity_text.split()])
    except ValueError:
        raise PatternFormatError("XRDML intensity data contains a non-numeric value") from None

    list_text = _xml_text(node.get("listPositions"))
    if list_text:
        try:
            angles = np.array([float(val) for val in list_text.split()])
        except ValueError:
            raise PatternFormatError("XRDML listPositions contains a non-numeric value") from None
        if len(angles) != len(intensities):
            raise PatternFormatError(
                f"XRDML lists {len(angles)} positions but {len(intensities)} intensity values"
            )
    else:
        start = _xml_float(node.get("startPosition"))
        end = _xml_float(node.get("endPosition"))
        if start is None or end is None:
            raise PatternFormatError(
                "XRDML 2Theta positions have no startPosition/endPosition or listPositions"
            )
        angles = np.linspace(start, end, len(intensities))

    unit_text = ""
    if isinstance(intensity_data, dict):
        unit_text = str(intensity_data.get("@unit") or "").strip().lower()
    if unit_text == "counts":
        unit = "counts"
    elif unit_text == "cps" or "second" in unit_text:
        unit = "cps"
    elif not unit_text and tag == "counts":
        unit = "counts"
    else:
        unit = None
    return angles, intensities, unit


def _xrdml_scan(xrd_dict: dict) -> tuple[dict, list[str]]:
    """Pick the measurement/scan to read; returns ``(scan, warnings)``."""
    root = xrd_dict.get("xrdMeasurements") if isinstance(xrd_dict, dict) else None
    if not isinstance(root, dict):
        raise PatternFormatError("XRDML has no xrdMeasurements element")
    warnings: list[str] = []

    measurements = _as_list(root.get("xrdMeasurement"))
    chosen = next(
        (
            (i, m)
            for i, m in enumerate(measurements, start=1)
            if isinstance(m, dict) and m.get("scan") is not None
        ),
        None,
    )
    if chosen is None:
        raise PatternFormatError("XRDML has no xrdMeasurement with a scan")
    measurement_number, measurement = chosen
    if len(measurements) > 1:
        warnings.append(
            f"XRDML contains {len(measurements)} measurements; "
            f"using measurement {measurement_number}."
        )

    scans = _as_list(measurement.get("scan"))
    candidates = [
        (i, s)
        for i, s in enumerate(scans, start=1)
        if isinstance(s, dict) and isinstance(s.get("dataPoints"), dict)
    ]
    if not candidates:
        raise PatternFormatError("XRDML scan has no dataPoints")
    scan_number, scan = next(
        ((i, s) for i, s in candidates if s.get("@status") == "Completed"), candidates[0]
    )
    if len(scans) > 1:
        detail = ""
        try:
            angles, _, _ = _xrdml_points(scan["dataPoints"])
            detail = f" (2Theta {angles.min():.2f}–{angles.max():.2f}°)"
        except (PatternFormatError, ValueError):
            pass
        warnings.append(f"XRDML contains {len(scans)} scans; using scan {scan_number}{detail}.")
    return scan, warnings


def _xrdml_extract(
    xrd_dict: dict,
) -> tuple[np.ndarray, np.ndarray, str | None, list[str]]:
    """Return ``(angles, intensities, unit, warnings)`` for an XRDML dictionary."""
    try:
        scan, warnings = _xrdml_scan(xrd_dict)
        data_points = scan["dataPoints"]
        angles, intensities, unit = _xrdml_points(data_points)
    except (KeyError, TypeError, AttributeError):
        raise PatternFormatError("unexpected XRDML structure") from None

    attenuation = _xml_text(data_points.get("beamAttenuationFactors"))
    if attenuation:
        try:
            factors = [float(val) for val in attenuation.split()]
        except ValueError:
            factors = []
        if any(factor != 1.0 for factor in factors):
            warnings.append(
                "XRDML lists beam attenuation factors; intensities are used as recorded."
            )
    return angles, intensities, unit, warnings


def get_xrdml_data(xrd_dict: dict) -> tuple[np.ndarray, np.ndarray]:
    """Get angles and intensities from an XRDML dictionary."""
    angles, intensities, _, _ = _xrdml_extract(xrd_dict)
    return angles, intensities


def _xrdml_intensity_unit(xrd_dict: dict) -> str | None:
    """Return ``"counts"``, ``"cps"`` or ``None`` for an XRDML dictionary."""
    return _xrdml_extract(xrd_dict)[2]


def _first_xml_mapping(value) -> dict:
    """Return the first mapping from an xmltodict mapping-or-list value."""
    if isinstance(value, list):
        value = value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _xml_text(value) -> str | None:
    """Return normalized text from an xmltodict scalar or ``#text`` node."""
    if isinstance(value, dict):
        value = value.get("#text")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _xml_float(value) -> float | None:
    text = _xml_text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def get_xrdml_metadata(xrd_dict: dict) -> dict:
    """Extract practical instrument metadata from an XRDML document.

    XRDML normally records the instrument family, detector, tube anode,
    wavelength, and scan range.  These values are useful for suggesting a
    bundled BGMN profile, but they do not fully describe every slit and optic,
    so the suggestion must remain user-overridable.
    """
    root = _first_xml_mapping(xrd_dict.get("xrdMeasurements"))
    measurement = _first_xml_mapping(root.get("xrdMeasurement"))
    scan = _first_xml_mapping(measurement.get("scan"))
    header = _first_xml_mapping(scan.get("header"))
    source = _first_xml_mapping(header.get("source"))
    wavelength = _first_xml_mapping(measurement.get("usedWavelength"))
    incident = _first_xml_mapping(measurement.get("incidentBeamPath"))
    diffracted = _first_xml_mapping(measurement.get("diffractedBeamPath"))
    tube = _first_xml_mapping(incident.get("xRayTube"))
    detector = _first_xml_mapping(diffracted.get("detector"))

    comments = []
    for owner in (root, measurement):
        comment = _first_xml_mapping(owner.get("comment"))
        entries = comment.get("entry", [])
        if not isinstance(entries, list):
            entries = [entries]
        comments.extend(text for entry in entries if (text := _xml_text(entry)))

    instrument_system = None
    for comment in comments:
        match = re.search(r"Diffractometer\s+system\s*=\s*([^,;]+)", comment, re.I)
        if match:
            instrument_system = match.group(1).strip()
            break

    try:
        angles, intensities = get_xrdml_data(xrd_dict)
        two_theta_min = float(np.nanmin(angles))
        two_theta_max = float(np.nanmax(angles))
        data_points = int(len(intensities))
    except (KeyError, TypeError, ValueError):
        two_theta_min = two_theta_max = None
        data_points = None

    anode = _xml_text(tube.get("anodeMaterial"))
    k_alpha1 = _xml_float(wavelength.get("kAlpha1"))
    known_wavelengths = {"Cu": 1.540598, "Co": 1.789010, "Cr": 2.289760, "Fe": 1.936042}
    radiation = None
    if anode:
        radiation = next(
            (name for name in known_wavelengths if name.lower() == anode.lower()),
            None,
        )
    if radiation is None and k_alpha1 is not None:
        nearest, distance = min(
            ((name, abs(value - k_alpha1)) for name, value in known_wavelengths.items()),
            key=lambda item: item[1],
        )
        if distance <= 0.03:
            radiation = nearest

    return {
        "instrument_system": instrument_system,
        "instrument_control_software": _xml_text(source.get("instrumentControlSoftware")),
        "application_software": _xml_text(source.get("applicationSoftware")),
        "instrument_id": _xml_text(source.get("instrumentID")),
        "detector": _xml_text(detector.get("@name")),
        "detector_type": _xml_text(detector.get("@xsi:type")),
        "xray_tube": _xml_text(tube.get("@name")),
        "anode_material": anode,
        "k_alpha1_angstrom": k_alpha1,
        "radiation": radiation,
        "incident_radius_mm": _xml_float(incident.get("radius")),
        "diffracted_radius_mm": _xml_float(diffracted.get("radius")),
        "two_theta_min": two_theta_min,
        "two_theta_max": two_theta_max,
        "data_points": data_points,
        "comments": comments,
    }


def recommend_xrdml_instrument_profile(metadata: dict) -> dict | None:
    """Suggest the closest bundled BGMN profile from XRDML metadata."""
    searchable = " ".join(
        str(value)
        for key, value in metadata.items()
        if key != "comments" and value is not None
    )
    searchable += " " + " ".join(metadata.get("comments") or [])
    text = searchable.lower().replace("'", "")

    rules = [
        (
            lambda: "aeris" in text and ("pixcel1d" in text or "medipix3" in text),
            "Aeris-fds-Pixcel1d-Medipix3", "high",
            "Aeris and PIXcel1D/Medipix3 metadata match the bundled profile.",
        ),
        (
            lambda: "smartlab" in text,
            "Rigaku-SmartLab-CBO-BB-FDS05deg", "high",
            "Rigaku SmartLab metadata matches the bundled profile family.",
        ),
        (
            lambda: "miniflex 600" in text and ("dtex" in text or "d/tex" in text),
            "Rigaku-Miniflex-600-DTEXultra2-fds", "high",
            "MiniFlex 600 and D/teX detector metadata match the bundled profile.",
        ),
        (
            lambda: "miniflex" in text,
            "Rigaku-Miniflex", "medium",
            "A Rigaku MiniFlex family instrument was detected; verify the optics.",
        ),
        (
            lambda: "ultima" in text,
            "rigaku-ultima", "medium",
            "A Rigaku Ultima family instrument was detected; verify the optics.",
        ),
        (
            lambda: "xpert" in text and "pixcel" in text,
            "xpert-pixcel-0500", "medium",
            "X'Pert and PIXcel metadata match the closest bundled profile family.",
        ),
        (
            lambda: "xpert" in text and "xcelerator" in text,
            "PW3040-FDS-ADS-Xcelerator", "medium",
            "X'Pert/PW30xx and X'Celerator metadata match the closest bundled profile.",
        ),
        (
            lambda: "d8" in text and ("lynxeyexe" in text or "lynxeye xe" in text),
            "d8-fds-02-LynxEyeXE", "medium",
            "Bruker D8 and LYNXEYE XE metadata match the closest bundled profile.",
        ),
        (
            lambda: "d8" in text and "lynxeye" in text,
            "d8-lynxeye-fds-02", "medium",
            "Bruker D8 and LYNXEYE metadata match the closest bundled profile.",
        ),
        (
            lambda: "d5000" in text,
            "siemens-d5000-fds1mm", "medium",
            "Siemens D5000 metadata match the closest bundled profile family.",
        ),
    ]
    for matches, profile, confidence, reason in rules:
        if matches():
            return {"profile": profile, "confidence": confidence, "reason": reason}
    return None


def xrdml2xy(fn: str | Path, target_folder: Path = None) -> Path:
    """Convert .xrdml file to .xy file (and save)."""
    fn = Path(fn)
    if target_folder is None:
        target_folder = fn.parent
    target_path = target_folder / fn.with_suffix(".xy").name

    XRDMLFile.from_file(fn).to_xy_file(target_path)
    return target_path


def hex2float(hex_string: bytes) -> float:
    # Assuming hex_string is a bytes object representing a float in hex
    return struct.unpack("<f", hex_string)[0]


def hex2int(hex_string: bytes) -> int:
    # Assuming hex_string is a bytes object representing an int in hex
    return struct.unpack("<i", hex_string)[0]


def _load_bruker_raw_v2(content: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Read Siemens/Bruker DIFFRAC-AT RAW version 2 scan ranges."""
    if len(content) < 256:
        raise ValueError("Bruker RAW2 file is shorter than its 256-byte header")

    range_count = struct.unpack_from("<H", content, 4)[0]
    if range_count < 1:
        raise ValueError("Bruker RAW2 file contains no scan ranges")

    offset = 256
    angle_ranges = []
    intensity_ranges = []
    for range_index in range(range_count):
        if offset + 48 > len(content):
            raise ValueError(f"Bruker RAW2 range {range_index + 1} header is truncated")

        header_length, point_count = struct.unpack_from("<HH", content, offset)
        if header_length < 48:
            raise ValueError(
                f"Bruker RAW2 range {range_index + 1} has invalid header length "
                f"{header_length}"
            )
        if point_count < 1:
            raise ValueError(f"Bruker RAW2 range {range_index + 1} has no data points")

        step_size = struct.unpack_from("<f", content, offset + 12)[0]
        start_angle = struct.unpack_from("<f", content, offset + 16)[0]
        data_offset = offset + header_length
        data_end = data_offset + point_count * 4
        if data_end > len(content):
            available = max((len(content) - data_offset) // 4, 0)
            raise ValueError(
                f"Bruker RAW2 range {range_index + 1} declares {point_count} points "
                f"but only {available} are available"
            )

        angles = start_angle + np.arange(point_count, dtype=float) * step_size
        intensities = np.frombuffer(
            content, dtype="<f4", count=point_count, offset=data_offset
        ).astype(float)
        angle_ranges.append(angles)
        intensity_ranges.append(intensities)
        offset = data_end

    return np.concatenate(angle_ranges), np.concatenate(intensity_ranges)


def load_raw(file: Path | str) -> tuple[tuple[np.ndarray, np.ndarray], bytes]:
    """Convert raw file to xy data."""
    name = Path(file).name
    with open(file, "rb") as f:
        content = f.read()

    if content.startswith(b"RAW2"):
        try:
            return _load_bruker_raw_v2(content), content
        except ValueError as exc:
            raise PatternFormatError(f"{name}: {exc}") from None
    if content.startswith((b"RAW1", b"RAW4", b"RAW ")):
        signature = content[:7].decode("ascii", errors="replace").rstrip("\x00")
        raise PatternFormatError(
            f"{name}: Bruker {signature} is recognized but not yet supported; "
            "export the scan as XY or XYE"
        )

    if len(content) < 3158:
        raise PatternFormatError(f"{name}: unsupported or truncated RAW diffraction file")
    size_float = 4  # Assuming 4 bytes for a float
    size_int = 4  # Assuming 4 bytes for an int

    # Extracting start angle, end angle, and step size from binary content
    start_ang = hex2float(content[2962 : 2962 + size_float])
    end_ang = hex2float(content[2966 : 2966 + size_float])
    count = hex2int(content[3154 : 3154 + size_int])
    start_idx = 3158
    angles = np.zeros(count)
    intensities = np.zeros(count)

    for j in range(count):
        ang = start_ang + (j / (count - 1)) * (end_ang - start_ang)
        its = hex2float(
            content[start_idx + j * size_float : start_idx + (j + 1) * size_float]
        )
        angles[j] = ang
        intensities[j] = its

    return (angles, intensities), content


def raw2xy(fn: str | Path, target_folder: Path = None) -> Path:
    """Convert .raw file to .xy file (and save)."""
    fn = Path(fn)
    if target_folder is None:
        target_folder = fn.parent
    target_path = target_folder / fn.with_suffix(".xy").name

    RawFile.from_file(fn).to_xy_file(target_path)
    return target_path


def _load_rasx(file: Path | str) -> tuple[np.ndarray, np.ndarray, bytes, list[str]]:
    """Read a RASX archive; returns ``(angles, intensities, content, warnings)``."""
    file = Path(file)
    name = file.name
    with open(file, "rb") as f:
        content = f.read()

    try:
        zip_ref = zipfile.ZipFile(io.BytesIO(content), "r")
    except zipfile.BadZipFile:
        raise PatternFormatError(f"{name}: not a valid RASX (ZIP) file.") from None

    with zip_ref:
        namelist = zip_ref.namelist()

        if "root.xml" not in namelist:
            raise PatternFormatError(f"{name}: no root.xml found in the RASX archive.")

        # Find Data folders
        data_folders: dict[str, list[str]] = {}
        for entry in namelist:
            if entry.startswith("Data") and "/" in entry:
                folder = entry.split("/")[0]
                if folder not in data_folders:
                    data_folders[folder] = []
                data_folders[folder].append(entry)

        # Get the first Profile file from the first Data folder
        profile_path = None
        for _folder_name, file_paths in sorted(data_folders.items()):
            profile_paths = [p for p in file_paths if "Profile" in p]
            if profile_paths:
                if len(profile_paths) > 1:
                    log_msg = (
                        f"Multiple Profile files found in the RASX archive. Using the first one: {profile_paths[0]}"
                    )
                    logger.warning(log_msg)
                profile_path = profile_paths[0]
                break

        if profile_path is None:
            raise PatternFormatError(f"{name}: no Profile file found in the RASX archive.")

        # Read profile data (three columns: scan_angle, intensity, attenuation)
        # Using numpy.loadtxt to handle whitespace-separated values
        # Decode with utf-8-sig to handle BOM if present
        with zip_ref.open(profile_path) as profile_file:
            # Read as text and decode with utf-8-sig to remove BOM if present
            text_content = profile_file.read().decode("utf-8-sig")
            # Use StringIO to create a file-like object for np.loadtxt
            try:
                profile_data = np.loadtxt(io.StringIO(text_content), unpack=False, ndmin=2)
            except ValueError as exc:
                raise PatternFormatError(
                    f"{name}: {profile_path} is not numeric ({exc})."
                ) from None

    if profile_data.shape[1] < 2:
        raise PatternFormatError(f"{name}: {profile_path} has fewer than two columns.")

    # Extract angles (first column) and intensities (second column)
    # Third column is attenuation, which we don't need for now
    angles, intensities, _, warnings = _clean_pattern(
        profile_data[:, 0], profile_data[:, 1], None, name
    )
    return angles, intensities, content, warnings


def load_rasx(file: Path | str) -> tuple[tuple[np.ndarray, np.ndarray], bytes]:
    """Convert RASX file to xy data.

    RASX files are ZIP archives containing:
    - root.xml file
    - Data*/ folders with Profile*.txt files (scan_angle, intensity, attenuation)
    - MesurementConditions*.xml files (note: typo in original format)

    This function extracts the first scan's data from the first Profile file found.
    Rows are cleaned like the other formats (non-finite rows dropped, 2θ sorted).

    Returns:
        Tuple of ((angles, intensities), content) where content is the raw ZIP bytes.
        The content is kept for potential round-trip saving, though it's rarely used.
    """
    angles, intensities, content, _ = _load_rasx(file)
    return (angles, intensities), content


def rasx2xy(fn: str | Path, target_folder: Path = None) -> Path:
    """Convert .rasx file to .xy file (and save)."""
    fn = Path(fn)
    if target_folder is None:
        target_folder = fn.parent
    target_path = target_folder / fn.with_suffix(".xy").name

    RASXFile.from_file(fn).to_xy_file(target_path)
    return target_path


_BRML_RAWDATA_RE = re.compile(r"^Experiment\d+/RawData\d+\.xml$", re.IGNORECASE)


def _strictly_monotonic(values: np.ndarray) -> bool:
    if not np.all(np.isfinite(values)):
        return False
    steps = np.diff(values)
    return bool(np.all(steps > 0) or np.all(steps < 0))


def _valid_two_theta(values: np.ndarray) -> bool:
    return (
        _strictly_monotonic(values)
        and bool(np.all(values > 0))
        and bool(np.all(values <= 180))
    )


def load_brml(file: Path | str) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Read the first scan of a Bruker .brml archive (ZIP of XML files).

    Returns:
        ``(angles, intensities, warnings)``.

    Raises:
        PatternFormatError: if the archive is not a readable BRML scan.
    """
    file = Path(file)
    name = file.name
    try:
        archive = zipfile.ZipFile(io.BytesIO(file.read_bytes()), "r")
    except zipfile.BadZipFile:
        raise PatternFormatError(f"{name}: not a valid BRML (ZIP) file.") from None

    warnings: list[str] = []
    with archive:
        members = sorted(n for n in archive.namelist() if _BRML_RAWDATA_RE.match(n))
        if "Experiment0/RawData0.xml" in archive.namelist():
            member = "Experiment0/RawData0.xml"
        elif members:
            member = members[0]
        else:
            raise PatternFormatError(
                f"{name}: no Experiment*/RawData*.xml found in the BRML archive."
            )
        if len(members) > 1:
            warnings.append(f"BRML contains {len(members)} data files; using {member}.")
        try:
            document = xmltodict.parse(archive.read(member))
        except ExpatError as exc:
            raise PatternFormatError(
                f"{name}: {member} is not valid XML ({exc})."
            ) from None

    raw_data = document.get("RawData") if isinstance(document, dict) else None
    routes_node = raw_data.get("DataRoutes") if isinstance(raw_data, dict) else None
    routes = [
        route
        for route in _as_list(routes_node.get("DataRoute") if isinstance(routes_node, dict) else None)
        if isinstance(route, dict)
    ]
    if not routes:
        raise PatternFormatError(f"{name}: no DataRoute found in the BRML data.")
    if len(routes) > 1:
        warnings.append(f"BRML contains {len(routes)} scan ranges; using range 1.")
    route = routes[0]

    rows: list[list[float]] = []
    for datum in _as_list(route.get("Datum")):
        text = _xml_text(datum)
        if not text:
            continue
        try:
            rows.append([float(val) for val in text.split(",")])
        except ValueError:
            raise PatternFormatError(
                f"{name}: BRML data row {len(rows) + 1} is not numeric ('{text[:60]}')."
            ) from None
    if not rows:
        raise PatternFormatError(f"{name}: the BRML data contains no Datum rows.")
    n_columns = min(len(row) for row in rows)
    table = np.array([row[:n_columns] for row in rows], dtype=float)

    angle_col = intensity_col = None

    # 1. Column names from DataViews, when present.
    views_node = route.get("DataViews")
    views = [
        view
        for view in _as_list(views_node.get("RawDataView") if isinstance(views_node, dict) else None)
        if isinstance(view, dict)
    ]
    angle_view = intensity_view = None
    for view in views:
        logic_name = str(view.get("@LogicName") or "").lower()
        start = _xml_text(view.get("@Start"))
        if not logic_name or start is None or not start.lstrip("+").isdigit():
            continue
        if angle_view is None and "twotheta" in logic_name:
            angle_view = int(start)
        elif intensity_view is None and "count" in logic_name:
            intensity_view = int(start)
    if (
        angle_view is not None
        and intensity_view is not None
        and angle_view < n_columns
        and intensity_view < n_columns
        and angle_view != intensity_view
    ):
        angle_col, intensity_col = angle_view, intensity_view

    # 2. Common Bruker layout: time, ?, 2theta, theta, ..., counts.
    if angle_col is None and n_columns >= 5:
        angle_col, intensity_col = 2, n_columns - 1

    # 3. Validate the 2theta column; otherwise search for one.
    if angle_col is None or not _valid_two_theta(table[:, angle_col]):
        intensity_col = n_columns - 1
        angle_col = next(
            (
                col
                for col in range(n_columns - 1)
                if _strictly_monotonic(table[:, col])
                and table[0, col] < table[-1, col]
                and _valid_two_theta(table[:, col])
            ),
            None,
        )
        if angle_col is None:
            raise PatternFormatError(
                f"{name}: could not identify the 2θ column in the BRML data."
            )

    angles, intensities, _, clean_warnings = _clean_pattern(
        table[:, angle_col], table[:, intensity_col], None, name
    )
    return angles, intensities, warnings + clean_warnings


# Extension-to-class mapping for all supported XRD formats.
# Add new formats here to make them available everywhere automatically.
_FORMAT_LOADERS: dict[str, type[XRDData]] = {
    ".xy": XYFile,
    ".xye": XYFile,
    ".txt": XYFile,
    ".scn": XYFile,
    ".csv": XYFile,
    ".dat": XYFile,
    ".asc": XYFile,
    ".ras": XYFile,
    ".xrdml": XRDMLFile,
    ".raw": RawFile,
    ".rasx": RASXFile,
    ".brml": BRMLFile,
}

SUPPORTED_PATTERN_EXTENSIONS: tuple[str, ...] = tuple(sorted(_FORMAT_LOADERS))


def load_pattern(path: str | Path) -> XRDData:
    """Load an XRD pattern from any supported file format.

    Supported formats: .xy, .xye, .txt, .scn, .csv, .dat, .asc, .ras (text),
    .xrdml, .raw, .rasx, .brml.

    Args:
        path: the path to the pattern file.

    Returns:
        an XRDData instance (XYFile, XRDMLFile, RawFile, RASXFile, or BRMLFile).
        ``load_warnings`` and ``intensity_unit`` describe how the file was read.

    Raises:
        PatternFormatError: (a ValueError) if the extension is not supported or
            the file cannot be read as a pattern.
    """
    path = Path(path)
    ext = path.suffix.lower()
    loader = _FORMAT_LOADERS.get(ext)
    if loader is None:
        raise PatternFormatError(
            f"Unsupported pattern file format: '{ext}'. "
            f"Supported formats: {', '.join(SUPPORTED_PATTERN_EXTENSIONS)}"
        )
    return loader.from_file(path)


def convert_pattern_to_xy(
    fn: str | Path, target_folder: Path | None = None
) -> Path:
    """Convert any supported XRD pattern file to a clean whitespace-separated .xy file.

    The pattern is always loaded (and normalised) into memory first, so the
    source and the target may be the same file.

    Args:
        fn: the path to the pattern file.
        target_folder: the folder to save the .xy file in. Defaults to the
            same folder as the input file.

    Returns:
        the path to the generated .xy file.
    """
    fn = Path(fn)
    if target_folder is None:
        target_folder = fn.parent
    target_path = Path(target_folder) / fn.with_suffix(".xy").name

    pattern = load_pattern(fn)
    pattern.to_xy_file(target_path)

    return target_path
