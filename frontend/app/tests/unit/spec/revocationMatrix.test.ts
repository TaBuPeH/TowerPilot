import { describe, expect, it } from 'vitest';
import {
  automationSlot,
  automationWord,
  type AutomationSlotView,
} from '../../../src/core/automation.ts';
import { pauseContextFromClickReads, type ConfigRead } from '../../../src/core/config.ts';
import { assessStatus, type ReadAssessment } from '../../../src/core/gate.ts';
import { pauseOffered, knownOn, type KnownOn } from '../../../src/core/harvestChoice.ts';
import { gemSeen } from '../../../src/core/harvestView.ts';
import { gameInputSeen, inputOffOffered, type GameInputFacts } from '../../../src/core/input.ts';
import {
  turnOffSuperseded,
  type EnableRequest,
  type TurnOffAnswer,
} from '../../../src/core/inputFacts.ts';
import type { KnownLeaseHolder } from '../../../src/core/lease.ts';
import type { StatusContext } from '../../../src/core/statusContext.ts';
import { rec } from '../../support/core.ts';
import {
  answered,
  configRead,
  failed,
  freshScan,
  sample,
  sampleNames,
  sampleRecord,
  serverTimeOf,
  statusBody,
} from '../../support/fixture.ts';
import { MATRIX, W_ROWS, X_ROWS } from './specData.ts';

/**
 * Spec 9.1, the core-decided columns, computed from the fixture bodies of the
 * loaded contract fixture (the checkout's r6 fixture, or another named by
 * TP_CONTRACT_FIXTURE): Pause (INV-1), Turn input off (INV-2), the tracked
 * Stop targets (INV-3) and the Gem, Input and Automation words of the section
 * 3.6 tables, under E0 and E1; the X rows core decides; the warm W rows. The
 * expected cells come from the spec (tests/unit/spec/specData.ts) and are only
 * compared with the result, never used as inputs.
 */

const WORD: Record<string, string> = {
  running: 'Running',
  waiting: 'Waiting',
  paused: 'Paused',
  idle: 'Idle',
  stopping: 'Stopping',
  unknown: 'Unknown',
  blocked: 'Blocked',
  failed: 'Failed',
};

function word(value: string | null): string {
  return value === null ? 'plain text' : (WORD[value] ?? value);
}

interface Page {
  readonly status: ReadAssessment | null;
  readonly context: StatusContext;
  readonly config: ConfigRead;
  readonly known: KnownOn | null;
  readonly enables: readonly EnableRequest[];
  readonly turnOff: TurnOffAnswer | null;
  readonly leaseHolders: readonly KnownLeaseHolder[];
}

function page(options: {
  readonly status: ReadAssessment | null;
  readonly lastGood?: { readonly read: ReadAssessment; readonly ageSec: number } | null;
  readonly allowTaps?: boolean;
  readonly configSeq?: number;
  readonly known?: KnownOn | null;
  readonly enables?: readonly EnableRequest[];
  readonly turnOff?: TurnOffAnswer | null;
  readonly leaseHolders?: readonly KnownLeaseHolder[];
}): Page {
  const leaseHolders = options.leaseHolders ?? [];
  return {
    status: options.status,
    context: {
      newest: options.status,
      lastGood: options.lastGood ?? null,
      leaseHolders,
    },
    config: configRead(options.allowTaps ?? false, options.configSeq ?? 1),
    known: options.known ?? null,
    enables: options.enables ?? [],
    turnOff: options.turnOff ?? null,
    leaseHolders,
  };
}

function inputFacts(p: Page, retainedHolder = false): GameInputFacts {
  return {
    config: p.config,
    status: p.status,
    enables: p.enables,
    turnOff: p.turnOff,
    leaseHolders: p.leaseHolders,
    retainedHolder,
  };
}

function automation(p: Page): AutomationSlotView {
  return automationSlot({ status: p.context, extraClaims: [], lastIdleAgeSec: null });
}

