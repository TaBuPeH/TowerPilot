import { describe, expect, it } from 'vitest';
import { automationSlot, automationWord } from '../../../src/core/automation.ts';
import { runGemReport } from '../../../src/core/collection.ts';
import {
  confirmCollecting,
  confirmFree,
  confirmIdle,
  confirmInputStopped,
  confirmStopped,
} from '../../../src/core/evidence.ts';
import { gateOf, type ReadAssessment } from '../../../src/core/gate.ts';
import { pauseOffered } from '../../../src/core/harvestChoice.ts';
import { gemSeen } from '../../../src/core/harvestView.ts';
import { gameInputSeen, inputOffOffered, type GameInputFacts } from '../../../src/core/input.ts';
import {
  configRead,
  sample,
  sampleNames,
  serverTimeOf,
  statusBody,
} from '../../support/fixture.ts';
import { readOf, setPath } from '../../support/core.ts';

/**
 * S-095: own-property reads only, no implicit string conversion. Every field
 * of every status sample (the harvest record, its nested records and the
 * process list and scan of the body) is replaced with each fuzz value. For
 * each mutation: no exception; Pause and Turn input off present whenever
 * S-001 and S-006 require them; and no Collecting, stopped, free, idle or
 * input stopped claim from a mutated routines, battle, ownership, scan or
 * input_revoked field that the unmutated read did not make, unless the
 * replacement is a well-formed value for that key: a null, boolean, number or
 * string where the contract fixture itself carries a value of that kind at a
 * key of that name (an operation may be null, a routine may be enabled), which
 * can legitimately change a reading. Structural replacements never count as
 * well-formed.
 */
const FUZZ: readonly unknown[] = [
  undefined,
  null,
  false,
  true,
  0,
  -1,
  1.5,
  '',
  'x',
  [],
  {},
  [{}],
  { toString: null },
  { constructor: 1 },
];

/** Every key path inside `value` (objects and arrays), depth first. */
function paths(value: unknown, prefix: readonly (string | number)[] = []): (string | number)[][] {
  const out: (string | number)[][] = [];
  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      out.push([...prefix, index], ...paths(item, [...prefix, index]));
    });
  } else if (typeof value === 'object' && value !== null) {
    for (const [key, item] of Object.entries(value)) {
      out.push([...prefix, key], ...paths(item, [...prefix, key]));
    }
  }
  return out;
}

function kindOf(value: unknown): string {
  return value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
}

/** For every key name in the status samples, the kinds of value the fixture carries there. */
const KINDS = new Map<string | number, Set<string>>();
for (const name of sampleNames('status')) {
  const body = statusBody(sample(`status.${name}`));
  for (const path of paths(body)) {
    let value: unknown = body;
    for (const key of path) {
      value = (value as Record<string | number, unknown>)[key];
    }
    const key = path[path.length - 1] ?? '';
    const kinds = KINDS.get(key) ?? new Set<string>();
    kinds.add(kindOf(value));
    KINDS.set(key, kinds);
  }
}

/** S-095's claim-bearing fields: routines, battle, ownership (processes, operation, owner), scan, input_revoked. */
const CLAIM_FIELDS = new Set([
  'routines',
  'battle',
  'processes',
  'operation',
  'owner',
  'process_refresh',
  'input_revoked',
]);

function claimField(path: readonly (string | number)[]): boolean {
  return path.some((key) => typeof key === 'string' && CLAIM_FIELDS.has(key));
}

function wellFormed(path: readonly (string | number)[], value: unknown): boolean {
  const kind = kindOf(value);
  if (kind === 'object' || kind === 'array' || kind === 'undefined') {
    return false;
  }
  return KINDS.get(path[path.length - 1] ?? '')?.has(kind) ?? false;
}

interface Claims {
  readonly collecting: boolean;
  readonly idle: boolean;
  readonly free: boolean;
  readonly gemObserved: boolean;
  readonly automationObserved: boolean;
  readonly inputIdle: boolean;
}

function facts(read: ReadAssessment): GameInputFacts {
  return {
    config: configRead(false),
    status: read,
    enables: [],
    turnOff: null,
    leaseHolders: [],
    retainedHolder: false,
  };
}

