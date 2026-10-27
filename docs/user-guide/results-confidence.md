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
| High confidence | The fit statistics and the phase evidence raise no flag | GOF ≤ 3 and at most 1 missing peak, and no phase is weak or ambiguous |
| Check recommended | At least one point deserves a look | Any phase is ambiguous or weak, GOF above 3, 2 to 4 missing peaks, or a displacement warning |
| Expert review recommended | Interpretation needs an experienced analyst | Any phase of 5 wt% or more has weak evidence, 5 or more peaks are missing, Rwp above 25 %, GOF above 4, severe specimen displacement, or half or more of the final refinements failed |

The same overall tag appears for every candidate in the candidate table (sortable), so
alternative phase sets can be compared on the same scale.

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
