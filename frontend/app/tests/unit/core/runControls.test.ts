import { describe, expect, it } from 'vitest';
import {
  checkDraft,
  controlGroups,
  liveMark,
  liveOverrideLeaves,
  liveResetGate,
  liveSaveGate,
  reviewDraft,
  savedSaveAnswer,
  savedSaveGate,
  settleSavedSave,
} from '../../../src/core/runControls.ts';
import { controlApplication } from '../../../src/core/battle.ts';
import { dashboardBody, dashboardPagesCase } from '../../support/dashboardFixture.ts';

/** Runs brief (backend/logs/reviews/ui-pages/runs-brief.md): core's run-control rules. */
const SAVED = dashboardBody('run_controls.ad_gems_on_floating_off') as Record<string, unknown>;
const groups = () =>
  controlGroups(
    (SAVED['capabilities'] as { controls: unknown }).controls,
    SAVED['settings'],
    SAVED['constraints'],
  );
const BATTLE = (
  dashboardBody('battles_active.commands_controls_applied_with_overrides_action_failed') as {
    battle: Record<string, unknown>;
  }
).battle;

describe('RUN-02 fields come only from the controls the server names', () => {
  it('RUN-02 every routines.* control is one field; shopping_plan and unknown shapes are not fields', () => {
    const fields = groups().flatMap((group) => group.fields);
    expect(fields.map((f) => f.path)).toEqual(
      (SAVED['capabilities'] as { controls: string[] }).controls.filter((p) =>
        p.startsWith('routines.'),
      ),
    );
    expect(fields.find((f) => f.path === 'routines.ad_gems.interval_sec')).toMatchObject({
      kind: 'number',
      value: 720,
      min: 1,
      max: 3600,
    });
    expect(fields.find((f) => f.path === 'routines.floating_gems.enabled')).toMatchObject({
      kind: 'toggle',
      value: false,
    });
    expect(controlGroups(['shopping_plan', 'routines.x', 'nope'], {}, {})).toEqual([]);
  });
});

describe('RUN-04 SR-3 a draft is judged, never changed', () => {
  const field = () => {
    const found = groups()
      .flatMap((g) => g.fields)
      .find((f) => f.path === 'routines.ad_gems.window_sec');
    if (found === undefined) throw new Error('no field');
    return found;
  };
  it.each([
    ['', 'empty'],
    ['  ', 'empty'],
    ['abc', 'not_a_number'],
    ['1e3', 'not_a_number'],
    ['-5', 'not_a_number'],
    ['0x10', 'not_a_number'],
    ['4', 'below_min'],
    ['61', 'above_max'],
  ])('RUN-04 %j is invalid: %s', (typed, reason) => {
    expect(checkDraft(field(), typed)).toEqual({ kind: 'invalid', reason });
  });
  it('RUN-04 a valid value is the number typed, unrounded and unclamped', () => {
    expect(checkDraft(field(), '7.25')).toEqual({ kind: 'valid', value: 7.25 });
    expect(checkDraft(field(), ' 60 ')).toEqual({ kind: 'valid', value: 60 });
  });
  it('RUN-03 a review sends only paths that differ; invalid paths are listed, unknown paths refused', () => {
    const review = reviewDraft(groups(), {
      'routines.ad_gems.interval_sec': '720',
      'routines.ad_gems.window_sec': '30',
      'routines.floating_gems.enabled': true,
      'routines.shopping.interval_sec': 'x',
      'routines.nope.enabled': true,
    });
    expect(review.changes).toEqual({
      'routines.ad_gems.window_sec': 30,
      'routines.floating_gems.enabled': true,
    });
    expect(review.invalid).toEqual({
      'routines.shopping.interval_sec': 'not_a_number',
      'routines.nope.enabled': 'not_editable',
    });
  });
});

describe('RUN-05 RUN-06 the saved save gate', () => {
  const read = { body: SAVED, sameContext: true };
  const review = () => reviewDraft(groups(), { 'routines.ad_gems.window_sec': '30' });
  it('RUN-05 a changed valid draft on a read of this context offers one save with the read revision and context', () => {
    expect(savedSaveGate(read, review(), 'none')).toEqual({
      enabled: true,
      command: {
        blueprint: 'farm',
        context: SAVED['context'],
        expectedRevision: SAVED['profile_revision'],
        changes: { 'routines.ad_gems.window_sec': 30 },
      },
    });
  });
  it.each([
    [null, 'none', 'run_not_read'],
    [{ body: SAVED, sameContext: false }, 'none', 'read_other_context'],
    [
      { body: { ...SAVED, profile_revision: undefined }, sameContext: true },
      'none',
      'revision_unknown',
    ],
    [read, 'sending', 'save_in_flight'],
    [read, 'unresolved', 'save_unresolved'],
    [read, 'refreshing', 'refreshing'],
  ] as const)('RUN-06 SR-4 no save: %#', (given, request, reason) => {
    expect(savedSaveGate(given, review(), request)).toEqual({ enabled: false, reason });
  });
  it('RUN-04 an invalid value or no change offers no save', () => {
    expect(
      savedSaveGate(read, reviewDraft(groups(), { 'routines.ad_gems.window_sec': '' }), 'none'),
    ).toEqual({ enabled: false, reason: 'invalid_value' });
    expect(savedSaveGate(read, reviewDraft(groups(), {}), 'none')).toEqual({
      enabled: false,
      reason: 'no_changes',
    });
  });
});

