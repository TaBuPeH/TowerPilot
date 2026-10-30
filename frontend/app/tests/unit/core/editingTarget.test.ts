import { expect, it } from 'vitest';
import {
  editTargetKey,
  editTargetScope,
  editingTarget,
  pickableProfiles,
} from '../../../src/core/editingTarget.ts';
import { profileFilesView, settleProfileWrite } from '../../../src/core/profiles.ts';
import { contextFromConfig } from '../../../src/api/context.ts';
import { restoreEditTargets, saveEditTargets } from '../../../src/api/editTargets.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';
import { createMemoryStorage } from '../../support/fakeTransport.ts';
import { storeHarness } from '../../support/storeHarness.ts';
import { settledByRead } from '../../../src/store/fences.ts';
import { entries } from '../../../src/core/read.ts';

const body = (name: string) => dashboardPagesCase(`profile_target.${name}`).body;
const context = contextFromConfig(body('inactive_editor_context_before_save'));
if (context === null) throw new Error('fixture context missing');

it('PE2 D2 pin follows identity rather than active profile; never falls back after deletion', () => {
  const files = profileFilesView(body('same_account_inactive_target_listed'));
  expect(pickableProfiles(files).map((file) => file.name)).toEqual(['mine', 'second']);
  expect(editTargetKey(context)).toBe(editTargetKey({ ...context, profile: 'second' }));
  expect(editTargetScope(context, 'second')).toBe(
    editTargetScope({ ...context, profile: 'second' }, 'second'),
  );
  expect(editTargetScope(context, 'second')).not.toBe(editTargetScope(context, 'mine'));
  const pick = { name: 'second', generation: 3 };
  expect(editingTarget(context, pick, null)).toMatchObject({
    name: 'second',
    blocked: 'files_unread',
  });
  expect(
    editingTarget(context, pick, profileFilesView(body('inactive_target_list_after_deletion'))),
  ).toMatchObject({ name: 'second', blocked: 'not_listed' });
  expect(editingTarget({ ...context, profile: null }, undefined, null)).toMatchObject({
    name: null,
    blocked: 'no_profile',
  });
});

it('PE2 persistence rejects malformed picks and restores only their exact identity keys', () => {
  const storage = createMemoryStorage();
  const key = editTargetKey(context);
  const picks = { [key]: { name: 'second', generation: 3 } };
  saveEditTargets(storage, picks, 3);
  expect(restoreEditTargets(storage)).toEqual({ editTargets: picks, editTargetGeneration: 3 });
  saveEditTargets(storage, {}, 4);
  expect(restoreEditTargets(storage)).toEqual({ editTargets: {}, editTargetGeneration: 4 });
  storage.setItem(
    'tp.editTarget.v1',
    JSON.stringify({
      bad: { name: 'mine', generation: 2 },
      [key]: { name: 'second', generation: -1 },
    }),
  );
  expect(restoreEditTargets(storage)).toEqual({ editTargets: {}, editTargetGeneration: 0 });
});

it('PE1 D9 an earlier config cannot settle and a later profile list is only supporting evidence', () => {
  const h = storeHarness({ context, config: body('read_after_unbind_account_context') });
  const record = h.intent({
    id: 'unbind',
    kind: 'profile_activate',
    phase: 'unconfirmed',
    context,
    meta: { unbind: true },
  });
  h.read(
    'profileFiles',
    dashboardPagesCase('profile_files_write.files_after_unbinding_have_no_active_profile').body,
  );
  expect(settledByRead(h.state(), record)).toBeNull();
  h.read('config', body('read_after_unbind_account_context'));
  expect(settledByRead(h.state(), record)).toBe('applied');
});

it.each(['instance', 'serial', 'account'])(
  'PE1 D9 a config with a different %s cannot settle the unbind',
  (field) => {
    // Modify the expected request identity, not the recorded read: each mismatched field must refuse settlement.
    const expected = { ...context, [field]: 'other' };
    expect(
      settleProfileWrite(
        'profile_activate',
        { unbind: true, context: expected },
        {
          accounts: undefined,
          files: undefined,
          config: body('read_after_unbind_account_context'),
        },
      ),
    ).toBeNull();
  },
);

it.each([undefined, null, '', 7])(
  'PE1 D9 incomplete config_revision %s cannot settle an unbind',
  (revision) => {
    // Modified recorded config: remove or corrupt only its revision to prove incomplete reads cannot settle.
    const config = Object.fromEntries(
      entries(body('read_after_unbind_account_context')).filter(
        ([key]) => key !== 'config_revision',
      ),
    );
    if (revision !== undefined) config['config_revision'] = revision;
    expect(
      settleProfileWrite(
        'profile_activate',
        { unbind: true, context },
        { accounts: undefined, files: undefined, config },
      ),
    ).toBeNull();
  },
);