function evaluate(body: unknown): {
  claims: Claims;
  pause: boolean;
  inputOff: boolean;
  readable: boolean;
  complete: boolean;
} {
  const read = readOf(body);
  const context = { newest: read, lastGood: null, leaseHolders: [] };
  const gate = gateOf(read, null);
  const gem = gemSeen(context);
  const automation = automationWord(
    automationSlot({ status: context, extraClaims: [], lastIdleAgeSec: null }),
  );
  const input = gameInputSeen(facts(read));
  return {
    claims: {
      collecting:
        read.harvest.kind === 'known' && confirmCollecting(read.harvest.value).kind === 'known',
      idle: gate.kind === 'known' && confirmIdle(gate.value, []).kind === 'known',
      free: gate.kind === 'known' && confirmFree(gate.value, []).kind === 'known',
      gemObserved: gem.word === 'running' || gem.word === 'paused' || gem.word === 'idle',
      automationObserved: automation.word === 'running' || automation.word === 'idle',
      inputIdle: input.word === 'idle',
    },
    pause: pauseOffered(read.harvest, null, []),
    inputOff: inputOffOffered(facts(read), input) !== null,
    readable: read.harvest.kind === 'known',
    complete: gate.kind === 'known',
  };
}

describe('S-095 robust reading of every status sample', () => {
  it.each(sampleNames('status'))(
    'S-095 %s: every field fuzzed, no throw, revocations kept, no new claim',
    (name) => {
      const baseBody = statusBody(sample(`status.${name}`));
      const base = evaluate(baseBody);
      const targets = paths(baseBody);
      expect(targets.length).toBeGreaterThan(3);
      let checked = 0;
      for (const target of targets) {
        for (const value of FUZZ) {
          const body = statusBody(sample(`status.${name}`));
          setPath(body, target, value === undefined ? undefined : structuredClone(value));
          const result = evaluate(body);
          checked += 1;
          const where = `${target.join('.')} = ${value === undefined ? 'undefined' : JSON.stringify(value)}`;
          if (!result.readable) {
            expect(result.pause, `Pause for ${where}`).toBe(true);
          }
          if (!result.complete) {
            expect(result.inputOff, `Turn input off for ${where}`).toBe(true);
          }
          if (claimField(target) && !wellFormed(target, value)) {
            for (const key of Object.keys(result.claims) as (keyof Claims)[]) {
              if (result.claims[key]) {
                expect(base.claims[key], `${key} appeared from ${where}`).toBe(true);
              }
            }
          }
        }
      }
      expect(checked).toBe(targets.length * FUZZ.length);
    },
  );

  it('S-095 inherited names never match: constructor, toString and __proto__ are not fields', () => {
    const harvest = JSON.parse('{"__proto__": {"version": 1}}') as unknown;
    const result = evaluate(statusBody(harvest));
    expect(result.readable).toBe(false);
    const inherited = Object.create({
      version: 1,
      server_time: 1,
      desired: { choice: 'off', enabled: false, revision: 1 },
    }) as unknown;
    expect(evaluate(statusBody(inherited)).readable).toBe(false);
  });

  it('S-095 control: every unmutated sample reads without a throw', () => {
    for (const name of sampleNames('status')) {
      expect(() => evaluate(statusBody(sample(`status.${name}`)))).not.toThrow();
    }
  });
});

/**
 * S-095, path by path: for each evidence-bearing field, the fuzz values that
 * are not a valid value for THAT field must revoke the claim it backs. The
 * claim is compared with false, never with the unmutated reading, so a check
 * that stops looking at a field fails here. `keeps` lists the fuzz values that
 * are valid for the field (and may keep the claim), each with its reason.
 */
interface PathCase {
  readonly claim: 'collecting' | 'runCollects' | 'idle' | 'free' | 'stopped' | 'inputStopped';
  readonly sample: string;
  readonly path: readonly (string | number)[];
  /** Fuzz values valid for this field (scalars by Object.is, [] and {} by their JSON). */
  readonly keeps: readonly unknown[];
}