describe('RUN-07 a saved save answer and its later read', () => {
  /** What the save sent: an older revision and a value the answer's settings hold. */
  const SENT = {
    blueprint: 'farm',
    profileRevision: 'a'.repeat(64),
    changes: { 'routines.ad_gems.interval_sec': 720 },
  };
  it('RUN-07 200 with a new revision whose settings hold every sent value saved it; 400/409 ok:false refused it; anything else is unknown', () => {
    expect(savedSaveAnswer({ status: 200, body: SAVED }, SENT)).toEqual({
      kind: 'saved',
      profileRevision: SAVED['profile_revision'],
    });
    expect(savedSaveAnswer({ status: 409, body: { ok: false, error: 'x' } }, SENT).kind).toBe(
      'refused',
    );
    expect(savedSaveAnswer({ status: 400, body: { ok: false, error: 'x' } }, SENT).kind).toBe(
      'refused',
    );
    expect(savedSaveAnswer({ status: 500, body: { ok: false } }, SENT).kind).toBe('unknown');
    expect(savedSaveAnswer({ status: 200, body: { ok: true } }, SENT).kind).toBe('unknown');
    expect(savedSaveAnswer({ status: 409, body: { text: '<html>' } }, SENT).kind).toBe('unknown');
  });
  it.each([
    ['settings without the sent value', { ok: true, profile_revision: 'new', settings: {} }],
    ['another run', { ...SAVED, blueprint: 'other' }],
    [
      'the revision the save was made against',
      { ...SAVED, profile_revision: SENT.profileRevision },
    ],
    ['no revision', { ...SAVED, profile_revision: undefined }],
    ['ok missing', { ...SAVED, ok: undefined }],
  ])('RUN-07 SR-2 a malformed 200 (%s) is unknown, never a receipt', (_label, body) => {
    expect(savedSaveAnswer({ status: 200, body }, SENT)).toEqual({ kind: 'unknown' });
  });
  it('RUN-07 a 200 is unknown when the record lost what it sent (no blueprint, revision or changes)', () => {
    expect(savedSaveAnswer({ status: 200, body: SAVED }, {}).kind).toBe('unknown');
    expect(
      savedSaveAnswer({ status: 200, body: SAVED }, { ...SENT, changes: undefined }).kind,
    ).toBe('unknown');
  });
  it('RUN-07 SR-8 a later read settles by revision and values, and only for the same run', () => {
    const sent = {
      blueprint: 'farm',
      profileRevision: String(SAVED['profile_revision']),
      changes: { 'routines.ad_gems.interval_sec': 720 },
    };
    // F6C-R1-02 (root decision a): an unchanged read, or a newer revision without the sent
    // values, proves nothing about a save still in flight or overwritten later; it stays Unknown.
    expect(settleSavedSave(SAVED, sent)).toBeNull();
    expect(settleSavedSave({ ...SAVED, profile_revision: 'new' }, sent)).toBe('applied');
    expect(
      settleSavedSave(
        { ...SAVED, profile_revision: 'new' },
        { ...sent, changes: { 'routines.ad_gems.interval_sec': 30 } },
      ),
    ).toBeNull();
    expect(settleSavedSave({ ...SAVED, blueprint: 'other' }, sent)).toBeNull();
  });
});

