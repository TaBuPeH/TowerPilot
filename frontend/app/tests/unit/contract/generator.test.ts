// @vitest-environment node
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import { DISCRIMINATOR_KEYS, generateModule, record } from '../../../scripts/generate-contract.mjs';
import { loadFixture } from '../../../scripts/contract-fixture.mjs';

const sample = {
  version: 1,
  battle: {
    commands: [{ kind: 'action', state: 'requested' }],
    effective: { shopping_plan: { version: 2, queue: [] } },
    runtime: {
      routines: {
        ad_gems: { manual_active: false, manual_pending: false, state: 'idle' },
        floating_gems: { manual_active: false, manual_pending: false, state: 'idle' },
        shopping: { last_outcome: { status: 'confirmed' }, state: 'idle' },
      },
    },
  },
  at: 1.25,
  nullable: null,
  mixed: 1,
};
const second = {
  ...sample,
  nullable: 'present',
  mixed: 'different kind',
  battle: {
    ...sample.battle,
    commands: [{ kind: 'action', state: 'completed' }],
    runtime: {
      routines: {
        ...sample.battle.runtime.routines,
        shopping: { manual_active: false, state: 'idle' },
      },
    },
  },
};
const descriptor = {
  name: 'synthetic',
  families: [{ key: 'samples', name: 'BattleResponse', rawPath: 'response' }],
};
const identity = { path: '/synthetic.json', sha256: '0'.repeat(64) };
const source = await generateModule(
  { version: 1, samples: { first: sample, second } },
  identity,
  descriptor,
);

