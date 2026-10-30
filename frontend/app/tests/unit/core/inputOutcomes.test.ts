import { describe, expect, it } from 'vitest';
import { gameInputSeen, type GameInputFacts } from '../../../src/core/input.ts';
import { turnOffSuperseded } from '../../../src/core/inputFacts.ts';
import { classifyInputAnswer, provesInputStopped } from '../../../src/core/outcomes/input.ts';
import { configRead, sampleRecord, statusBody } from '../../support/fixture.ts';
import { readOf, rec } from '../../support/core.ts';

function answer(name: string): Record<string, unknown> {
  return sampleRecord(`automation_access.${name}`);
}

function revocation(value: Record<string, unknown>): Record<string, unknown> {
  return rec(rec(value['body'])['revocation']);
}

describe('S-040 confirmed input-off only from a proving answer', () => {
  it.each(['disable_input_stopped', 'disable_driver_input_refused'])(
    'S-040 %s proves input stopped',
    (name) => {
      expect(provesInputStopped(answer(name))).toBe(true);
      expect(classifyInputAnswer(answer(name), 'disable').kind).toBe('input_stopped');
    },
  );

  it('S-040 disable_nothing_running proves "nothing was running"', () => {
    expect(provesInputStopped(answer('disable_nothing_running'))).toBe(true);
    expect(classifyInputAnswer(answer('disable_nothing_running'), 'disable').kind).toBe(
      'nothing_running',
    );
  });

  const mutations: [string, unknown[]][] = [
    ["[{input:'refused'}]", [{ input: 'refused' }]],
    [
      "[{kind:'process', pid:-1, input:'refused'}]",
      [{ kind: 'process', pid: -1, input: 'refused' }],
    ],
    [
      'an operation owner with a numeric operation_id',
      [{ kind: 'operation', operation_id: 31, pid: 1, input: 'refused' }],
    ],
    ['[null]', [null]],
    ['[[]]', [[]]],
    ['an entry without input', [{ kind: 'operation', operation_id: 'x', pid: 1 }]],
  ];

  it.each(mutations)('S-040 still_owned %s is never green', (_label, stillOwned) => {
    const value = answer('disable_driver_input_refused');
    revocation(value)['still_owned'] = stillOwned;
    expect(provesInputStopped(value)).toBe(false);
    expect(classifyInputAnswer(value, 'disable').kind).not.toBe('input_stopped');
  });

  it('S-040 the H2UI-02 answer (stopping, fenced false, an unfenced run) is not green', () => {
    const value = answer('disable_input_stopped');
    Object.assign(revocation(value), {
      state: 'stopping',
      fenced: false,
      latched: true,
      stopping: [],
      still_owned: [{ operation_kind: 'run', input: 'unfenced' }],
    });
    expect(provesInputStopped(value)).toBe(false);
    expect(classifyInputAnswer(value, 'disable').kind).not.toMatch(
      /^(input_stopped|nothing_running)$/,
    );
  });

  it('S-040 the answer alone is a past result: the chip needs a later D-4 read and config read (EV-7)', () => {
    const facts: GameInputFacts = {
      config: configRead(false, 1),
      status: readOf(statusBody(sampleRecord('status.input_revoked')), 1),
      enables: [],
      turnOff: { answer: answer('disable_input_stopped'), answerSeq: 5 },
      leaseHolders: [],
      retainedHolder: false,
    };
    expect(gameInputSeen(facts)).toMatchObject({ row: 'I4a', word: 'unknown' });
    const later = {
      ...facts,
      config: configRead(false, 6),
      status: readOf(statusBody(sampleRecord('status.input_revoked')), 6),
    };
    expect(gameInputSeen(later)).toMatchObject({ row: 'I4', word: 'idle', evidence: 'EV-7' });
  });
});

describe('S-041 not_enforced or an unfenced owner is a warning naming the owner and its way out', () => {
  it('S-041 disable_not_enforced names the owner and its Stop recovery', () => {
    const result = classifyInputAnswer(answer('disable_not_enforced'), 'disable');
    expect(result.kind).toBe('not_enforced');
    if (result.kind === 'not_enforced') {
      const listed = revocation(answer('disable_not_enforced'))['still_owned'] as Record<
        string,
        unknown
      >[];
      expect(result.owners).toHaveLength(1);
      expect(result.owners[0]?.owner).toMatchObject({
        kind: 'operation',
        operationId: listed[0]?.['operation_id'],
      });
      expect(result.owners[0]?.recovery).toMatchObject({ action: 'stop_operation' });
    }
  });

  it('S-041 control: disable_driver_input_refused stays green', () => {
    expect(classifyInputAnswer(answer('disable_driver_input_refused'), 'disable').kind).toBe(
      'input_stopped',
    );
  });
});