const OP = ['harvest', 'operation'];
const BATTLE = [...OP, 'battle'];
const SCAN = ['process_refresh'];
const PATH_CASES: readonly PathCase[] = [
  { claim: 'collecting', sample: 'collecting', path: ['harvest', 'input_revoked'], keeps: [false] },
  { claim: 'collecting', sample: 'collecting', path: ['harvest', 'server_time'], keeps: [] },
  { claim: 'collecting', sample: 'collecting', path: OP, keeps: [] },
  // a string id is valid whatever its characters; '' is not an id
  { claim: 'collecting', sample: 'collecting', path: [...OP, 'id'], keeps: ['x'] },
  { claim: 'collecting', sample: 'collecting', path: [...OP, 'status'], keeps: [] },
  { claim: 'collecting', sample: 'collecting', path: [...OP, 'worker_alive'], keeps: [true] },
  { claim: 'collecting', sample: 'collecting', path: [...OP, 'routines'], keeps: [] },
  { claim: 'collecting', sample: 'collecting', path: [...OP, 'routines', 'ad_gems'], keeps: [] },
  {
    claim: 'collecting',
    sample: 'collecting',
    path: [...OP, 'routines', 'ad_gems', 'enabled'],
    keeps: [true],
  },
  { claim: 'collecting', sample: 'collecting', path: BATTLE, keeps: [] },
  { claim: 'collecting', sample: 'collecting', path: [...BATTLE, 'fresh'], keeps: [true] },
  {
    claim: 'collecting',
    sample: 'collecting',
    path: [...BATTLE, 'publication_valid'],
    keeps: [true],
  },
  { claim: 'collecting', sample: 'collecting', path: [...BATTLE, 'observation_status'], keeps: [] },
  // D-6 needs string id and generation; any string id is an id
  { claim: 'collecting', sample: 'collecting', path: [...BATTLE, 'id'], keeps: ['', 'x'] },
  { claim: 'collecting', sample: 'collecting', path: [...BATTLE, 'generation'], keeps: [] },
  { claim: 'collecting', sample: 'collecting', path: [...BATTLE, 'published_at'], keeps: [] },
  // ages 0 and 1.5 stay within 1 s of H - published_at (1 s in the sample)
  {
    claim: 'collecting',
    sample: 'collecting',
    path: [...BATTLE, 'observation_age_sec'],
    keeps: [0, 1.5],
  },
  // evidence.generation counts only when present: deleting it is valid
  {
    claim: 'collecting',
    sample: 'collecting',
    path: [...OP, 'evidence', 'generation'],
    keeps: [undefined],
  },
  // D-6: an evidence time counts when present; 0, -1 and 1.5 lie before H+1, absence is allowed
  {
    claim: 'collecting',
    sample: 'collecting',
    path: [...OP, 'evidence', 'started_at'],
    keeps: [undefined, 0, -1, 1.5],
  },
  {
    claim: 'collecting',
    sample: 'collecting',
    path: [...OP, 'evidence', 'observed_through_at'],
    keeps: [undefined, 0, -1, 1.5],
  },
  // battle.status is valid absent or 'active'
  { claim: 'collecting', sample: 'collecting', path: [...BATTLE, 'status'], keeps: [undefined] },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'kind'],
    keeps: [],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'status'],
    keeps: [],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'gathers'],
    keeps: [true],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'gems'],
    keeps: [],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'gems', 'basis'],
    keeps: [],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'gems', 'ad_gems'],
    keeps: [true],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'battle'],
    keeps: [],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'battle', 'fresh'],
    keeps: [true],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'battle', 'publication_valid'],
    keeps: [true],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'battle', 'published_at'],
    keeps: [],
  },
  {
    claim: 'runCollects',
    sample: 'owned_by_run_gathers',
    path: ['harvest', 'owner', 'operation_id'],
    keeps: ['x'],
  },
  { claim: 'stopped', sample: 'off', path: ['processes'], keeps: [[]] },
  { claim: 'stopped', sample: 'off', path: SCAN, keeps: [] },
  { claim: 'stopped', sample: 'off', path: [...SCAN, 'state'], keeps: [] },
  { claim: 'stopped', sample: 'off', path: [...SCAN, 'error'], keeps: [null, undefined] },
  { claim: 'stopped', sample: 'off', path: [...SCAN, 'age_sec'], keeps: [0, 1.5] },
  { claim: 'stopped', sample: 'off', path: [...SCAN, 'last_success_at'], keeps: [] },
  { claim: 'stopped', sample: 'off', path: ['harvest', 'operation'], keeps: [null] },
  { claim: 'stopped', sample: 'off', path: ['harvest', 'owner'], keeps: [null] },
  // D-1: a revision is a safe integer >= 0
  { claim: 'stopped', sample: 'off', path: ['harvest', 'desired', 'revision'], keeps: [0] },
  { claim: 'idle', sample: 'off', path: ['processes'], keeps: [[]] },
  { claim: 'idle', sample: 'off', path: SCAN, keeps: [] },
  { claim: 'idle', sample: 'off', path: [...SCAN, 'state'], keeps: [] },
  // error null or absent is a clean scan
  { claim: 'idle', sample: 'off', path: [...SCAN, 'error'], keeps: [null, undefined] },
  // ages 0 and 1.5 are within 0..6 and consistent with last_success_at H
  { claim: 'idle', sample: 'off', path: [...SCAN, 'age_sec'], keeps: [0, 1.5] },
  { claim: 'idle', sample: 'off', path: [...SCAN, 'last_success_at'], keeps: [] },
  { claim: 'idle', sample: 'off', path: ['harvest', 'operation'], keeps: [null] },
  { claim: 'idle', sample: 'off', path: ['harvest', 'owner'], keeps: [null] },
  { claim: 'free', sample: 'starting_due', path: ['processes'], keeps: [[]] },
  { claim: 'free', sample: 'starting_due', path: ['harvest', 'operation'], keeps: [null] },
  { claim: 'free', sample: 'starting_due', path: ['harvest', 'owner'], keeps: [null] },
  { claim: 'free', sample: 'starting_due', path: [...SCAN, 'state'], keeps: [] },
  { claim: 'inputStopped', sample: 'off', path: ['harvest', 'input_revoked'], keeps: [true] },
  { claim: 'inputStopped', sample: 'off', path: ['processes'], keeps: [[]] },
  { claim: 'inputStopped', sample: 'off', path: [...SCAN, 'age_sec'], keeps: [0, 1.5] },
  { claim: 'inputStopped', sample: 'off', path: ['harvest', 'owner'], keeps: [null] },
];

