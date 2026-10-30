import { describe, expect, it } from 'vitest';
import * as evidence from '../../../src/core/evidence.ts';
import {
  confirmCollecting,
  confirmFree,
  confirmIdle,
  confirmInputStopped,
  confirmStopped,
  readConfirmed,
} from '../../../src/core/evidence.ts';
import { readHarvest } from '../../../src/core/readability.ts';
import { configRead, sampleRecord, serverTimeOf, statusBody } from '../../support/fixture.ts';
import { gateReadOf, readOf, rec } from '../../support/core.ts';

/**
 * The five evidence claims (spec 5.1, ADR rule 2) and R7-01: claims come only
 * from the five checked constructors, each fed by records core issued (a
 * ReadableHarvest, a GateRead, a ReadAssessment); genuine claims here are
 * obtained from valid fixture bodies, never from a timestamp.
 */
const NOW = serverTimeOf(sampleRecord('status.off'));

describe('the five checked constructors', () => {
  it('are the only issuers: evidence.ts exports no generic or timestamp-only issuer (R7-01)', () => {
    expect(Object.keys(evidence).sort()).toEqual([
      'confirmCollecting',
      'confirmFree',
      'confirmIdle',
      'confirmInputStopped',
      'confirmStopped',
      'readConfirmed',
    ]);
  });

  it("issue frozen claims of their own state at the read's server time, from valid fixture bodies", () => {
    const collecting = readHarvest(sampleRecord('status.collecting'));
    const off = gateReadOf(statusBody(sampleRecord('status.off')));
    const input = sampleRecord('status.off');
    input['input_revoked'] = true;
    const facts = [
      collecting.kind === 'known' ? confirmCollecting(collecting.value) : collecting,
      confirmIdle(off, []),
      confirmFree(off, []),
      confirmStopped({ operationId: 'gone-operation', pid: null }, off),
      confirmInputStopped({
        turnOff: { answer: sampleRecord('automation_access.disable_input_stopped'), answerSeq: 1 },
        enables: [],
        status: readOf(statusBody(input), 2),
        config: configRead(false, 2),
      }),
    ];
    expect(
      facts.map((fact) => (fact.kind === 'known' ? readConfirmed(fact.value) : fact.reason)),
    ).toEqual([
      { state: 'collecting', observedAt: NOW },
      { state: 'idle', observedAt: NOW },
      { state: 'free', observedAt: NOW },
      { state: 'stopped', observedAt: NOW },
      { state: 'input_stopped', observedAt: NOW },
    ]);
    for (const fact of facts) {
      expect(fact.kind === 'known' && Object.isFrozen(fact.value)).toBe(true);
    }
  });
});

describe('EV-7 decides supersession and earlier enables itself (S-040, S-047, S-048)', () => {
  const answer = sampleRecord('automation_access.disable_input_stopped');
  const revoked = (): Record<string, unknown> => {
    const harvest = sampleRecord('status.off');
    harvest['input_revoked'] = true;
    return harvest;
  };
  const facts = (overrides: Partial<Parameters<typeof confirmInputStopped>[0]> = {}) => ({
    turnOff: { answer, answerSeq: 5 },
    enables: [],
    status: readOf(statusBody(revoked()), 6),
    config: configRead(false, 6),
    ...overrides,
  });
  const reason = (input: Parameters<typeof confirmInputStopped>[0]) => {
    const fact = confirmInputStopped(input);
    return fact.kind === 'known' ? 'confirmed' : fact.reason;
  };

  it('S-040 control: a proving answer, a later D-4 read with input_revoked true and a later config read with allow_taps false', () => {
    expect(reason(facts())).toBe('confirmed');
  });

  it('S-047 a newer enable request, even pending, supersedes the answer', () => {
    expect(reason(facts({ enables: [{ sentSeq: 7, settled: false }] }))).toBe('superseded');
    expect(reason(facts({ enables: [{ sentSeq: 7, settled: true }] }))).toBe('superseded');
  });

  it('S-047 a later config read with allow_taps true supersedes it', () => {
    expect(reason(facts({ config: configRead(true, 6) }))).toBe('superseded');
  });

  it('S-047 a later readable status without input_revoked true supersedes it', () => {
    const off = sampleRecord('status.off');
    expect(reason(facts({ status: readOf(statusBody(off), 6) }))).toBe('superseded');
  });

  it('S-048 an earlier enable still unresolved blocks it; a settled one does not', () => {
    expect(reason(facts({ enables: [{ sentSeq: 3, settled: false }] }))).toBe(
      'earlier_enable_unresolved',
    );
    expect(reason(facts({ enables: [{ sentSeq: 3, settled: true }] }))).toBe('confirmed');
  });

  it('S-040 reads that started before the answer, a missing answer or a non-proving one never confirm', () => {
    expect(reason(facts({ status: readOf(statusBody(revoked()), 4) }))).toBe('read_not_later');
    expect(reason(facts({ config: configRead(false, 4) }))).toBe('no_later_config');
    expect(reason(facts({ turnOff: null }))).toBe('no_turn_off_answer');
    expect(
      reason(
        facts({
          turnOff: { answer: sampleRecord('automation_access.disable_stopping'), answerSeq: 5 },
        }),
      ),
    ).toBe('answer_not_proving');
  });
});

describe('EV-5 and EV-6 decide lease holders themselves (S-073)', () => {
  const lease = rec(rec(sampleRecord('refusals.device_owned_lease')['body'])['owner']);
  const listed = { ...lease, lease: { held: true, holders: [{ pid: 4242, role: 'primary' }] } };
  const unlisted = { ...lease, lease: { held: true, holders: [] } };
  const off = statusBody(sampleRecord('status.off'));

  it('S-073 a listed holder absent from a later D-4 read is resolved: idle and free hold', () => {
    const gate = gateReadOf(off, null, 3);
    expect(confirmIdle(gate, [{ owner: listed, learnedSeq: 2 }]).kind).toBe('known');
    expect(confirmFree(gate, [{ owner: listed, learnedSeq: 2 }]).kind).toBe('known');
  });

  it('S-073 an unlisted holder, a read not later than the notice, or the holder PID still listed keeps them unknown', () => {
    const gate = gateReadOf(off, null, 3);
    expect(confirmIdle(gate, [{ owner: unlisted, learnedSeq: 2 }]).kind).toBe('unknown');
    expect(confirmFree(gate, [{ owner: listed, learnedSeq: 3 }]).kind).toBe('unknown');
    const withHolder = statusBody(sampleRecord('status.off'), { processes: [{ pid: 4242 }] });
    expect(
      confirmFree(gateReadOf(withHolder, null, 3), [{ owner: listed, learnedSeq: 2 }]).kind,
    ).toBe('unknown');
  });
});
