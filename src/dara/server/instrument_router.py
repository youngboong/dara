import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from dara.instrument_profiles import registry_directory

router = APIRouter(prefix="/api")


@router.post("/instrument-profiles")
async def register_profile(
    label: str = Form(..., min_length=1, max_length=120),
    geometry: str = Form(..., min_length=1, max_length=250),
    radiation: str = Form(..., min_length=1, max_length=60),
    notes: str = Form("", max_length=2000),
    verified: bool = Form(False),
    verified_by: str = Form("", max_length=120),
    geq: UploadFile = File(...),
    ger: UploadFile | None = File(None),
):
    if not label.strip() or not geometry.strip() or not radiation.strip():
        raise HTTPException(400, "Label, measurement geometry and radiation are required.")
    if verified and (not verified_by.strip() or not notes.strip()):
        raise HTTPException(400, "Record the verifier and calibration/standard evidence before marking a profile verified.")
    files = {}
    for upload, extension in ((geq, ".geq"), (ger, ".ger")):
        if upload is None:
            continue
        if not (upload.filename or "").lower().endswith(extension):
            raise HTTPException(400, f"Expected a {extension} file")
        content = await upload.read(8 * 1024 * 1024 + 1)
        if not content or len(content) > 8 * 1024 * 1024:
            raise HTTPException(400, "Profile files must be nonempty and at most 8 MB each.")
        files[extension] = content
    identity = str(uuid4())
    metadata = {"id": f"user-{identity}", "label": label.strip(), "geometry": geometry.strip(),
                "radiation": radiation.strip(), "notes": notes, "verified": verified,
                "verified_by": verified_by if verified else "", "verification": "operator-declared" if verified else "unverified",
                "created_at": datetime.now(timezone.utc).isoformat(), "has_ger": ".ger" in files,
                "sha256": {key: hashlib.sha256(value).hexdigest() for key, value in files.items()}}
    directory = registry_directory() / identity
    directory.mkdir(parents=True)
    for extension, content in files.items():
        (directory / f"instrument{extension}").write_bytes(content)
    # Publish metadata last. Existing registrations are never overwritten.
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata
