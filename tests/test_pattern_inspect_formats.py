"""Cross-workstream check: every bundled pattern fixture can be inspected.

Skipped until the multi-format readers (PatternFormatError, tests/test_data/formats)
are present.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

import dara.xrd
from dara.server import api_router

FORMATS_DIR = Path(__file__).parent / "test_data" / "formats"

pytestmark = pytest.mark.skipif(
    not hasattr(dara.xrd, "PatternFormatError") or not FORMATS_DIR.is_dir(),
    reason="pattern reader fixtures are not available yet",
)

EXPECTED_FAILURES = {"garbage.xy", "two_rows.xy"}


def _inspect(path: Path):
    with open(path, "rb") as handle:
        return asyncio.run(api_router.inspect_pattern(UploadFile(file=handle, filename=path.name)))


def _fixtures():
    if not FORMATS_DIR.is_dir():
        return []
    return sorted(p for p in FORMATS_DIR.iterdir() if p.is_file() and p.name not in EXPECTED_FAILURES)


@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: p.name)
def test_fixture_can_be_inspected(path):
    payload = _inspect(path)
    assert payload["data_points"] >= 3
    assert isinstance(payload["quality_checks"], list)
    assert isinstance(payload["load_warnings"], list)


def test_garbage_is_a_line_numbered_400():
    path = FORMATS_DIR / "garbage.xy"
    if not path.exists():
        pytest.skip("garbage.xy fixture is missing")
    with pytest.raises(HTTPException) as caught:
        _inspect(path)
    assert caught.value.status_code == 400
    assert "line" in str(caught.value.detail).lower()
