import { record } from '../../src/core/read.ts';
import { dashboardPagesCase } from './dashboardFixture.ts';

/** The body of the landed r17b recording `<family>.<case>` as a plain object (a fresh deep copy). */
export function r17bBody(path: string): Record<string, unknown> {
  const body = record(dashboardPagesCase(path).body);
  if (body === undefined) throw new Error(`The landed r17b case ${path} has no object body`);
  return Object.fromEntries(Object.entries(body));
}

/** A member of a recorded body as a list of plain objects (`executions.items`, `events`, ...). */
export function rowsOf(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new Error('Expected a list');
  return value.map((row: unknown) => {
    const found = record(row);
    if (found === undefined) throw new Error('Expected an object row');
    return Object.fromEntries(Object.entries(found));
  });
}

/** A member of a recorded body as a plain object. */
export function objectOf(value: unknown): Record<string, unknown> {
  const found = record(value);
  if (found === undefined) throw new Error('Expected an object');
  return Object.fromEntries(Object.entries(found));
}

/** A copy of `object` without the member `key` (a changed recording that drops a member). */
export function without(object: Record<string, unknown>, key: string): Record<string, unknown> {
  return Object.fromEntries(Object.entries(object).filter(([name]) => name !== key));
}
