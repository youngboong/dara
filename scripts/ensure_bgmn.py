"""Make sure the BGMN refinement programs exist before DARA starts.

Runs with the plain standard library (``python -I scripts/ensure_bgmn.py``) and never imports
the ``dara`` package, which would load ray and pymatgen and take several seconds.

Exit codes: 0 = BGMN present, 3 = missing and could not be downloaded.
"""

from __future__ import annotations

import argparse
import importlib.util
import platform
import sys
from pathlib import Path

REQUIRED = ("BGMN.EXE", "EFLECH.EXE", "TEIL.EXE", "OUTPUT.EXE")  # Windows; compared case-insensitively
REQUIRED_POSIX = ("bgmn", "eflech", "teil", "output")


def bgmn_folder(repo_root: Path) -> Path:
    """Return the folder that holds the bundled BGMN programs."""
    return Path(repo_root) / "src" / "dara" / "bgmn" / "BGMNwin"


def _required_names() -> tuple[str, ...]:
    return REQUIRED if platform.system() == "Windows" else REQUIRED_POSIX


def missing_executables(folder: Path) -> list[str]:
    """Return the required executables that are not present in ``folder``."""
    folder = Path(folder)
    present = {item.name.lower() for item in folder.iterdir() if item.is_file()} if folder.is_dir() else set()
    return [name for name in _required_names() if name.lower() not in present]


def _load_download_module(repo_root: Path):
    path = Path(repo_root) / "src" / "dara" / "bgmn" / "download_bgmn.py"
    spec = importlib.util.spec_from_file_location("dara_download_bgmn", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _download_url(repo_root: Path) -> str:
    try:
        template = _load_download_module(repo_root).BGMN_URL_TEMPLATE
    except Exception:  # noqa: BLE001 - only used to build an error message
        template = "https://cedergrouphub.github.io/dara/_static/bgmnwin_{os_name}.zip"
    return template.format(os_name=platform.system())


def _failure(stream, names: list[str], url: str, reason: str) -> None:
    joined = ", ".join(names)
    print(
        f"BGMN refinement programs are missing ({joined}) and could not be downloaded from {url}: {reason}. "
        "Check the internet connection, or restore src\\dara\\bgmn\\BGMNwin from the original DARA package.",
        file=stream,
    )
    print(
        f"BGMN 정제 프로그램({joined})이 없고 다운로드에도 실패했습니다. "
        "인터넷 연결을 확인하거나 원본 DARA 패키지의 src\\dara\\bgmn\\BGMNwin 폴더를 복원하세요.",
        file=stream,
    )


def ensure_bgmn(repo_root: Path, *, offline: bool = False, downloader=None, stream=sys.stderr) -> int:
    """Return 0 when all BGMN programs are present (downloading them if needed), otherwise 3."""
    repo_root = Path(repo_root)
    folder = bgmn_folder(repo_root)
    missing = missing_executables(folder)
    if not missing:
        print("BGMN OK", file=stream)
        return 0

    url = _download_url(repo_root)
    if offline:
        _failure(stream, missing, url, "offline mode is on (-Offline) / 오프라인 모드")
        return 3

    try:
        if downloader is None:
            downloader = _load_download_module(repo_root).download_bgmn
        print(f"BGMN programs missing ({', '.join(missing)}); downloading from {url} ...", file=stream)
        downloader(repo_root / "src" / "dara" / "bgmn")
    except (OSError, RuntimeError, ValueError) as error:  # URLError and zip problems are OSError/RuntimeError
        _failure(stream, missing, url, f"{type(error).__name__}: {error}")
        return 3

    still_missing = missing_executables(folder)
    if still_missing:
        _failure(stream, still_missing, url, "the downloaded package did not contain them")
        return 3
    print("BGMN OK", file=stream)
    return 0


def main(argv=None) -> int:
    """Command-line entry point."""
    for text_stream in (sys.stdout, sys.stderr):
        try:
            text_stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Check (and if needed download) the BGMN programs.")
    parser.add_argument("--offline", action="store_true", help="never download")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    return ensure_bgmn(args.repo_root, offline=args.offline)


if __name__ == "__main__":
    sys.exit(main())
