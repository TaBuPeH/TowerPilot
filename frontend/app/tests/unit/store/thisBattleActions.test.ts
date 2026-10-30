import { describe, expect, it } from 'vitest';
import { serverTextContent, type ServerText } from '../../../src/copy/server.ts';
import { ownPath } from '../../../src/core/read.ts';
import { selectThisBattle, type ThisBattleVM } from '../../../src/store/selectors/thisBattle.ts';
import type { DashboardState } from '../../../src/store/types.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';
import { addBody, addCase, expectStateVM, regionState, tick } from './regionHelpers.ts';

const P = 'battle_actions_projection';

function stateOf(body: unknown): DashboardState {
  return addBody(regionState(), 'battlesActive', body, { path: '/api/battles/active' });
}

function vmOf(name: string, edit?: (body: Record<string, unknown>) => void): ThisBattleVM {
  const body = dashboardPagesCase(`${P}.${name}`).body as Record<string, unknown>;
  edit?.(body);
  return selectThisBattle(stateOf(body));
}

function items(body: Record<string, unknown>): Record<string, unknown>[] {
  return ownPath(body, 'battle', 'runtime', 'actions', 'items') as Record<string, unknown>[];
}

function rows(vm: ThisBattleVM) {
  return vm.actions ?? [];
}

const text = (value: ServerText | null | undefined) =>
  value === null || value === undefined ? null : serverTextContent(value);

describe('AC1 This battle rows from runtime.actions', () => {
  it.each(
    dashboardPagesCaseNames(P).filter(
      (name) => name !== 'projection_never_built_the_actions_key_is_absent',
    ),
  )('AC1 %s renders one row per item, in item order, named by its label', (name) => {
    const body = dashboardPagesCase(`${P}.${name}`).body as Record<string, unknown>;
    const state = stateOf(body);
    const vm = selectThisBattle(state);
    expect(vm.routines).toEqual([]);
    expect(rows(vm).map((row) => text(row.label))).toEqual(
      items(body).map((item) => item['label']),
    );
    expect(rows(vm).map((row) => row.token)).toEqual(items(body).map((item) => item['key']));
    expectStateVM(vm, state);
  });

  it('AC1 idle: the ad gems blocker comes before its state, floating gems Due, shopping in 18 s', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting');
    const [ad, floating, shopping] = rows(vm);
    expect(ad?.next).toMatchObject({ kind: 'blocker' });
    expect(ad?.next.kind === 'blocker' ? text(ad.next.code) : null).toBe('not_observed');
    expect(floating?.next).toEqual({ kind: 'due' });
    expect(shopping?.next).toEqual({ kind: 'in', sec: 18 });
  });

  it('AC1 the countdown runs on the monotonic time since the read arrived', () => {
    const body = dashboardPagesCase(`${P}.idle_ad_and_floating_gems_due_shopping_waiting`).body;
    const vm = selectThisBattle(tick(stateOf(body), 5));
    expect(rows(vm)[2]?.next).toEqual({ kind: 'in', sec: 13 });
  });

  it('AC1 ad window open: running, with its window end', () => {
    expect(rows(vmOf('ad_window_open_is_running'))[0]?.next).toEqual({
      kind: 'running',
      endsInSec: 18,
    });
  });

  it('AC1 paused and switched off: Off, with no countdown', () => {
    const paused = rows(vmOf('ad_gems_paused_by_a_control_is_disabled'));
    expect(paused[0]?.next).toEqual({ kind: 'off' });
    expect(paused[0]?.enabled).toBe(false);
    const off = rows(vmOf('shopping_switched_off_by_a_control_is_disabled'));
    expect(off[2]?.next).toEqual({ kind: 'off' });
    expect(off[2]?.enabled).toBe(false);
  });

  it('AC1 a finished execution: Succeeded, offer gone after the tap, its age and its reason', () => {
    const ad = rows(vmOf('a_finished_execution_is_the_last_of_its_instance'))[0];
    expect(ad?.last).toMatchObject({
      result: { kind: 'known', code: 'succeeded' },
      effect: { kind: 'known', code: 'offer_gone' },
      ageSec: 2,
    });
    expect(text(ad?.last?.reason)).toBe('offer_disappeared');
    expect(rows(vmOf('a_finished_execution_is_the_last_of_its_instance'))[1]?.last).toBeNull();
  });

  it('AC1 a failed unconfirmed execution never reads confirmed', () => {
    const ad = rows(vmOf('a_failed_unconfirmed_execution_is_the_last_of_its_instance'))[0];
    expect(ad?.last).toMatchObject({
      result: { kind: 'known', code: 'failed' },
      effect: { kind: 'known', code: 'unconfirmed' },
    });
    expect(ad?.next).toMatchObject({ kind: 'blocker' });
  });

  it('AC1 settings: On or Off and every other field with its label, value and unit', () => {
    const [ad, floating, shopping] = rows(vmOf('idle_ad_and_floating_gems_due_shopping_waiting'));
    expect(ad?.enabled).toBe(true);
    const shape = (row: typeof ad) =>
      row?.settings.map((field) => [text(field.label), field.value, text(field.unit)]);
    expect(shape(ad)).toEqual([
      ['Interval', 720, 's'],
      ['Window', 60, 's'],
    ]);
    expect(shape(floating)?.length).toBe(3);
    expect(shape(shopping)).toEqual([['Interval', 30, 's']]);
  });

  it('AC1 a test-local item with an unknown id and label renders without any copy entry', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
      const list = items(body);
      const probe = JSON.parse(JSON.stringify(list[1])) as Record<string, unknown>;
      Object.assign(probe, { id: 'tidal_probe', instance: 'tidal_probe', key: 'tidal_probe' });
      Object.assign(probe, { label: 'Tidal probe' });
      list.push(probe);
    });
    const probe = rows(vm)[3];
    expect(text(probe?.label)).toBe('Tidal probe');
    expect(probe?.next).toEqual({ kind: 'due' });
    expect(probe?.runNow.reason).toBe('action_unsupported');
  });

  it('AC1 the projection absent: the three compatibility rows, no action rows', () => {
    const vm = vmOf('projection_never_built_the_actions_key_is_absent');
    expect(vm.actions).toBeNull();
    expect(vm.routines.map((row) => row.routine)).toEqual(['ad_gems', 'floating_gems', 'shopping']);
    expect(vm.actionsNotice).toBeNull();
  });
});

