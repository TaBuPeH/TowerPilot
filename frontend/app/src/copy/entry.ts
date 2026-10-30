import type { CopyEntry, CopyText } from './text.ts';

/**
 * The only constructor of CopyText, used by the catalogue's own modules
 * (src/copy/catalogue.ts and src/copy/dashboard/*.ts). Lint refuses importing
 * this file from outside src/copy/, so wording is chosen only in the catalogue.
 *
 * Who is told about each CopyText made: nobody by default, so nothing
 * accumulates in the running app. The runtime render check
 * (tests/unit/copy/renderedText.ts) listens, so formatter output such as
 * `elapsedSeconds(12)` counts as catalogue text and anything else does not.
 */
const observers = new Set<(text: string) => void>();

/** Listen to every CopyText made from now on. Returns the function that stops listening. */
export function observeCopy(listener: (text: string) => void): () => void {
  observers.add(listener);
  return () => {
    observers.delete(listener);
  };
}

/** Catalogue text from a string chosen in src/copy/. */
export function made(text: string): CopyText {
  for (const listener of observers) {
    listener(text);
  }
  return text as CopyText;
}

/** One catalogue entry: the short line, and an optional "why" behind a disclosure. */
export function entry(short: string, why?: string): CopyEntry {
  if (short.trim() === '') {
    throw new Error('A copy entry needs a short line');
  }
  if (why === undefined) {
    return Object.freeze({ short: made(short) });
  }
  if (why.trim() === '') {
    throw new Error('A "why" line, when present, must say something');
  }
  return Object.freeze({ short: made(short), why: made(why) });
}