describe('S-041 and S-044 a mixed answer keeps every fact', () => {
  it('S-041 stopping with failed steps still names each unfenced owner with its way out (S-044)', () => {
    const mixed = answer('disable_stopping');
    const unfencedOwners = revocation(answer('disable_not_enforced'))['still_owned'] as Record<
      string,
      unknown
    >[];
    revocation(mixed)['still_owned'] = unfencedOwners;
    revocation(mixed)['errors'] = [{ step: 'lease_state', message: 'injected lease scan failure' }];
    const result = classifyInputAnswer(mixed, 'disable');
    expect(result.kind).toBe('stopping_with_errors');
    if (result.kind === 'stopping_with_errors') {
      expect(result.stopping).toEqual(revocation(answer('disable_stopping'))['stopping']);
      expect(result.errors.map((error) => error.step)).toEqual(['lease_state']);
      expect(result.unfenced.map((entry) => entry.owner)).toEqual([
        expect.objectContaining({
          kind: 'operation',
          operationId: unfencedOwners[0]?.['operation_id'],
        }),
      ]);
      expect(result.unfenced[0]?.recovery).toMatchObject({ action: 'stop_operation' });
    }
  });

  it('S-041 disable_stopping names its own unfenced collector beside the stopping list', () => {
    const result = classifyInputAnswer(answer('disable_stopping'), 'disable');
    expect(result.kind).toBe('stopping');
    if (result.kind === 'stopping') {
      expect(result.unfenced).toHaveLength(1);
      expect(result.errors).toEqual([]);
    }
  });
});

describe('S-042 stopping is unconfirmed; confirmed only through S-020 with choice Off', () => {
  const stopping = answer('disable_stopping');
  const collector = (revocation(stopping)['stopping'] as string[])[0] ?? '';

  function facts(body: unknown): GameInputFacts {
    return {
      config: configRead(false, 6),
      status: readOf(body, 6),
      enables: [],
      turnOff: { answer: stopping, answerSeq: 5 },
      leaseHolders: [],
      retainedHolder: false,
    };
  }

  it('S-042 with its collector still listed the chip reads Stopping "turn-off not confirmed for {collector}" (I3)', () => {
    const harvest = sampleRecord('status.off');
    harvest['input_revoked'] = true;
    const body = statusBody(harvest, {
      processes: [{ pid: 4242, operation_id: collector, operation_revision: 2 }],
    });
    expect(classifyInputAnswer(stopping, 'disable').kind).toBe('stopping');
    expect(gameInputSeen(facts(body))).toMatchObject({
      row: 'I3',
      word: 'stopping',
      line: 'turn_off_not_confirmed_for',
      params: { collectors: [collector] },
    });
  });

  it('S-042 an unlinked process with the collector PID keeps it stopping (D-5), a different PID does not', () => {
    const owner = (revocation(stopping)['still_owned'] as Record<string, unknown>[])[0] ?? {};
    const harvest = sampleRecord('status.off');
    harvest['input_revoked'] = true;
    const samePid = statusBody(harvest, { processes: [{ pid: owner['pid'] }] });
    expect(gameInputSeen(facts(samePid))).toMatchObject({ row: 'I3', word: 'stopping' });
    const otherPid = statusBody(harvest, {
      processes: [{ pid: (owner['pid'] as number) + 1, created: 1 }],
    });
    expect(gameInputSeen(facts(otherPid)).row).not.toBe('I3');
  });

  it('S-042 a collector whose PID the answer does not name is not proven gone by an unlinked process', () => {
    const unnamed = answer('disable_stopping');
    revocation(unnamed)['still_owned'] = [];
    const harvest = sampleRecord('status.off');
    harvest['input_revoked'] = true;
    const body = statusBody(harvest, { processes: [{ pid: 5555 }] });
    const result = gameInputSeen({ ...facts(body), turnOff: { answer: unnamed, answerSeq: 5 } });
    expect(result).toMatchObject({ row: 'I3', word: 'stopping' });
  });

  it('S-042 a later complete read with that id gone and choice Off confirms it (no longer I3)', () => {
    const harvest = sampleRecord('status.off');
    harvest['input_revoked'] = true;
    expect(gameInputSeen(facts(statusBody(harvest))).row).not.toBe('I3');
  });

  it('S-042 a later read with choice On (status.starting_due) does not confirm it; the answer becomes history', () => {
    const f = facts(statusBody(sampleRecord('status.starting_due')));
    expect(turnOffSuperseded(f)).toBe(true);
    const result = gameInputSeen(f);
    // The newest facts decide the chip (I5 by EV-6 on this complete read), never the answer (I3, I4, EV-7).
    expect(result.row).not.toMatch(/^I(3|4|4a)$/);
    expect(result.evidence).not.toBe('EV-7');
  });
});