describe('AC3 Run now for any item', () => {
  it('AC3 offered with the item key as the token when the descriptor and battle admit it', () => {
    const [ad, , shopping] = rows(vmOf('idle_ad_and_floating_gems_due_shopping_waiting'));
    expect(ad?.runNow).toMatchObject({ enabled: true, reason: null });
    expect(ad?.runNow.command).toMatchObject({ action: 'ad_gems', expectedRevision: 0 });
    expect(shopping?.runNow.command).toMatchObject({ action: 'shopping' });
  });

  it('AC3 a token the battle does not list is refused (action_unsupported)', () => {
    const shopping = rows(vmOf('a_failed_unconfirmed_execution_is_the_last_of_its_instance'))[2];
    expect(shopping?.runNow).toMatchObject({ enabled: false, reason: 'action_unsupported' });
  });

  it('AC3 run_now false or a reason string disables the button with that reason', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
      const [ad, floating] = items(body);
      Object.assign(ownPath(ad, 'descriptor', 'capabilities') as object, { run_now: false });
      Object.assign(ownPath(floating, 'descriptor', 'capabilities') as object, {
        run_now: 'needs a calibrated offer button',
      });
    });
    const [ad, floating] = rows(vm);
    expect(ad?.runNow).toMatchObject({ enabled: false, reason: 'run_now_unsupported' });
    expect(floating?.runNow).toMatchObject({ enabled: false, reason: 'server_reason' });
    expect(text(floating?.runNowServerReason)).toBe('needs a calibrated offer button');
  });

  it('AC3 a completed Run now reads its outcome from the execution it started (last.command_id)', () => {
    const vm = vmOf('run_now_completed_links_its_execution');
    const command = vm.commands[0];
    expect(command).toMatchObject({ row: 'K4', resultChip: null });
    expect(command?.outcome).toMatchObject({
      result: { kind: 'known', code: 'succeeded' },
      effect: { kind: 'known', code: 'offer_gone' },
      ageSec: 2,
    });
    expect(rows(vm)[0]?.last).toMatchObject({ manual: true });
  });

  it('AC3 a completion whose execution is unconfirmed never reads Confirmed', () => {
    const vm = vmOf('run_now_completed_links_its_execution', (body) => {
      const last = items(body)[0]?.['last'] as Record<string, unknown>;
      Object.assign(last, {
        result: 'failed',
        effect: 'unconfirmed',
        reason: 'offer_still_visible',
      });
    });
    expect(vm.commands[0]?.outcome).toMatchObject({
      result: { kind: 'known', code: 'failed' },
      effect: { kind: 'known', code: 'unconfirmed' },
    });
  });

  it('AC3 one open action disables every Run now, with one shared reason', () => {
    const vm = vmOf('run_now_completed_links_its_execution', (body) => {
      const commands = ownPath(body, 'battle', 'commands') as Record<string, unknown>[];
      Object.assign(commands[0] ?? {}, { state: 'started' });
    });
    expect(rows(vm).map((row) => row.runNow.reason)).toEqual([
      'another_action_in_progress',
      'another_action_in_progress',
      'action_unsupported',
    ]);
    expect(vm.commands[0]?.outcome).toBeNull();
  });
});

