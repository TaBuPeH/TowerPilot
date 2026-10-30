import { describe, expect, it } from 'vitest';
import { gateOf } from '../../../src/core/gate.ts';
import {
  choiceView,
  knownOn,
  offConfirmed,
  onFromRead,
  pauseOffered,
  settlePause,
} from '../../../src/core/harvestChoice.ts';
import { turnOnView } from '../../../src/core/harvestView.ts';
import { readHarvest } from '../../../src/core/readability.ts';
import { sample, sampleRecord, statusBody } from '../../support/fixture.ts';
import { contextOf, gateReadOf, readOf, rec, TURN_ON_READY } from '../../support/core.ts';

const ALLOW = { blocks_launch: false, scope: 'collection', code: null };

function offAt(revision: unknown, enabled: unknown = false): Record<string, unknown> {
  const harvest = sampleRecord('status.off');
  rec(harvest['desired'])['revision'] = revision;
  rec(harvest['desired'])['enabled'] = enabled;
  return harvest;
}

const unreadable: [string, unknown][] = [
  ['harvest null', null],
  ['harvest omitted', undefined],
  ['version 2', { ...sampleRecord('status.off'), version: 2 }],
  ['status_failed', sample('status.status_failed')],
];

describe('S-001 Pause shown whenever On may be in effect or the choice is unknown; hidden only on D-8', () => {
  const offRevision = rec(sampleRecord('status.off')['desired'])['revision'] as number;

  it('S-001 (a) a Turn on whose answer was dropped keeps Pause while the status stays Off', () => {
    expect(
      pauseOffered(readHarvest(offAt(offRevision)), null, [{ expectedRevision: offRevision }]),
    ).toBe(true);
  });

  it.each(unreadable)(
    'S-001 (b) a held Turn on and a status with %s shows Pause',
    (_label, harvest) => {
      expect(pauseOffered(readHarvest(harvest), null, [{ expectedRevision: offRevision }])).toBe(
        true,
      );
      expect(pauseOffered(readHarvest(harvest), null, [])).toBe(true);
    },
  );

  const afterKnownOn: [string, unknown][] = [
    ...unreadable,
    ['Off rev 4', offAt(4)],
    ['Off rev 5', offAt(5)],
    ['Off rev 5.5', offAt(5.5)],
    ['Off rev 6 with enabled true', offAt(6, true)],
    ['a shell {version:1}', { version: 1 }],
  ];

  it.each(afterKnownOn)(
    'S-001 (c) after enable_ok at rev 5, a status with %s shows Pause',
    (_label, harvest) => {
      const known = knownOn([{ revision: 5, learnedSeq: 1 }]);
      expect(pauseOffered(readHarvest(harvest), known, [])).toBe(true);
    },
  );

  it('S-001 control: after (c) a later valid Off rev 6 hides Pause; a cold Off with no On shows none', () => {
    const known = knownOn([{ revision: 5, learnedSeq: 1 }]);
    expect(pauseOffered(readHarvest(offAt(6)), known, [])).toBe(false);
    expect(pauseOffered(readHarvest(sampleRecord('status.off')), null, [])).toBe(false);
  });

  it('S-001 with no status read at all Pause is shown (spec 9.1 X1)', () => {
    expect(pauseOffered(null, null, [])).toBe(true);
  });

  it('S-001 a lost On is settled only by a read whose revision is above its expected_revision (D-8)', () => {
    expect(
      offConfirmed(readHarvest(offAt(offRevision + 1)), null, [{ expectedRevision: offRevision }]),
    ).toBe(true);
    expect(offConfirmed(readHarvest(offAt(offRevision)), null, [{ expectedRevision: null }])).toBe(
      false,
    );
  });

  it('S-001 the Known On revision comes from enable_ok (D-7)', () => {
    const ack = sampleRecord('post_harvest.enable_ok');
    const k = rec(rec(ack['body'])['desired'])['revision'] as number;
    const fromRead = readHarvest(rec(rec(ack['body'])['harvest']));
    expect(fromRead.kind === 'known' && onFromRead(fromRead.value, 3)).toEqual({
      revision: k,
      learnedSeq: 3,
    });
    expect(
      knownOn([
        { revision: 2, learnedSeq: 4 },
        { revision: 5, learnedSeq: 9 },
        { revision: 5, learnedSeq: 7 },
      ]),
    ).toEqual({
      revision: 5,
      learnedSeq: 7,
    });
  });
});

