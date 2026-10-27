# Using the web server

For installation and screen-by-screen operation see the
[Korean user manual PDF](../DARA_사용설명서_한글.pdf); for how the advanced features
behave and where their limits are see [Practical analysis workflows](practical-workflows.md).
For the meaning of the confidence verdicts shown on result pages see
[Interpreting result confidence](user-guide/results-confidence.md).

## Basic launch

Run `START.cmd` in the repository root. It walks through the first-time API key
prompt, installs the required packages, starts the server and opens the browser.
The default address is <http://localhost:8898>. API keys are optional: without a
key, switch MP off and COD on in Submit.
Keep the START window open while you work and press `Ctrl+C` in it to stop the server.
Start-up logs are written to `artifacts/launcher/`.

## Direct launch for developers (optional)

Run this from an environment where DARA is installed.

```console
dara server
```

Server options are listed by `dara server --help`; the settings are defined in
`src/dara/server/setting.py`. The defaults are host `127.0.0.1`, port `8898` and a
file-based MontyDB at `.dara-server/montydb` in the user's home directory, so no
separate MongoDB installation is needed. Environment variables use the
`DARA_SERVER_` prefix.

The request formats of every endpoint are documented by the running server at
<http://localhost:8898/docs>.

## Pages

| Page | Address | Purpose |
| --- | --- | --- |
| Submit | `/` | Upload one pattern, or several as a series, and choose the analysis settings |
| Results | `/results` | All tasks; tasks that belong to a series carry a `Series: <name> #<n>` tag |
| Task | `/task/?id=<id>` | One result: confidence banner, phase verdicts, figures and exports |
| Series | `/series` and `/series/?id=<id>` | Series list, and one series with trend charts |
| Compare | `/compare?tasks=<ids>` | Overlay of measured patterns |
| Tutorial | `/tutorial` | Short in-app guide |

## Series analysis

Switch on **Submit as a series** in Submit, enter a series name and add 2 to 50 pattern
files. Every file is analysed as an independent task with identical settings. The
order and a plotting variable are parsed from the file names: a whole token made of a
number and a unit, such as `300C`, `60min`, `0p5wt%` (`p` stands for the decimal point). The first
token position that is present in every file and changes between files is used, so
`ZnO_300C_60min.xy`, `ZnO_500C_60min.xy` and `ZnO_700C_60min.xy` give `°C` as the
variable and ignore the constant `60min`. Chemical formulas such as `Bi2O3-2MoO3`
never parse as a variable. When no variable is found the files are ordered by name.
The variable is used for ordering and plotting only; it never changes the analysis.
The preview table in Submit shows the parsed order before you submit.

Submission is all-or-nothing: every file is read and every job is built first, and
nothing is queued if any file fails (the error names the file). Pattern inspection in
the form (instrument profile suggestion, data-quality pre-check) uses the first file.

The series page lists the tasks with state, Rwp, GOF, overall confidence and phases,
refreshes every 10 s while tasks are queued or running, and draws

- phase weight fractions against the variable (one trace per phase identity, error bars from the BGMN e.s.d.),
- lattice parameters of a chosen phase against the variable, and
- Rwp and GOF against the variable.

Missing values are drawn as gaps, not zeros. Without a parsed variable the x axis is the sample order.
**Export CSV** downloads the final-result table of all tasks, and **Compare patterns** opens the measured patterns of the completed tasks.

| Endpoint | Description |
| --- | --- |
| `POST /api/series/preview` | JSON `{"filenames": [...]}`; returns the parsed unit, order and variable per file |
| `POST /api/submit-series` | Multipart form: `user`, `series_name`, `pattern_files` (2 to 50) and the same settings as `/api/submit` |
| `GET /api/series` | Series overview, newest first |
| `GET /api/series/{series_id}` | Tasks of one series in order, with their result summaries |

## Confidence verdicts

Completed task pages show a banner with the overall level (High confidence, Check
recommended, Expert review recommended), a verdict for every refined phase
(Confident, Ambiguous, Weak evidence), the specimen-displacement estimate and a
confidence column in the candidate table. Each verdict lists the evidence behind it. Tasks
stored by older versions simply lack these elements. See
[Interpreting result confidence](user-guide/results-confidence.md) for the criteria.

## Figures and exports

The task page offers:

- **Figure (PNG)** and **Figure (SVG)** — a publication-style refinement figure
  (`GET /api/task/{id}/figure.png|figure.svg`, optional `idx`, `best`, `dpi`, `width_mm`);
- **Export for…** — starting models for other programs as a ZIP
  (`GET /api/task/{id}/export/{profex|topas|gsas2|highscore}`, optional `idx`).

Without a selection the server uses the adopted result of the task review, or the best
fit. Inside the candidate dialog the buttons act on the displayed candidate.

## Run-time controls

