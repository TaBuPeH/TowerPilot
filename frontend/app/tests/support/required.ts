/** Narrow a required test subject, failing at its lookup rather than its later use. */
export function required<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error('Missing required test subject');
  return value;
}