describe('S-025 a Pause confirmation needs choice Off or unset in the same D-4 read', () => {
  const refusal = sampleRecord('refusals.device_owned_collector');
  const A = rec(rec(refusal['body'])['owner'])['operation_id'] as string;

  it('S-025 the refusal recovers with pause_harvesting for operation A', () => {
    expect(rec(rec(refusal['body'])['recovery'])['action']).toBe('pause_harvesting');
    expect(A).toMatch(/\S/);
  });

  it('S-025 a later read with fresh [] and harvest status_failed stays unresolved; Pause still offered', () => {
    const read = readOf(statusBody(sample('status.status_failed')), 5);
    expect(gateOf(read, 4).kind).toBe('unknown');
    expect(pauseOffered(read.harvest, null, [])).toBe(true);
  });

  it('S-025 a complete read with A absent but choice On is not a confirmed Pause', () => {
    const on = sampleRecord('status.starting_due');
    expect(settlePause([A], gateReadOf(statusBody(on), 4, 5))).toEqual({
      kind: 'not_confirmed',
      reason: 'choice_not_off',
    });
  });

  it('S-025 control: a complete Off read with A absent resolves it', () => {
    expect(settlePause([A], gateReadOf(statusBody(offAt(99)), 4, 5)).kind).toBe('stopped');
  });
});

describe('S-032 a malformed or contradictory choice reads "choice unknown"', () => {
  const variants: [string, (desired: Record<string, unknown>) => void][] = [
    ["{choice:'off', enabled:true}", (d) => (d['enabled'] = true)],
    ['revision 4.5', (d) => (d['revision'] = 4.5)],
    ['revision -1', (d) => (d['revision'] = -1)],
    ["choice 'ON'", (d) => (d['choice'] = 'ON')],
  ];

  it.each(variants)(
    'S-032 desired %s: choice unknown, Pause shown, no Turn on',
    (_label, mutate) => {
      const harvest = sampleRecord('status.off');
      mutate(rec(harvest['desired']));
      const fact = readHarvest(harvest);
      expect(choiceView(fact, null).word).toBe('unknown');
      expect(pauseOffered(fact, null, [])).toBe(true);
      expect(turnOnView(contextOf(statusBody(harvest)), ALLOW, TURN_ON_READY).offered).toBe(false);
    },
  );

  it.each([
    ['desired []', []],
    ['desired omitted', undefined],
  ])('S-032 %s: choice unknown, Pause shown, no Turn on', (_label, desired) => {
    const harvest = sampleRecord('status.off');
    if (desired === undefined) delete harvest['desired'];
    else harvest['desired'] = desired;
    const fact = readHarvest(harvest);
    expect(choiceView(fact, null).word).toBe('unknown');
    expect(pauseOffered(fact, null, [])).toBe(true);
    expect(turnOnView(contextOf(statusBody(harvest)), ALLOW, TURN_ON_READY).offered).toBe(false);
  });

  it('S-032 control: status.off offers Turn on', () => {
    const view = turnOnView(
      contextOf(statusBody(sampleRecord('status.off'))),
      ALLOW,
      TURN_ON_READY,
    );
    expect(view.offered).toBe(true);
  });
});

describe('S-033 after a Known On an Off that is not strictly newer is stale and qualified', () => {
  const known = knownOn([{ revision: 5, learnedSeq: 1 }]);

  it.each([4, 5])(
    'S-033 Off rev %i reads "Off (from before your Turn on)" and keeps Pause',
    (revision) => {
      const fact = readHarvest(offAt(revision));
      expect(choiceView(fact, known)).toEqual({
        word: 'off',
        qualifier: 'from_before_turn_on',
        reason: null,
      });
      expect(pauseOffered(fact, known, [])).toBe(true);
    },
  );

  it('S-033 control: a consistent Off rev 6 reads plain Off and hides Pause', () => {
    const fact = readHarvest(offAt(6));
    expect(choiceView(fact, known)).toEqual({ word: 'off', qualifier: null, reason: null });
    expect(pauseOffered(fact, known, [])).toBe(false);
  });
});