describe('AC5 old, unreadable or foreign readings', () => {
  const STALE = 'projection_fails_later_the_last_projection_stays_and_its_stamps_stop';

  it('AC5 the stale case: every item last known 2 s ago, no Due chip, Run now and Try once off', () => {
    const vm = vmOf(STALE);
    expect(rows(vm).map((row) => row.next)).toEqual([
      { kind: 'last_known', ageSec: 2 },
      { kind: 'last_known', ageSec: 2 },
      { kind: 'last_known', ageSec: 2 },
    ]);
    expect(rows(vm).every((row) => row.runNow.reason === 'action_report_old')).toBe(true);
    expect(vm.tryOnce.every((item) => item.button.reason === 'action_report_old')).toBe(true);
    expect(vm.actionsNotice).toEqual({ reason: 'action_report_old', ageSec: 2 });
    expect(vm.sharedReason).toBe('action_report_old');
  });

  it('AC5 a newer schema is read-only with action_schema_newer', () => {
    const vm = vmOf('projection_written_by_a_newer_driver_is_read_only');
    expect(rows(vm).every((row) => row.next.kind === 'not_current')).toBe(true);
    expect(rows(vm).every((row) => row.runNow.reason === 'action_schema_newer')).toBe(true);
    expect(vm.actionsNotice).toEqual({ reason: 'action_schema_newer', ageSec: null });
  });

  it('AC5 a context of another profile is read-only with action_context_mismatch', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
      const context = ownPath(body, 'battle', 'runtime', 'actions', 'context') as object;
      Object.assign(context, { profile: 'theirs' });
    });
    expect(rows(vm).every((row) => row.runNow.reason === 'action_context_mismatch')).toBe(true);
    expect(vm.actionsNotice).toEqual({ reason: 'action_context_mismatch', ageSec: null });
  });

  it('AC5 an unrecognised state renders not recognised with its code and disables that item', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
      Object.assign(items(body)[1] ?? {}, { state: 'hibernating' });
    });
    const floating = rows(vm)[1];
    expect(floating?.next.kind).toBe('unrecognised');
    expect(floating?.next.kind === 'unrecognised' ? text(floating.next.code) : null).toBe(
      'hibernating',
    );
    expect(floating?.runNow.reason).toBe('action_code_unrecognised');
    expect(rows(vm)[0]?.runNow.enabled).toBe(true);
  });

  it('AC5 an unrecognised effect renders not recognised with its code', () => {
    const vm = vmOf('a_finished_execution_is_the_last_of_its_instance', (body) => {
      Object.assign(items(body)[0]?.['last'] as object, { effect: 'sparkled' });
    });
    const ad = rows(vm)[0];
    expect(ad?.last?.result).toEqual({ kind: 'known', code: 'succeeded' });
    expect(ad?.last?.effect.kind).toBe('unrecognised');
    expect(ad?.last?.effect.kind === 'unrecognised' ? text(ad.last.effect.code) : null).toBe(
      'sparkled',
    );
    expect(ad?.runNow.reason).toBe('action_code_unrecognised');
  });

  it('AC5 D stale_report: the compatibility rows keep the old-report gates; no actions notice', () => {
    const vm = selectThisBattle(
      addCase(regionState(), 'battlesActive', 'battles_active.stale_report'),
    );
    expect(vm.actions).toBeNull();
    expect(vm.actionsNotice).toBeNull();
    expect(vm.header.row).toBe('B7');
    const reasons = new Set([
      ...vm.routines.map((row) => row.runNow.reason),
      ...vm.tryOnce.map((item) => item.button.reason),
    ]);
    expect([...reasons]).toEqual(['report_unavailable']);
    expect(vm.sharedReason).toBe('report_unavailable');
  });
});

