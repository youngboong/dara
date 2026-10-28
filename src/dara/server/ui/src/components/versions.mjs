// Pure helpers for result versions, quick re-refinement requests and selection strings.
// Selection grammar (same as the server): 'best' | <candidate source index> | 'version:<n>'.
// 'version:1' is the original analysis and is treated as 'best'.

const VERSION_RE = /^version:([1-9]\d{0,5})$/;
const COD_STEM_RE = /_\(cod_(\d+)\)-\d+$/;

const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
const fixed = (value, digits = 2) => (finite(value) ? Number(value).toFixed(digits) : '—');

/** `'best' | number | {version: n}` or `null` when the value is not a valid selection. */
export function parseSelection(value) {
  if (value === 'best') return 'best';
  if (typeof value === 'boolean') return null;
  if (typeof value === 'number') return Number.isInteger(value) && value >= 0 ? value : null;
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const n = value.version;
    if (!Number.isInteger(n) || n < 1 || n > 999999) return null;
    return n === 1 ? 'best' : { version: n };
  }
  if (typeof value === 'string') {
    const text = value.trim();
    if (/^\d+$/.test(text)) return Number(text);
    const match = VERSION_RE.exec(text);
    if (match) return Number(match[1]) === 1 ? 'best' : { version: Number(match[1]) };
  }
  return null;
}

/** Inverse of `parseSelection`: `'best' | number | 'version:n'`. */
export function formatSelection(sel) {
  const parsed = parseSelection(sel);
  if (parsed === null) return null;
  if (parsed === 'best' || typeof parsed === 'number') return parsed;
  return `version:${parsed.version}`;
}

/**
 * True when an HTTP error means "this server has no such endpoint" (FastAPI's generic 404/405
 * or a non-JSON error page), as opposed to a handler answering 404 with its own detail such as
 * "Version 99 not found" - that detail should be shown to the user.
 */
export function isMissingEndpoint(status, detail) {
  if (status === 405) return true;
  if (status !== 404) return false;
  const text = String(detail ?? '').trim();
  return !text || text === 'Not Found' || /^Request failed \(HTTP \d+\)\.?$/.test(text);
}

/** Human label for a selection. `data` = `{all_results, versions}`. */
export function selectionLabel(sel, data = {}) {
  const parsed = parseSelection(sel);
  if (parsed === 'best') return 'Best fit';
  if (typeof parsed === 'number') {
    const results = data.all_results || [];
    const index = results.findIndex((result, i) => (result.source_index ?? i) === parsed);
    if (index < 0) return `Candidate ${parsed + 1}`;
    const rwp = results[index].rwp;
    return `Candidate ${index + 1}${finite(rwp) ? ` — Rwp ${rwp}%` : ''}`;
  }
  if (parsed) {
    const version = (data.versions || []).find(item => item.version === parsed.version);
    return `v${parsed.version}${version?.label ? ` — ${version.label}` : ''}`;
  }
  return '';
}

/** Query string for `/task/{id}/plot`. */
export function plotQuery(sel) {
  const parsed = parseSelection(sel);
  if (parsed === null || parsed === 'best') return '';
  if (typeof parsed === 'number') return `?idx=${parsed}`;
  return `?sel=version:${parsed.version}`;
}

/** COD stem -> numeric code, or `null`. */
export function codCodeFromStem(stem) {
  const match = COD_STEM_RE.exec(String(stem || ''));
  return match ? match[1] : null;
}

/**
 * `{ref, source}` for a trace hint (hint.phase is the reference stem). COD stems need a
 * source of `cod:<code>`; a COD hint without a recoverable code cannot be added (`null`).
 */
