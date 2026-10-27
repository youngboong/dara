"""Download BGMN executable for Linux, Mac, and Windows (standard library only)."""
from __future__ import annotations

import hashlib
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

BGMN_URL_TEMPLATE = "https://cedergrouphub.github.io/dara/_static/bgmnwin_{os_name}.zip"
KNOWN_SHA256 = {"Windows": "ab939d1bde0ee88d1608119484563d9cc126e95f3be41a92fa2adfd218e9e026"}
_TOP_FOLDER = "BGMNwin"


def _download_to(opener, url: str, destination: Path, timeout: float) -> str:
    """Stream ``url`` into ``destination`` and return the SHA-256 of the downloaded bytes."""
    digest = hashlib.sha256()
    with opener.open(url, timeout=timeout) as response:
        status = getattr(response, "status", None)
        if status is not None and status != 200:
            raise RuntimeError(f"Cannot download from {url} (HTTP {status}).")
        total = int(response.headers.get("content-length", 0) or 0)
        received = 0
        next_report = 10
        with destination.open("wb") as handle:
            while True:
                block = response.read(64 * 1024)
                if not block:
                    break
                handle.write(block)
                digest.update(block)
                received += len(block)
                if total:
                    percent = received * 100 // total
                    if percent >= next_report:
                        print(f"BGMN download: {percent}%", file=sys.stderr, flush=True)
                        next_report = (percent // 10 + 1) * 10
    return digest.hexdigest()


def _check_members(archive: zipfile.ZipFile) -> None:
    """Refuse archives that could write outside the BGMNwin folder (zip-slip guard)."""
    for name in archive.namelist():
        posix = name.replace("\\", "/")
        parts = PurePosixPath(posix).parts
        if (
            posix.startswith("/")
            or (len(posix) > 1 and posix[1] == ":")
            or ".." in parts
            or not parts
            or parts[0] != _TOP_FOLDER
        ):
            raise RuntimeError(f"Unsafe path in the BGMN archive: {name!r}.")


def _merge_tree(source: Path, destination: Path) -> None:
    """Move ``source`` into ``destination`` without deleting files that already exist there."""
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            _merge_tree(item, target)
        else:
            if target.exists():
                target.unlink()
            shutil.move(str(item), str(target))


def download_bgmn(
    target_folder: Path | None = None,
    *,
    timeout: float = 60,
    url: str | None = None,
    expected_sha256: str | None = None,
    opener=None,
) -> Path:
    """Download BGMN executable for Linux, Mac, and Windows and return the ``BGMNwin`` folder."""
    os_name = platform.system()  # Darwin, Linux, Windows
    if os_name not in ["Darwin", "Linux", "Windows"]:
        raise RuntimeError("Unsupported OS: " + os_name + ".")

    url = url or BGMN_URL_TEMPLATE.format(os_name=os_name)
    expected_sha256 = expected_sha256 or KNOWN_SHA256.get(os_name)
    target_folder = Path(target_folder) if target_folder is not None else Path(__file__).parent
    target_folder.mkdir(parents=True, exist_ok=True)
    if opener is None:
        opener = urllib.request.build_opener()  # honours environment/system proxies

    with tempfile.TemporaryDirectory(prefix=".bgmn-download-", dir=target_folder) as work:
        work_dir = Path(work)
        archive_path = work_dir / "bgmnwin.zip"
        digest = _download_to(opener, url, archive_path, timeout)
        if expected_sha256 and digest.lower() != expected_sha256.lower():
            raise RuntimeError(
                f"The downloaded BGMN archive failed its SHA-256 check (expected {expected_sha256}, got {digest}). "
                f"A proxy or antivirus program may have altered the download from {url}."
            )
        try:
            with zipfile.ZipFile(archive_path) as archive:
                _check_members(archive)
                extract_dir = work_dir / "extracted"
                archive.extractall(extract_dir)
        except zipfile.BadZipFile as error:
            raise RuntimeError(f"The downloaded BGMN archive from {url} is not a valid zip file: {error}") from error

        extracted = extract_dir / _TOP_FOLDER
        if not extracted.is_dir():
            raise RuntimeError(f"The BGMN archive from {url} does not contain a {_TOP_FOLDER} folder.")
        bgmn_folder = target_folder / _TOP_FOLDER
        _merge_tree(extracted, bgmn_folder)

    # give permission
    if os_name in ("Linux", "Darwin"):
        for name in ("bgmn", "teil", "eflech", "output"):
            executable = bgmn_folder / name
            if executable.exists():
                executable.chmod(executable.stat().st_mode | 0o111)

    return bgmn_folder


if __name__ == "__main__":
    download_bgmn()