describe('AC1 AC3 AC5 edge cases of an item', () => {
  it('AC3 an item whose controls cannot be addressed (no key, instance differs) offers no Run now', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
      const item = items(body)[1] ?? {};
      delete item['key'];
      Object.assign(item, { instance: 'floating_gems_b' });
    });
    const floating = rows(vm)[1];
    expect(floating?.token).toBeNull();
    expect(floating?.runNow).toMatchObject({
      enabled: false,
      reason: 'control_path_unpublished',
      command: null,
    });
    expect(rows(vm)[0]?.runNow.enabled).toBe(true);
  });

  it('AC1 a prepared battle that publishes actions shows no Next and offers no Run now', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
      Object.assign(ownPath(body, 'battle') as object, { status: 'prepared' });
    });
    expect(rows(vm).map((row) => row.next)).toEqual([
      { kind: 'prepared' },
      { kind: 'prepared' },
      { kind: 'prepared' },
    ]);
    expect(rows(vm).every((row) => !row.runNow.enabled)).toBe(true);
  });

  it('AC1 a blocker that is not a string reads as a blocker with no code', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
      Object.assign(items(body)[0]?.['eligibility'] as object, { blocker: { code: 7 } });
    });
    expect(rows(vm)[0]?.next).toEqual({ kind: 'blocker', code: null });
  });

  it('AC5 an unrecognised field type shows in its own setting and disables that item', () => {
    const vm = vmOf('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
      const fields = ownPath(items(body)[0], 'descriptor', 'fields') as Record<string, unknown>[];
      Object.assign(fields[2] ?? {}, { type: 'duration' });
    });
    const ad = rows(vm)[0];
    const window = ad?.settings.find((field) => field.path === 'window_sec');
    expect(text(window?.unrecognisedType)).toBe('duration');
    expect(
      ad?.settings.find((field) => field.path === 'interval_sec')?.unrecognisedType,
    ).toBeNull();
    expect(ad?.runNow.reason).toBe('action_code_unrecognised');
  });

  it('AC1 an absent effect is unreported, not unrecognised, and keeps Run now', () => {
    const vm = vmOf('a_finished_execution_is_the_last_of_its_instance', (body) => {
      delete (items(body)[0]?.['last'] as Record<string, unknown>)['effect'];
    });
    const ad = rows(vm)[0];
    expect(ad?.last?.effect).toEqual({ kind: 'unreported' });
    expect(ad?.runNow.reason).not.toBe('action_code_unrecognised');
  });
});
