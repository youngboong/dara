"""Load and process XRD data files (.xrdml, .xy, .rasx)."""

from __future__ import annotations

import io
import re
import struct
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import xmltodict
from dict2xml import dict2xml
from monty.json import MSONable

from dara.utils import get_logger

logger = get_logger(__name__)

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
        xrdml_dict = load_xrdml(Path(path))
        angles, intensities = get_xrdml_data(xrdml_dict)
        return cls(angles=angles, intensities=intensities, xrdml_dict=xrdml_dict)

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
        """Load data from a .xy file."""
        path = Path(path)
        try:
            data = np.loadtxt(path, unpack=True)
        except ValueError:
            # Try to load the file with a comma delimiter
            data = np.loadtxt(path, unpack=True, delimiter=",")
        if len(data) == 2:
            angles, intensities = data
            errors = None
        else:
            angles, intensities, errors = data  # if it is xye data
        return cls(angles, intensities, errors)


class RASXFile(XRDData):
    """Load Rigaku RASX file data format."""

    def __init__(self, angles, intensities, binary_data: bytes | None = None):
        super().__init__(angles, intensities)
        self._binary_data = binary_data

    @classmethod
    def from_file(cls, path: str | Path) -> RASXFile:
        """Load data from a RASX file."""
        path = Path(path)
        (angles, intensities), binary_data = load_rasx(path)
        return cls(angles, intensities, binary_data)

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


def load_xrdml(file: Path) -> dict:
    """Load an XRDML file and returns a dictionary using xmltodict."""
    with file.open("r", encoding="utf-8") as f:
        return xmltodict.parse(f.read())


def get_xrdml_data(xrd_dict: dict) -> tuple[np.ndarray, np.ndarray]:
    """Get angles and intensities from an XRDML dictionary."""
    data_points = xrd_dict["xrdMeasurements"]["xrdMeasurement"]["scan"][
        "dataPoints"
    ]
    min_angle = float(
        data_points["positions"][0]["startPosition"]
    )
    max_angle = float(
        data_points["positions"][0]["endPosition"]
    )

    # XRDML producers use either <counts> (newer Data Collector exports) or
    # <intensities> (including older X'Pert Data Collector files). Both contain
    # the same whitespace-separated observed intensity series.
    intensity_data = data_points.get("counts", data_points.get("intensities"))
    if intensity_data is None:
        raise ValueError("XRDML dataPoints contains neither counts nor intensities")
    intensity_text = (
        intensity_data.get("#text")
        if isinstance(intensity_data, dict)
        else intensity_data
    )
    if not intensity_text:
        raise ValueError("XRDML intensity data is empty")
    intensities = np.array([float(val) for val in intensity_text.split()])
    angles = np.linspace(min_angle, max_angle, len(intensities))
    return angles, intensities


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
    with open(file, "rb") as f:
        content = f.read()

    if content.startswith(b"RAW2"):
        return _load_bruker_raw_v2(content), content
    if content.startswith((b"RAW1", b"RAW4", b"RAW ")):
        signature = content[:7].decode("ascii", errors="replace").rstrip("\x00")
        raise ValueError(
            f"Bruker {signature} is recognized but not yet supported; "
            "export the scan as XY or XYE"
        )

    if len(content) < 3158:
        raise ValueError("Unsupported or truncated RAW diffraction file")
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


