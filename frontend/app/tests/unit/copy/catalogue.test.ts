import { describe, expect, it } from 'vitest';
import * as catalogueModule from '../../../src/copy/catalogue.ts';
import { copy, documentTitle, elapsedSeconds, observeCopy } from '../../../src/copy/catalogue.ts';
import { equipment } from '../../../src/copy/equipment.ts';
import * as textModule from '../../../src/copy/text.ts';

/** Every {short, why?} entry of the catalogue, read as plain data (no CopyText is made here). */
function entries(
  node: unknown,
  path: string[] = [],
): [string, Readonly<Record<string, unknown>>][] {
  if (typeof node !== 'object' || node === null) {
    return [];
  }
  if ('short' in node) {
    return [[path.join('.'), Object.fromEntries(Object.entries(node))]];
  }
  return Object.entries(node).flatMap(([key, child]) => entries(child, [...path, key]));
}

function text(value: unknown): string {
  expect(typeof value).toBe('string');
  return String(value);
}

describe('copy catalogue', () => {
  const all = entries(copy);

  it('has entries', () => {
    expect(all.length).toBeGreaterThan(0);
  });

  it.each(all)('%s has the {short, why?} shape with real text', (_path, value) => {
    const why = value['why'];
    expect(Object.keys(value).sort()).toEqual(why === undefined ? ['short'] : ['short', 'why']);
    expect(text(value['short']).trim()).not.toBe('');
    if (why !== undefined) {
      expect(text(why).trim()).not.toBe('');
      expect(why).not.toBe(value['short']);
    }
  });

  it('says the Dashboard is being rebuilt', () => {
    expect(copy.dashboard.placeholder.short).toBe('Dashboard (rebuild in progress)');
  });

  it('states that creating loadouts for this account is unavailable', () => {
    expect(equipment.empty.short).toBe(
      'No saved loadouts. Creating loadouts for this account is not available yet.',
    );
  });

  it('keeps the CopyText constructor private to the catalogue', () => {
    expect(Object.keys(textModule)).toEqual([]);
    expect(Object.keys(catalogueModule).sort()).toEqual([
      'copy',
      'documentTitle',
      'elapsedSeconds',
      'observeCopy',
    ]);
  });

  it('is frozen', () => {
    expect(Object.isFrozen(copy.dashboard.placeholder)).toBe(true);
    expect(Object.isFrozen(copy.routes.dashboard)).toBe(true);
  });

  it('builds the tab title from the page name', () => {
    expect(documentTitle(copy.routes.dashboard)).toBe('Dashboard - Tower Pilot');
  });

  it.each([
    [0, '0 seconds'],
    [1, '1 second'],
    [1.4, '1 second'],
    [12, '12 seconds'],
    [-3, '0 seconds'],
  ])('formats %s elapsed seconds as %j', (seconds, expected) => {
    expect(elapsedSeconds(seconds)).toBe(expected);
  });

  it('tells a listener about every CopyText it makes, and nobody once it stops', () => {
    const heard: string[] = [];
    const stop = observeCopy((text) => heard.push(text));
    try {
      documentTitle(copy.app.name);
      elapsedSeconds(97);
    } finally {
      stop();
    }
    elapsedSeconds(98);
    expect(heard).toEqual(['Tower Pilot - Tower Pilot', '97 seconds']);
  });
});
