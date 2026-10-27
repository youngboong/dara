![DARA logo](https://github.com/CederGroupHub/dara/blob/main/logo/dara.jpg?raw=true)

# DARA: Data-driven automated Rietveld analysis for phase search and refinement
[![GitHub licence](https://img.shields.io/github/license/CederGroupHub/dara)](https://github.com/CederGroupHub/dara/blob/main/LICENSE)
[![DOI](https://img.shields.io/badge/DOI-10.1021%2Facs.chemmater.5c02820-blue)](https://doi.org/10.1021/acs.chemmater.5c02820)
[![Pytest](https://github.com/CederGroupHub/dara/actions/workflows/pytest.yaml/badge.svg?branch=main)](https://github.com/CederGroupHub/dara/actions/workflows/pytest.yaml)
[![Ruff Check](https://github.com/CederGroupHub/dara/actions/workflows/ruff.yaml/badge.svg?branch=main)](https://github.com/CederGroupHub/dara/actions/workflows/ruff.yaml)

**한글 사용설명서: [manual-ko.pdf](manual-ko.pdf)** (source: [`docs/user-guide/manual_ko.md`](docs/user-guide/manual_ko.md))

Automated phase search and Rietveld refinement of powder XRD patterns with BGMN, in a browser.
This fork of [CederGroupHub/dara](https://github.com/CederGroupHub/dara) adds a zero-install Windows
launcher and a web interface built for reviewing results, not only producing them.

**Windows: double-click [`START.cmd`](START.cmd).** No Python installation is needed.

## Features

- **Input**: .xrdml (metadata-aware: instrument profile and wavelength are suggested from the file), .xy, .xye, .txt, .csv, .dat, .asc, .scn, .ras, .rasx, .brml, .raw (Bruker RAW v2). Text readers tolerate headers and delimiters and report the failing line.
- **Upload pre-check**: data quality advice (normalised counts, S/N, step, range) and elements suggested from the file name.
- **Analysis modes**: Phase identification (element system), Known-phase refinement (given phases only), Reaction check (products predicted from precursors, temperature and atmosphere).
- **Speed controls**: candidate pre-filter (kept every ground-truth phase on 60 benchmark samples), per-reference screening time limit, maximum analysis time with best-so-far results, run-time estimate before submission.
- **References**: COD and Materials Project (with MP key status), a server-side reference CIF library and per-task CIF uploads; wavelength mismatch check against XRDML metadata; user-registered instrument profiles (GEQ/GER).
- **Series**: submit 2 to 50 patterns with identical settings; the series page plots phase fractions, lattice parameters and fit quality against the variable parsed from the file names.
- **Results**: list with best Rwp, GOF, final phases, adopted flag, series and file-name search; overall confidence banner (High confidence / Check recommended / Expert review recommended) and per-phase verdicts (Confident / Ambiguous / Weak evidence) with their reasons, see [`docs/user-guide/results-confidence.md`](docs/user-guide/results-confidence.md).
- **Diagnostics**: unexplained peaks graded significant / minor / instrument / noise with trace-phase hints, review cautions, specimen displacement in mm, candidate comparison, re-refinement of selected phases, pattern comparison of several tasks.
- **Output**: paper-style English HTML report, 600 dpi PNG and SVG figures, CSV tables, export bundles for Profex/BGMN, TOPAS, GSAS-II and HighScore, full result archive.

## Windows one-click launch

Run **`START.cmd`** in the repository folder. It installs what is missing, starts the server and opens
http://localhost:8898.

The first launch needs internet access to `github.com` and `pypi.org` (Windows proxy settings are honoured). On networks that inspect HTTPS, START builds a CA bundle from certifi plus the Windows certificate store (`tools\certs\dara-ca-bundle.pem`) and passes it to the server via `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`, so COD and Materials Project downloads do not fail with `CERTIFICATE_VERIFY_FAILED`; set `DARA_CA_BUNDLE=off` to disable.
It downloads several hundred MB (up to about 1 GB) and takes roughly 5 to 15 minutes. Everything is installed inside the DARA folder:

1. a pinned [uv](https://docs.astral.sh/uv/) release, SHA-256 verified, into `tools\uv\`;
2. CPython 3.12 into `tools\python\` (no system-wide Python, PATH, registry or user-profile changes);
3. the environment `.venv\` with this checkout in editable mode, versions pinned by `scripts\constraints-py312-win.txt`;
4. the BGMN refinement programs, downloaded only if `src\dara\bgmn\BGMNwin` lacks them (checked on every launch).

Requirements: Windows 10/11 (64-bit) and about 3 GB of free disk space. Use a short path outside OneDrive
(for example `C:\DARA`). Later launches start in about a second and work offline; structure database
searches (COD, Materials Project) still need the internet.

- **Materials Project key**: on first launch an optional dialog offers **Save and continue** or **Skip for now**. The key is stored as the `MP_API_KEY` user environment variable, never in the repository. Without a key, switch **MP** off under **Reference databases** and use COD. The Submit page shows the key status.
- **Several windows**: run START again to open another window on the same server; no duplicate server is started.
- **Auto-stop**: the server stops about 20 seconds after the last DARA window is closed (within about 3 minutes if a window vanished without closing cleanly). A queued or running analysis keeps it alive until it finishes. `Ctrl+C` in the START window stops it at once.
- **Logs**: startup logs in `artifacts\launcher\`, setup logs in `artifacts\launcher\setup-YYYYMMDD-HHMMSS.log` (newest 10 kept).

Switches go after `START.cmd`, for example `START.cmd -Reinstall`:

| Switch | Effect |
| --- | --- |
| `-Reinstall` | Recreate `.venv`; `tools\` and the download cache are kept, so this also works offline. |
| `-SetupOnly` | Install and verify, then exit without starting the server. |
| `-Offline` | Never use the network; fail with a clear message if something is missing. |
| `-PythonExe <path>` | Expert option: use this interpreter as-is and skip setup (same as `DARA_PYTHON`). |
| `-NoBrowser` | Do not open a browser window. |
| `-SkipApiKeySetup` | Do not show the Materials Project key dialog. |
| `-StartupTimeoutSeconds <n>` | How long to wait for the server (default 180). |

Exit codes: 0 normal, 1 unexpected error or another START is installing, 2 no internet and no usable
environment, 3 BGMN missing and not downloadable, 4 uv download or checksum failed, 5 Python 3.12 not
installable, 6 package installation failed, 7 import check failed.

If setup is interrupted, run START again; it resumes. If the folder is moved, START rebuilds the
environment (offline as long as `tools\uv-cache` is intact). To reset, delete `.venv` or run
`START.cmd -Reinstall`; delete `tools\` too to download everything again.

## Other platforms and development

```bash
pip install dara-xrd   # upstream release
dara server            # web server on http://localhost:8898
```

For this fork, install the checkout in your own environment (`pip install -e .`). Front-end development
notes are in [`src/dara/server/ui/README.md`](src/dara/server/ui/README.md); the upstream documentation is at
https://CederGroupHub.github.io/dara/.

## Benchmark tools

`scripts/run_benchmark.py` runs the accuracy benchmark against curated ground truth in
`dataset/ground_truth/` (`validate-gt`, `run`, `score`, `compare` with regression gates; results in
`benchmarks/<run>/`). `scripts/check_prefilter_retention.py` checks that the candidate pre-filter keeps
every ground-truth phase. See [`scripts/README.md`](scripts/README.md).

## 한국어 요약

- DARA: 분말 XRD 패턴의 상 탐색과 BGMN Rietveld 정밀화를 브라우저에서 수행하는 프로그램. 원본(CederGroupHub/dara)에 설치 도구와 결과 검토 기능을 더한 맞춤판임.
- 실행: Windows에서 `START.cmd` 더블클릭. Python 사전 설치 불필요.
- 첫 실행: 인터넷 필요. uv, Python 3.12, 패키지, BGMN을 DARA 폴더 안에 설치(수백 MB~약 1 GB, 5~15분). 이후 실행은 오프라인 가능, 단 COD/MP 검색에는 인터넷 필요.
- MP API 키: 첫 실행 대화상자에서 입력(Save and continue) 또는 건너뛰기(Skip for now). 키 없이 쓰려면 Reference databases에서 MP를 끄고 COD 사용.
- 종료: 마지막 DARA 창을 닫으면 약 20초 뒤 서버 자동 종료. 분석 중에는 유지. START를 다시 실행하면 같은 서버에 창 추가.
- 입력 형식: .xrdml(권장), .xy, .xye, .txt, .csv, .dat, .asc, .scn, .ras, .rasx, .brml, .raw.
- 분석 모드: Phase identification, Known-phase refinement, Reaction check. 후보 사전 필터, 선별 시간 제한, 최대 분석 시간, 예상 시간 표시 지원.
- 결과: 신뢰도 배너와 상별 판정, 미설명 피크 등급과 미량상 힌트, 시료 변위, 후보 비교, 선택 상 재정밀화, 시리즈 추세 그래프.
- 출력: 논문형 영문 보고서, PNG/SVG 그림, CSV, Profex·TOPAS·GSAS-II·HighScore용 파일.
- 자세한 사용법: [manual-ko.pdf](manual-ko.pdf). 원문은 `docs/user-guide/manual_ko.md`, PDF 생성은 `python docs/user-guide/build_manual.py`(reportlab 필요).

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