function tracked(view: AutomationSlotView): string {
  const targets = view.rows.flatMap((row) =>
    row.stop.kind === 'cancel'
      ? [`${row.stop.operationId.slice(0, 8)}@${row.stop.expectedRevision.toString()}`]
      : [],
  );
  return targets.length === 0 ? 'none' : targets.join(', ');
}

function readFor(p: Page) {
  const drawn = p.context.newest?.answered === true ? p.context.newest : p.context.lastGood?.read;
  return drawn === undefined ? null : drawn.harvest;
}

function row(p: Page): string[] {
  const input = gameInputSeen(inputFacts(p));
  const view = automation(p);
  return [
    pauseOffered(readFor(p), p.known, []) ? 'shown' : 'hidden',
    inputOffOffered(inputFacts(p), input) === null ? 'hidden' : 'shown',
    tracked(view),
    word(gemSeen(p.context).word),
    word(input.word),
    word(automationWord(view).word),
  ];
}

function e0(harvest: unknown, options: { readonly processes?: unknown } = {}): ReadAssessment {
  return assessStatus(answered(statusBody(harvest, options), 2));
}

describe('9.1 sample rows, computed from the fixture under E0 and E1', () => {
  it('covers exactly the status samples of the loaded fixture', () => {
    expect(MATRIX.map((entry) => entry.sample)).toEqual(sampleNames('status'));
  });

  it.each(MATRIX.map((entry) => [entry.sample, entry] as const))('%s (E0)', (name, expected) => {
    const p = page({ status: e0(sample(`status.${name}`)) });
    expect(row(p)).toEqual([
      expected.pause,
      expected.turnInputOff,
      expected.stopTargets,
      expected.gem,
      expected.input,
      expected.automation,
    ]);
  });

  it.each(MATRIX.map((entry) => [entry.sample, entry] as const))(
    '%s (E1: only Turn input off changes, to shown)',
    (name, expected) => {
      const p = page({ status: e0(sample(`status.${name}`)), allowTaps: true });
      expect(row(p)).toEqual([
        expected.pause,
        'shown',
        expected.stopTargets,
        expected.gem,
        expected.input,
        expected.automation,
      ]);
    },
  );

  it('under E0 shows Turn input off exactly when the Input word is not Idle (INV-2)', () => {
    for (const entry of MATRIX) {
      expect({ sample: entry.sample, shown: entry.turnInputOff === 'shown' }).toEqual({
        sample: entry.sample,
        shown: entry.input !== 'Idle',
      });
    }
  });
});

function xRow(id: string) {
  const found = X_ROWS.find((entry) => entry.id === id);
  if (found === undefined) {
    throw new Error(`spec 9.1 has no row ${id}`);
  }
  return found;
}

function startsWithWord(cell: string): string {
  return /^(shown|hidden)/.exec(cell)?.[1] ?? cell;
}

