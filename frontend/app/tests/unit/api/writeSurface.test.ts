// @vitest-environment node
import { SRC } from '../../support/paths.ts';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The write surface of the app, read from the source (done criterion R8.1):
 * `fetch` is called in exactly one place (api/transport.ts), and a POST is
 * built only by the client's `postEndpoint`, which only the write-intent
 * machine (a person's click or explicit resend) and the C-20 diagnostics call.
 */

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? files(full) : /\.tsx?$/.test(name) ? [full] : [];
  });
}

function sites(pattern: RegExp): string[] {
  return files(SRC)
    .filter((file) => pattern.test(readFileSync(file, 'utf8')))
    .map((file) => path.relative(SRC, file).split(path.sep).join('/'))
    .sort();
}

describe('the write surface', () => {
  it('calls fetch in exactly one module', () => {
    expect(sites(/\bfetch\(/)).toEqual(['api/transport.ts']);
  });

  it('builds a POST only in the client, called only by the intent machine and the diagnostics', () => {
    expect(sites(/method: 'POST'/)).toEqual(['api/client.ts']);
    expect(sites(/postEndpoint\(/)).toEqual([
      'api/client.ts',
      'api/diagnostics.ts',
      'api/intents/machine.ts',
    ]);
  });

  it('sends a write record only from send and resend (no timer, read or restore calls post)', () => {
    const machine = readFileSync(path.join(SRC, 'api/intents/machine.ts'), 'utf8');
    const calls = [...machine.matchAll(/\bpost\((\w+)\)/g)].map((match) => match[1]);
    expect(calls).toEqual(['item', 'resent']);
  });
});
