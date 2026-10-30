import { entries, list } from './read.ts';

/** Key order is not meaning: compare objects by sorted keys, lists in order. */
function canonical(value: unknown): unknown {
  const items = list(value);
  if (items !== undefined) return items.map(canonical);
  if (typeof value !== 'object' || value === null) return value;
  return Object.fromEntries(
    [...entries(value)].sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, v]) => [k, canonical(v)]),
  );
}

export function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
}
