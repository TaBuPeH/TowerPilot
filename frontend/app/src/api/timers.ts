/** Timers and the browser clock, injected so tests drive time with fake timers. */
export type TimerHandle = ReturnType<typeof setTimeout>;

export interface Timers {
  /** Browser clock, ms. Only intervals between two readings are ever used, never the wall time. */
  readonly now: () => number;
  readonly setTimeout: (callback: () => void, ms: number) => TimerHandle;
  readonly clearTimeout: (handle: TimerHandle) => void;
}

export const browserTimers: Timers = {
  now: () => Date.now(),
  setTimeout: (callback, ms) => setTimeout(callback, ms),
  clearTimeout: (handle) => {
    clearTimeout(handle);
  },
};

/** A fresh random id (record ids and idempotency keys: 36 characters of [0-9a-f-]). */
export function randomId(): string {
  return crypto.randomUUID();
}