describe('9.1 scenario rows decided by core', () => {
  it.each(['X1', 'X2a', 'X2b'])(
    '%s: no status yet shows Pause and Turn input off, no Stop, every chip Unknown',
    (id) => {
      const expected = xRow(id);
      const status = assessStatus(failed('http_502', 2));
      const p = page({ status });
      const result = row(p);
      expect([result[0], result[1], result[2]]).toEqual([
        startsWithWord(expected.pause),
        startsWithWord(expected.turnInputOff),
        'none',
      ]);
      expect(result.slice(3)).toEqual(['Unknown', 'Unknown', 'Unknown']);
    },
  );

  it('X2a and X2b: the click sends the E0 body when the config read names the context, nothing when no read does (OD-13)', () => {
    const configBody = { active_instance: 'main', instances: { main: { serial: 'fake:1' } } };
    expect(pauseContextFromClickReads(configBody, undefined)).toEqual({
      instance: 'main',
      serial: 'fake:1',
    });
    expect(xRow('X2a').pause).toContain('sends the E0 body');
    expect(pauseContextFromClickReads(undefined, undefined)).toBeNull();
    expect(xRow('X2b').pause).toContain('sends no POST');
  });

  it('X3: a warm 502 keeps Pause, Turn input off and the retained tracked Stop; every chip Unknown', () => {
    const expected = xRow('X3');
    const good = e0(sample('status.collecting'));
    const p = page({
      status: assessStatus(failed('http_502', 3)),
      lastGood: { read: good, ageSec: 4 },
    });
    const result = row(p);
    expect(result.slice(0, 3)).toEqual([
      startsWithWord(expected.pause),
      startsWithWord(expected.turnInputOff),
      (/[0-9a-f]{8}@\d+/.exec(expected.stop) ?? [''])[0],
    ]);
    expect(result.slice(3)).toEqual(['Unknown', 'Unknown', 'Unknown']);
  });

  it.each(['X7a', 'X7b'])(
    '%s: a Known On and a later Off at its revision or above (D-8, S-033)',
    (id) => {
      const expected = xRow(id);
      const ack = sampleRecord('post_harvest.enable_ok');
      const k = ((ack['body'] as Record<string, unknown>)['desired'] as Record<string, unknown>)[
        'revision'
      ] as number;
      const off = sampleRecord('status.off');
      (off['desired'] as Record<string, unknown>)['revision'] = id === 'X7a' ? k : k + 1;
      const p = page({ status: e0(off), known: knownOn([{ revision: k, learnedSeq: 1 }]) });
      const result = row(p);
      expect([result[0], result[1], result[2]]).toEqual([
        startsWithWord(expected.pause),
        startsWithWord(expected.turnInputOff),
        'none',
      ]);
    },
  );

  it('X8: two untracked runners: a kill with the exact pid and created, and a disabled Stop for created null', () => {
    const expected = xRow('X8');
    const rows = sample('stop_operation.processes') as Record<string, unknown>[];
    const p = page({ status: e0(sample('status.off'), { processes: rows }) });
    const view = automation(p);
    expect(view.rows.map((entry) => entry.stop)).toEqual([
      { kind: 'kill', pid: rows[0]?.['pid'], created: rows[0]?.['created'] },
      { kind: 'disabled', reason: 'created_missing', operationId: null, pid: rows[1]?.['pid'] },
    ]);
    expect(view.rows.map((entry) => [entry.seen.word, entry.seen.evidence])).toEqual([
      ['running', 'EV-1'],
      ['running', 'EV-1'],
    ]);
    const result = row(p);
    expect([result[0], result[1]]).toEqual([
      startsWithWord(expected.pause),
      startsWithWord(expected.turnInputOff),
    ]);
    expect(result[4]).toBe('Unknown');
    expect(expected.stop).toContain('kill');
    expect(expected.stop).toContain('disabled');
  });

  it('X9: a malformed linked row: its Stop disabled ("can\'t identify the operation"), Automation and Input Unknown', () => {
    const expected = xRow('X9');
    const p = page({
      status: e0(sample('status.off'), { processes: [{ pid: 9009, operation_id: 7 }] }),
    });
    const view = automation(p);
    expect(view.rows.map((entry) => entry.stop)).toEqual([
      { kind: 'disabled', reason: 'operation_unidentified', operationId: null, pid: 9009 },
    ]);
    const result = row(p);
    expect([result[0], result[1]]).toEqual([
      startsWithWord(expected.pause),
      startsWithWord(expected.turnInputOff),
    ]);
    expect(result.slice(4)).toEqual(['Unknown', 'Unknown']);
  });

  it('X10: a retained unidentified lease holder: Gem Waiting, Input Unknown, Automation Unknown (AS5)', () => {
    const expected = xRow('X10');
    // The retained notice: the r6 device_owned_lease, used with `holders: []` as the row says,
    // so no later read can resolve it (S-073).
    const notice = sampleRecord('refusals.device_owned_lease');
    const owner = rec(rec(notice['body'])['owner']);
    if (rec(owner)['lease'] !== undefined) {
      rec(rec(owner)['lease'])['holders'] = [];
    }
    const p = page({
      status: e0(sample('status.starting_due')),
      leaseHolders: [{ owner, learnedSeq: 1 }],
    });
    const view = automation(p);
    expect(automationWord(view)).toMatchObject({ row: 'AS5', line: 'holder_unresolved' });
    const result = row(p);
    expect(result).toEqual([
      startsWithWord(expected.pause),
      startsWithWord(expected.turnInputOff),
      'none',
      'Waiting',
      'Unknown',
      'Unknown',
    ]);
    expect(expected.other).toContain('Gem Waiting (G5b)');
    expect(expected.other).toContain('Input Unknown (I6)');
    expect(expected.other).toContain('Automation Unknown (AS5)');
  });
});

