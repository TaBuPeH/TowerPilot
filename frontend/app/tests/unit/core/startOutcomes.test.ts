import { describe, expect, it } from 'vitest';
import {
  classifyClearFlagAnswer,
  classifyHandoffAnswer,
  classifyStartAnswer,
} from '../../../src/core/outcomes/start.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { sample, sampleNames, sampleRecord } from '../../support/fixture.ts';
import {
  dashboardBody,
  dashboardCase,
  dashboardCaseNames,
} from '../../support/dashboardFixture.ts';

function body(): Record<string, unknown> {
  return own(sample('start_transfer.run_takes_over_collector'), 'body') as Record<string, unknown>;
}
describe('start answers', () => {
  it.each(sampleNames('start_transfer'))(
    'S-102 %s reports stopped at handover and a started run',
    (name) => {
      const answer = sample(`start_transfer.${name}`);
      expect(classifyStartAnswer(answer)).toMatchObject({
        kind: 'started',
        operationId: ownPath(answer, 'body', 'operation_id'),
        transfer: {
          kind: 'handover',
          stoppedAtHandover: ownPath(answer, 'body', 'transfer', 'stopped'),
          wasStoppingAtHandover: [],
        },
      });
    },
  );
  it('S-102 a successful stopping list describes the handover moment', () => {
    const raw = body();
    raw['transfer'] = { stopped: [], stopping: own(raw['transfer'], 'stopped'), waited_sec: 0 };
    expect(classifyStartAnswer({ status: 200, body: raw })).toMatchObject({
      kind: 'started',
      transfer: { kind: 'handover', wasStoppingAtHandover: own(raw['transfer'], 'stopping') },
    });
  });
  it.each(['x', {}, null, undefined, [7], [null]])(
    'S-102 malformed stopping %j leaves the run started with handover unconfirmed',
    (stopping) => {
      const raw = body();
      raw['transfer'] = { stopped: [], stopping, waited_sec: 0 };
      expect(classifyStartAnswer({ status: 200, body: raw })).toMatchObject({
        kind: 'started',
        transfer: { kind: 'unconfirmed' },
      });
    },
  );
  it('S-102 overlapping or duplicate transfer ids cannot claim how the collector stopped', () => {
    const raw = body();
    const stopped = own(raw['transfer'], 'stopped');
    raw['transfer'] = { stopped, stopping: stopped, waited_sec: 0 };
    expect(classifyStartAnswer({ status: 200, body: raw })).toMatchObject({
      kind: 'started',
      transfer: { kind: 'unconfirmed' },
    });
    if (!Array.isArray(stopped)) throw new Error('list required');
    const ids: readonly unknown[] = stopped;
    raw['transfer'] = { stopped: [...ids, ...ids], stopping: [], waited_sec: 0 };
    expect(classifyStartAnswer({ status: 200, body: raw })).toMatchObject({
      kind: 'started',
      transfer: { kind: 'unconfirmed' },
    });
  });
  it('S-102 collector_stopping means not started with the owner recovery', () => {
    expect(classifyStartAnswer(sample('refusals.collector_stopping'))).toMatchObject({
      kind: 'owner_refused',
      notice: { kind: 'operation_owner', recovery: { kind: 'pause' } },
    });
  });
  it('S-100 a refused setup guard supplies its own guard reason', () => {
    const answer = sample('refusals.setup_battle_unresolved');
    expect(classifyStartAnswer(answer)).toEqual({
      kind: 'guard_refused',
      guard: ownPath(answer, 'body', 'setup_battle_guard'),
      error: ownPath(answer, 'body', 'error'),
    });
  });
  it('S-103 reserved private fields are a definite refusal', () => {
    expect(classifyStartAnswer(sample('refusals.reserved_fields'))).toMatchObject({
      kind: 'refused',
      code: 'invalid_request',
    });
  });
  it('S-102 an ordinary accepted start needs no transfer and a lock keeps the explicit-repeat key', () => {
    const raw = body();
    delete raw['transfer'];
    expect(classifyStartAnswer({ status: 200, body: raw })).toMatchObject({
      kind: 'started',
      transfer: { kind: 'none' },
    });
    expect(classifyStartAnswer(sample('refusals.action_lock'))).toMatchObject({
      kind: 'unknown',
      keepKey: true,
    });
  });
  it('S-102 context changes are refused and lost or 5xx starts keep their key', () => {
    expect(classifyStartAnswer(sample('post_harvest.context_changed'))).toMatchObject({
      kind: 'context_changed',
    });
    expect(classifyStartAnswer(null)).toMatchObject({ kind: 'unknown', keepKey: true });
    expect(classifyStartAnswer({ status: 500, body: { error: 'raw error' } })).toMatchObject({
      kind: 'unknown',
      keepKey: true,
      error: 'raw error',
    });
    const malformed = sampleRecord('start_transfer.run_takes_over_collector');
    malformed['body'] = null;
    expect(classifyStartAnswer(malformed).kind).toBe('unknown');
  });
});