describe('S-043 HTTP 500 input_revocation_failed is never confirmed; body.error shown verbatim', () => {
  it('S-043 disable_marker_write_failed: not green, input NOT revoked, what was done listed separately', () => {
    const value = answer('disable_marker_write_failed');
    expect(provesInputStopped(value)).toBe(false);
    const result = classifyInputAnswer(value, 'disable');
    expect(result).toMatchObject({ kind: 'revocation_failed', settingSaved: true });
    if (result.kind === 'revocation_failed') {
      expect(result.error).toBe(rec(value['body'])['error']);
      expect(result.harvestPaused).toMatchObject({ paused: true });
      expect(result.errors.map((error) => error.step)).toEqual(['revoke_input']);
      expect(result.stopped.length).toBeGreaterThan(0);
    }
  });

  it('S-043 control: changing body.error changes only the shown error', () => {
    const value = answer('disable_marker_write_failed');
    rec(value['body'])['error'] = 'A different server message.\nSecond line.';
    const result = classifyInputAnswer(value, 'disable');
    expect(result).toMatchObject({
      kind: 'revocation_failed',
      error: 'A different server message.\nSecond line.',
    });
  });
});

describe('S-044 stopping with errors names the failed steps, with a retry', () => {
  it('S-044 disable_lease_scan_failed: "not confirmed: couldn\'t check the device lease"', () => {
    const value = answer('disable_lease_scan_failed');
    const result = classifyInputAnswer(value, 'disable');
    expect(result.kind).toBe('stopping_with_errors');
    if (result.kind === 'stopping_with_errors') {
      expect(result.errors.map((error) => error.step)).toEqual(['lease_state']);
    }
    const facts: GameInputFacts = {
      config: configRead(false, 6),
      status: readOf(statusBody(sampleRecord('status.input_revoked')), 6),
      enables: [],
      turnOff: { answer: value, answerSeq: 5 },
      leaseHolders: [],
      retainedHolder: false,
    };
    expect(gameInputSeen(facts)).toMatchObject({
      row: 'I3',
      line: 'turn_off_step_failed',
      params: { steps: ['lease_state'] },
    });
  });

  it('S-044 control: disable_stopping without errors names its stopping collectors instead', () => {
    const result = classifyInputAnswer(answer('disable_stopping'), 'disable');
    expect(result.kind).toBe('stopping');
    if (result.kind === 'stopping') {
      expect(result.stopping.length).toBeGreaterThan(0);
    }
  });
});

describe('S-045 config_persisted is its own fact; false downgrades green to a warning', () => {
  it('S-045 config_persisted false is a warning, not green', () => {
    const value = answer('disable_input_stopped');
    revocation(value)['config_persisted'] = false;
    expect(classifyInputAnswer(value, 'disable').kind).toBe('input_stopped_not_saved');
  });

  it('S-045 control: true keeps green', () => {
    expect(classifyInputAnswer(answer('disable_input_stopped'), 'disable')).toMatchObject({
      kind: 'input_stopped',
      settingSaved: true,
    });
  });
});

describe('enable answers', () => {
  it('classifies enable_ok, enable_refused_owner (S-049 notice) and enable_during_action_lock', () => {
    expect(classifyInputAnswer(answer('enable_ok'), 'enable').kind).toBe('enabled');
    expect(classifyInputAnswer(answer('enable_refused_owner'), 'enable').kind).toBe(
      'enable_refused_owner',
    );
    expect(classifyInputAnswer(answer('enable_during_action_lock'), 'enable')).toMatchObject({
      kind: 'enable_refused',
      code: 'action_lock',
    });
    expect(classifyInputAnswer(null, 'enable').kind).toBe('unknown');
  });
});
