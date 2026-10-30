import { describe, expect, it } from 'vitest';
import { battleIdentity } from '../../../src/core/battle.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { actionGate, liveChangeProgress, runNowAnswer } from '../../../src/core/routines.ts';
import { liveSaveGate } from '../../../src/core/runControls.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';
const body = (name: string) => dashboardPagesCase(`shopping_queue.${name}`).body;
const applied = body('shopping_plan_read_applied');
const identity = battleIdentity(own(applied, 'battle'), 0);
if (identity.kind !== 'known') throw new Error('recorded identity missing');
const shown = identity.value;
const read = (value: unknown) => ({ kind: 'answered' as const, sameContext: true, body: value });
describe('SQ7 SQ8 SQ12 shopping live evidence', () => {
  it('SQ12 requires applied_paths and the effective sent plan, beyond an applied command', () => {
    const answer = body('shopping_plan_accepted');
    expect(
      liveChangeProgress(read(body('shopping_plan_read_requested')), shown, answer, null),
    ).toBe('waiting');
    expect(liveChangeProgress(read(applied), shown, answer, null)).toBe('applied');
    // Alter only effective state to simulate a stale/inconsistent read: command alone cannot prove application.
    const inconsistent = {
      ...(applied as object),
      battle: {
        ...(own(applied, 'battle') as object),
        effective: ownPath(body('shopping_plan_read_requested'), 'battle', 'effective'),
      },
    };
    expect(liveChangeProgress(read(inconsistent), shown, answer, null)).toBe('waiting');
    // A command marked applied without its shopping path is insufficient evidence.
    const noPath = structuredClone(applied) as {
      battle: { commands: { applied_paths: string[] }[] };
    };
    const command = noPath.battle.commands[0];
    if (command === undefined) throw new Error('fixture command absent');
    command.applied_paths = [];
    expect(liveChangeProgress(read(noPath), shown, answer, null)).toBe('waiting');
    // The receipt retains the sent plan even if a later malformed command omits its payload.
    const missingPayload = structuredClone(noPath) as {
      battle: { commands: { payload?: unknown }[] };
    };
    const missing = missingPayload.battle.commands[0];
    if (missing === undefined) throw new Error('fixture command absent');
    missing.payload = {};
    expect(liveChangeProgress(read(missingPayload), shown, answer, null)).toBe('waiting');
  });
  it('SQ8 blocks unsaved queue drafts and unresolved commands while keeping ordinary actions unchanged', () => {
    expect(
      actionGate(read(applied), shown, 'shopping:attack', false, 0, { liveDraft: true }),
    ).toMatchObject({ enabled: false, reason: 'unsaved_live_queue' });
    expect(
      actionGate(read(applied), shown, 'shopping', false, 0, { unresolvedCommand: true }),
    ).toMatchObject({ enabled: false, reason: 'earlier_outcome_unknown' });
    expect(actionGate(read(applied), shown, 'shopping', false)).toMatchObject({ enabled: true });
    expect(actionGate(read(applied), shown, 'shopping:attack:damage', false)).toMatchObject({
      enabled: true,
    });
    expect(actionGate(read(applied), shown, 'shopping:attack:made_up', false)).toMatchObject({
      enabled: false,
      reason: 'action_unsupported',
    });
  });
  it('SQ7 uses the live gate revision, freshness and pending-change fences for full plans', () => {
    const review = {
      changes: { shopping_plan: ownPath(applied, 'battle', 'effective', 'shopping_plan') },
      invalid: {},
    };
    expect(liveSaveGate(own(applied, 'battle'), true, review, 'none')).toMatchObject({
      enabled: true,
      command: { expectedRevision: 1 },
    });
    expect(liveSaveGate(own(applied, 'battle'), false, review, 'none')).toMatchObject({
      enabled: false,
      reason: 'report_stale',
    });
    expect(liveSaveGate(own(applied, 'battle'), true, review, 'unresolved')).toMatchObject({
      enabled: false,
      reason: 'save_unresolved',
    });
  });
});

it.each([
  ['shopping_legacy_refused', 'not_recorded'],
  ['shopping_upgrade_reply_unknown', 'unknown'],
  ['shopping_upgrade_identical_replay', 'recorded'],
])('SQ8 recorded %s action reply is %s', (name, expected) => {
  const answer = dashboardPagesCase(`shopping_queue.${name}`);
  expect(runNowAnswer({ kind: 'answered', status: answer.status, body: answer.body }).kind).toBe(
    expected,
  );
});