const controlCases: Readonly<Record<string, string>> = {
  action_lock: 'unknown',
  clear_flag_accepted: 'cleared',
  clear_flag_refused_for_a_changed_revision: 'refused',
  start_accepted: 'started',
  start_accepted_with_transfer: 'started',
  start_context_changed: 'refused',
  start_gems_accepted: 'started',
  start_gems_context_changed: 'refused',
  start_gems_refused_by_collection_guard: 'guard_refused',
  start_gems_retry_answered_from_the_receipt: 'started',
  start_refused_by_setup_guard: 'guard_refused',
  start_retry_answered_from_the_receipt: 'started',
  unknown_action: 'refused',
};
const unknownControlCases: Readonly<Record<string, string>> = {
  clear_flag_removed_then_the_answer_failed: 'unknown',
  kill_access_denied: 'unknown',
  kill_the_process_did_not_exit: 'unknown',
  start_accepted_then_the_process_refresh_raised: 'unknown',
  start_accepted_then_the_worker_spawn_failed: 'unknown',
  start_gems_accepted_then_the_process_refresh_raised: 'unknown',
  start_gems_accepted_then_the_worker_spawn_failed: 'unknown',
  start_gems_refresh_failed_identical_retry: 'started',
  start_gems_spawn_failed_identical_retry: 'failed',
  start_refresh_failed_identical_retry: 'started',
  start_spawn_failed_identical_retry: 'failed',
};
const handoffCases: Readonly<Record<string, string>> = {
  identical_retry_answered_from_its_record: 'recorded',
  recorded: 'recorded',
  refused_attestation_missing: 'refused',
  refused_blocker_changed: 'refused',
  refused_context_changed: 'refused',
  refused_device_owned: 'refused',
  refused_device_owner_unreadable: 'owner_refused',
  refused_nothing_to_hand_off: 'refused',
};

