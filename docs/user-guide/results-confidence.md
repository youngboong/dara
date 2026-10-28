# Interpreting result confidence

Result pages in the DARA web interface show an evidence-based assessment next to the
refinement. It states what the data support; it does not say that a result is right or
wrong, and it never replaces your own judgement about the sample. Tasks stored by
older versions of DARA have no assessment and simply do not show these elements.

## Overall level

The banner at the top of a result has one of three levels. Each level lists the reasons
that determined it.

| Level | Meaning | Conditions |
| --- | --- | --- |
| High confidence | The fit statistics and the phase evidence raise no flag | GOF ≤ 3, no significant unexplained peak, and no phase is weak or ambiguous |
| Check recommended | At least one point deserves a look | Any phase is ambiguous or weak, GOF above 3, 1 to 3 significant unexplained peaks, a displacement warning, or a mass-balance warning |
| Expert review recommended | Interpretation needs an experienced analyst | Any phase of 5 wt% or more has weak evidence, 4 or more significant unexplained peaks (or their heights sum to 20 % of the strongest peak), Rwp above 25 %, GOF above 4, severe specimen displacement, or half or more of the final refinements failed |

The same overall tag appears for every candidate in the candidate table (sortable), so
alternative phase sets can be compared on the same scale.

## Unexplained peaks

Observed peaks that the calculated pattern does not reproduce are graded by size, not
counted. The height is the smoothed obs − calc residual at the peak, relative to the
strongest observed peak above background; S/N is that height over the noise level.

| Class | Rule | Effect on the overall level |
| --- | --- | --- |
| significant | ≥ 2 % of the strongest peak and S/N ≥ 8 | 1–3 → Check recommended; 4 or more, or heights summing to ≥ 20 % → Expert review recommended |
| minor | between noise and significant | None; listed as information |
| instrument | within 0.15° of a Cu Kβ or W Lα1 satellite of a strong reflection and ≤ 5 % of it | None; the line is named in the reason |
| noise | < 0.5 % of the strongest peak or S/N < 3 | None |

When minor or significant peaks line up with strong reflections of a candidate, input or
locally cached COD structure (after the specimen-displacement correction), the reason
adds "Positions are consistent with trace …". This is a hint for a re-refinement, not a
change to the result. The Fit diagnostics tag on the task page shows the counts per class,
and the report lists every unexplained peak with its class and interpretation.

## Phase verdicts

Every refined phase receives one verdict. Hover over the tag to read the reasons and the
numbers behind it.

| Verdict | Meaning |
| --- | --- |
| Confident | Present in the candidate models, well determined, no competing explanation |
| Ambiguous | The data do not clearly separate the phase from an alternative |
| Weak evidence | The amount is small or poorly determined relative to its uncertainty |

The criteria and thresholds are:

- **Presence across candidates** — a phase is supported when it appears in at least 60 % of the candidate models.
- **Amount against its uncertainty** — the weight fraction divided by its estimated standard deviation (wt%/σ) is Confident at 3 or more and Weak below 2. A fraction below 1 wt% is Weak.
- **Alternative explanation** — if another candidate without this phase reaches an Rwp within max(0.5 percentage points, 5 % of the Rwp), the phase is Ambiguous. The tooltip names the alternative candidate.
- **Hamilton significance test** — when a candidate without the phase exists, the ratio of the weighted residuals is compared with the critical value at α = 0.01. A significant result means the phase measurably improves the fit.
- **Computed reference structure** — when the reference comes from a computed database entry (Materials Project) rather than an experimental structure, a refined lattice that moved by more than 1.5 % from the reference counts against the phase. A lattice parameter that ended at its refinement limit is flagged for any phase.

## Specimen displacement

BGMN refines a displacement parameter (EPS2). The task page converts it to millimetres
with the goniometer radius of the selected instrument profile, using
s = EPS2 × R, and shows it in the Refinement quality row. When the radius is not known only
EPS2 and the 2θ shift at 30° are given.

