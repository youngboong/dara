![DARA logo](https://github.com/CederGroupHub/dara/blob/main/logo/dara.jpg?raw=true)

# DARA: Data-driven automated Rietveld analysis for phase search and refinement
[![GitHub licence](https://img.shields.io/github/license/CederGroupHub/dara)](https://github.com/CederGroupHub/dara/blob/main/LICENSE)
[![DOI](https://img.shields.io/badge/DOI-10.1021%2Facs.chemmater.5c02820-blue)](https://doi.org/10.1021/acs.chemmater.5c02820)
[![Pytest](https://github.com/CederGroupHub/dara/actions/workflows/pytest.yaml/badge.svg?branch=main)](https://github.com/CederGroupHub/dara/actions/workflows/pytest.yaml)
[![Ruff Check](https://github.com/CederGroupHub/dara/actions/workflows/ruff.yaml/badge.svg?branch=main)](https://github.com/CederGroupHub/dara/actions/workflows/ruff.yaml)

Automated phase search with BGMN.

**Windows: run [`START.cmd`](START.cmd) to install DARA if needed, start the server, and open the web app automatically.** Python 3.10 or newer is required.

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

With **Python 3.10 or newer** installed, run **`START.cmd`** in the top-level repository folder. Installation, server startup, and opening the browser happen in that single launch. No manual pip command or second launch is needed. The launcher:

- uses the repository's Python environment when one is available;
- on first launch, shows an optional Materials Project API key dialog with **Save and continue** and **Skip for now**;
- skips installation when DARA is already installed;
- otherwise creates `.venv`, runs `pip install dara-xrd`, and installs this checkout in editable mode so its customized UI and server are used;
- opens a startup page immediately before loading the scientific libraries, showing progress and any startup errors;
- automatically switches to http://localhost:8898 when the server is ready; and
- opens the existing server without starting a duplicate if DARA is already running.

Keep the START window open while using DARA. Press `Ctrl+C` there to stop the server started by START. No separate `dara.exe` console needs to be launched. Startup logs are saved under `artifacts/launcher/`. The launcher bypasses HTTP proxy settings for local readiness checks and tries installed Edge or Chrome if the default browser cannot be opened.

The API key is saved as the `MP_API_KEY` environment variable for your Windows account and reused automatically. It is not saved in this repository. Existing keys are detected without prompting. If you skip setup, turn **MP** off under **Reference databases** and use COD. The skip choice is remembered. There is no separate key-change launcher. The dialog saves the key without contacting Materials Project to validate it.

The first installation needs an internet connection and may take several minutes. Installation errors remain visible in the terminal; fix the reported issue and run START again to retry. This installs Python packages only; any separate scientific tools or credentials required by your analysis still need to be configured.


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
