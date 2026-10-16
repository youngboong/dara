"""Immutable user instrument-profile registry. Registration is not calibration."""
import json
from pathlib import Path
from uuid import UUID


def registry_directory():
    from dara.server.setting import get_dara_server_settings

    return Path(get_dara_server_settings().montydb_path).resolve().parent / "instrument-profiles"


def registered_profiles():
    profiles = {}
    for path in registry_directory().glob("*/metadata.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            profiles[item["id"]] = item
        except (OSError, ValueError, KeyError):
            continue
    return profiles


def registered_profile_path(identifier):
    suffix = str(identifier).removeprefix("user-")
    canonical = str(UUID(suffix))
    if str(identifier) != f"user-{canonical}":
        raise ValueError("Invalid registered profile identifier")
    path = registry_directory() / canonical / "instrument.geq"
    if not path.is_file():
        raise FileNotFoundError("Registered instrument profile not found")
    return path
