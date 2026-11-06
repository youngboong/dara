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

/**
 * Displayed candidate holding a source index: `{number, result, member}` (1-based number, the
 * displayed row, and the `merged_results` entry when the index was merged into that row), or null.
 */
export function displayedCandidateOf(allResults = [], sourceIndex) {
  const results = allResults || [];
  for (let i = 0; i < results.length; i += 1) {
    const result = results[i];
    if ((result.source_index ?? i) === sourceIndex) return { number: i + 1, result, member: null };
    const member = (result.merged_results || []).find(item => item.source_index === sourceIndex);
    if (member) return { number: i + 1, result, member };
  }
  return null;
}

/** Short database record of a reference name: `cod_1010004` from `Bi2O3_14_(cod_1010004)-0`. */
const recordOf = name => /\(([^()]+)\)[^()]*$/.exec(String(name ?? ''))?.[1] ?? String(name ?? '');

/** Merged results of a displayed row that use other database records (not only another phase order). */
export function referenceVariants(result) {
  return (result?.merged_results || []).filter(m => m.differing_phases?.length);
}

/**
 * Label of a result merged into displayed candidate `number`, the same wording as the server
 * (`versions.merged_label`): `Candidate 1 · other reference (cod_1010004)` for other database
 * records, `Candidate 1 (stored #2)` when only the phase order differs.
 */
export function mergedLabel(number, member) {
  const differing = member?.differing_phases || [];
  return differing.length
    ? `Candidate ${number} · other reference (${differing.map(recordOf).join(', ')})`
    : `Candidate ${number} (stored #${member.source_index + 1})`;
}

/**
 * Short suffix for a result merged into a displayed candidate, worded like `mergedLabel`:
 * ` (other reference)` when it used other database records, ` (stored #N)` when only the phase
 * order differs; empty for the displayed row itself.
 */
function memberSuffix(member) {
  if (!member) return '';
  return member.differing_phases?.length ? ' (other reference)' : ` (stored #${member.source_index + 1})`;
}

/** `mergedLabel` for a merged result; null for a displayed row. */
function memberLabel(found) {
  return found?.member ? mergedLabel(found.number, found.member) : null;
}

/** Human label for a selection. `data` = `{all_results, versions}`. */
export function selectionLabel(sel, data = {}) {
  const parsed = parseSelection(sel);
  if (parsed === 'best') return 'Best fit';
  if (typeof parsed === 'number') {
    const found = displayedCandidateOf(data.all_results, parsed);
    if (!found) return `Candidate (stored #${parsed + 1})`;
    const rwp = (found.member || found.result).rwp;
    const rwpText = finite(rwp) ? ` — Rwp ${rwp}%` : '';
    return `${memberLabel(found) || `Candidate ${found.number}`}${rwpText}`;
  }
  if (parsed) {
    const version = (data.versions || []).find(item => item.version === parsed.version);
    if (!version && versionGone(data.versions, parsed)) return `v${parsed.version} (deleted)`;
    return `v${parsed.version}${version?.label ? ` — ${versionLabel(version, data.versions)}` : ''}`;
  }
  return '';
}

/**
 * Fit view state of a task page address (`?id=9&fit=version:12`): `undefined` (closed, or an
 * invalid value), `null` (best fit), a candidate source index or `'version:n'`.
 */
export function fitFromSearch(search) {
  const value = new URLSearchParams(search || '').get('fit');
  if (value === null) return undefined;
  const parsed = parseSelection(value);
  if (parsed === null) return undefined;
  if (parsed === 'best') return null;
  return typeof parsed === 'number' ? parsed : `version:${parsed.version}`;
}

