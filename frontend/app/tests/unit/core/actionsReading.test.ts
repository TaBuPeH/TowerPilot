import { describe, expect, it } from 'vitest';
import { copy } from '../../../src/copy/catalogue.ts';
import {
  ACTION_CONTROL_BLOCKS,
  actionControlsBlocked,
  actionsReading,
  runNowReason,
  type ActionsReading,
} from '../../../src/core/actions.ts';
import { BATTLE_REPORT_MAX_AGE_SEC, battleReportAge } from '../../../src/core/battle.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import {
  dashboardCase,
  dashboardPagesCase,
  dashboardPagesCaseNames,
} from '../../support/dashboardFixture.ts';

/** A deep copy of a recorded body with `edit` applied to its `battle.runtime.actions`. */
function edited(path: string, edit: (actions: Record<string, unknown>) => void): unknown {
  const body = dashboardPagesCase(path).body;
  const actions = ownPath(body, 'battle', 'runtime', 'actions') as Record<string, unknown>;
  edit(actions);
  return body;
}

const STALE =
  'battle_actions_projection.projection_fails_later_the_last_projection_stays_and_its_stamps_stop';
const IDLE = 'battle_actions_projection.idle_ad_and_floating_gems_due_shopping_waiting';

describe('AC5 the action reading rule (core/actions actionsReading)', () => {
  it('AC5 every recorded projection reads current except the stale, newer-schema and absent cases', () => {
    const readings = Object.fromEntries(
      dashboardPagesCaseNames('battle_actions_projection').map((name) => [
        name,
        actionsReading(dashboardPagesCase(`battle_actions_projection.${name}`).body, 0).kind,
      ]),
    );
    expect(readings).toEqual({
      a_failed_unconfirmed_execution_is_the_last_of_its_instance: 'current',
      a_finished_execution_is_the_last_of_its_instance: 'current',
      a_live_control_changes_the_value_in_force: 'current',
      ad_gems_paused_by_a_control_is_disabled: 'current',
      ad_window_open_is_running: 'current',
      each_item_names_its_routine_key: 'current',
      game_not_on_screen_every_routine_waits_for_the_game_state: 'current',
      idle_ad_and_floating_gems_due_shopping_waiting: 'current',
      projection_fails_later_the_last_projection_stays_and_its_stamps_stop: 'old',
      projection_never_built_the_actions_key_is_absent: 'absent',
      projection_written_by_a_newer_driver_is_read_only: 'read_only',
      run_now_completed_links_its_execution: 'current',
      shopping_switched_off_by_a_control_is_disabled: 'current',
      values_carry_every_field_in_force: 'current',
    });
  });

  it('AC5 the recorded stale case is old (2 s plus elapsed) although the 15 s battle rule calls it fresh', () => {
    const body = dashboardPagesCase(STALE).body;
    const report = battleReportAge(body, 0);
    expect(report).toMatchObject({ kind: 'fresh', ageSec: 0 });
    expect(actionsReading(body, 0)).toEqual({ kind: 'old', ageSec: 2 });
    expect(actionsReading(body, 3)).toEqual({ kind: 'old', ageSec: 5 });
    expect(actionControlsBlocked(actionsReading(body, 0))).toBe('action_report_old');
  });

  it('AC5 an old battle report is unknown before the projection is read (EV-9 owner, no new number)', () => {
    const body = dashboardPagesCase(IDLE).body;
    expect(actionsReading(body, BATTLE_REPORT_MAX_AGE_SEC).kind).toBe('current');
    const old = actionsReading(body, BATTLE_REPORT_MAX_AGE_SEC + 0.5);
    expect(old).toEqual({ kind: 'unknown', reason: 'battle_report_old' });
    expect(actionControlsBlocked(old)).toBe('battle_report_old');
    expect(actionsReading(body, -1)).toEqual({ kind: 'unknown', reason: 'battle_time_invalid' });
    expect(actionsReading({ battle: own(body, 'battle') }, 0)).toEqual({
      kind: 'unknown',
      reason: 'battle_time_invalid',
    });
  });

  it('AC5 a newer schema and a foreign context are read-only with their reasons', () => {
    const newer = actionsReading(
      dashboardPagesCase(
        'battle_actions_projection.projection_written_by_a_newer_driver_is_read_only',
      ).body,
      0,
    );
    expect(newer).toEqual({ kind: 'read_only', reason: 'action_schema_newer' });
    expect(actionControlsBlocked(newer)).toBe('action_schema_newer');
    for (const key of ['device_key', 'account', 'instance_name', 'profile']) {
      const body = edited(IDLE, (actions) => {
        const context = actions['context'] as Record<string, unknown>;
        context[key] = 'someone-else';
      });
      const reading = actionsReading(body, 0);
      expect(reading, key).toEqual({ kind: 'read_only', reason: 'action_context_mismatch' });
      expect(actionControlsBlocked(reading)).toBe('action_context_mismatch');
    }
    const noContext = edited(IDLE, (actions) => {
      delete actions['context'];
    });
    expect(actionsReading(noContext, 0)).toEqual({
      kind: 'read_only',
      reason: 'action_context_mismatch',
    });
  });

  it('AC5 a future or non-finite observed_at is unknown (action_report_time_invalid)', () => {
    for (const observed of [1800000003, 'soon', null, Number.NaN]) {
      const body = edited(IDLE, (actions) => {
        actions['observed_at'] = observed;
      });
      const reading = actionsReading(body, 0);
      expect(reading, String(observed)).toEqual({
        kind: 'unknown',
        reason: 'action_report_time_invalid',
      });
      expect(actionControlsBlocked(reading)).toBe('action_report_time_invalid');
    }
  });

  it('AC5 current and absent readings block nothing; the checkout stale_report is unknown', () => {
    const current: ActionsReading = actionsReading(dashboardPagesCase(IDLE).body, 0);
    expect(actionControlsBlocked(current)).toBeNull();
    expect(actionControlsBlocked({ kind: 'absent' })).toBeNull();
    const stale = dashboardCase('battles_active.stale_report');
    expect(actionsReading(stale.body, 0)).toEqual({ kind: 'unknown', reason: 'battle_report_old' });
  });
});