describe('landed Dashboard write receipts', () => {
  it('F-043 locks the consumed control and control_unknown_outcomes case lists', () => {
    expect(dashboardCaseNames('control')).toEqual(Object.keys(controlCases).sort());
    expect(dashboardCaseNames('control_unknown_outcomes')).toEqual(
      Object.keys(unknownControlCases).sort(),
    );
  });

  it.each(Object.keys(controlCases).filter((name) => !name.startsWith('clear_flag_')))(
    'F-043 classifies the real control.%s Start or refusal receipt',
    (name) => {
      const answer = dashboardCase(`control.${name}`);
      const result = classifyStartAnswer(answer);
      expect(result.kind).toBe(controlCases[name]);
      if (result.kind === 'started') {
        expect(result.operationId).toBe(own(answer.body, 'operation_id'));
        expect(result.operation).toEqual(own(answer.body, 'operation'));
        expect(result.transfer.kind).toBe(
          name === 'start_accepted_with_transfer' ? 'handover' : 'none',
        );
      } else {
        expect(result.error).toEqual(own(answer.body, 'error'));
      }
      if (name === 'action_lock')
        expect(result).toMatchObject({ kind: 'unknown', keepKey: true, code: 'action_lock' });
    },
  );

  it.each(Object.keys(unknownControlCases).filter((name) => name.startsWith('start')))(
    'F-043 settles the real control_unknown_outcomes.%s by its returned operation status',
    (name) => {
      const answer = dashboardCase(`control_unknown_outcomes.${name}`);
      const result = classifyStartAnswer(answer);
      expect(result.kind).toBe(unknownControlCases[name]);
      if (result.kind === 'failed') {
        expect(result.operationId).toBe(own(answer.body, 'operation_id'));
        expect(result.error).toEqual(ownPath(answer.body, 'operation', 'error'));
        expect(result.error).toMatchObject({ code: 'launch_failed' });
        expect(result).not.toHaveProperty('transfer');
      }
      if (result.kind === 'unknown') {
        expect(result.keepKey).toBe(true);
        expect(own(answer.body, 'text')).toEqual(expect.any(String));
      }
    },
  );

  it.each(['start', 'start_gems'])(
    'F-043 %s identical retries use the same request and return the recorded identity',
    (action) => {
      const accepted = dashboardCase(`control.${action}_accepted`);
      const replayed = dashboardCase(`control.${action}_retry_answered_from_the_receipt`);
      expect(replayed.request).toEqual(accepted.request);
      expect(own(replayed.body, 'operation_id')).toBe(own(accepted.body, 'operation_id'));
      expect(classifyStartAnswer(replayed).kind).toBe('started');
      for (const cause of ['spawn', 'refresh']) {
        const failedAnswer = dashboardCase(
          `control_unknown_outcomes.${action}_accepted_then_the_${cause === 'spawn' ? 'worker_spawn_failed' : 'process_refresh_raised'}`,
        );
        const retry = dashboardCase(
          `control_unknown_outcomes.${action}_${cause}_failed_identical_retry`,
        );
        expect(retry.request).toEqual(failedAnswer.request);
        expect(classifyStartAnswer(failedAnswer)).toMatchObject({ kind: 'unknown', keepKey: true });
        expect(classifyStartAnswer(retry).kind).toBe(cause === 'spawn' ? 'failed' : 'started');
      }
    },
  );

  it.each(['cancelled', 'interrupted', 'succeeded'])(
    'F-043 a final %s receipt has its own non-started outcome and raw error',
    (status) => {
      const receipt = dashboardCase('control_unknown_outcomes.start_spawn_failed_identical_retry');
      const operation = {
        ...(own(receipt.body, 'operation') as Record<string, unknown>),
        status,
        error: { toString: null },
      };
      const answerBody = {
        ...(receipt.body as Record<string, unknown>),
        operation,
        transfer: { stopped: [], stopping: [], waited_sec: 0 },
      };
      expect(classifyStartAnswer({ status: 200, body: answerBody })).toEqual({
        kind: status,
        operationId: own(receipt.body, 'operation_id'),
        operation,
        error: operation.error,
      });
    },
  );

  it.each(['accepted', 'running', 'applying', 'cancelling'])(
    'F-043 only a D-9 active %s receipt can report started',
    (status) => {
      const receipt = dashboardCase('control.start_accepted');
      const operation = { ...(own(receipt.body, 'operation') as Record<string, unknown>), status };
      expect(
        classifyStartAnswer({
          status: 200,
          body: { ...(receipt.body as Record<string, unknown>), operation },
        }),
      ).toMatchObject({ kind: 'started' });
    },
  );

  it.each([401, 403, 422, 429])(
    'F-043 other HTTP %i clears a Start receipt as a refusal',
    (status) => {
      expect(classifyStartAnswer({ status, body: { error: 'raw refusal' } })).toEqual({
        kind: 'refused',
        code: undefined,
        error: 'raw refusal',
      });
    },
  );

  it('S-100 real Start and Collect guard refusals retain the scope sent under setup_battle_guard', () => {
    for (const [name, scope] of [
      ['start_refused_by_setup_guard', 'run'],
      ['start_gems_refused_by_collection_guard', 'collection'],
    ] as const) {
      const answer = dashboardCase(`control.${name}`);
      const result = classifyStartAnswer(answer);
      expect(result).toEqual({
        kind: 'guard_refused',
        guard: own(answer.body, 'setup_battle_guard'),
        error: own(answer.body, 'error'),
      });
      if (result.kind !== 'guard_refused') throw new Error('guard receipt required');
      expect(own(result.guard, 'scope')).toBe(scope);
    }
  });

  it('S-102 the real transfer receipt describes the stopped collectors at handover', () => {
    const answer = dashboardCase('control.start_accepted_with_transfer');
    expect(classifyStartAnswer(answer)).toMatchObject({
      kind: 'started',
      transfer: {
        kind: 'handover',
        stoppedAtHandover: ownPath(answer.body, 'transfer', 'stopped'),
        wasStoppingAtHandover: ownPath(answer.body, 'transfer', 'stopping'),
        waitedSec: ownPath(answer.body, 'transfer', 'waited_sec'),
      },
    });
  });

  it('F-043 uncoded context conflicts remain definite refusals without parsing their message', () => {
    for (const name of ['start_context_changed', 'start_gems_context_changed']) {
      const answer = dashboardCase(`control.${name}`);
      expect(answer.body).not.toHaveProperty('code');
      expect(classifyStartAnswer(answer)).toEqual({
        kind: 'refused',
        code: undefined,
        error: own(answer.body, 'error'),
      });
    }
  });

  it('F-014 real clear_flag answers distinguish accepted, refused and outcome unknown without resend', () => {
    for (const [name, kind] of [
      ['clear_flag_accepted', 'cleared'],
      ['clear_flag_refused_for_a_changed_revision', 'refused'],
    ] as const) {
      const answer = dashboardCase(`control.${name}`);
      expect(classifyClearFlagAnswer(answer)).toEqual({
        kind,
        code: own(answer.body, 'code'),
        error: own(answer.body, 'error'),
        resend: false,
      });
    }
    const lost = dashboardCase(
      'control_unknown_outcomes.clear_flag_removed_then_the_answer_failed',
    );
    expect(classifyClearFlagAnswer(lost)).toMatchObject({ kind: 'unknown', resend: false });
    expect(classifyClearFlagAnswer(null)).toMatchObject({ kind: 'unknown', resend: false });
    expect(dashboardBody('status.stop_flag_false_after_the_lost_clear_flag_answer')).toHaveProperty(
      'stop_flag',
      false,
    );
    expect(classifyClearFlagAnswer(lost).kind).toBe('unknown');
  });

  it('S-101 locks every consumed handoff case name', () => {
    expect(dashboardCaseNames('handoff')).toEqual(Object.keys(handoffCases).sort());
  });
  it.each(Object.keys(handoffCases))(
    'S-101 classifies real handoff.%s without granting a run start',
    (name) => {
      const answer = dashboardCase(`handoff.${name}`);
      const result = classifyHandoffAnswer(answer);
      expect(result.kind).toBe(handoffCases[name]);
      if (result.kind === 'recorded') {
        expect(result.handoffId).toBe(ownPath(answer.body, 'handoff', 'id'));
        expect(result.created).toBe(own(answer.body, 'created'));
        expect(result.collectionGuard).toEqual(own(answer.body, 'collection_guard'));
        expect(result.setupBattleGuard).toEqual(own(answer.body, 'setup_battle_guard'));
        expect(result.setupBattleGuard).toMatchObject({ blocks_launch: true, scope: 'run' });
        expect(result.collectionGuard).toMatchObject({ blocks_launch: false, scope: 'collection' });
      } else {
        expect(result.error).toEqual(own(answer.body, 'error'));
      }
    },
  );
  it('S-101 a lost handoff remains unknown and its identical confirmed retry reads the recorded receipt', () => {
    const lost = dashboardCase('write_unknown_outcomes.handoff_recorded_then_the_answer_failed');
    const replay = dashboardCase('write_unknown_outcomes.handoff_identical_retry_created_false');
    expect(replay.request).toEqual(lost.request);
    expect(classifyHandoffAnswer(lost)).toMatchObject({ kind: 'unknown', reconfirm: true });
    expect(classifyHandoffAnswer(replay)).toMatchObject({
      kind: 'recorded',
      created: false,
      handoffId: ownPath(replay.body, 'handoff', 'id'),
    });
    expect(classifyHandoffAnswer(null)).toMatchObject({ kind: 'unknown', reconfirm: true });
  });
});