describe('RUN-08 RUN-09 the live save gate and marks', () => {
  const liveGroups = () =>
    controlGroups(
      (BATTLE['capabilities'] as { controls: unknown }).controls,
      BATTLE['effective'],
      SAVED['constraints'],
    );
  const review = () => reviewDraft(liveGroups(), { 'routines.ad_gems.window_sec': '30' });
  it('RUN-08 an active battle with a fresh report and its last change applied offers one save at control_revision', () => {
    expect(liveSaveGate(BATTLE, true, review(), 'none')).toEqual({
      enabled: true,
      command: {
        operationId: BATTLE['operation_id'],
        battleId: BATTLE['id'],
        generation: BATTLE['generation'],
        expectedRevision: BATTLE['control_revision'],
        changes: { 'routines.ad_gems.window_sec': 30 },
      },
    });
  });
  it.each([
    [null, true, 'none', 'no_battle'],
    [{ ...BATTLE, status: 'prepared' }, true, 'none', 'battle_not_active'],
    [BATTLE, false, 'none', 'report_stale'],
    [
      { ...BATTLE, applied_revision: Number(BATTLE['control_revision']) - 1 },
      true,
      'none',
      'change_pending',
    ],
    [
      { ...BATTLE, applied_revision: Number(BATTLE['control_revision']) + 1 },
      true,
      'none',
      'revision_unknown',
    ],
    [{ ...BATTLE, applied_revision: undefined }, true, 'none', 'revision_unknown'],
    [BATTLE, true, 'unresolved', 'save_unresolved'],
  ] as const)('RUN-09 no live save: %#', (battle, fresh, request, reason) => {
    expect(liveSaveGate(battle, fresh, review(), request)).toEqual({ enabled: false, reason });
  });
  it('RUN-09 SR-6 the live gate and the battle chip (B5, B6) share one rule: only equal revisions are applied', () => {
    const control = Number(BATTLE['control_revision']);
    expect(controlApplication(BATTLE)).toBe('applied');
    expect(controlApplication({ ...BATTLE, applied_revision: control - 1 })).toBe('pending');
    expect(controlApplication({ ...BATTLE, applied_revision: control + 1 })).toBe('unknown');
    expect(controlApplication({ ...BATTLE, control_revision: null })).toBe('unknown');
  });
  it('RUN-08 marks: an applied override is Changed for this battle; a requested one not yet applied is Waiting', () => {
    const field = liveGroups()
      .flatMap((g) => g.fields)
      .find((f) => f.path === 'routines.ad_gems.enabled');
    const plain = liveGroups()
      .flatMap((g) => g.fields)
      .find((f) => f.path === 'routines.shopping.enabled');
    if (field === undefined || plain === undefined) throw new Error('fields');
    expect(liveMark(BATTLE, field)).toBe('changed');
    expect(liveMark(BATTLE, plain)).toBe('saved');
    expect(liveMark({ ...BATTLE, applied_overrides: {} }, field)).toBe('waiting');
  });
});

describe('RL2 F6-11-P2-05 every applied override leaf belongs to reset-all', () => {
  const battle = (name: string) =>
    (dashboardPagesCase(name).body as { battle: Record<string, unknown> }).battle;
  const mixed = () => battle('shopping_queue.shopping_plan_routine_overrides_applied');
  const paths = (read: unknown) => liveOverrideLeaves(read).map((leaf) => leaf.path);

  it('RL2 F6-11-P2-05 routine-only recorded reads list every leaf and retain the recorded reset body paths', () => {
    const read = battle(
      'run_controls_live_reset_all.read_before_the_reset_two_overrides_are_in_force',
    );
    const found = paths(read);
    expect(found).toEqual(['routines.ad_gems.interval_sec', 'routines.floating_gems.enabled']);
    expect(liveResetGate(read, true, found, 'none')).toMatchObject({
      enabled: true,
      command: { paths: found },
    });
    expect(
      paths(
        battle('run_controls_live_reset_all.read_after_the_partial_reset_one_override_remains'),
      ),
    ).toEqual(['routines.ad_gems.window_sec']);
  });

  it('RL2 the recorded mixed read treats shopping_plan as one applied leaf and enables its reset', () => {
    const read = mixed();
    const found = paths(read);
    expect(found).toEqual([
      'routines.shopping.enabled',
      'routines.shopping.interval_sec',
      'shopping_plan',
    ]);
    expect(liveResetGate(read, true, found, 'none')).toEqual({
      enabled: true,
      command: {
        operationId: read['operation_id'],
        battleId: read['id'],
        generation: read['generation'],
        expectedRevision: read['control_revision'],
        paths: found,
      },
    });
    expect(paths(battle('shopping_queue.shopping_plan_read_applied'))).toEqual(['shopping_plan']);
  });

  it('RL2 F6-11-P2-05 recorded empty routine mappings after reset-all contain no leaves', () => {
    expect(
      paths(battle('run_controls_live_reset_all.read_after_every_override_was_reset')),
    ).toEqual([]);
    expect(
      paths(battle('run_controls_live_reset_all.read_after_the_second_reset_nothing_to_remove')),
    ).toEqual([]);
  });

  it('RL2 F6-11-P2-05 discovery ignores renderer capabilities and excludes unapplied overrides', () => {
    // Modified recorded read: remove only the renderer capability list to prove discovery
    // comes from the battle overrides rather than which fields the editor can render.
    expect(paths({ ...mixed(), capabilities: { controls: [] } })).toEqual(paths(mixed()));
    expect(
      paths(battle('shopping_queue.shopping_plan_requested_over_routine_overrides_read')),
    ).toEqual(['routines.shopping.enabled', 'routines.shopping.interval_sec']);
  });

  it('RL2 F6-11-P2-05 null routine values are not overrides and applied plan equality ignores object key order', () => {
    const read = mixed();
    const applied = read['applied_overrides'] as Record<string, unknown>;
    const plan = applied['shopping_plan'] as Record<string, unknown>;
    // Modified recorded read: a null is a removal marker, and JSON object key order has no meaning.
    expect(
      paths({
        ...read,
        overrides: { routines: { shopping: { enabled: null } }, shopping_plan: plan },
        applied_overrides: {
          routines: { shopping: { enabled: null } },
          shopping_plan: Object.fromEntries(Object.entries(plan).reverse()),
        },
      }),
    ).toEqual(['shopping_plan']);
  });
});
