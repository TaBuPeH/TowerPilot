import { describe, expect, it } from 'vitest';
import { collectionEvidence } from '../../../src/core/collection.ts';
import { tapsSetting } from '../../../src/core/config.ts';
import { pauseOffered } from '../../../src/core/harvestChoice.ts';
import { gemSeen } from '../../../src/core/harvestView.ts';
import { gameInputSeen, inputOffOffered, type GameInputFacts } from '../../../src/core/input.ts';
import { harvestView, inputRevoked, readHarvest } from '../../../src/core/readability.ts';
import { configRead, sampleRecord, statusBody } from '../../support/fixture.ts';
import { contextOf, readOf, rec } from '../../support/core.ts';

function facts(body: unknown): GameInputFacts {
  return {
    config: configRead(false),
    status: readOf(body),
    enables: [],
    turnOff: null,
    leaseHolders: [],
    retainedHolder: false,
  };
}

describe('D-1 readable harvest', () => {
  it('accepts every agreeing choice and refuses contradictions, fractions and failure codes', () => {
    for (const [choice, enabled, ok] of [
      ['on', true, true],
      ['off', false, true],
      ['unset', null, true],
      ['off', true, false],
      ['on', false, false],
      ['ON', true, false],
      ['unset', false, false],
    ] as const) {
      const harvest = sampleRecord('status.off');
      rec(harvest['desired'])['choice'] = choice;
      rec(harvest['desired'])['enabled'] = enabled;
      expect({ choice, enabled, known: readHarvest(harvest).kind === 'known' }).toEqual({
        choice,
        enabled,
        known: ok,
      });
    }
    for (const code of ['status_failed', 'harvest_state_unreadable']) {
      const harvest = sampleRecord('status.off');
      harvest['code'] = code;
      expect(readHarvest(harvest)).toEqual({ kind: 'unknown', reason: code });
    }
    for (const bad of [null, undefined, [], 'x', { ...sampleRecord('status.off'), version: 2 }]) {
      expect(readHarvest(bad).kind).toBe('unknown');
    }
  });

  it('issues a frozen record (its brand is type-only: tests/core/readable.test-d.ts)', () => {
    const fact = readHarvest(sampleRecord('status.collecting'));
    expect(fact.kind).toBe('known');
    if (fact.kind === 'known') {
      expect(Object.isFrozen(fact.value)).toBe(true);
      expect(Object.isFrozen(harvestView(fact.value))).toBe(true);
    }
  });
});

describe('S-007 input_revoked counts only as boolean true in a D-1 record', () => {
  it('S-007 a contradictory record with input_revoked true still shows Turn input off with state unknown', () => {
    const harvest = sampleRecord('status.off');
    rec(harvest['desired'])['enabled'] = true;
    harvest['input_revoked'] = true;
    const body = statusBody(harvest);
    const input = gameInputSeen(facts(body));
    expect(input.word).toBe('unknown');
    expect(inputOffOffered(facts(body), input)).not.toBeNull();
    expect(tapsSetting(configRead(false))).toMatchObject({ kind: 'known', allowTaps: false });
  });

  it.each([
    ['"true"', 'true'],
    ['{}', {}],
    ['1', 1],
  ])('S-007 input_revoked %s neither revokes nor allows collecting', (_label, value) => {
    const harvest = sampleRecord('status.collecting');
    harvest['input_revoked'] = value;
    const fact = readHarvest(harvest);
    expect(fact.kind).toBe('known');
    if (fact.kind === 'known') {
      expect(inputRevoked(fact.value)).toBe(false);
      expect(collectionEvidence(fact.value).kind).toBe('unknown');
    }
    expect(gemSeen(contextOf(statusBody(harvest))).word).not.toBe('running');
  });

  it('S-007 control: a valid Off record with input_revoked true counts', () => {
    const harvest = sampleRecord('status.input_revoked');
    const fact = readHarvest(harvest);
    expect(fact.kind === 'known' && inputRevoked(fact.value)).toBe(true);
  });
});

describe('S-031 collection and ownership helpers receive only gate-accepted records', () => {
  const variants: [string, (harvest: Record<string, unknown>) => void][] = [
    ['choice off with enabled true', (harvest) => (rec(harvest['desired'])['choice'] = 'off')],
    ['revision 4.5', (harvest) => (rec(harvest['desired'])['revision'] = 4.5)],
  ];

  it.each(variants)(
    'S-031 %s reads choice unknown and collector unknown, cold and warm',
    (_label, mutate) => {
      const valid = statusBody(sampleRecord('status.collecting'));
      const harvest = sampleRecord('status.collecting');
      mutate(harvest);
      const body = statusBody(harvest);
      expect(readHarvest(harvest).kind).toBe('unknown');
      // cold: the first read is the mutated one
      const cold = contextOf(body);
      expect(gemSeen(cold)).toMatchObject({ row: 'G1', word: 'unknown' });
      // warm: a valid collecting read came first; the newest answered read decides
      const warm = { newest: readOf(body, 3), lastGood: null, leaseHolders: [] };
      expect(gemSeen({ ...warm, lastGood: { read: readOf(valid, 2), ageSec: 1 } })).toMatchObject({
        row: 'G1',
      });
      expect(pauseOffered(readOf(body).harvest, null, [])).toBe(true);
      const input = gameInputSeen(facts(body));
      expect(inputOffOffered(facts(body), input)).not.toBeNull();
    },
  );

  it('S-031 control: the unmodified sample is collecting, cold and warm', () => {
    const body = statusBody(sampleRecord('status.collecting'));
    expect(gemSeen(contextOf(body))).toMatchObject({
      row: 'G3a',
      word: 'running',
      evidence: 'EV-3',
    });
    expect(
      gemSeen({
        newest: readOf(body, 3),
        lastGood: { read: readOf(body, 2), ageSec: 1 },
        leaseHolders: [],
      }),
    ).toMatchObject({ row: 'G3a' });
  });
});
