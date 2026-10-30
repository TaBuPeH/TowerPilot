import { describe, expect, it } from 'vitest';
import { classifyKillAnswer } from '../../../src/core/outcomes/kill.ts';
import { stopTarget } from '../../../src/core/ownership.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { sample, sampleNames } from '../../support/fixture.ts';
import { dashboardCase, dashboardCaseNames } from '../../support/dashboardFixture.ts';

describe('untracked runner answers', () => {
  const expected: Record<string, string> = {
    stopped: 'no_longer_running',
    gone: 'no_longer_running',
    pid_passed_to_another_runner: 'identity_unverified',
    created_missing: 'identity_unverified',
    not_a_runner: 'refused',
    invalid_request: 'refused',
  };
  it.each(sampleNames('stop_operation.kill'))(
    'S-060 classifies kill.%s without attributing a kill or allowing resend',
    (name) => {
      const answer = sample(`stop_operation.kill.${name}`);
      expect(classifyKillAnswer(answer)).toEqual({
        kind: expected[name],
        code: ownPath(answer, 'body', 'code'),
        error: ownPath(answer, 'body', 'error'),
        resend: false,
      });
    },
  );
  it.each([
    null,
    { status: 500, body: { error: 'raw failure' } },
    { status: 503, body: { gone: true } },
    { status: 200, body: { gone: 'true' } },
    { status: 409, body: { code: 'other_code' } },
  ])('S-060 unknown kill %j may still be running and is never resent', (answer) => {
    expect(classifyKillAnswer(answer)).toMatchObject({ kind: 'unknown', resend: false });
  });
  it('S-060 uses the exact process row pid and created and disables a missing creation time', () => {
    const rows = sample('stop_operation.processes');
    if (!Array.isArray(rows)) throw new Error('process rows required');
    expect(stopTarget(rows[0])).toEqual({
      kind: 'kill',
      pid: own(rows[0], 'pid'),
      created: own(rows[0], 'created'),
    });
    expect(stopTarget(rows[1])).toMatchObject({ kind: 'disabled', reason: 'created_missing' });
  });
});

describe('Dashboard kill unknown outcomes', () => {
  const cases = ['kill_access_denied', 'kill_the_process_did_not_exit'];
  it('S-060 locks the real kill case list selected by request action', () => {
    expect(
      dashboardCaseNames('control_unknown_outcomes').filter(
        (name) =>
          own(dashboardCase(`control_unknown_outcomes.${name}`).request.body, 'action') === 'kill',
      ),
    ).toEqual(cases);
    expect(
      dashboardCaseNames('control').filter(
        (name) => own(dashboardCase(`control.${name}`).request.body, 'action') === 'kill',
      ),
    ).toEqual([]);
  });
  it.each(cases)(
    'S-060 real control_unknown_outcomes.%s may still be running and is never resent',
    (name) => {
      const answer = dashboardCase(`control_unknown_outcomes.${name}`);
      expect(answer.status).toBe(500);
      expect(answer.body).toHaveProperty('ok', false);
      expect(classifyKillAnswer(answer)).toEqual({
        kind: 'unknown',
        code: own(answer.body, 'code'),
        error: own(answer.body, 'error'),
        resend: false,
      });
    },
  );
});
