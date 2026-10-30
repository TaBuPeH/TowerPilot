import { describe, expect, it } from 'vitest';
import { serverTextContent } from '../../../src/copy/server.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { sample, sampleNames, sampleRecord, statusBody } from '../../support/fixture.ts';
import { storeHarness } from '../../support/storeHarness.ts';
import { MATRIX } from '../spec/specData.ts';

/**
 * Spec 7.3: every chip of every fixture status sample, drawn through the
 * store's selectors (not core directly), against the tables of section 3.6 as
 * the 9.1 matrix records them under E0 and E1; and S-027: Idle needs positive
 * evidence. An observed word always carries the evidence id behind it.
 */
const WORD: Readonly<Record<string, string>> = {
  running: 'Running',
  waiting: 'Waiting',
  paused: 'Paused',
  idle: 'Idle',
  stopping: 'Stopping',
  unknown: 'Unknown',
  blocked: 'Blocked',
  failed: 'Failed',
};

function row(harvest: unknown, allowTaps: boolean) {
  const h = storeHarness({ config: e0Config(allowTaps) });
  h.read('status', statusBody(harvest));
  const state = h.state();
  const gem = selectGemSlot(state);
  const input = selectInputSlot(state);
  const automation = selectAutomationSlot(state);
  const targets = automation.rows.flatMap((entry) =>
    entry.stop.command?.kind === 'cancel'
      ? [
          `${entry.stop.command.operationId.slice(0, 8)}@${entry.stop.command.expectedRevision.toString()}`,
        ]
      : [],
  );
  for (const chip of [gem.seen, input.seen, automation.word]) {
    if (chip.word === 'running' || chip.word === 'paused' || chip.word === 'idle') {
      expect(chip.evidence, `${chip.row} needs its evidence`).not.toBeNull();
    }
  }
  return {
    cells: [
      gem.pause.shown ? 'shown' : 'hidden',
      input.turnOff.shown ? 'shown' : 'hidden',
      targets.length === 0 ? 'none' : targets.join(', '),
      WORD[gem.seen.word ?? ''] ?? 'plain text',
      WORD[input.seen.word ?? ''] ?? 'plain text',
      WORD[automation.word.word ?? ''] ?? 'plain text',
    ],
    gem,
    input,
    automation,
  };
}

describe('every status sample through the store (spec 7.3, 9.1)', () => {
  it('covers exactly the status samples of the loaded fixture', () => {
    expect(MATRIX.map((entry) => entry.sample)).toEqual(sampleNames('status'));
  });

  it.each(MATRIX.map((entry) => [entry.sample, entry] as const))('%s (E0)', (name, expected) => {
    expect(row(sample(`status.${name}`), false).cells).toEqual([
      expected.pause,
      expected.turnInputOff,
      expected.stopTargets,
      expected.gem,
      expected.input,
      expected.automation,
    ]);
  });

  it.each(MATRIX.map((entry) => [entry.sample, entry] as const))('%s (E1)', (name, expected) => {
    expect(row(sample(`status.${name}`), true).cells).toEqual([
      expected.pause,
      'shown',
      expected.stopTargets,
      expected.gem,
      expected.input,
      expected.automation,
    ]);
  });

  it('a Stop target names its operation as server text, never as raw data', () => {
    const { automation } = row(sample('status.collecting'), false);
    const operation = automation.rows[0]?.operation;
    if (operation === null || operation === undefined) throw new Error('no operation');
    expect(serverTextContent(operation)).toMatch(/^[0-9a-f-]+$/);
  });
});

describe('S-027 Idle needs positive evidence', () => {
  const cases: readonly [string, () => unknown][] = [
    ['worker_unverified, fresh []', () => sample('status.worker_unverified')],
    [
      'collecting with worker_alive null',
      () => {
        const harvest = sampleRecord('status.collecting');
        (harvest['operation'] as Record<string, unknown>)['worker_alive'] = null;
        return harvest;
      },
    ],
    ['status_failed', () => sample('status.status_failed')],
    ['state_unreadable', () => sample('status.state_unreadable')],
    ['harvest null', () => null],
    [
      'harvest version 2 with a retained operation',
      () => ({ ...sampleRecord('status.stopping'), version: 2 }),
    ],
  ];

  it.each(cases)(
    'S-027 %s: state unknown with Pause, Stop where known and Turn input off; never idle',
    (_name, harvest) => {
      const { gem, input, automation } = row(harvest(), false);
      expect([gem.seen.word, input.seen.word, automation.word.word]).not.toContain('idle');
      expect(gem.pause.shown).toBe(true);
      expect(input.turnOff.shown).toBe(true);
    },
  );

  it('S-027 a fresh-labelled scan aged 180 s is never idle', () => {
    const h = storeHarness({ config: e0Config(false) });
    const off = sample('status.off');
    h.read(
      'status',
      statusBody(off, { scan: { state: 'fresh', error: null, age_sec: 180, last_success_at: 0 } }),
    );
    expect(selectAutomationSlot(h.state()).word.word).not.toBe('idle');
    expect(selectGemSlot(h.state()).seen.word).not.toBe('idle');
  });

  it('S-027 control: S(F.status.off, [], fresh) is idle (EV-5); a listed collector is running (EV-1)', () => {
    expect(row(sample('status.off'), false).automation.word).toMatchObject({
      word: 'idle',
      evidence: 'EV-5',
    });
    const h = storeHarness({ config: e0Config(false) });
    h.read(
      'status',
      statusBody(sample('status.off'), {
        processes: [{ pid: 4101, runner: 'gem_collector', created: 1.5 }],
      }),
    );
    expect(selectAutomationSlot(h.state()).word).toMatchObject({
      word: 'running',
      evidence: 'EV-1',
    });
  });
});

describe('A-01 a confirmed state reaches the view model with its claim', () => {
  const CLAIMED: Readonly<Record<string, string>> = {
    'EV-3': 'collecting',
    'EV-5': 'idle',
    'EV-6': 'free',
    'EV-7': 'input_stopped',
    'EV-8': 'stopped',
  };

  it.each(sampleNames('status'))(
    'S-027 status.%s: every chip of EV-3, EV-5 to EV-8 carries the core claim of its state; no other chip carries one',
    (name) => {
      const h = storeHarness({ config: e0Config(false) });
      h.read('status', statusBody(sample(`status.${name}`)));
      const state = h.state();
      for (const chip of [
        selectGemSlot(state).seen,
        selectInputSlot(state).seen,
        selectAutomationSlot(state).word,
      ]) {
        const claimed = chip.evidence === null ? undefined : CLAIMED[chip.evidence];
        if (claimed === undefined) {
          expect(chip.claim).toBeNull();
        } else {
          expect(chip.claim).toMatchObject({ state: claimed });
          expect(Number.isFinite(chip.claim?.observedAt)).toBe(true);
        }
      }
    },
  );

  it('S-027 control: the Off sample shows Idle backed by an idle claim at the read time', () => {
    const off = sampleRecord('status.off');
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(off));
    expect(selectAutomationSlot(h.state()).word).toMatchObject({
      row: 'AS2',
      word: 'idle',
      evidence: 'EV-5',
      claim: { state: 'idle', observedAt: off['server_time'] },
    });
  });
});