describe('AC3 AC5 the Run now reason order (core/actions runNowReason)', () => {
  const OLD: ActionsReading = { kind: 'old', ageSec: 2 };
  const reason = (
    gate: string | null,
    reading: ActionsReading | null,
    refusal: Parameters<typeof runNowReason>[0]['refusal'] = null,
    sending = false,
  ) => runNowReason({ gate, reading, refusal, sending });

  it('AC3 battle-wide gate reasons come first, then the readings, then the item, then the gate', () => {
    for (const gate of ['report_unavailable', 'battle_not_active', 'no_tracked_battle'])
      expect(reason(gate, OLD)).toBe(gate);
    expect(reason('another_action_in_progress', OLD)).toBe('action_report_old');
    expect(
      reason(
        'action_unsupported',
        { kind: 'current' },
        { kind: 'code', code: 'run_now_unsupported' },
      ),
    ).toBe('run_now_unsupported');
    expect(reason(null, { kind: 'current' }, { kind: 'server', reason: 'calibrate first' })).toBe(
      'server_reason',
    );
    expect(reason('another_action_in_progress', { kind: 'current' })).toBe(
      'another_action_in_progress',
    );
  });

  it('AC3 sending only when nothing else stops the button; null when it is offered', () => {
    expect(reason(null, { kind: 'current' }, null, true)).toBe('sending');
    expect(reason(null, { kind: 'current' })).toBeNull();
    expect(reason(null, { kind: 'absent' })).toBeNull();
  });

  it('AC5 the compatibility rows pass no reading, so an old battle report stays the gate reason', () => {
    expect(reason('report_unavailable', null)).toBe('report_unavailable');
    expect(reason('action_unsupported', null)).toBe('action_unsupported');
    expect(reason(null, { kind: 'unknown', reason: 'battle_report_old' })).toBe(
      'battle_report_old',
    );
  });
});

describe('AC5 one sentence per readings reason', () => {
  it('AC5 copy.actions.blocked words every ACTION_CONTROL_BLOCKS code and nothing else; This battle uses them', () => {
    expect(Object.keys(copy.actions.blocked).sort()).toEqual([...ACTION_CONTROL_BLOCKS].sort());
    for (const code of ACTION_CONTROL_BLOCKS)
      expect(Reflect.get(copy.dashboard.thisBattle.disabled, code)).toBe(
        Reflect.get(copy.actions.blocked, code),
      );
  });
});
