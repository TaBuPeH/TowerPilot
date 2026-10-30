// @vitest-environment node
import { APP } from '../../support/paths.ts';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { satisfies } from 'semver';
import { describe, expect, it } from 'vitest';

/**
 * R3-10: the Node range the app declares must be exactly the versions every
 * locked dependency accepts - no wider (Node 23 and 25 were admitted before),
 * no narrower.
 */

function json(file: string): unknown {
  return JSON.parse(readFileSync(path.join(APP, file), 'utf8'));
}

function field(value: unknown, key: string): unknown {
  return typeof value === 'object' && value !== null ? Reflect.get(value, key) : undefined;
}

const declared = field(field(json('package.json'), 'engines'), 'node');
const lockPackages = field(json('package-lock.json'), 'packages');
const ranges = Object.entries(
  typeof lockPackages === 'object' && lockPackages !== null ? lockPackages : {},
)
  .filter(([name, entry]) => name !== '' && field(entry, 'optional') !== true)
  .map(([name, entry]) => [name, field(field(entry, 'engines'), 'node')] as const)
  .filter((pair): pair is readonly [string, string] => typeof pair[1] === 'string');

const CANDIDATES: string[] = [];
for (let major = 18; major <= 32; major += 1) {
  for (const minor of [0, 1, 9, 12, 13, 14, 18, 19, 22, 40]) {
    CANDIDATES.push(`${String(major)}.${String(minor)}.0`);
  }
}

function acceptedByLock(version: string): boolean {
  return ranges.every(([, range]) => satisfies(version, range));
}

describe('engines.node', () => {
  it('is declared and the lock carries engine ranges to check it against', () => {
    expect(typeof declared).toBe('string');
    expect(ranges.length).toBeGreaterThan(100);
  });

  it.each(CANDIDATES)('agrees with the locked dependencies on Node %s', (version) => {
    expect(satisfies(version, String(declared))).toBe(acceptedByLock(version));
  });

  it('admits the tested floor and refuses the odd releases the dependencies reject', () => {
    expect(satisfies('22.13.0', String(declared))).toBe(true);
    for (const version of ['22.12.0', '23.0.0', '25.0.0']) {
      expect(satisfies(version, String(declared))).toBe(false);
    }
  });
});