/** `search` with `fit` set to the open fit (`plotId` as in `fitFromSearch`) or removed. */
export function searchWithFit(search, plotId) {
  const params = new URLSearchParams(search || '');
  params.delete('fit');
  const value = plotId === undefined ? null : plotId === null ? 'best' : formatSelection(plotId);
  // Written by hand: URLSearchParams would encode `version:12` as `version%3A12`.
  const text = [params.toString(), value === null ? '' : `fit=${value}`].filter(Boolean).join('&');
  return text ? `?${text}` : '';
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

/**
 * Weight-fraction rows of two fits keyed by phase identity (formula + space group; the server's
 * `phase_identity` map gives the reduced formula), so one compound refined from different
 * database records shares a row. Unknown identities fall back to the name. Phases of one
 * identity in a fit are paired with the other fit's by weight and numbered (`Fe2O3 #2`).
 * `a`/`b` are fit-compare sides (`{weight_percent, phase_identity}`).
 */
export function weightRows(a, b, symbolOf = () => null) {
  const rows = identityRows(a, b);
  // Two compounds with one formula (polymorphs: Y2O3 Ia-3 and I2₁3) get their space group.
  const keysOf = new Map();
  rows.forEach(row => keysOf.set(row.base, (keysOf.get(row.base) || new Set()).add(row.group)));
  return rows.map(({ base, group, ...row }) => {
    if (keysOf.get(base).size < 2) return row;
    const name = row.names[0];
    const sg = formatSpacegroup(symbolOf(name), spacegroupOfPhase(name));
    return sg === '?' ? row : { ...row, formula: row.formula.replace(base, `${base} (${sg})`) };
  });
}

function identityRows(a, b) {
  const groups = new Map();
  [['a', a], ['b', b]].forEach(([side, fit]) => {
    const identity = fit?.phase_identity || {};
    Object.entries(fit?.weight_percent || {}).forEach(([name, value]) => {
      const number = spacegroupOfPhase(name);
      const key = identity[name] || (number == null ? `name:${name}` : `${formulaOfPhase(name)}_${number}`);
      if (!groups.has(key)) groups.set(key, { a: [], b: [] });
      groups.get(key)[side].push({ name, value: finite(value) ? Number(value) : null });
    });
  });
  const byWeight = (x, y) => (y.value ?? -Infinity) - (x.value ?? -Infinity);
  return [...groups].flatMap(([key, group]) => {
    group.a.sort(byWeight);
    group.b.sort(byWeight);
    const size = Math.max(group.a.length, group.b.length);
    return Array.from({ length: size }, (_, i) => {
      const names = [...new Set([group.a[i]?.name, group.b[i]?.name].filter(Boolean))];
      return {
        key: size > 1 ? `${key}#${i + 1}` : key,
        base: formulaOfPhase(names[0]),
        group: key,
        formula: `${formulaOfPhase(names[0])}${size > 1 ? ` #${i + 1}` : ''}`,
        names,
        a: group.a[i]?.value ?? null,
        b: group.b[i]?.value ?? null,
      };
    });
  });
}

/** `R-3c`, `P2₁/c` (from BGMN's `P2_1/c`) or `SG 14` when only the number is known. */
export function formatSpacegroup(symbol, number) {
  if (symbol) return String(symbol).replace(/_(\d)/g, (_, digit) => SUBSCRIPT_DIGITS[Number(digit)]);
  return finite(number) ? `SG ${Number(number)}` : '?';
}

const clip = text => (text.length > MAX_LABEL ? `${text.slice(0, MAX_LABEL - 1)}…` : text);

/**
 * Formulas of a phase list, with the space group appended to a formula that occurs more than once
 * (`Bi2O3 (SG 14), Nb16.8O42, Bi2O3 (SG 15)`). `symbolOf(name)` may supply the symbol.
 */
export function phaseLabels(phases = [], symbolOf = () => null) {
  const formulas = (phases || []).map(formulaOfPhase);
  return (phases || []).map((name, i) => (formulas.filter(f => f === formulas[i]).length > 1
    ? `${formulas[i]} (${formatSpacegroup(symbolOf(name), spacegroupOfPhase(name))})` : formulas[i]));
}

/**
 * `name -> space-group symbol` from phase metadata maps (`{name: {spacegroup_symbol}}`, as in
 * task results and version fits) and symbol maps (`{name: 'R-3c'}`, the `phase_symbols` of
 * version rows and fit-compare sides); `null` when unknown (labels then fall back to `SG n`).
 */
export function phaseSymbolLookup({ metadata = [], symbols = [] } = {}) {
  const map = new Map();
  (metadata || []).forEach(block => Object.entries(block || {}).forEach(([name, value]) => {
    if (value?.spacegroup_symbol && !map.has(name)) map.set(name, value.spacegroup_symbol);
  }));
  (symbols || []).forEach(block => Object.entries(block || {}).forEach(([name, symbol]) => {
    if (symbol && !map.has(name)) map.set(name, symbol);
  }));
  return name => map.get(name) ?? null;
}

/**
 * Label of version row `item`; when another version has the same label (older versions named
 * only the formula they added), the added references' space groups are appended.
 */
export function versionLabel(item, versions = [], symbolOf = () => null) {
  const label = item?.label || '';
  const added = item?.origin?.added || [];
  const twin = label && added.length && (versions || []).some(other => other !== item
    && other.version !== item.version && other.label === label);
  if (!twin) return label;
  const groups = added.map(ref => formatSpacegroup(item.phase_symbols?.[ref] ?? symbolOf(ref), spacegroupOfPhase(ref)))
    .filter(sg => sg !== '?');
  return groups.length ? `${label} (${groups.join(', ')})` : label;
}

/**
 * Label of a version that adds one phase: `Added Fe2O3`, or `Added Fe2O3 (R-3m)` when that
 * formula is already in the displayed fit or in another version (otherwise two versions read
 * the same). `spacegroup` = `{symbol, number}`; `ref` is the added reference name.
 */
export function addedLabel(formula, { symbol = null, number = null, ref = null } = {}, { displayedPhases = [], versions = [] } = {}) {
  const name = String(formula || formulaOfPhase(ref) || '');
  const known = new Set([...(displayedPhases || []),
    ...(versions || []).flatMap(item => [...(item.phases || []), ...(item.origin?.added || [])])].map(formulaOfPhase));
  const clash = known.has(name) || (ref && known.has(formulaOfPhase(ref)));
  const group = clash ? formatSpacegroup(symbol, number ?? spacegroupOfPhase(ref)) : '?';
  return clip(`Added ${name}${group !== '?' ? ` (${group})` : ''}`);
}

/**
 * The evidence object offered for a phase verdict. The API nests it under `verdict.evidence`;
 * a structure-model alternative (same compound, other space group) wins over a phase-set
 * alternative. Either one is a stored candidate with its own fit, so the UI only links to it.
 */
export function alternativeOfVerdict(verdict) {
  const evidence = verdict?.evidence || verdict;
  return evidence?.structure_model_alternative || evidence?.alternative || null;
}

/**
 * Link from a phase verdict's alternative (a stored candidate) to its fit: `{sel, text, title}`,
 * or `null` when there is none or it is the fit already shown (`displayed`). The server's
 * `candidate_number` is a stored index + 1 for results merged into a displayed candidate, so the
 * label comes from the displayed candidates (`Candidate 1 · other reference (cod_…)`).
 */
export function alternativeLink(alternative, allResults = [], displayed = null) {
  const index = alternative?.source_index;
  if (!Number.isInteger(index) || index < 0) return null;
  if (formatSelection(displayed) === index) return null;
  const found = displayedCandidateOf(allResults, index);
  const title = selectionLabel(index, { all_results: allResults }).split(' — ')[0];
  const text = found ? `View candidate ${found.number}${memberSuffix(found.member)}` : `View stored result #${index + 1}`;
  return { sel: index, text, title: `Show ${title}` };
}

/**
 * Quick-refine body for the "Select phases" checklist: adds the checked references that are not in
 * the displayed fit and removes the displayed ones that were unchecked (only references that were
 * offered in the checklist). `null` when nothing changes or nothing would be left.
 */
export function selectedPhasesRequest({ current = [], selected = [], available = [], controls = null, defaultControls = null, sources = {}, symbolOf = () => null }) {
  const have = new Set(current);
  const want = new Set(selected);
  const offered = new Set(available);
  const add = [...want].filter(name => !have.has(name));
  const remove = [...have].filter(name => offered.has(name) && !want.has(name));
  // Refinement controls alone (same phases) are a valid request too.
  const changedControls = controls && JSON.stringify(controls) !== JSON.stringify(defaultControls) ? controls : null;
  if (!add.length && !remove.length && !changedControls) return null;
  if (have.size - remove.length + add.length < 1) return null;
  // Formulas, with the space group when one repeats ("TiO2, Fe2O3 (R-3c), Fe2O3 (R-3m)").
  const names = phaseLabels([...have].filter(name => !remove.includes(name)).concat(add), symbolOf);
  const body = {
    phases: add.map(ref => ({ ref, source: sources[ref] || 'task' })),
    remove,
    origin: 'manual',
    label: clip(add.length || remove.length ? `Selected phases: ${names.join(', ')}` : 'Changed refinement controls'),
  };
  if (changedControls) body.controls = changedControls;
  return body;
}

/**
 * References offered in "Select phases" besides the task's own: the displayed fit's phases (they
 * resolve from the base fit) and COD references added by any version (fetched from the COD cache
 * again). Library references of other versions are left out: their library id is not stored.
 * Returns `[{ref, source}]` without duplicates.
 */
export function versionPhaseOptions(displayedPhases = [], versions = []) {
  const out = new Map();
  (displayedPhases || []).forEach(ref => out.set(ref, 'task'));
  (versions || []).filter(item => item.version > 1).forEach(item => (item.origin?.added || []).forEach(ref => {
    const code = codCodeFromStem(ref);
    if (code && !out.has(ref)) out.set(ref, `cod:${code}`);
  }));
  return [...out].map(([ref, source]) => ({ ref, source }));
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
export function deltaStrip(base, next, symbolOf = () => null) {
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
  const phases = [...new Set([...Object.keys(wa), ...Object.keys(wb)])];
  // A formula that occurs twice (Fe2O3 R-3c and R-3m) gets its space group, as in phaseLabels.
  const labels = phaseLabels(phases, symbolOf);
  for (const [i, phase] of phases.entries()) {
    const a = finite(wa[phase]) ? Number(wa[phase]) : null;
    const b = finite(wb[phase]) ? Number(wb[phase]) : null;
    rows.push({ key: `wt:${phase}`, label: `${labels[i]} wt%`, phase, a, b, digits: 2,
      delta: a !== null && b !== null ? b - a : null, better: false });
  }
  return rows;
}

/**
 * Lead text of a version's delta strip naming what it is compared with: `vs v2:` (the baseline
 * or the version it was made from), `vs best fit:` or `vs candidate 2:`.
 */
export function deltaBaseText(base, allResults = []) {
  const parsed = parseSelection(base);
  if (parsed === null || parsed === 'best') return 'vs best fit:';
  if (typeof parsed === 'number') {
    const found = displayedCandidateOf(allResults, parsed);
    return `vs ${found ? `candidate ${found.number}${memberSuffix(found.member)}` : `stored result #${parsed + 1}`}:`;
  }
  return `vs v${parsed.version}:`;
}

/** Text for a delta row such as `7.10 → 6.20 (−0.90)`. */
export function deltaText(row) {
  const d = row.digits ?? 2;
  const arrow = `${fixed(row.a, d)} → ${fixed(row.b, d)}`;
  if (row.delta === null || row.delta === undefined) return arrow;
  // The sign follows the rounded value: a change below the shown precision is "±0.00", not "−0.00".
  const magnitude = Math.abs(row.delta).toFixed(d);
  const sign = Number(magnitude) === 0 ? '±' : row.delta > 0 ? '+' : '−';
  return `${arrow} (${sign}${magnitude})`;
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

/** True when `sel` is a fast Add-phase version (base phases fixed): a quick check, not a final fit. */
export function isQuickCheck(versions = [], sel) {
  const parsed = parseSelection(sel);
  if (!parsed || typeof parsed !== 'object') return false;
  return (versions || []).some(item => item.version === parsed.version && item.refine_mode === 'fast');
}

/**
 * Quick-refine body that refines every phase of quick check `n` again (its full fit). The quick
 * check is removed once refined, so the label repeats what it did instead of naming it.
 */
export function refineAllRequest(n, versions = []) {
  const quickLabel = (versions || []).find(item => item.version === n)?.label;
  return { base: `version:${n}`, origin: 'refine_all', label: refinedLabel(quickLabel) };
}

/** "Added Fe2O3" -> "Added Fe2O3 · all phases refined"; plain "All phases refined" without one. */
export function refinedLabel(quickLabel) {
  const text = String(quickLabel || '').replace(/\s*·\s*all phases refined$/i, '').trim();
  return text ? `${text} · all phases refined` : 'All phases refined';
}

export const QUICK_CHECK_HINT = 'Quick check — base phases kept fixed; Rwp and wt% are approximate.';

/** Why an action is unavailable on a quick check (adopt, report, export, further changes). */
export const REFINE_ALL_FIRST = 'Refine all phases first';

/**
 * Selection the full fit made from quick check `n` is compared with: the quick check's own base
 * (the quick check itself is removed by the server once refined), mapped through `comparisonBase`.
 */
export function refineAllBase(versions = [], n) {
  const row = (versions || []).find(item => item.version === n);
  const base = formatSelection(row?.origin?.base ?? 'best') ?? 'best';
  return comparisonBase(base, versions, null);
}

/** True when `sel` names a version that is not in the (loaded, non-empty) `versions` list. */
export function versionGone(versions = [], sel) {
  const parsed = parseSelection(sel);
  if (!parsed || typeof parsed !== 'object' || !(versions || []).length) return false;
  return !versions.some(item => item.version === parsed.version);
}

/**
 * Where to go when version `n` disappeared: the full fit that replaced it (`replaces: n`, or a
 * refine_all made from it), otherwise the best fit.
 */
export function replacementOf(versions = [], n) {
  const found = [...(versions || [])].sort((a, b) => b.version - a.version).find(item => item.replaces === n
    || (item.origin?.kind === 'refine_all' && formatSelection(item.origin?.base ?? null) === `version:${n}`));
  return found ? `version:${found.version}` : 'best';
}

/**
 * Message after a finished quick job: a duplicate request (`reused`) only opens the existing
 * version; otherwise the new version is announced with its Rwp.
 */
export function versionReadyText(n, job = {}, rwp = null) {
  if (job?.reused) return `Same fit as ${n === 1 ? 'the best fit' : `v${n}`} — opened it`;
  return `Version v${n} ready${finite(rwp) ? ` (Rwp ${Number(rwp).toFixed(2)} %)` : ''}`;
}

/**
 * Whether reference `ref` is already in the displayed fit (`{label: 'In this fit'}`) or was added
 * by an existing version (`{label: 'In vN', version: N}`, newest first); `null` when it is new.
 */
export function existingPhase(ref, displayedPhases = [], versions = []) {
  if (!ref) return null;
  if ((displayedPhases || []).includes(ref)) return { label: 'In this fit', version: null };
  const found = [...(versions || [])].filter(item => item.version > 1)
    .sort((a, b) => b.version - a.version)
    .find(item => (item.origin?.added || []).includes(ref));
  return found ? { label: `In v${found.version}`, version: found.version } : null;
}

/** Quick-refine body that re-runs the stored automatic result (v1) with today's code. */
export function baselineRequest() {
  return { base: 'best', controls: {}, origin: 'manual', label: BASELINE_LABEL };
}

/** The stored baseline version (v1 re-refined with today's code), or `null`. */
export function baselineVersion(versions = []) {
  return (versions || []).find(item => item.version > 1 && item.label === BASELINE_LABEL) || null;
}

/**
 * Selection used as "the original fit" in comparisons and deltas: the baseline version when it
 * exists (same code as the new version), otherwise the stored automatic result. `self` is the
 * version being compared (the baseline itself is compared with 'best').
 */
export function comparisonBase(sel, versions = [], self = null) {
  const parsed = parseSelection(sel);
  if (parsed !== 'best') return sel;
  const baseline = baselineVersion(versions);
  return baseline && baseline.version !== self ? `version:${baseline.version}` : 'best';
}

/**
 * `comparisonBase` for the delta strip of a version: a base that no longer exists (a replaced
 * quick check, a deleted version) falls back to the best fit instead of a missing version.
 */
export function availableComparisonBase(sel, versions = [], self = null) {
  const wanted = formatSelection(sel ?? 'best') ?? 'best';
  return comparisonBase(versionGone(versions, wanted) ? 'best' : wanted, versions, self);
}

/** True when the first re-refinement should be preceded by a silent baseline job. */
export function needsBaseline(baselineStale, versions = [], body = {}) {
  if (!baselineStale || baselineVersion(versions)) return false;
  if (body?.label === BASELINE_LABEL) return false;
  return parseSelection(body?.base ?? 'best') === 'best';
}

/**
 * Option groups for the fit selector (Best fit / Candidates / Versions); values are selections:
 * 'best', a candidate source index or 'version:n'. Used by the fit modal, the compare window and
 * (with `adoptable`, which leaves out quick checks) the adopted-fit select. A quick check that is
 * the `current` (legacy adopted) choice stays listed there as a readable, disabled option.
 */
export function selectOptionGroups(allResults = [], versions = [], { adoptable = false, current = null } = {}) {
  const currentSel = formatSelection(current);
  const legacy = item => adoptable && item.refine_mode === 'fast';
  const shown = (versions || []).filter(item => item.version > 1 && (!legacy(item) || `version:${item.version}` === currentSel));
  // A saved choice whose version was deleted stays readable instead of showing `version:N`.
  const gone = adoptable && versionGone(versions, currentSel)
    ? [{ value: currentSel, label: `${selectionLabel(currentSel, { versions })} — adopt another fit`, disabled: true }] : [];
  return [
    { label: 'Best fit', options: [{ value: 'best', label: 'Best fit' }] },
    { label: 'Candidates', options: (allResults || []).flatMap((r, i) => [
      { value: r.source_index ?? i, label: `Candidate ${i + 1} — Rwp ${r.rwp}%` },
      // the same phases refined with another database record, right after their candidate
      ...referenceVariants(r).map(m => ({
        value: m.source_index,
        label: `${mergedLabel(i + 1, m)} — Rwp ${m.rwp}%`,
      })),
    ]) },
    ...(shown.length ? [{ label: 'Versions',
      options: shown.map(item => (legacy(item)
        ? { value: `version:${item.version}`, label: `v${item.version} (quick check)`, disabled: true }
        : { value: `version:${item.version}`,
          label: `v${item.version} — ${versionLabel(item, versions) || 'Re-refinement'}${finite(item.rwp) ? ` — Rwp ${Number(item.rwp).toFixed(2)}%` : ''}` })) }] : []),
    ...(gone.length ? [{ label: 'Deleted', options: gone }] : []),
  ];
}

/** Tag text for the results list / review panel. `null` for 'best' and unset. */
export function adoptedTag(selection) {
  const parsed = parseSelection(selection);
  if (parsed === null || parsed === 'best') return null;
  if (typeof parsed === 'number') return `candidate ${parsed + 1} adopted`;
  return `v${parsed.version} adopted`;
}

function durationText(seconds, min = 1) {
  const s = Math.max(min, Math.round(Number(seconds) || 0));
  if (s < 90) return `${s} s`;
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

/** Rounded total for an estimate: `about 40 s` / `about 3 min`. */
function aboutText(seconds) {
  const s = Math.max(1, Math.round(seconds));
  return s < 90 ? `about ${s} s` : `about ${Math.round(s / 60)} min`;
}

/** `2026-10-25 09:02, 51 min` for the run row of a task (`start` as stored, `runtime` in s). */
export function runText(start, runtime) {
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(String(start || ''));
  const when = match ? `${match[1]} ${match[2]}` : String(start || '');
  if (!finite(runtime)) return when || '—';
  const s = Math.max(0, Math.round(Number(runtime)));
  const length = s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min`
    : `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
  return when ? `${when}, ${length}` : length;
}

/** Version label in the progressive form while the job runs ("Added X" → "Adding X"). */
export function progressLabel(label) {
  const text = String(label || '');
  // "Added X · all phases refined" is the full fit of a quick check: nothing is being added.
  const refined = text.match(/^(.*?)\s*·\s*all phases refined$/i);
  if (refined) return refined[1] ? `Refining all phases (${refined[1]})` : 'Refining all phases';
  if (/^all phases refined$/i.test(text)) return 'Refining all phases';
  for (const [done, doing] of [['Added ', 'Adding '], ['Removed ', 'Removing '], ['Swapped ', 'Swapping ']]) {
    if (text.startsWith(done)) return doing + text.slice(done.length);
  }
  return text;
}

/**
 * Queue text or total-time estimate of a quick job record. Queued: the job in front and how long
 * it has run. Running: nothing without a comparable estimate (`eta_s` null), `about N s in total`
 * while within it and `taking longer than usual` once `elapsed_s` exceeds it. Never a countdown.
 */
export function etaText(job) {
  if (!job) return '';
  if (job.status === 'queued') {
    // `ahead` = jobs in front of this one; older records only have the 1-based `position`.
    const ahead = finite(job.ahead) ? Number(job.ahead) : Math.max(0, Number(job.position || 0) - 1);
    if (ahead <= 0) return 'Starting…';
    if (!job.ahead_label) return `Queued (${ahead} ahead)`;
    // Name the job in front (often the silent baseline re-refinement) and how long it has run.
    const ran = finite(job.ahead_elapsed_s) && Number(job.ahead_elapsed_s) > 0 ? ` (running ${durationText(job.ahead_elapsed_s)})` : '';
    const more = ahead > 1 ? ` + ${ahead - 1} more` : '';
    return `Waiting for ${job.ahead_label}${ran}${more}`;
  }
  if (job.status !== 'running' || !finite(job.eta_s) || Number(job.eta_s) <= 0) return '';
  const elapsed = finite(job.elapsed_s) ? Number(job.elapsed_s) : 0;
  if (elapsed > Number(job.eta_s)) return 'taking longer than usual';
  return `${aboutText(job.eta_s)} in total`;
}

/**
 * What the progress strip shows for a quick job, with exactly one time value per state.
 * `elapsed` is the live running time in seconds. Returns `{stage, time, note, percent, basis}`:
 * `time` is empty while queued (the queue text carries the only time), `percent` is `null` for
 * the indeterminate bar (no estimate, or past it) and `basis` explains the estimate.
 */
export function progressView(job, elapsed = 0) {
  if (!job) return null;
  if (job.status === 'starting') return { stage: 'Starting…', time: '', note: '', percent: null, basis: null };
  if (job.status === 'queued') return { stage: etaText(job) || 'Queued', time: '', note: '', percent: null, basis: null };
  const eta = finite(job.eta_s) && Number(job.eta_s) > 0 ? Number(job.eta_s) : null;
  const n = finite(job.eta_n) ? Number(job.eta_n) : 0;
  return {
    stage: job.stage || 'Working',
    time: durationText(elapsed, 0),
    note: etaText({ ...job, elapsed_s: elapsed }),
    percent: eta !== null && elapsed <= eta ? Math.round((elapsed / eta) * 100) : null,
    basis: eta !== null && n > 0 ? `Estimate from ${n} similar earlier run${n === 1 ? '' : 's'}` : null,
  };
}
