![DARA logo](https://github.com/CederGroupHub/dara/blob/main/logo/dara.jpg?raw=true)

# DARA: Data-driven automated Rietveld analysis for phase search and refinement
[![GitHub licence](https://img.shields.io/github/license/CederGroupHub/dara)](https://github.com/CederGroupHub/dara/blob/main/LICENSE)
[![DOI](https://img.shields.io/badge/DOI-10.1021%2Facs.chemmater.5c02820-blue)](https://doi.org/10.1021/acs.chemmater.5c02820)
[![Pytest](https://github.com/CederGroupHub/dara/actions/workflows/pytest.yaml/badge.svg?branch=main)](https://github.com/CederGroupHub/dara/actions/workflows/pytest.yaml)
[![Ruff Check](https://github.com/CederGroupHub/dara/actions/workflows/ruff.yaml/badge.svg?branch=main)](https://github.com/CederGroupHub/dara/actions/workflows/ruff.yaml)

Automated phase search with BGMN.

**Windows: run [`START.cmd`](START.cmd) to install DARA if needed, start the server, and open the web app automatically.** Python does not need to be installed; the first launch downloads about 1 GB.

**한글 사용법:** [DARA 사용 설명서 PDF](DARA_사용설명서_한글.pdf) — 새 Windows PC의 Python 설치부터 DARA 다운로드·실행까지 설명합니다. 필수/Optional(선택) 준비물, GEQ·CIF 확보 방법, 분석 조건, 결과 해석과 보고서 저장을 실제 UI 이미지와 함께 안내합니다.

Windows 실행 파일은 **`START.cmd` 하나**입니다. 최초 API 키 입력도 여기에 포함됩니다. `src/`, `scripts/`, `pyproject.toml`은 실행에 필요한 구성요소이므로 함께 보관하세요. 시험 출력물은 Git에서 제외되는 `artifacts/`에 보관합니다.

## Installation
```bash
pip install dara-xrd
```

For more details about installation, please refer to [installation guide](https://CederGroupHub.github.io/dara/install.html).

## Web Server
Dara ships with a browser-based web server for an out-of-box experience of Dara. To launch the webserver, run
```bash
dara server
```

Then you can open http://localhost:8898 to see an application that can submit, manage, and view jobs.

### Windows one-click launch

Run **`START.cmd`** in the top-level repository folder. **Python does not need to be installed.** Installation, server startup, and opening the browser happen in that single launch; no manual pip command or second launch is needed.

On the first launch START downloads about **1 GB** (this takes roughly 5-15 minutes) and installs everything *inside the DARA folder*:

1. a pinned [uv](https://docs.astral.sh/uv/) release (SHA-256 verified) into `tools\uv\`;
2. CPython 3.12.15 into `tools\python\` (no Python is installed system-wide, no PATH, registry or user-profile changes);
3. the project environment `.venv\`, with this checkout installed in editable mode and package versions pinned by `scripts\constraints-py312-win.txt`;
4. the BGMN refinement programs, only if `src\dara\bgmn\BGMNwin` is missing them (checked on every launch).

Requirements: Windows 10/11 (64-bit), internet access to `github.com` and `pypi.org` on the first launch (proxy settings of Windows are honoured), and about **3 GB** of free disk space. Keep the folder path short (for example `C:\DARA`) and outside OneDrive-synced folders: the environment contains tens of thousands of small files, and very long paths can break the installation.

Later launches skip the installation (about a second) and start the server immediately. START also:

- on first launch, shows an optional Materials Project API key dialog with **Save and continue** and **Skip for now**;
- opens a startup page immediately before loading the scientific libraries, showing progress and any startup errors;
- automatically switches to http://localhost:8898 when the server is ready; and
- opens the existing server without starting a duplicate if DARA is already running.

Keep the START window open while using DARA. Press `Ctrl+C` there to stop the server started by START. Startup logs are saved under `artifacts/launcher/`, and every setup step is logged to `artifacts\launcher\setup-YYYYMMDD-HHMMSS.log` (the newest 10 are kept). The launcher bypasses HTTP proxy settings for local readiness checks and tries installed Edge or Chrome if the default browser cannot be opened.

The API key is saved as the `MP_API_KEY` environment variable for your Windows account and reused automatically. It is not saved in this repository. Existing keys are detected without prompting. If you skip setup, turn **MP** off under **Reference databases** and use COD. The skip choice is remembered. There is no separate key-change launcher. The dialog saves the key without contacting Materials Project to validate it.

Supported pattern files: .xrdml, .xy, .xye, .txt, .csv, .dat, .asc, .scn, .ras, .rasx, .brml, .raw (Bruker RAW v2 only).

#### START switches

Pass switches after `START.cmd`, for example `START.cmd -Reinstall`.

| Switch | Effect |
| --- | --- |
| `-Reinstall` | Recreate `.venv` and reinstall the packages. `tools\` and the download cache are kept, so this also works offline. |
| `-SetupOnly` | Install/verify everything, then exit without starting the server. |
| `-Offline` | Never use the network; fail with a clear message if something is missing. |
| `-PythonExe <path>` | Expert option: use this interpreter as-is and skip all setup (the environment variable `DARA_PYTHON` does the same). It must already have DARA's dependencies. |
| `-NoBrowser` | Do not open a browser window. |
| `-SkipApiKeySetup` | Do not show the Materials Project key dialog. |
| `-StartupTimeoutSeconds <n>` | How long to wait for the server (default 180). |

#### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Started/stopped normally, or `-SetupOnly` finished |
| 1 | Unexpected error, server failed to start, or another START window is already installing |
| 2 | No internet connection and no usable environment |
| 3 | BGMN programs are missing and could not be downloaded |
| 4 | uv download failed or its SHA-256 check failed |
| 5 | Python 3.12 could not be installed or found |
| 6 | Package installation failed |
| 7 | The installed environment failed the import check |

#### Troubleshooting and resetting

- If setup is interrupted (window closed, power loss, network failure), just run START again: it resumes and reuses everything already downloaded.
- With no internet connection, an already installed DARA starts without checking for updates. A first-time installation needs the internet.
- If you move or copy the DARA folder, START detects it and rebuilds the environment automatically (this works offline as long as `tools\uv-cache` is intact; use `START.cmd -Offline` when there is no connection).
- To reset the environment, delete the `.venv` folder or run `START.cmd -Reinstall`. Delete `tools\` as well to download everything again.
- Developers: `uv pip install -e . -c scripts\constraints-py312-win.txt` (or a plain `pip install -e .`) in your own environment works too; START never uses it.

## Documentation
For more details about usage, please refer to the [documentation](https://CederGroupHub.github.io/dara/).

## Citation
If you use DARA in your research, please consider citing the following paper:

```
@article{doi:10.1021/acs.chemmater.5c02820,
  author = {Fei, Yuxing and McDermott, Matthew J. and Rom, Christopher L. and Wang, Shilong and Ceder, Gerbrand},
  title = {Dara: Automated Multiple-Hypothesis Phase Identification and Refinement from Powder X-ray Diffraction},
  journal = {Chemistry of Materials},
  volume = {38},
  number = {3},
  pages = {1364-1376},
  year = {2026},
  doi = {10.1021/acs.chemmater.5c02820},
  url = {https://doi.org/10.1021/acs.chemmater.5c02820},
  eprint = {https://doi.org/10.1021/acs.chemmater.5c02820}
}
```
