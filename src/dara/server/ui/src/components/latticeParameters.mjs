const GREEK_NAMES = { alpha: 'α', beta: 'β', gamma: 'γ' };

export function crystalSystemFromSpaceGroup(spaceGroup) {
  if (!spaceGroup) return undefined;
  if (spaceGroup <= 2) return 'triclinic';
  if (spaceGroup <= 15) return 'monoclinic';
  if (spaceGroup <= 74) return 'orthorhombic';
  if (spaceGroup <= 142) return 'tetragonal';
  if (spaceGroup <= 167) return 'trigonal';
  if (spaceGroup <= 194) return 'hexagonal';
  return 'cubic';
}

function reported(names, parameters) {
  return names
    .filter(name => parameters?.[name])
    .map(name => ({ names: [name], parameter: parameters[name], constrained: false }));
}

function fixed(names, value) {
  return { names, parameter: { value, esd: null, unit: 'deg' }, constrained: true, fixed: true };
}

function equal(names, parameter) {
  return parameter ? { names, parameter, constrained: names.length > 1 } : null;
}

/** Return conventional-cell rows while marking symmetry-derived equalities. */
export function latticeParameterRows(parameters = {}, spaceGroup) {
  const system = crystalSystemFromSpaceGroup(Number(spaceGroup));
  const rows = [];
  const add = (...items) => rows.push(...items.filter(Boolean));

  if (system === 'cubic') {
    add(equal(['a', 'b', 'c'], parameters.a || parameters.b || parameters.c));
    add(fixed(['alpha', 'beta', 'gamma'], 90));
  } else if (system === 'tetragonal') {
    add(equal(['a', 'b'], parameters.a || parameters.b));
    add(...reported(['c'], parameters));
    add(fixed(['alpha', 'beta', 'gamma'], 90));
  } else if (system === 'trigonal' || system === 'hexagonal') {
    add(equal(['a', 'b'], parameters.a || parameters.b));
    add(...reported(['c'], parameters));
    add(fixed(['alpha', 'beta'], 90), fixed(['gamma'], 120));
  } else if (system === 'orthorhombic') {
    add(...reported(['a', 'b', 'c'], parameters));
    add(fixed(['alpha', 'beta', 'gamma'], 90));
  } else if (system === 'monoclinic') {
    add(...reported(['a', 'b', 'c'], parameters));
    add(fixed(['alpha', 'gamma'], 90));
    add(...reported(['beta'], parameters));
  } else {
    add(...reported(['a', 'b', 'c', 'alpha', 'beta', 'gamma'], parameters));
  }

  return rows.map(row => ({
    ...row,
    label: row.names.map(name => GREEK_NAMES[name] || name).join(' = '),
  }));
}

export function formatParameterWithEsd(parameter) {
  if (!parameter || !Number.isFinite(Number(parameter.value))) return null;
  const value = Number(parameter.value);
  const esd = parameter.esd == null ? null : Math.abs(Number(parameter.esd));
  let valueText;
  let esdText = null;

  if (Number.isFinite(esd) && esd > 0) {
    const decimals = Math.max(0, Math.min(8, -Math.floor(Math.log10(esd)) + 1));
    valueText = value.toFixed(decimals);
    esdText = esd.toFixed(decimals);
  } else if (parameter.unit === 'deg' && Number.isInteger(value)) {
    valueText = String(value);
  } else {
    valueText = value.toFixed(5);
  }

  const unitText = parameter.unit === 'deg' ? '°' : ` ${parameter.unit}`;
  return `${valueText}${esdText === null ? '' : ` ± ${esdText}`}${unitText}`;
}
