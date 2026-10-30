import { expect, it } from 'vitest';
import {
  dashboardBody,
  dashboardCase,
  dashboardCaseNames,
} from '../../support/dashboardFixture.ts';

const fixtureRuns: Readonly<Record<string, Readonly<Record<string, readonly [string, number]>>>> = {
  combo_entry: {
    bp_farm: ['images_missing', 1],
    combo: ['images_missing', 1],
  },
  defined_and_undefined_with_labels: {
    combo: ['ready', 0],
    farm: ['ready', 0],
    quest_chronofield: ['images_missing', 1],
  },
  not_ready_config_row: {
    bp_farm: ['not_ready', 0],
  },
  not_ready_image_row: {
    bp_farm: ['images_missing', 1],
    bp_other: ['ready', 0],
  },
  not_ready_module_restoration: {
    bp_farm: ['not_ready', 0],
  },
  readiness_check_raised: {
    bp_farm: ['readiness_check_failed', 0],
  },
  ready: {
    bp_farm: ['ready', 0],
  },
  setup_required_apply_scan: {
    bp_farm: ['needs_setup', 0],
  },
  setup_required_battle_setup: {
    bp_farm: ['needs_setup', 0],
  },
  setup_required_edit_run: {
    bp_farm: ['needs_setup', 0],
  },
};
const fixtureReadiness: Readonly<Record<string, readonly [string, number]>> = {
  context_other_than_the_page: ['ready', 0],
  not_ready_second_wind: ['images_missing', 1],
  not_ready_with_message_and_next_action: ['images_missing', 1],
  not_ready_without_message: ['images_missing', 1],
  ready_with_context: ['ready', 0],
  setup_required: ['needs_setup', 0],
  unknown_preset: ['readiness_unknown', 0],
};
const fixturePreviews = [
  'ad_gems_on_floating_off',
  'both_off',
  'combo_is_no_blueprint',
  'unknown_blueprint',
];
it('F-039 fixture runs and readiness inventories are exhaustive', () => {
  expect(dashboardCaseNames('runs')).toEqual(Object.keys(fixtureRuns).sort());
  expect(dashboardCaseNames('readiness')).toEqual(Object.keys(fixtureReadiness).sort());
  expect(dashboardCaseNames('run_controls')).toEqual(fixturePreviews);
});
it.each(Object.entries(fixtureRuns))(
  'F-039 fixture runs.%s preserves image counts and setup cases',
  (name, expected) => {
    const body = dashboardBody(`runs.${name}`);
    expect(
      entries(own(body, 'readiness'))
        .map(([key]) => key)
        .sort(),
    ).toEqual(Object.keys(expected).sort());
    for (const [run, [line, imageCount]] of Object.entries(expected))
      expect(readiness(own(own(body, 'readiness'), run))).toMatchObject({ line, imageCount });
    const selected = selectRun(body, null);
    expect(selected).not.toBeNull();
    expect(runDefined(own(own(body, 'runs'), selected ?? ''))).toBe(true);
  },
);
it.each(Object.entries(fixtureReadiness))(
  'F-039 F-040 fixture readiness.%s uses the direct-check shape and page context',
  (name, [line, imageCount]) => {
    const c = dashboardCase(`readiness.${name}`);
    const page = own(dashboardBody('readiness.ready_with_context'), 'context');
    expect(readiness(c.body, 'check')).toMatchObject({ line, imageCount });
    expect(readiness(c.body, 'check').diagnostics).toEqual(own(c.body, 'diagnostics'));
    expect(readiness(c.body, 'check').error).toEqual(own(c.body, 'error'));
    const gate = startGate(
      setup(),
      c.status === 200 ? answer(c.body) : { kind: 'failed' },
      page,
      'bp_farm',
    );
    expect(gate.enabled).toBe(name === 'ready_with_context');
    if (name === 'context_other_than_the_page') expect(gate.reason).toBe('context_changed');
    if (name === 'setup_required') {
      expect(own(c.body, 'setup_required')).toBeUndefined();
      expect(readiness(c.body, 'check').nextAction).toBe('edit_run');
    }
  },
);
it.each(fixturePreviews)(
  'F-042 fixture run_controls.%s gates the actual saved blueprint',
  (name) => {
    const c = dashboardCase(`run_controls.${name}`);
    const page = own(dashboardBody('readiness.ready_with_context'), 'context');
    const free = evidenceGate(answered(statusBody(sample('status.off'))), null);
    const gate = collectGate(
      collection(),
      c.status === 200 ? answer(c.body) : { kind: 'failed' },
      page,
      name === 'combo_is_no_blueprint' ? 'combo' : 'bp_farm',
      name === 'combo_is_no_blueprint',
      free,
    );
    expect(gate.enabled).toBe(name === 'ad_gems_on_floating_off');
    if (name === 'both_off') expect(gate.reason).toBe('gem_routines_off');
    if (name === 'combo_is_no_blueprint') expect(gate.reason).toBe('pick_single_run');
    if (name === 'unknown_blueprint') expect(gate.reason).toBe('preview_unavailable');
  },
);
import {
  collectGate,
  contextsEqual,
  otherRunsMissingImages,
  readiness,
  runDefined,
  selectRun,
  startGate,
  transferHint,
} from '../../../src/core/readiness.ts';
import { collectionGuard, setupGuard } from '../../../src/core/guards.ts';
import { evidenceGate } from '../../../src/core/gate.ts';
import { entries, own } from '../../../src/core/read.ts';
import { classifyStartAnswer } from '../../../src/core/outcomes/start.ts';
import { answered, sample, statusBody } from '../../support/fixture.ts';
import type { ContextRead } from '../../../src/core/connection.ts';

