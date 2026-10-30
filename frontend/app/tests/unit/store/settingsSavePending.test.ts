import { expect, it } from 'vitest';
import { settledByRead } from '../../../src/store/fences.ts';
import { saveOutcome, settingsSavePending } from '../../../src/store/selectors/settingsModule.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';
import { storeHarness } from '../../support/storeHarness.ts';

const recorded = (name: string) => dashboardPagesCase(`profile_target.${name}`);
const context = { instance: 'main', serial: 'fake:1', account: 'alpha', profile: 'mine' };

for (const name of ['mine', 'second'] as const) {
  it(`PE4 C-04 shared save fence for ${name} requires its own advanced source holding the sent value`, () => {
    const h = storeHarness({
      context,
      config: recorded('inactive_editor_context_before_save').body,
    });
    const before = recorded('same_account_inactive_target_source').body as { revision: string };
    const saved = recorded('inactive_gather_label_saved').body as {
      profile: unknown;
      revision: string;
    };
    const source = recorded('same_account_inactive_target_source');
    const resource = name === 'second' ? 'targetSource' : 'profileSource';
    const record = h.intent({
      id: 'patch',
      kind: 'profile_patch',
      phase: 'unconfirmed',
      lostSeq: h.tick(),
      meta: {
        name,
        baseRevision: before.revision,
        patchPath: ['policies', 'gather', 'all_on', 'label'],
        patchValue: 'Offline',
      },
    });
    const read = (body: unknown) => {
      h.read(resource, body, { path: `/api/profile-src/${name}` });
      h.read('settings', dashboardPagesCase('settings.populated_account_every_module').body);
      if (resource !== 'profileSource')
        h.read(
          'profileSource',
          dashboardPagesCase('collection_policies.read_before_the_first_save').body,
        );
    };
    const assertPending = (pending: boolean) => {
      // Runtime and Emulators use settings only; Collection uses both or targetSource;
      // active Runs entry and shopping library use profileSource only.
      expect(settingsSavePending(h.state())).toBe(pending);
      expect(settingsSavePending(h.state(), ['settings'])).toBe(pending);
      expect(settingsSavePending(h.state(), [resource])).toBe(pending);
    };
    // Modified recorded GET: the same source envelope names the tested active/inactive file.
    read({ ...(source.body as object), name });
    assertPending(true);
    // A matching value at the original revision and the value in the wrong file prove nothing.
    read({ ...(source.body as object), name, profile: saved.profile, revision: before.revision });
    assertPending(true);
    read({ ...(source.body as object), name: name === 'second' ? 'mine' : 'second', ...saved });
    assertPending(true);
    expect(settledByRead(h.state(), record)).toBeNull();
    expect(saveOutcome(h.state(), record.target)).toMatchObject({
      code: 'unknown',
      acceptOffered: false,
      dismissOffered: false,
    });

    read({ ...(source.body as object), name, ...saved });
    assertPending(false);
    expect(settledByRead(h.state(), record)).toBe('applied');
    h.fail(resource);
    assertPending(false);
  });
}

it('PE4 C-04 shared save fence preserves explicit acceptance by existing Runs and shopping reconciliation', () => {
  const h = storeHarness({ context, config: recorded('inactive_editor_context_before_save').body });
  const record = h.intent({
    id: 'patch',
    kind: 'profile_patch',
    phase: 'unconfirmed',
    meta: { name: 'mine' },
  });
  h.read(
    'profileSource',
    dashboardPagesCase('collection_policies.read_before_the_first_save').body,
  );
  expect(settingsSavePending(h.state(), ['profileSource'])).toBe(true);
  h.intent({ ...record, accepted: true });
  expect(settingsSavePending(h.state(), ['profileSource'])).toBe(false);
  expect(saveOutcome(h.state(), record.target)).toMatchObject({ code: 'unknown', accepted: true });
});

it('PE4 C-04 an older unresolved profile patch remains visible ahead of newer legacy outcomes', () => {
  const h = storeHarness({ context, config: recorded('inactive_editor_context_before_save').body });
  h.intent({
    id: 'unknown',
    kind: 'profile_patch',
    phase: 'unconfirmed',
    target: 'label',
    dismissed: true,
  });
  h.intent({ id: 'newer', kind: 'profile_patch', phase: 'refused', target: 'label' });
  expect(saveOutcome(h.state(), 'label')).toMatchObject({
    id: 'unknown',
    code: 'unknown',
    dismissOffered: false,
  });
});
