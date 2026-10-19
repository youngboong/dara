![DARA logo](https://github.com/CederGroupHub/dara/blob/main/logo/dara.jpg?raw=true)

# DARA: Data-driven automated Rietveld analysis for phase search and refinement
[![GitHub licence](https://img.shields.io/github/license/CederGroupHub/dara)](https://github.com/CederGroupHub/dara/blob/main/LICENSE)
[![DOI](https://img.shields.io/badge/DOI-10.1021%2Facs.chemmater.5c02820-blue)](https://doi.org/10.1021/acs.chemmater.5c02820)
[![Pytest](https://github.com/CederGroupHub/dara/actions/workflows/pytest.yaml/badge.svg?branch=main)](https://github.com/CederGroupHub/dara/actions/workflows/pytest.yaml)
[![Ruff Check](https://github.com/CederGroupHub/dara/actions/workflows/ruff.yaml/badge.svg?branch=main)](https://github.com/CederGroupHub/dara/actions/workflows/ruff.yaml)

Automated phase search with BGMN.

**Windows: run [`START.cmd`](START.cmd) to install DARA if needed, start the server, and open the web app automatically.** Python 3.10 or newer is required.

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

With **Python 3.10 or newer** installed, run **`START.cmd`** in the top-level repository folder. Installation, server startup, and opening the browser happen in that single launch. No manual pip command or second launch is needed. The previous `Start DARA Web.cmd` also forwards to START. The launcher:

- uses the repository's Python environment when one is available;
- on first launch, shows an optional Materials Project API key dialog with **Save and continue** and **Skip for now**;
- skips installation when DARA is already installed;
- otherwise creates `.venv`, runs `pip install dara-xrd`, and installs this checkout in editable mode so its customized UI and server are used;
- starts `dara server` in a separate minimized terminal;
- waits until the server is ready, then opens http://localhost:8898 automatically; and
- opens the existing server without starting a duplicate if DARA is already running.

The START window closes after opening the browser; the server keeps running. Restore the minimized server terminal and press `Ctrl+C`, or close it, to stop DARA.

The API key is saved as the `MP_API_KEY` environment variable for your Windows account and reused automatically. It is not saved in this repository. Existing keys are detected without prompting. If you skip setup, run **`Configure MP API.cmd`** later to add a key; the same file can replace an existing key. Restart the DARA server after changing the key, then enable **MP** under **Reference databases** in the web app. The dialog saves the key without contacting Materials Project to validate it.

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