// Constructed backend/dashboard.py _runs_readiness/api_readiness/api_run_controls;
// backend/player/readiness.py check; backend/control/run_workspace.py read.
const context = { instance: 'main', account: 'a', serial: 'fake:1', rendering: 'r', profile: 'p' };
const answer = (body: unknown): ContextRead => ({ kind: 'answered', sameContext: true, body });
const image = { alternatives: ['screen/a.png', 'screen/b.png'], reasons: ['raw'], docs: [] };
const setup = () => {
  const guard = setupGuard({ scope: 'run', blocks_launch: false }, 100);
  if (guard.kind !== 'permit') throw new Error('missing setup permit');
  return guard.permit;
};
const collection = () => {
  const guard = collectionGuard({ scope: 'collection', blocks_launch: false }, 100);
  if (guard.kind !== 'permit') throw new Error('missing collection permit');
  return guard.permit;
};
it('F-039 counts only image requirements, once per alternatives row', () => {
  const setting = { alternatives: ['config: rois.wall_bar'], reasons: ['raw'], docs: [] };
  const restore = {
    alternatives: [],
    reasons: ['Restore the original modules: return to Home and resume calibration'],
    docs: [],
  };
  expect(readiness({ ready: false, details: [image, setting, restore] })).toMatchObject({
    line: 'images_missing',
    imageCount: 1,
    imageRows: [image],
    otherRows: [setting, restore],
  });
  expect(readiness({ ready: false, details: [setting, restore] })).toMatchObject({
    line: 'not_ready',
    imageCount: 0,
  });
});
it('F-039 restoration instructions and a caught check failure count zero', () => {
  expect(
    readiness({ ready: false, details: [{ alternatives: [], reasons: ['restore'] }] }),
  ).toMatchObject({ line: 'not_ready', imageCount: 0 });
  expect(
    readiness({ ready: false, missing: ['readiness check failed: raw exception'], advisory: [] }),
  ).toMatchObject({ line: 'readiness_check_failed', imageCount: 0 });
});
it('F-039 setup_required precedes counts and malformed unchecked entries are Unknown', () => {
  expect(
    readiness({ ready: false, setup_required: true, details: [image], missing: ['instruction'] }),
  ).toMatchObject({ line: 'needs_setup', imageCount: 0 });
  for (const entry of [
    {},
    null,
    { ready: 'true' },
    { ready: false, details: [{}] },
    Object.create({ ready: true }) as unknown,
  ])
    expect(readiness(entry).line).toBe('readiness_unknown');
  expect(readiness({ ready: true }).line).toBe('ready');
});
it('F-037 picker prefers ready bp_coin_default and preserves a surviving selection', () => {
  const body = {
    runs: {
      other: { defined: true },
      bp_coin_default: { defined: true },
      absent: { defined: false },
    },
    readiness: { other: { ready: true }, bp_coin_default: { ready: true } },
  };
  expect(selectRun(body, null)).toBe('bp_coin_default');
  expect(selectRun(body, 'other')).toBe('other');
  expect(selectRun({ ...body, readiness: {} }, null)).toBe('other');
  expect(selectRun({}, null)).toBeNull();
});

it('F-037 compiled presets omit defined while placeholders and malformed entries are excluded', () => {
  // backend/player/playerprofile.py compile_preset omits defined; dashboard.py _display_runs adds bp_.
  const body = {
    runs: {
      placeholder: { defined: false },
      malformed: [],
      bp_coin_default: { label: 'raw', kind: 'coin' },
    },
    readiness: { bp_coin_default: { ready: true } },
  };
  expect(selectRun(body, null)).toBe('bp_coin_default');
  expect(runDefined({ defined: true })).toBe(true);
  for (const row of [
    null,
    [],
    'run',
    { defined: false },
    { defined: 'true' },
    Object.create({ defined: true }) as unknown,
  ])
    expect(runDefined(row)).toBe(false);
});
it('F-039 other-run info counts exclude selected combo undefined and unchecked entries', () => {
  const entry = { ready: false, details: [image] };
  expect(
    otherRunsMissingImages(
      {
        runs: {
          selected: { defined: true },
          other: { defined: true },
          combo: { defined: true },
          unchecked: { defined: true },
        },
        readiness: { selected: entry, other: entry, combo: entry, unchecked: {} },
      },
      'selected',
    ),
  ).toBe(1);
});