| Displacement magnitude | Level |
| --- | --- |
| up to 0.3 mm | OK |
| above 0.3 mm | Info |
| above 1.0 mm | Warning |
| above 2.0 mm, or at the refinement limit | Severe |

A large displacement shifts all peak positions together. Lattice parameters and phase
matching from such a fit should be treated with care until the specimen height and the
instrument profile have been checked. A broad background hump that the background model
may have absorbed is reported as a caution.

## Mass balance check

A fit can look excellent and still quantify wrongly, or identify a phase whose chemistry
does not match the sample. When the precursor amounts are known, DARA compares the element
ratio implied by the refined weight fractions with the element ratio of the precursors.

Enter amounts in the **Precursor / expected formulas** field (plain formulas keep working
exactly as before and simply skip the check):

| Entry | Meaning |
| --- | --- |
| `Fe2O3, 2 TiO2` (also `2TiO2`, `2*TiO2`) | Molar coefficients; an entry without a coefficient counts as 1 |
| `In2O3:0.3022g, TiO2:0.2004g` (also `mg`, `mol`, `mmol`) | Weighed amounts; every entry then needs an amount |
| `Fe2O3, TiO2` | No amounts: the check is skipped (a plain list never implies 1:1) |

Without amounts in the field, DARA reads them from the pattern file name only when every
listed precursor matches exactly one formula of the name: `2Fe3O4-3Y2O3_1000C_60min`
(coefficients, missing coefficient = 1) or `In2O3_0p3022-TiO2_0p2004--8min` (grams,
`p` = decimal point). The message then says that the amounts were read from the file name.

Only elements that cannot leave the sample are compared: O, H, C and N (and the open
element in reaction mode) are excluded, and at least two elements must remain. The
metric is the largest absolute difference between an element's mole fraction in the
precursors and in the refined phases. The weight-fraction e.s.d. widens the tolerance by
two standard deviations.

| Largest deviation | Result |
| --- | --- |
| up to 0.10 | No message |
| 0.10 to 0.20 | Info caution; listed as a reason without changing the overall level |
| above 0.20 | Warning caution; the overall level becomes at least Check recommended |

Example: precursors Fe2O3 + 2 TiO2 (Fe:Ti = 1:1) reacted to Fe2TiO5 + TiO2 must give
75 wt% Fe2TiO5 and 25 wt% TiO2. A fit reporting 26.8/73.2 implies Fe:Ti = 0.18:0.82 (deviation
0.32) and is flagged. A deviation can also come from an unidentified or amorphous phase,
volatile loss or an incomplete reaction with a missing phase, so the caution does not say
which phase is wrong. The thresholds were set on the 20 pairwise-reaction ground truths:
12 expert refinements stay below 0.07, five lie between 0.11 and 0.17 (unidentified
material or a phase missing from the model) and three exceed 0.29.

## Candidate screening

Under **Candidate CIFs** the task page can show

- how many CIF files were excluded by structure checks (hover for the file names and reasons),
- how many phases were available only as computed structures, and
- for how many computed structures the lattice window was widened.

## Limitations

- Weight fractions are relative: they are normalised over the modelled crystalline phases. Amorphous and unidentified material is not included.
- The reported uncertainties are BGMN estimated standard deviations of the least-squares fit. They do not include systematic effects such as preferred orientation, microabsorption, specimen displacement, an incorrect structure model or sampling statistics, so they are not the total uncertainty of a quantity.
- The levels are thresholds on statistics, not probabilities. A phase can be physically present and still be marked Weak evidence when it is a trace, and a high-confidence result can still be affected by a phase that is missing from every candidate.
- Candidate phase sets come from the structures that were available (COD, Materials Project, your own CIFs). A phase that is not in any database or reference list cannot be found.
- Always compare the result with the sample history and, where possible, with a second technique.
