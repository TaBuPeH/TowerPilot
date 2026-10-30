/**
 * Text that comes from the server: names (accounts, runs, emulators, upgrades),
 * messages (`message`, `error`, guard texts), details (command lines, detector
 * reports) and ids. It is data, never copy: it is not a CopyText, not a
 * CopyNode, and only src/ui/ServerValue.tsx renders it (dashboard-spec 6.3).
 *
 * A ServerText is a string with a type-only brand made only by `fromServer`:
 * a plain string or catalogue text is not one, and lint refuses `as`
 * outside this file. Only store selectors call `fromServer`
 * (no-restricted-imports).
 */
declare const serverBrand: unique symbol;

/** name and id: one-line labels; message and detail: kept exactly as sent. */
export type ServerTextKind = 'name' | 'message' | 'detail' | 'id';

/** Who is told about each ServerText's displayed text: the render check in tests. */
const observers = new Set<(text: string) => void>();

/** Listen to the displayed text of every ServerText made from now on. Returns the function that stops listening. */
export function observeServerText(listener: (text: string) => void): () => void {
  observers.add(listener);
  return () => {
    observers.delete(listener);
  };
}

/** The type of server text of one kind. */
export type ServerText<K extends ServerTextKind = ServerTextKind> = string & {
  readonly [serverBrand]: K;
};

/**
 * Control characters other than tab, line feed and carriage return are shown
 * as the visible sequence \u{hex} instead of being drawn invisibly. Nothing is
 * removed or cut; every other character, bidirectional controls included, is
 * kept (ServerValue isolates them in a <bdi>).
 */
function visible(raw: string): string {
  let out = '';
  for (const char of raw) {
    const code = char.codePointAt(0) ?? 0;
    const control =
      (code < 0x20 && code !== 0x09 && code !== 0x0a && code !== 0x0d) ||
      (code >= 0x7f && code <= 0x9f);
    out += control ? `\\u{${code.toString(16)}}` : char;
  }
  return out;
}

/**
 * Server text from a raw contract value, or null when there is none. A
 * message or detail is shown exactly as sent (line breaks and length kept); a
 * name or id is one line: whitespace runs shown as one space, ends trimmed.
 */
export function fromServer<K extends ServerTextKind>(kind: K, raw: unknown): ServerText<K> | null {
  if (typeof raw !== 'string') {
    return null;
  }
  const kept = visible(raw);
  const shown = kind === 'name' || kind === 'id' ? kept.replace(/\s+/gu, ' ').trim() : kept;
  if (shown.trim() === '') {
    return null;
  }
  for (const listener of observers) {
    listener(shown);
  }
  return shown as ServerText<K>;
}

/** The text a ServerText shows. */
export function serverTextContent(value: ServerText): string {
  return value;
}