it('F-039 compiled runs without defined still contribute image requirements', () => {
  expect(
    otherRunsMissingImages(
      {
        runs: { bp_other: { kind: 'coin' } },
        readiness: { bp_other: { ready: false, details: [image] } },
      },
      'bp_selected',
    ),
  ).toBe(1);
});
it('F-040 Start needs the setup permit and exact same-context readiness for the selected run', () => {
  const body = { ready: true, context, preset: 'bp_farm' };
  expect(startGate(setup(), answer(body), context, 'bp_farm').enabled).toBe(true);
  expect(startGate(null, answer(body), context, 'bp_farm').reason).toBe('guard_unconfirmed');
  for (const patch of [
    { ready: 'true' },
    { ready: false },
    { preset: 'other' },
    { context: { ...context, serial: 'other' } },
    { context: {} },
  ])
    expect(startGate(setup(), answer({ ...body, ...patch }), context, 'bp_farm').enabled).toBe(
      false,
    );
  expect(
    startGate(setup(), { kind: 'answered', sameContext: false, body }, context, 'bp_farm').enabled,
  ).toBe(false);
  expect(contextsEqual(Object.create(context), context, ['instance'])).toBe(false);
});
it('F-042 Collect needs its own permit current saved preview a gem routine and complete empty ownership', () => {
  const free = evidenceGate(answered(statusBody(sample('status.off'))), null);
  const previewBody = {
    blueprint: 'farm',
    context,
    settings: { routines: { ad_gems: { enabled: true }, floating_gems: { enabled: false } } },
  };
  const gate = (body: unknown = previewBody) =>
    collectGate(collection(), answer(body), context, 'farm', false, free);
  expect(gate().enabled).toBe(true);
  expect(
    collectGate(collection(), answer(previewBody), context, 'bp_farm', false, free).enabled,
  ).toBe(true);
  expect(collectGate(null, answer(previewBody), context, 'farm', false, free).reason).toBe(
    'guard_unconfirmed',
  );
  expect(collectGate(collection(), answer(previewBody), context, 'farm', true, free).reason).toBe(
    'pick_single_run',
  );
  expect(gate({ ...previewBody, blueprint: 'other' }).reason).toBe('preview_unavailable');
  expect(gate({ ...previewBody, context: { ...context, profile: 'other' } }).reason).toBe(
    'context_changed',
  );
  expect(
    gate({
      ...previewBody,
      settings: { routines: { ad_gems: { enabled: false }, floating_gems: { enabled: false } } },
    }).reason,
  ).toBe('gem_routines_off');
  const busy = evidenceGate(
    answered(statusBody(sample('status.off'), { processes: [{ pid: 10, runner: 'run' }] })),
    null,
  );
  expect(collectGate(collection(), answer(previewBody), context, 'farm', false, busy).reason).toBe(
    'automation_listed',
  );
  const unknown = evidenceGate(
    answered(statusBody(sample('status.off'), { processes: [{}] })),
    null,
  );
  expect(
    collectGate(collection(), answer(previewBody), context, 'farm', false, unknown).reason,
  ).toBe('ownership_unconfirmed');
});
it('F-041 a listed collector and a retained record give differently qualified hints', () => {
  expect(transferHint({ processes: [{ runner: 'gem_collector' }] })).toBe('collector_stops_first');
  expect(transferHint({ processes: [{ operation: { kind: 'gems' } }] })).toBe(
    'collector_stops_first',
  );
  expect(transferHint({ processes: [], harvest: { operation: { id: 'op', kind: 'gems' } } })).toBe(
    'collector_recorded',
  );
  expect(transferHint({ processes: [] })).toBeNull();
});
it('F-041 fixture-backed transfer lists describe only handover and empty or malformed lists overclaim nothing', () => {
  const fixture = sample('start_transfer.run_takes_over_collector');
  expect(classifyStartAnswer(fixture)).toMatchObject({
    kind: 'started',
    transfer: { kind: 'handover', wasStoppingAtHandover: [] },
  });
  const body = own(fixture, 'body');
  const operation = own(body, 'operation');
  const operation_id = own(body, 'operation_id');
  for (const transfer of [
    { stopped: [], stopping: [], waited_sec: 0 },
    { stopped: [], stopping: ['x'], waited_sec: 1 },
  ])
    expect(
      classifyStartAnswer({ status: 200, body: { ok: true, operation, operation_id, transfer } }),
    ).toMatchObject({ kind: 'started', transfer: { kind: 'handover' } });
  for (const transfer of [
    { stopped: 'x', stopping: [] },
    { stopped: ['x'], stopping: ['x'], waited_sec: 0 },
  ])
    expect(
      classifyStartAnswer({ status: 200, body: { ok: true, operation, operation_id, transfer } }),
    ).toMatchObject({ kind: 'started', transfer: { kind: 'unconfirmed' } });
});