/** The W rows of 9.1: the later reads they name, built from the fixture bodies. */
function wPage(id: string): Page {
  const answerSeq = 10;
  const stopped = sample('automation_access.disable_input_stopped');
  const refused = sampleRecord('automation_access.disable_driver_input_refused');
  let receipt: unknown = stopped;
  let harvest: unknown = (() => {
    const off = sampleRecord('status.off');
    off['input_revoked'] = true;
    return off;
  })();
  let processes: unknown = [];
  let age = 0;
  let allowTaps = false;
  let startedSeq = 11;
  let enables: EnableRequest[] = [];
  if (id === 'W2') {
    receipt = refused;
    const revocation = (refused['body'] as Record<string, unknown>)['revocation'] as Record<
      string,
      unknown
    >;
    const owner = (revocation['still_owned'] as Record<string, unknown>[])[0] ?? {};
    processes = [
      {
        pid: 4101,
        runner: 'orchestrator',
        created: 1790000000.5,
        operation_id: owner['operation_id'],
        operation_revision: 2,
      },
    ];
  }
  if (id === 'W3' || id === 'W12' || id === 'W13') age = 180;
  if (id === 'W4') processes = undefined;
  if (id === 'W5') processes = [{}];
  if (id === 'W6') harvest = sample('status.status_failed');
  if (id === 'W7') startedSeq = 9;
  if (id === 'W8') enables = [{ sentSeq: 12, settled: false }];
  if (id === 'W9') enables = [{ sentSeq: 8, settled: false }];
  if (id === 'W10' || id === 'W12') allowTaps = true;
  if (id === 'W11' || id === 'W13') (harvest as Record<string, unknown>)['input_revoked'] = false;
  const body: Record<string, unknown> = {
    harvest,
    process_refresh: freshScan(serverTimeOf(harvest), age),
  };
  if (processes !== undefined) body['processes'] = processes;
  if (age === 180) {
    (body['process_refresh'] as Record<string, unknown>)['last_success_at'] =
      serverTimeOf(harvest) - 180;
  }
  return page({
    status: assessStatus(answered(body, startedSeq)),
    allowTaps,
    configSeq: 11,
    enables,
    turnOff: { answer: receipt, answerSeq },
  });
}

describe('9.1 warm Game input rows W1 to W13 (S-040, S-047, S-048, INV-2)', () => {
  it('has the rows W1 to W13', () => {
    expect(W_ROWS.map((entry) => entry.id)).toEqual(
      Array.from({ length: 13 }, (_, index) => `W${(index + 1).toString()}`),
    );
  });

  it.each(W_ROWS.map((entry) => [entry.id, entry] as const))('%s', (id, expected) => {
    const p = wPage(id);
    const facts = inputFacts(p);
    const input = gameInputSeen(facts);
    const rowId = /\((I\d+a?)/.exec(expected.input)?.[1];
    expect({ word: word(input.word), row: input.row }).toEqual({
      word: expected.input.split(' ')[0],
      row: rowId,
    });
    if (input.row === 'I4') {
      expect(input.evidence).toBe('EV-7');
      expect(expected.input).toContain('EV-7');
    }
    expect(inputOffOffered(facts, input) === null ? 'hidden' : 'shown').toBe(
      startsWithWord(expected.turnInputOff),
    );
    expect(turnOffSuperseded(facts) ? 'history' : 'kept').toBe(
      /^(kept|history)/.exec(expected.answerLine)?.[1],
    );
  });
});
