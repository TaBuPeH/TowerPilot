import { describe, expect, it } from 'vitest';
import { DEFAULT_ROUTE, hrefFor, matchHash } from '../../../src/app/router.ts';

describe('matchHash', () => {
  it.each(['', '#', '#/'])('opens the default page for %j', (hash) => {
    expect(matchHash(hash)).toEqual({ kind: 'page', id: DEFAULT_ROUTE, target: null });
  });

  it.each(['#dashboard', '#/dashboard'])('matches %s', (hash) => {
    expect(matchHash(hash)).toEqual({ kind: 'page', id: 'dashboard', target: null });
  });

  it.each([
    '#nowhere',
    '#dashboard/extra',
    '#Dashboard',
    '#runs/other',
    '#runs/saved',
    '#runs/saved/',
    '#runs/saved/%E0%A4%A',
    '#runs/live/extra',
  ])('reports %s as not found', (hash) => {
    expect(matchHash(hash)).toEqual({ kind: 'not_found', hash });
  });

  it('RUN-12 matches the Runs page, its live editor and one run, by its encoded name', () => {
    expect(matchHash('#runs')).toEqual({ kind: 'page', id: 'runs', target: null });
    expect(matchHash('#runs/live')).toEqual({
      kind: 'page',
      id: 'runs',
      target: { kind: 'live' },
    });
    expect(matchHash('#runs/saved/bp_farm')).toEqual({
      kind: 'page',
      id: 'runs',
      target: { kind: 'saved', run: 'bp_farm' },
    });
    expect(matchHash(hrefFor('runs', { kind: 'saved', run: 'a b/c' }))).toEqual({
      kind: 'page',
      id: 'runs',
      target: { kind: 'saved', run: 'a b/c' },
    });
  });

  it('T4 a page state after `?` never decides the route (#runtime?kind=x)', () => {
    expect(matchHash('#dashboard?limit=5&kind=orbit_sweep')).toEqual({
      kind: 'page',
      id: 'dashboard',
      target: null,
    });
    expect(matchHash('#nowhere?x=1')).toMatchObject({ kind: 'not_found' });
  });

  it('links to a route by its hash', () => {
    expect(hrefFor('dashboard')).toBe('#dashboard');
    expect(hrefFor('runs', { kind: 'live' })).toBe('#runs/live');
    expect(hrefFor('runs', { kind: 'saved', run: 'bp_farm' })).toBe('#runs/saved/bp_farm');
  });
});
