// @vitest-environment node
/**
 * R7-02 and R7-03 against the committed generated modules: response variants
 * are inferred per envelope (HTTP status and body members) and command objects
 * per kind, so neither an error body nor another kind loosens a variant.
 */
import { describe, expect, it } from 'vitest';
import {
  fixtureFamilies,
  loadContractInputs,
  record,
} from '../../../scripts/generate-contract.mjs';
import { accountsAnswerSchema } from '../../../src/contract/dashboard.ts';
import { contractForFixture } from '../../../src/contract/generated/index.ts';
import { COMMAND_WIRE_STATES } from '../../../src/core/commands.ts';

type Path = readonly (string | number)[];

function plain(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function at(value: unknown, path: Path): unknown {
  let node = value;
  for (const step of path) {
    if (typeof step === 'number') node = Array.isArray(node) ? node[step] : undefined;
    else node = plain(node) ? node[step] : undefined;
  }
  return node;
}
/** A deep copy with the member at `path` removed (`member` undefined) or replaced. */
function edited(value: unknown, path: Path, member?: unknown): unknown {
  const copy: unknown = structuredClone(value);
  const parent = at(copy, path.slice(0, -1));
  const last = path.at(-1);
  if (!plain(parent) || typeof last !== 'string') throw new Error(`No member ${path.join('.')}`);
  if (member === undefined) Reflect.deleteProperty(parent, last);
  else parent[last] = member;
  return copy;
}
function members(value: unknown): string {
  return plain(value) ? Object.keys(value).sort().join(',') : typeof value;
}
/** The oracle's envelope, independent of the generator: HTTP status and body members. */
function envelope(value: unknown): string {
  const object = record(value);
  return typeof object['status'] === 'number' && Object.hasOwn(object, 'body')
    ? `${object['status']}|${members(object['body'])}`
    : members(object);
}
interface ObjectNode {
  /** Where this object is in its sample, array indices included. */
  readonly path: Path;
  /** Its raw response path: array elements share `[]`. */
  readonly rawPath: string;
  readonly object: Record<string, unknown>;
}
/** Every plain object in a sample, through object members and array elements. */
function objectNodes(value: unknown, path: Path = [], rawPath = ''): ObjectNode[] {
  if (Array.isArray(value))
    return value.flatMap((element, i) => objectNodes(element, [...path, i], rawPath + '[]'));
  if (!plain(value)) return [];
  return [
    { path, rawPath, object: value },
    ...Object.entries(value).flatMap(([key, member]) =>
      objectNodes(member, [...path, key], `${rawPath}.${key}`),
    ),
  ];
}

const inputs = loadContractInputs().map((input) => ({
  ...input,
  families: fixtureFamilies(input.fixture, input.descriptor),
  schemas: contractForFixture(input.identity).familySchemas,
}));
function schemaOf(input: (typeof inputs)[number], key: string) {
  const schema = input.schemas[key];
  if (schema === undefined) throw new Error(`Missing schema for ${key}`);
  return schema;
}

describe('R7-03 every family keeps the members each recorded envelope always carries', () => {
  // A member is required when every object at its raw path, over all samples of
  // the same envelope, carries it; removing it from any one of them must fail.
  for (const input of inputs)
    for (const family of input.families) {
      // A scan plan contains the entire native manifest, and a settings read every module's
      // fields. Keep every mutation, but give each recorded sample its own bounded test
      // instead of one test that takes over 30 s on a CI runner.
      const batches = /(\/api\/wizard\/scan-plan|GET \/api\/settings)$/.test(family.key)
        ? family.samples.map((sample) => ({ suffix: ` ${sample.name}`, samples: [sample] }))
        : [{ suffix: '', samples: family.samples }];
      for (const batch of batches)
        it(`${input.descriptor.name} ${family.key}${batch.suffix}`, () => {
          const schema = schemaOf(input, family.key);
          const envelopes = new Map<string, unknown[]>();
          for (const { value } of family.samples)
            envelopes.set(envelope(value), [...(envelopes.get(envelope(value)) ?? []), value]);
          const objectsAt = new Map<string, Map<string, Record<string, unknown>[]>>();
          for (const [name, group] of envelopes) {
            const byPath = new Map<string, Record<string, unknown>[]>();
            for (const value of group)
              for (const node of objectNodes(value))
                byPath.set(node.rawPath, [...(byPath.get(node.rawPath) ?? []), node.object]);
            objectsAt.set(name, byPath);
          }
          const leaks: string[] = [];
          let checked = 0;
          for (const sample of batch.samples) {
            const originalEnvelope = envelope(sample.value);
            const byPath = objectsAt.get(originalEnvelope);
            for (const node of objectNodes(sample.value))
              for (const key of Object.keys(node.object)) {
                const peers = byPath?.get(node.rawPath) ?? [];
                if (!peers.every((peer) => Object.hasOwn(peer, key))) continue;
                // Keep every sample/member and the full root parse. Cloning a large scan plan
                // for each member makes this quadratic in payload size. A synchronous deletion
                // with unconditional restoration produces the identical mutated input instead.
                const original = node.object[key];
                Reflect.deleteProperty(node.object, key);
                try {
                  const moved = envelope(sample.value);
                  // Removing a body member can land on another recorded envelope, which is valid.
                  if (moved !== originalEnvelope && envelopes.has(moved)) continue;
                  checked += 1;
                  if (schema.safeParse(sample.value).success)
                    leaks.push(`${sample.name}: ${[...node.path, key].join('.')}`);
                } finally {
                  node.object[key] = original;
                }
              }
          }
          expect(leaks).toEqual([]);
          expect(checked).toBeGreaterThan(0);
          // Thousands of schema parses: seconds of work on a loaded machine, not a hang.
        }, 30_000);
    }
});

describe('R7-03 accounts: the 200 and the default 500 are separate variants', () => {
  const dashboard = inputs.find((input) => input.descriptor.name === 'dashboard_contract_v1');
  const accounts = record(record(record(dashboard?.fixture)['cases'])['accounts']);
  const answer = (name: string) => {
    const sample = record(accounts[name]);
    return { status: sample['status'], body: sample['body'] };
  };
  const success = answer('active_among_several');
  it('accepts the recorded success, empty-account and default-500 answers', () => {
    for (const name of ['active_among_several', 'none_active', 'read_error'])
      expect(accountsAnswerSchema.safeParse(answer(name)).success, name).toBe(true);
  });
  it('refuses a 200 with an empty body', () => {
    expect(accountsAnswerSchema.safeParse({ status: 200, body: {} }).success).toBe(false);
  });
  it.each(['accounts', 'active', 'has_personal_settings', 'instance', 'max_tier'])(
    'refuses a 200 without %s',
    (member) => {
      expect(Object.keys(record(success.body))).toContain(member);
      expect(accountsAnswerSchema.safeParse(edited(success, ['body', member])).success).toBe(false);
    },
  );
  it('refuses the default-500 body under 200 and a success body under 500', () => {
    expect(accountsAnswerSchema.safeParse({ ...answer('read_error'), status: 200 }).success).toBe(
      false,
    );
    expect(accountsAnswerSchema.safeParse({ ...success, status: 500 }).success).toBe(false);
  });
});

/** Command objects: what one serializer writes at these raw paths (F-058). */
const COMMAND_PATH = /\.commands\[\]$|\.command$/;
const ALL_STATES = [...new Set(Object.values(COMMAND_WIRE_STATES).flat())];
const DISPLAY_LABELS = ['Accepted', 'Executing', 'Finished', 'Waiting', 'Done', 'Failed'];

function commandsIn(value: unknown, rawPath: string, path: Path = []) {
  const found: { path: Path; kind: 'controls' | 'action' }[] = [];
  if (Array.isArray(value))
    value.forEach((element, i) => found.push(...commandsIn(element, rawPath + '[]', [...path, i])));
  else if (plain(value)) {
    const kind = value['kind'];
    if (COMMAND_PATH.test(rawPath) && (kind === 'controls' || kind === 'action'))
      found.push({ path, kind });
    for (const [key, member] of Object.entries(value))
      found.push(...commandsIn(member, `${rawPath}.${key}`, [...path, key]));
  }
  return found;
}

describe('R7-02 command schemas accept exactly the wire states of each kind', () => {
  const bearing = inputs.flatMap((input) =>
    input.families
      .map((family) => ({
        input,
        family,
        commands: family.samples.flatMap((sample) =>
          commandsIn(sample.value, family.rawPath).map((command) => ({ sample, ...command })),
        ),
      }))
      .filter(({ commands }) => commands.length > 0),
  );
  it('RL2 SQ5 finds commands in exactly the command-bearing Dashboard endpoints', () => {
    expect(bearing.map(({ family }) => family.key).sort()).toEqual([
      // A2-7 R0 (landed r17b): the battle detail of a Run now execution and the projection reads.
      'action_executions_more GET /api/battles/',
      'battle_actions_projection GET /api/battles/active',
      'cases.battle_actions',
      'cases.battle_detail',
      'cases.battles_active',
      'cases.battles_history',
      // The pages fixture's live changes: the POST answers and the reads that settle them.
      'run_controls_live.GET',
      'run_controls_live.POST',
      // F6-11 RL2: a reset of every override and the reads that settle it (r12).
      'run_controls_live_reset_all GET /api/battles/active',
      'run_controls_live_reset_all POST /api/operations/',
      'run_controls_live_reset_all_mixed GET /api/battles/active',
      'run_controls_live_reset_all_mixed POST /api/operations/',
      // F6-12: shopping queue changes and Try once commands, and the reads around them.
      'shopping_queue GET /api/battles/active',
      'shopping_queue POST /api/operations/',
      'shopping_queue_bounds GET /api/battles/active',
      'shopping_queue_bounds POST /api/operations/',
    ]);
  });
  for (const { input, family, commands } of bearing)
    it(`${family.key}: every recorded command, every state`, () => {
      const schema = schemaOf(input, family.key);
      const refused: string[] = [];
      const admitted: string[] = [];
      for (const { sample, path, kind } of commands) {
        const own: readonly string[] = COMMAND_WIRE_STATES[kind];
        for (const state of [...ALL_STATES, ...DISPLAY_LABELS]) {
          const parsed = schema.safeParse(edited(sample.value, [...path, 'state'], state)).success;
          const where = `${sample.name} ${path.join('.')} ${kind}=${state}`;
          if (own.includes(state) && !parsed) refused.push(where);
          if (!own.includes(state) && parsed) admitted.push(where);
        }
      }
      expect(admitted).toEqual([]);
      expect(refused).toEqual([]);
    });
});