export function refSourceForHint(hint) {
  const ref = hint?.phase;
  if (!ref || typeof ref !== 'string') return null;
  if (hint.source === 'cod') {
    const code = codCodeFromStem(ref);
    return code ? { ref, source: `cod:${code}` } : null;
  }
  if (/_\(cod_/.test(ref) && !codCodeFromStem(ref)) return null;
  return { ref, source: 'task' };
}

/** Plain formula of a phase name such as `Fe3O4_227_(cod_9002316)-0`. */
export function formulaOfPhase(name) {
  const match = String(name || '').match(/^(.+?)_(\d+)_\(/);
  return match ? match[1] : String(name || '');
}

const SUBSCRIPT_DIGITS = '₀₁₂₃₄₅₆₇₈₉';
const MAX_LABEL = 120; // the server rejects longer quick-refine labels

/** Space-group number of a phase name such as `CaCO3_167_(cod_1547347)-0`, or `null`. */
export function spacegroupOfPhase(name) {
  const match = String(name || '').match(/^.+?_(\d+)_\(/);
  return match ? Number(match[1]) : null;
}

/** `R-3c`, `P2₁/c` (from BGMN's `P2_1/c`) or `SG 14` when only the number is known. */
export function formatSpacegroup(symbol, number) {
  if (symbol) return String(symbol).replace(/_(\d)/g, (_, digit) => SUBSCRIPT_DIGITS[Number(digit)]);
  return finite(number) ? `SG ${Number(number)}` : '?';
}

const clip = text => (text.length > MAX_LABEL ? `${text.slice(0, MAX_LABEL - 1)}…` : text);

function headsOf(result) {
  const heads = [];
  for (const group of result?.phases || []) {
    const head = Array.isArray(group) ? group[0] : group;
    if (head && !heads.includes(head)) heads.push(head);
  }
  return heads;
}

const structureKey = name => `${formulaOfPhase(name)}\u0000${spacegroupOfPhase(name) ?? '?'}`;

/**
 * Which swap the confidence evidence describes: `'model'` for `structure_model_alternative`
 * (same compound, other space group) and `'phase_set'` for `alternative` (a candidate that
 * explains the pattern with another set of phases).
 */
export function swapKind(alternative) {
  return finite(alternative?.spacegroup_number) ? 'model' : 'phase_set';
}

/**
 * The evidence object offered for a phase verdict. The API nests it under `verdict.evidence`;
 * a structure-model alternative (smaller change) wins over a phase-set alternative.
 */
export function alternativeOfVerdict(verdict) {
  const evidence = verdict?.evidence || verdict;
  return evidence?.structure_model_alternative || evidence?.alternative || null;
}

/**
 * Quick-refine body for a swap offered by the confidence evidence (`null` when it cannot be built).
 *
 * `structure_model_alternative` (same compound, other space group): add only the alternative
 * candidate's phase of that compound with `spacegroup_number` and remove the clicked phase;
 * nothing else of the candidate is touched.
 *
 * `alternative` (a different phase set): switch to exactly the alternative candidate's phase
 * set - candidate phases missing from `basePhases` are added and base phases the candidate does
 * not contain are removed. Phases are matched by compound AND space group. (The evidence's
 * `replaced_by` holds display labels only, so the candidate's own phase list is used instead.)
 *
 * `metadata` is the displayed result's `phase_metadata` (space-group symbols for the label).
 */
export function swapRequest(row, alternative, allResults = [], basePhases = [], metadata = {}) {
  if (!row?.phase || !alternative) return null;
  const sourceIndex = alternative.source_index;
  const result = (allResults || []).find((item, i) => (item.source_index ?? i) === sourceIndex);
  if (!result) return null;
  const base = [...new Set(basePhases || [])];
  const heads = headsOf(result);
  const symbolOf = (name, meta) => formatSpacegroup(meta?.[name]?.spacegroup_symbol, spacegroupOfPhase(name));

  if (swapKind(alternative) === 'model') {
    const formula = formulaOfPhase(row.phase);
    const target = Number(alternative.spacegroup_number);
    const head = heads.find(name => formulaOfPhase(name) === formula
      && spacegroupOfPhase(name) === target && !base.includes(name));
    if (!head) return null;
    return {
      phases: [{ ref: head, source: 'task' }],
      remove: [row.phase],
      origin: 'swap',
      label: clip(`Swapped ${formula} (${symbolOf(row.phase, metadata)}) for ${formula} (${symbolOf(head, result.phase_metadata)})`),
    };
  }

  const wanted = new Map(heads.map(name => [structureKey(name), name]));
  const keep = new Set();
  const removes = [];
  for (const name of base) {
    const key = structureKey(name);
    if (wanted.has(key) && !keep.has(key)) keep.add(key); else removes.push(name);
  }
  const adds = heads.filter(name => !keep.has(structureKey(name)));
  if (!adds.length || !removes.length || base.length - removes.length + adds.length < 1) return null;
  if (!removes.includes(row.phase)) return null; // the candidate keeps this phase: not an alternative to it
  const number = alternative.candidate_number ?? (sourceIndex != null ? sourceIndex + 1 : '?');
  const names = list => list.map(formulaOfPhase).join(', ');
  return {
    phases: adds.map(ref => ({ ref, source: 'task' })),
    remove: removes,
    origin: 'swap',
    label: clip(`Switched to candidate ${number} phases (−${names(removes)}; +${names(adds)})`),
  };
}

/** Quick-refine body that drops one phase. */
export function removeRequest(phaseName) {
  return {
    phases: [], remove: [phaseName], origin: 'remove',
    label: `Remove ${formulaOfPhase(phaseName)}`,
  };
}

const METRICS = [
  { key: 'rwp', label: 'Rwp', digits: 2, read: s => s?.fit_quality?.rwp ?? s?.rwp },
  { key: 'gof', label: 'GOF', digits: 2, read: s => s?.fit_quality?.gof ?? s?.gof },
  { key: 'chi_squared', label: 'χ²', digits: 2, read: s => s?.fit_quality?.chi_squared ?? s?.chi_squared },
  { key: 'n_unexplained', label: 'Unexplained', digits: 0, read: unexplainedCount },
];

function unexplainedCount(summary) {
  if (finite(summary?.n_unexplained)) return Number(summary.n_unexplained);
  const counts = summary?.peak_diagnostics?.counts_by_class;
  if (counts && typeof counts === 'object') return Number(counts.significant || 0) + Number(counts.minor || 0);
  const missing = summary?.peak_diagnostics?.counts?.missing;
  return finite(missing) ? Number(missing) : null;
}

/** Metric deltas next -> base. `better` = lower (for residual-style metrics, and for wt% never). */
export function deltaStrip(base, next) {
  const rows = METRICS.map(({ key, label, digits, read }) => {
    const a = read(base);
    const b = read(next);
    const known = finite(a) && finite(b);
    const delta = known ? Number(b) - Number(a) : null;
    return { key, label, a: finite(a) ? Number(a) : null, b: finite(b) ? Number(b) : null, delta, digits,
      better: known ? delta < 0 : false };
  });
  const wa = base?.weight_percent || {};
  const wb = next?.weight_percent || {};
  for (const phase of [...new Set([...Object.keys(wa), ...Object.keys(wb)])]) {
    const a = finite(wa[phase]) ? Number(wa[phase]) : null;
    const b = finite(wb[phase]) ? Number(wb[phase]) : null;
    rows.push({ key: `wt:${phase}`, label: `${formulaOfPhase(phase)} wt%`, phase, a, b, digits: 2,
      delta: a !== null && b !== null ? b - a : null, better: false });
  }
  return rows;
}

/** Text for a delta row such as `7.10 → 6.20 (−0.90)`. */
export function deltaText(row) {
  const d = row.digits ?? 2;
  const arrow = `${fixed(row.a, d)} → ${fixed(row.b, d)}`;
  if (row.delta === null || row.delta === undefined) return arrow;
  const sign = row.delta > 0 ? '+' : row.delta < 0 ? '−' : '±';
  return `${arrow} (${sign}${Math.abs(row.delta).toFixed(d)})`;
}

/**
 * Quick-refine `background` object and label from the editor state; `null` when it would change
 * nothing. `baseDegree` is the explicit polynomial degree of the fit being edited (`null` when
 * BGMN chooses it): choosing Auto on such a fit sends `degree: null` to reset it.
 */
export function backgroundRequest({ auto, degree, ranges, baseDegree = null }) {
  const excluded = (ranges || []).filter(([lo, hi]) => Number.isFinite(lo) && Number.isFinite(hi) && lo < hi)
    .map(([lo, hi]) => [Number(lo), Number(hi)]);
  const hasBase = finite(baseDegree);
  const terms = auto ? null : degree;
  const resetsToAuto = terms === null && hasBase;
  const sameDegree = terms !== null && hasBase && Number(terms) === Number(baseDegree);
  if (!excluded.length && ((terms === null && !hasBase) || sameDegree)) return null;
  const parts = [];
  if (terms !== null && !sameDegree) parts.push(`Background: ${terms} terms`);
  if (resetsToAuto) parts.push('Background: automatic');
  if (excluded.length) parts.push(`Excluded ${excluded.map(([lo, hi]) => `${lo}–${hi}`).join(', ')}°`);
  // An unchanged explicit degree is re-sent as is; `null` (Auto) always resets to BGMN's choice.
  return { background: { degree: terms, excluded_ranges: excluded }, label: parts.join('; ') };
}

export const BASELINE_LABEL = 'Baseline (current code)';

/** Quick-refine body that re-runs the stored automatic result (v1) with today's code. */
export function baselineRequest() {
  return { base: 'best', controls: {}, origin: 'manual', label: BASELINE_LABEL };
}

/** Note under the delta strip when a version is compared with the stored automatic result (v1). */
export const DRIFT_NOTE = 'v1 was refined when the task ran; re-refine the baseline to compare with the current code.';

/** True when the delta strip compares `version` against the stored original analysis (v1 or a stored candidate). */
export function showsDriftNote(version) {
  if (!version) return false;
  if (version.label === BASELINE_LABEL) return false;
  const base = parseSelection(version.origin?.base ?? 'best');
  return base === 'best' || typeof base === 'number'; // stored candidates were refined at task time too
}

/** Tag text for the results list / review panel. `null` for 'best' and unset. */
export function adoptedTag(selection) {
  const parsed = parseSelection(selection);
  if (parsed === null || parsed === 'best') return null;
  if (typeof parsed === 'number') return `candidate ${parsed + 1} adopted`;
  return `v${parsed.version} adopted`;
}

function durationText(seconds) {
  const s = Math.max(1, Math.round(seconds));
  if (s < 90) return `${s} s`;
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

/** ETA / queue text for a quick job record. */
export function etaText(job) {
  if (!job) return '';
  if (job.status === 'queued') {
    // `ahead` = jobs in front of this one; older records only have the 1-based `position`.
    const ahead = finite(job.ahead) ? Number(job.ahead) : Math.max(0, Number(job.position || 0) - 1);
    return ahead > 0 ? `Queued (${ahead} ahead)` : 'Starting…';
  }
  if (job.status !== 'running') return '';
  if (!finite(job.eta_s)) return 'estimating…';
  const left = Number(job.eta_s) - (finite(job.elapsed_s) ? Number(job.elapsed_s) : 0);
  return `≈ ${durationText(Math.max(1, left))} left`;
}
