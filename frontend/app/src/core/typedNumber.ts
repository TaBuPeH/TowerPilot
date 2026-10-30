/**
 * A number as a person types it into a field: an optional minus, digits and an optional
 * fraction, nothing else (no exponent, no "Infinity", no blank). The one parser every editor
 * uses (settings modules, run controls, shopping queues); each caller adds its own rules.
 */
const DECIMAL = /^-?\d+(\.\d+)?$/u;

export function typedDecimal(
  raw: string,
): { readonly value: number } | { readonly reason: 'empty' | 'not_a_number' } {
  const trimmed = raw.trim();
  if (trimmed === '') return { reason: 'empty' };
  if (!DECIMAL.test(trimmed)) return { reason: 'not_a_number' };
  const value = Number(trimmed);
  return Number.isFinite(value) ? { value } : { reason: 'not_a_number' };
}