function sameFuzz(kept: unknown, value: unknown): boolean {
  if (typeof kept === 'object' && kept !== null) {
    return typeof value === 'object' && JSON.stringify(kept) === JSON.stringify(value);
  }
  return Object.is(kept, value);
}

function claimHolds(claim: PathCase['claim'], body: unknown): boolean {
  if (claim === 'inputStopped') {
    const read = readOf(body, 11);
    return (
      confirmInputStopped({
        turnOff: { answer: sample('automation_access.disable_input_stopped'), answerSeq: 10 },
        enables: [],
        status: read,
        config: configRead(false, 11),
      }).kind === 'known'
    );
  }
  if (claim === 'runCollects') {
    const read = readOf(body);
    return read.harvest.kind === 'known' && runGemReport(read.harvest.value).kind === 'known';
  }
  if (claim === 'stopped') {
    const gate = gateOf(readOf(body, 11), 10);
    return (
      gate.kind === 'known' &&
      confirmStopped({ operationId: 'operation-x', pid: 4242 }, gate.value).kind === 'known'
    );
  }
  return evaluate(body).claims[claim];
}

function baseBody(entry: PathCase): Record<string, unknown> {
  const harvest = sample(`status.${entry.sample}`) as Record<string, unknown>;
  if (entry.claim === 'inputStopped') {
    harvest['input_revoked'] = true;
  }
  return statusBody(harvest);
}

describe('S-095 every invalid value of an evidence field revokes its claim', () => {
  it.each(PATH_CASES.map((entry) => [`${entry.claim} ${entry.path.join('.')}`, entry] as const))(
    'S-095 %s',
    (_label, entry) => {
      expect(claimHolds(entry.claim, baseBody(entry)), 'the unmutated control holds').toBe(true);
      for (const value of FUZZ) {
        if (entry.keeps.some((kept) => sameFuzz(kept, value))) {
          continue;
        }
        const body = baseBody(entry);
        setPath(body, entry.path, value === undefined ? undefined : structuredClone(value));
        const shown = value === undefined ? 'undefined' : JSON.stringify(value);
        expect(claimHolds(entry.claim, body), `${entry.path.join('.')} = ${shown}`).toBe(false);
      }
    },
  );
});

describe('S-095 evidence times after H+1 revoke their claim (D-6)', () => {
  const H = serverTimeOf(sample('status.collecting'));
  const future: readonly (readonly (string | number)[])[] = [
    [...OP, 'evidence', 'started_at'],
    [...OP, 'evidence', 'observed_through_at'],
    [...OP, 'evidence', 'latest_confirmations', 'ad_gems', 'observed_at'],
    [...BATTLE, 'published_at'],
  ];

  it.each(future.map((path) => [path.join('.'), path] as const))(
    'S-095 %s at H+5 is not collecting',
    (_label, path) => {
      const body = statusBody(sample('status.collecting'));
      expect(claimHolds('collecting', body)).toBe(true);
      setPath(body, path, H + 5);
      expect(claimHolds('collecting', body)).toBe(false);
    },
  );

  it('S-095 control: an evidence time at exactly H+1 is allowed', () => {
    const body = statusBody(sample('status.collecting'));
    setPath(body, [...OP, 'evidence', 'observed_through_at'], H + 1);
    expect(claimHolds('collecting', body)).toBe(true);
  });
});