def load_rasx(file: Path | str) -> tuple[tuple[np.ndarray, np.ndarray], bytes]:
    """Convert RASX file to xy data.

    RASX files are ZIP archives containing:
    - root.xml file
    - Data*/ folders with Profile*.txt files (scan_angle, intensity, attenuation)
    - MesurementConditions*.xml files (note: typo in original format)

    This function extracts the first scan's data from the first Profile file found.

    Returns:
        Tuple of ((angles, intensities), content) where content is the raw ZIP bytes.
        The content is kept for potential round-trip saving, though it's rarely used.
    """
    file = Path(file)
    with open(file, "rb") as f:
        content = f.read()

    with zipfile.ZipFile(io.BytesIO(content), "r") as zip_ref:
        namelist = zip_ref.namelist()

        if "root.xml" not in namelist:
            raise ValueError("No XML file found in the RASX archive.")

        # Find Data folders
        data_folders: dict[str, list[str]] = {}
        for name in namelist:
            if name.startswith("Data") and "/" in name:
                folder = name.split("/")[0]
                if folder not in data_folders:
                    data_folders[folder] = []
                data_folders[folder].append(name)

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
            raise ValueError("No Profile file found in the RASX archive.")

        # Read profile data (three columns: scan_angle, intensity, attenuation)
        # Using numpy.loadtxt to handle whitespace-separated values
        # Decode with utf-8-sig to handle BOM if present
        with zip_ref.open(profile_path) as profile_file:
            # Read as text and decode with utf-8-sig to remove BOM if present
            text_content = profile_file.read().decode("utf-8-sig")
            # Use StringIO to create a file-like object for np.loadtxt
            profile_data = np.loadtxt(io.StringIO(text_content), unpack=False)

        # Extract angles (first column) and intensities (second column)
        # Third column is attenuation, which we don't need for now
        if profile_data.ndim == 1:
            # Single row case
            angles = np.array([profile_data[0]])
            intensities = np.array([profile_data[1]])
        else:
            angles = profile_data[:, 0]
            intensities = profile_data[:, 1]

    return (angles, intensities), content


def rasx2xy(fn: str | Path, target_folder: Path = None) -> Path:
    """Convert .rasx file to .xy file (and save)."""
    fn = Path(fn)
    if target_folder is None:
        target_folder = fn.parent
    target_path = target_folder / fn.with_suffix(".xy").name

    RASXFile.from_file(fn).to_xy_file(target_path)
    return target_path


# Extension-to-class mapping for all supported XRD formats.
# Add new formats here to make them available everywhere automatically.
_FORMAT_LOADERS: dict[str, type[XRDData]] = {
    ".xy": XYFile,
    ".xye": XYFile,
    ".txt": XYFile,
    ".scn": XYFile,
    ".xrdml": XRDMLFile,
    ".raw": RawFile,
    ".rasx": RASXFile,
}


def load_pattern(path: str | Path) -> XRDData:
    """Load an XRD pattern from any supported file format.

    Supported formats: .xy, .xye, .txt, .scn, .xrdml, .raw, .rasx.

    Args:
        path: the path to the pattern file.

    Returns:
        an XRDData instance (XYFile, XRDMLFile, RawFile, or RASXFile).

    Raises:
        ValueError: if the file extension is not supported.
    """
    path = Path(path)
    ext = path.suffix.lower()
    loader = _FORMAT_LOADERS.get(ext)
    if loader is None:
        raise ValueError(
            f"Unsupported pattern file format: '{ext}'. "
            f"Supported formats: {', '.join(sorted(_FORMAT_LOADERS.keys()))}"
        )
    return loader.from_file(path)


def convert_pattern_to_xy(
    fn: str | Path, target_folder: Path | None = None
) -> Path:
    """Convert any supported XRD pattern file to .xy format.

    If the file is already in .xy format, it is copied to the target folder.

    Args:
        fn: the path to the pattern file.
        target_folder: the folder to save the .xy file in. Defaults to the
            same folder as the input file.

    Returns:
        the path to the generated .xy file.
    """
    import shutil

    fn = Path(fn)
    if target_folder is None:
        target_folder = fn.parent
    target_path = target_folder / fn.with_suffix(".xy").name

    ext = fn.suffix.lower()
    if ext in (".xy", ".xye", ".txt", ".scn"):
        if fn.resolve() != target_path.resolve():
            shutil.copy2(fn, target_path)
    else:
        load_pattern(fn).to_xy_file(target_path)

    return target_path
