"""Library of user-supplied reference CIFs, stored next to the server database.

Files live in ``<db folder>/reference-cifs/<id>.cif`` with a ``registry.json``
index. Analyses can pull library entries in through ``load_library_cifs``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from dara.cif import Cif
from dara.server.setting import get_dara_server_settings
from dara.structure_quality import validate_cif

router = APIRouter(prefix="/api")

MAX_CIF_BYTES = 5 * 1024 * 1024
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_FATAL_CODES = {"no_sites", "negative_or_zero_lattice"}
_lock = threading.Lock()


def library_directory() -> Path:
    """Folder holding the library (created on demand by the writers)."""
    return Path(get_dara_server_settings().montydb_path).resolve().parent / "reference-cifs"


def _registry_path() -> Path:
    return library_directory() / "registry.json"


def _read_registry() -> list[dict]:
    try:
        data = json.loads(_registry_path().read_text(encoding="utf-8"))
        items = data.get("items", [])
        return items if isinstance(items, list) else []
    except (OSError, ValueError):
        return []


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as temp_file:
            temp_file.write(data)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _write_registry(items: list[dict]) -> None:
    payload = json.dumps({"items": items}, indent=1, ensure_ascii=False).encode("utf-8")
    _atomic_write(_registry_path(), payload)


def _describe(content: bytes, filename: str) -> dict:
    """Parse a CIF and return ``formula``, ``spacegroup_number`` and validation issues."""
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "candidate.cif"
        path.write_bytes(content)
        try:
            cif = Cif.from_file(path)
        except Exception as exc:
            raise HTTPException(400, f"The CIF could not be used: it could not be read ({exc}).")
        if not cif.data:
            raise HTTPException(400, "The CIF could not be used: it contains no data block.")

        issues = validate_cif(path)
        for issue in issues:
            if issue["code"] in _FATAL_CODES:
                raise HTTPException(400, f"The CIF could not be used: {issue['message']}")

        formula = None
        spacegroup = None
        try:
            structure = cif.to_structure()
            formula = structure.composition.reduced_formula
            from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

            try:
                spacegroup = int(SpacegroupAnalyzer(structure, symprec=0.1).get_space_group_number())
            except Exception:
                spacegroup = None
        except Exception as exc:
            # Over-occupied sites and similar quirks are kept as warnings, but a file from
            # which no structure can be built for no recognised reason is useless to BGMN too.
            if any(issue["code"] == "parse_error" for issue in issues):
                raise HTTPException(
                    400,
                    "The CIF could not be used: no crystal structure could be built from it "
                    f"({type(exc).__name__}). Check the cell parameters and atom sites.",
                )
    # The user chose to supply this file: keep every remaining issue as a warning.
    validation = [{**issue, "severity": "warning"} for issue in issues]
    return {"formula": formula, "spacegroup_number": spacegroup, "validation": validation}


@router.get("/reference-cifs")
async def list_reference_cifs():
    with _lock:
        items = _read_registry()
    return {"items": sorted(items, key=lambda item: item.get("added_at", ""), reverse=True)}


@router.post("/reference-cifs")
async def add_reference_cif(file: UploadFile = File(...), note: str = Form("")):
    content = await file.read(MAX_CIF_BYTES + 1)
    if len(content) > MAX_CIF_BYTES:
        raise HTTPException(413, "The CIF is larger than 5 MB.")
    if not content.strip():
        raise HTTPException(400, "The CIF could not be used: the file is empty.")
    filename = Path(file.filename or "reference.cif").name
    details = _describe(content, filename)
    digest = hashlib.sha256(content).hexdigest()

    with _lock:
        items = _read_registry()
        for existing in items:
            if existing.get("sha256") == digest:
                return JSONResponse(
                    status_code=409,
                    content={"message": "This CIF is already in the library.", "id": existing["id"]},
                )
        item_id = uuid.uuid4().hex
        item = {
            "id": item_id,
            "filename": filename,
            "formula": details["formula"],
            "spacegroup_number": details["spacegroup_number"],
            "sha256": digest,
            "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": note,
            "validation": details["validation"],
        }
        _atomic_write(library_directory() / f"{item_id}.cif", content)
        try:
            _write_registry([*items, item])
        except BaseException:
            try:
                (library_directory() / f"{item_id}.cif").unlink()
            except OSError:
                pass
            raise
    return item


@router.delete("/reference-cifs/{item_id}")
async def delete_reference_cif(item_id: str):
    if not _ID_PATTERN.match(item_id):
        raise HTTPException(404, "Reference CIF not found")
    with _lock:
        items = _read_registry()
        if not any(item.get("id") == item_id for item in items):
            raise HTTPException(404, "Reference CIF not found")
        _write_registry([item for item in items if item.get("id") != item_id])
        try:
            (library_directory() / f"{item_id}.cif").unlink()
        except OSError:
            pass
    return {"deleted": item_id}


def load_library_cifs(ids: list[str]) -> list[Cif]:
    """Parsed library CIFs in the order given; ``KeyError(id)`` for an unknown id."""
    with _lock:
        known = {item.get("id") for item in _read_registry()}
    cifs: list[Cif] = []
    for item_id in ids:
        if not isinstance(item_id, str) or not _ID_PATTERN.match(item_id) or item_id not in known:
            raise KeyError(item_id)
        path = library_directory() / f"{item_id}.cif"
        if not path.is_file():
            raise KeyError(item_id)
        cifs.append(Cif.from_file(path))
    return cifs
