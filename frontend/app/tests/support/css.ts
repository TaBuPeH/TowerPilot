import { readdirSync } from 'node:fs';
import path from 'node:path';

/** Every `.css` file under a folder, depth first. */
export function stylesheets(folder: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) return stylesheets(full);
    return entry.name.endsWith('.css') ? [full] : [];
  });
}

/** A CSS identifier with its escapes resolved (`c\6f ntent`, `c\ontent`), lower-cased. */
export function identifier(raw: string): string {
  return raw
    .replace(/\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?/g, (_escape, hex: string) =>
      String.fromCodePoint(Number.parseInt(hex, 16)),
    )
    .replace(/\\(.)/g, '$1')
    .toLowerCase();
}
