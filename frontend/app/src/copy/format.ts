/**
 * Formatting shared by the catalogue's modules (one rule per fact). Whole
 * seconds as "45 s", "12 m", "12 m 05 s"; used where a number of seconds is
 * shown as a readable duration.
 */
/**
 * A server time (epoch seconds) as the date and time it names, en-US in this browser's time
 * zone; never compared with this browser's clock.
 */
export function serverDateText(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString('en-US');
}

/**
 * A moment (epoch milliseconds) as the UTC instant it names, e.g. 2026-10-24T00:00:00.000Z, or null
 * when a date cannot hold it. Used where a recorded or read time is shown exactly.
 */
export function isoText(ms: number): string | null {
  return Number.isFinite(ms) && Math.abs(ms) <= 8.64e15 ? new Date(ms).toISOString() : null;
}

export function durationText(seconds: number): string {
  const n = Math.max(0, Math.floor(seconds));
  if (n < 60) return `${String(n)} s`;
  const rest = n % 60;
  return rest === 0
    ? `${String(Math.floor(n / 60))} m`
    : `${String(Math.floor(n / 60))} m ${String(rest).padStart(2, '0')} s`;
}

/**
 * A duration that may hold a fraction of a second: whole seconds as `durationText`, a fraction
 * as seconds to one decimal ("2.5 s"). The Runs battle details show an appearance gap this way
 * (moved unchanged from copy/runs.ts, A2-7 AC2).
 */
export function fractionalDurationText(seconds: number): string {
  return Number.isInteger(seconds) ? durationText(seconds) : `${seconds.toFixed(1)} s`;
}
