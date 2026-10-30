import { describe, expect, it } from 'vitest';
import type { ContextRead } from '../../../src/core/connection.ts';
import { ownPath } from '../../../src/core/read.ts';
import { liveChangeProgress } from '../../../src/core/routines.ts';
import { opaqueToken } from '../../../src/core/tokens.ts';
import { dashboardBody, dashboardPagesCase } from '../../support/dashboardFixture.ts';

/**
 * F6-11-IR-01 (backend/logs/reviews/frontend-f6-runs-leftovers-review/report.md): a recorded
 * live change is applied only when a later read of its own battle lists its command applied.
 * Recorded reads: dashboard_contract_pages_v1.json run_controls_live (one change, answer and
 * reads) and dashboard_contract_v1.json battles_active (applied, pending, rejected, expired).
 */
const live = (name: string) => dashboardPagesCase(`run_controls_live.${name}`).body;
const answered = (body: unknown, sameContext = true): ContextRead => ({
  kind: 'answered',
  body,
  sameContext,
});
const identity = (body: unknown) => ({
  id: String(ownPath(body, 'battle', 'id')),
  generation: String(ownPath(body, 'battle', 'generation')),
  operationId: String(ownPath(body, 'battle', 'operation_id')),
});
const ANSWER = live('accepted_switch_off');
const SENT_TO = identity(ANSWER);
const key = (value: string) => opaqueToken(value);

describe('F6-11-IR-01 live change progress from a later read of its battle', () => {
  it('IR-01 the recorded change is waiting while its command is requested, applied once it is applied', () => {
    expect(
      liveChangeProgress(
        answered(live('read_after_the_change_lists_the_command_and_its_key')),
        SENT_TO,
        ANSWER,
        key('another-key'),
      ),
    ).toBe('waiting');
    expect(
      liveChangeProgress(
        answered(live('read_after_the_driver_applied_the_change')),
        SENT_TO,
        ANSWER,
        key('another-key'),
      ),
    ).toBe('applied');
  });

  it('IR-01 a read that does not list the command, or of another context, proves nothing', () => {
    expect(
      liveChangeProgress(answered(live('read_before_the_first_change')), SENT_TO, ANSWER, null),
    ).toBe('not_listed');
    expect(
      liveChangeProgress(
        answered(live('read_after_the_driver_applied_the_change'), false),
        SENT_TO,
        ANSWER,
        null,
      ),
    ).toBe('not_listed');
    expect(liveChangeProgress({ kind: 'failed' }, SENT_TO, ANSWER, null)).toBe('not_listed');
    expect(liveChangeProgress(null, SENT_TO, ANSWER, null)).toBe('not_listed');
  });

  it('IR-01 a read of another battle, generation or operation never settles the change', () => {
    const read = answered(live('read_after_the_driver_applied_the_change'));
    for (const other of [
      { ...SENT_TO, id: '000000ff-0000-4000-8000-000000000000' },
      { ...SENT_TO, generation: '000000ff-0000-4000-8000-000000000000' },
      { ...SENT_TO, operationId: '000000ff-0000-4000-8000-000000000000' },
    ])
      expect(liveChangeProgress(read, other, ANSWER, key('live-1'))).toBe('not_listed');
  });

  it("IR-01 without the answer's command id, the change's request key finds its command", () => {
    const read = answered(live('read_after_the_driver_applied_the_change'));
    expect(liveChangeProgress(read, SENT_TO, undefined, key('live-1'))).toBe('applied');
    expect(liveChangeProgress(read, SENT_TO, undefined, key('live-2'))).toBe('not_listed');
    expect(liveChangeProgress(read, SENT_TO, undefined, null)).toBe('not_listed');
  });

  it('IR-01 the answer names its command: another command applied with the same key does not count', () => {
    const read = answered(live('read_after_the_driver_applied_the_change'));
    const otherCommand = {
      ...(ANSWER as object),
      command: { id: '000000ee-0000-4000-8000-000000000000' },
    };
    expect(liveChangeProgress(read, SENT_TO, otherCommand, key('live-1'))).toBe('not_listed');
  });

  it.each([
    ['commands_controls_applied_with_overrides_action_failed', 'ctl-1', 'applied'],
    ['commands_controls_pending_no_path_applied_action_started', 'ctl-2', 'waiting'],
    ['commands_controls_rejected_action_rejected', 'ctl-4', 'not_applied'],
    ['commands_controls_expired_action_second_key_joined', 'ctl-5', 'not_applied'],
    // An action command carrying a key is never a live settings change.
    ['commands_controls_rejected_action_rejected', 'act-1', 'not_listed'],
  ])('IR-01 recorded %s, key %s: %s', (name, requestKey, expected) => {
    const body = dashboardBody(`battles_active.${name}`);
    expect(liveChangeProgress(answered(body), identity(body), undefined, key(requestKey))).toBe(
      expected,
    );
  });
});
