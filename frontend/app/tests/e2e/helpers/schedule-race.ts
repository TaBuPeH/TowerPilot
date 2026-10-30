import { contextFromConfig } from '../../../src/api/context.ts';
import { readFileSync } from 'node:fs';
import { expect, type BrowserContext, type Route } from '@playwright/test';
import { entries, finite, own, ownPath } from '../../../src/core/read.ts';

const samples: unknown = JSON.parse(
  readFileSync(new URL('../../fixtures/schedule-race-samples.json', import.meta.url), 'utf8'),
);

/** Replay real route answers, comparing the requests against one shared revision. */
export async function scheduleRace(context: BrowserContext, first: 'schedule' | 'runs') {
  const sample = (name: string) => {
    const value = ownPath(samples, 'cases', `${first}_first`, name);
    const status = finite(own(value, 'status'));
    if (status === undefined) throw new Error(`Missing recorded race case ${name}`);
    return {
      status,
      body: own(value, 'body'),
      request: own(value, 'request'),
    };
  };
  let stage = 0;
  let releaseFirst: (() => void) | undefined;
  let firstStarted: (() => void) | undefined;
  const firstPending = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  const started = new Promise<void>((resolve) => {
    firstStarted = resolve;
  });
  const writes: { readonly path: string; readonly body: unknown; readonly status: number }[] = [];
  const current = (prefix: string) =>
    sample(`${prefix}_${stage === 0 ? 'initial' : stage === 1 ? 'after_first' : 'final'}`);
  const reply = (route: Route, value: ReturnType<typeof sample>) =>
    route.fulfill({ status: value.status, json: value.body });

  await context.route('**/api/config', (route) => reply(route, sample('config')));
  await context.route('**/api/profile-src/mine', (route) => reply(route, current('profile')));
  await context.route('**/api/run-controls?*', (route) => reply(route, current('run')));
  const expectedBody = (value: ReturnType<typeof sample>) => {
    const body = own(value.request, 'body');
    if (own(value.request, 'path') !== '/api/profile-patch') return body;
    const named = contextFromConfig(sample('config').body);
    if (named === null) throw new Error('Missing recorded race context');
    // Modified recorded request: preserve the race's exact value/revision and add the
    // F6-14b runtime fence. Run-controls requests keep their separate recorded contract.
    return { ...Object.fromEntries(entries(body)), expected_context: named };
  };
  const write = async (route: Route) => {
    const body: unknown = route.request().postDataJSON();
    const path = new URL(route.request().url()).pathname;
    const revision = own(current('profile').body, 'revision');
    if (own(body, 'expected_revision') !== revision) {
      const refused = sample('second_stale');
      expect(path).toBe(own(refused.request, 'path'));
      expect(body).toEqual(expectedBody(refused));
      writes.push({ path, body, status: refused.status });
      await reply(route, refused);
      return;
    }
    const saved = sample(stage === 0 ? 'first_saved' : 'second_saved_after_read');
    expect(path).toBe(own(saved.request, 'path'));
    expect(body).toEqual(expectedBody(saved));
    writes.push({ path, body, status: saved.status });
    stage += 1;
    if (stage === 1) {
      firstStarted?.();
      await firstPending;
    }
    await reply(route, saved);
  };
  await context.route('**/api/profile-patch', write);
  await context.route('**/api/run-controls', write);
  return {
    writes,
    started,
    releaseFirst: () => releaseFirst?.(),
    finalProfile: sample('profile_final').body,
    finalRun: sample('run_final').body,
  };
}