Large element systems can produce hundreds of candidate references, and each one is
screened with BGMN. Submit therefore shows an estimate and offers three controls.

**Estimate.** While you edit the form, Submit calls `POST /api/submission/estimate`
(JSON body) and shows the expected number of candidates, the expected run time and
suggestions for shortening it. The estimate never queues a job.

| Request field | Default | Meaning |
| --- | --- | --- |
| `analysis_mode` | `elemental` | `elemental`, `known_mixture` or `reaction` |
| `elements`, `precursor_formulas` | empty | Lists or comma-separated text; elements are derived from the formulas when empty |
| `use_cod`, `use_mp` | `true` | Reference databases to count |
| `mp_e_hull_cutoff`, `max_mp_structures` | `0.05`, `100` | Same limits as in the submission |
| `candidate_prefilter` | `false` | Whether the pre-filter is switched on |
| `wavelength`, `two_theta_max`, `data_points` | `Cu`, none, none | Used for the reflection count and run-time model |
| `max_analysis_minutes` | none | Only used to decide whether to suggest a time limit |

The response contains `candidates` (`cod`, `mp`, `mp_capped`, `total_before_dedup`,
`expected_after_dedup`, `prefilter_expected_after`, `note`), `reflections` (`median`,
`very_heavy`, `source`), `runtime_s` (`low`, `expected`, `high`; `null` when the cost model is
unavailable), `workers`, `level` (`ok`, `long` above 30 min, `very_long` above 90 min or
`unknown`), `suggestions`, `mp_status` (`ok`, `disabled`, `not_configured`, `timeout` or
`error`), `assumptions` and `elapsed_ms`. Invalid input returns 400 with the same messages as a
submission. COD counts come from the local index. Materials Project counts use a metadata-only
query (no structures are downloaded) limited to 15 s and cached for 10 min; if it fails the
estimate counts COD only and says so. Duplicates, precursor-formula references of Materials
Project and uploaded CIFs are estimated or ignored; treat the numbers as an order of magnitude.

**Submission fields.** `POST /api/submit` and `POST /api/submit-series` accept three optional
form fields. When a field is omitted, the job behaves as in earlier versions, so existing
API clients are unaffected. The web form sends explicit values.

| Field | Web form default | When omitted | Range |
| --- | --- | --- | --- |
| `candidate_prefilter` | on | off | true / false |
| `screening_timeout_s` | 300 | 1200 s per reference | 60-1200 |
| `max_analysis_minutes` | blank | no limit | 5-720 |

Cloning a task restores the three values it was run with (tasks stored by older versions
clone with the pre-filter off and no limits).

**Candidate pre-filter.** Before screening, each candidate's strongest calculated reflections
are compared with the measured pattern; a reference whose strongest peaks are all absent
from the pattern is skipped. Uploaded CIFs, reference-library CIFs and phases listed as
precursors or known phases are never skipped, and at least a minimum number of candidates
is always kept. Skipped references are listed in the result (`candidate_summary.prefilter`),
in the screening tags of the task page and in the report. Phases present only in traces
too weak to detect are the usual cause of a wrongly skipped reference; use
**Known-phase refinement** to test a specific phase.

**Screening limit.** Each reference gets at most `screening_timeout_s` of BGMN time during
screening (twice that for very large structures, never more than 1200 s). A reference
that exceeds it is not considered; the task page shows a tag and a caution that names it,
and the confidence level is lowered when a quarter or more of the references are missing.

**Maximum analysis time.** When `max_analysis_minutes` is exceeded the search stops at
the current stage (reference screening, phase-combination search or final refinement) and
returns the best result found so far. The result is marked as incomplete: the task page
shows a "Stopped at time limit" tag, a caution describes the stage, the confidence
level asks for a check, and the report states the limit. The running task page shows the
elapsed time against the limit and, during reference screening, the number of references
screened with an estimate of the time left in that stage.

**Environment switches.** `DARA_CANDIDATE_PREFILTER`, `DARA_SCREENING_TIMEOUT_S` and
`DARA_MAX_RUNTIME_S` are read by the analysis engine on the server. The value `off`
is a kill switch: it disables the feature for every job, even when a submission asks for it.

## Reference CIF library

The **Reference library CIFs** control in Submit selects CIF files stored on the server
(`GET/POST /api/reference-cifs`, `DELETE /api/reference-cifs/{id}`); **Manage library**
uploads, lists and deletes them. Files are checked when added and any warnings are
shown. Selected ids are submitted as `library_cif_ids`. The control is hidden when the
server does not provide the library endpoints.

## Materials Project key status

Next to the MP switch, Submit shows whether `MP_API_KEY` is configured and accepted
(`GET /api/mp-key/status`, `?refresh=true` forces a new check). Materials Project
queries require the key in the server environment; restart `START.cmd` to enter one.
