/**
 * A fact the interface may state, or the reason it cannot.
 *
 * Every value the Dashboard shows about the game, the emulator or a process is
 * a Fact: either `known` with the time it was observed, or `unknown` with a
 * reason. There is no third shape, so a missing or malformed reading can never
 * be drawn as if it were a real one.
 *
 * Times are seconds since the Unix epoch on the server clock, the unit the
 * backend reports (`server_time`, `observed_at`).
 */
export type Fact<T> =
  | { readonly kind: 'unknown'; readonly reason: string }
  | { readonly kind: 'known'; readonly value: T; readonly observedAt: number };

/** A fact that cannot be stated, and why. */
export function unknownFact(reason: string): Fact<never> {
  return Object.freeze({ kind: 'unknown', reason: reason.trim() === '' ? 'unspecified' : reason });
}

/**
 * A fact observed at `observedAt`. A non-finite observation time is not an
 * observation, so it yields an unknown fact instead of a known one.
 */
export function knownFact<T>(value: T, observedAt: number): Fact<T> {
  if (!Number.isFinite(observedAt)) {
    return unknownFact('observed_at_invalid');
  }
  return Object.freeze({ kind: 'known', value, observedAt });
}

/** The value of a known fact, or `fallback` for an unknown one. */
export function factValueOr<T, F>(fact: Fact<T>, fallback: F): T | F {
  return fact.kind === 'known' ? fact.value : fallback;
}