// Execute the actual generated schemas. Transpilation erases types only; Node's
// data-module loader imports zod locally and never opens a socket or writes a file.
const javascript = transpileModule(source, {
  compilerOptions: { module: ModuleKind.ESNext, target: ScriptTarget.ES2023 },
}).outputText.replace("from 'zod'", `from ${JSON.stringify(import.meta.resolve('zod'))}`);
const generated: unknown = await import(
  /* @vite-ignore */ `data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`
);
function schema(name: string, module: unknown = generated): z.ZodType {
  const value = record(module)[name];
  if (!(value instanceof z.ZodType)) throw new Error(`No generated schema ${name}`);
  return value;
}
/** Generate and import a synthetic module the same way as the shared one above. */
async function generatedFrom(fixture: unknown, synthetic = descriptor): Promise<unknown> {
  const output = await generateModule(fixture, identity, synthetic);
  const code = transpileModule(output, {
    compilerOptions: { module: ModuleKind.ESNext, target: ScriptTarget.ES2023 },
  }).outputText.replace("from 'zod'", `from ${JSON.stringify(import.meta.resolve('zod'))}`);
  return import(
    /* @vite-ignore */ `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
  );
}
const root = schema('battleResponseSchema');

describe('raw-path generator inference', () => {
  it('parses both observed shapes, decimals, nullable values and mixed kinds', () => {
    expect(root.safeParse(sample).success).toBe(true);
    expect(root.safeParse(second).success).toBe(true);
    expect(source).not.toContain('.int()');
    expect(root.safeParse({ ...sample, version: 2 }).success).toBe(false);
  });
  it('keeps optionality local to the routine and observed key path', () => {
    const ad = schema('battleResponse_Battle_Runtime_Routines_AdGemsSchema');
    const floating = schema('battleResponse_Battle_Runtime_Routines_FloatingGemsSchema');
    const shopping = schema('battleResponse_Battle_Runtime_Routines_ShoppingSchema');
    expect(ad.safeParse({ state: 'idle' }).success).toBe(false);
    expect(floating.safeParse({ state: 'idle', manual_active: false }).success).toBe(false);
    expect(shopping.safeParse({ state: 'idle' }).success).toBe(true);
    expect(shopping.safeParse({ state: 'idle', manual_active: false }).success).toBe(true);
    // Never observed for shopping: strict refuses it rather than inventing an optional field.
    expect(shopping.safeParse({ state: 'idle', manual_pending: false }).success).toBe(false);
  });
  it('names and documents full raw response paths', () => {
    expect(source).toContain('response.battle.commands');
    expect(source).toContain('battleResponse_Battle_CommandsSchema');
    expect(source).toContain('response.battle.effective.shopping_plan');
    expect(source).toContain('battleResponse_Battle_Effective_ShoppingPlanSchema');
    expect(source).toContain('response.battle.runtime.routines.shopping.last_outcome');
    expect(source).toContain('battleResponse_Battle_Runtime_Routines_Shopping_LastOutcomeSchema');
  });
  it('exports a named type only for the family root, never per raw path', () => {
    // Thousands of unused per-path aliases pushed typed lint past Node's default heap.
    expect(source).toContain('export type BattleResponse = z.infer<typeof battleResponseSchema>;');
    expect(source).not.toMatch(/export type BattleResponse_/u);
  });
  it('refuses unknown nested fields and unseen discriminator values', () => {
    expect(
      root.safeParse({ ...sample, battle: { ...sample.battle, surprise: true } }).success,
    ).toBe(false);
    expect(
      schema('battleResponse_Battle_Commands_ArrayElementSchema').safeParse({
        kind: 'action',
        state: 'invented',
      }).success,
    ).toBe(false);
  });
  it('documents never-non-empty arrays and allows unknown future elements only there', () => {
    expect(source).toContain('Never observed non-empty at this raw path.');
    const queue = schema('battleResponse_Battle_Effective_ShoppingPlan_QueueSchema');
    expect(queue.safeParse([{ newlyObserved: true }, 1, null]).success).toBe(true);
    expect(queue.safeParse({}).success).toBe(false);
  });
  it('infers HTTP status as literals and string status as enums within each envelope', async () => {
    const output = await generateModule(
      {
        version: 1,
        samples: {
          first: { status: 200, body: { status: 'ready' } },
          second: { status: 409, body: { status: 'refused' } },
        },
      },
      identity,
      descriptor,
    );
    expect(output).toContain('z.literal(200)');
    expect(output).toContain('z.literal(409)');
    expect(output).toContain("z.enum(['ready'])");
    expect(output).toContain("z.enum(['refused'])");
  });
  it('R7-03 infers success and error envelopes separately, so no success member turns optional', async () => {
    const module = await generatedFrom({
      version: 1,
      samples: {
        first: { status: 200, body: { accounts: [], active: 'main', status: 'ready' } },
        second: { status: 200, body: { accounts: [{ id: 'a' }], active: null, status: 'ready' } },
        failed: { status: 500, body: { text: 'Internal Server Error' } },
        refused: { status: 409, body: { error: 'no', status: 'refused' } },
        coded: { status: 409, body: { code: 'busy', error: 'no', owner: 'x', status: 'refused' } },
      },
    });
    const answer = schema('battleResponseSchema', module);
    for (const accepted of [
      { status: 200, body: { accounts: [], active: 'main', status: 'ready' } },
      { status: 500, body: { text: 'Internal Server Error' } },
      { status: 409, body: { error: 'no', status: 'refused' } },
      { status: 409, body: { code: 'busy', error: 'no', owner: 'x', status: 'refused' } },
    ])
      expect(answer.safeParse(accepted).success).toBe(true);
    for (const refused of [
      { status: 200, body: {} },
      { status: 200, body: { accounts: [], status: 'ready' } },
      { status: 200, body: { active: null, status: 'ready' } },
      { status: 200, body: { text: 'Internal Server Error' } },
      { status: 500, body: { accounts: [], active: 'main', status: 'ready' } },
      { status: 409, body: { status: 'ready', error: 'no' } },
      { status: 409, body: { code: 'busy', error: 'no', status: 'refused' } },
      { status: 409, body: { error: 'no', owner: 'x', status: 'refused' } },
    ])
      expect(answer.safeParse(refused).success, JSON.stringify(refused)).toBe(false);
  });
  it('R7-02 infers each command kind on its own, so a kind never admits another kind', async () => {
    const commands = (action: string, controls: string) => ({
      commands: [
        { kind: 'action', state: action, payload: { action: 'claim' } },
        { kind: 'controls', state: controls, applied_paths: [] },
      ],
    });
    const output = await generateModule(
      {
        version: 1,
        samples: { first: commands('started', 'pending'), second: commands('failed', 'applied') },
      },
      identity,
      descriptor,
    );
    expect(output).toContain("z.discriminatedUnion('kind'");
    const module = await generatedFrom({
      version: 1,
      samples: { first: commands('started', 'pending'), second: commands('failed', 'applied') },
    });
    const command = schema('battleResponse_Commands_ArrayElementSchema', module);
    const action = { kind: 'action', state: 'started', payload: { action: 'claim' } };
    const controls = { kind: 'controls', state: 'pending', applied_paths: [] };
    expect(command.safeParse(action).success).toBe(true);
    expect(command.safeParse(controls).success).toBe(true);
    for (const state of ['pending', 'applied'])
      expect(command.safeParse({ ...action, state }).success, state).toBe(false);
    for (const state of ['started', 'failed'])
      expect(command.safeParse({ ...controls, state }).success, state).toBe(false);
    // A member of one kind is not an optional member of the other.
    expect(command.safeParse({ ...controls, payload: { action: 'claim' } }).success).toBe(false);
    expect(command.safeParse({ kind: 'action', state: 'started' }).success).toBe(false);
  });
  it('pools a vocabulary per tag across the raw paths it names and nowhere else', async () => {
    const pooled = {
      name: 'synthetic',
      families: [
        { key: 'active', name: 'Active', rawPath: 'answer' },
        { key: 'detail', name: 'Detail', rawPath: 'answer' },
      ],
      vocabularies: [
        {
          tag: 'kind',
          key: 'state',
          rawPath: /\.commands\[\]\.state$|\.command\.state$/,
          source: 'synthetic',
        },
      ],
    };
    const module = await generatedFrom(
      {
        version: 1,
        active: { one: { battle: { commands: [{ kind: 'action', state: 'started' }] } } },
        detail: {
          one: {
            events: [
              { command: { kind: 'action', state: 'expired' } },
              { command: { kind: 'controls', state: 'pending' } },
            ],
            other: [{ kind: 'action', state: 'other' }],
          },
        },
      },
      pooled,
    );
    const active = schema('active_Battle_Commands_ArrayElementSchema', module);
    expect(active.safeParse({ kind: 'action', state: 'expired' }).success).toBe(true);
    expect(active.safeParse({ kind: 'action', state: 'pending' }).success).toBe(false);
    expect(active.safeParse({ kind: 'action', state: 'other' }).success).toBe(false);
    const other = schema('detail_Other_ArrayElementSchema', module);
    expect(other.safeParse({ kind: 'action', state: 'started' }).success).toBe(false);
    await expect(
      generateModule(
        { version: 1, active: { one: { x: 1 } }, detail: { one: { y: 1 } } },
        identity,
        pooled,
      ),
    ).rejects.toThrow('matched no object');
  });
  it('uses only discriminator keys present as strings in the loaded fixtures except r6 role', () => {
    const strings = new Set<string>();
    function visit(value: unknown): void {
      if (Array.isArray(value)) value.forEach(visit);
      else if (value !== null && typeof value === 'object') {
        for (const [key, member] of Object.entries(value)) {
          if (typeof member === 'string') strings.add(key);
          visit(member);
        }
      }
    }
    visit(loadFixture());
    for (const key of DISCRIMINATOR_KEYS)
      if (key !== 'role') expect(strings.has(key), key).toBe(true);
  });
});
