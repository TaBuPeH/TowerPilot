import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  editingFiles,
  selectEditingChoices,
  selectEditingTarget,
} from '../../../src/store/selectors/editingTarget.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';
import { bootDashboard, flush } from '../../support/dataLayer.ts';

const recorded = (name: string) => dashboardPagesCase(`profile_target.${name}`);
const stops: (() => void)[] = [];

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  for (const stop of stops.splice(0)) stop();
  vi.useRealTimers();
});

it('PE2 the picker offers and pins no profile until a config read confirms the page context', async () => {
  const config = recorded('inactive_editor_context_before_save');
  let holdConfig = true;
  const { data, fake } = bootDashboard({
    before: (transport) => {
      transport.route('GET', '/api/config', () => (holdConfig ? 'hold' : config));
      transport.route('GET', '/api/profile-files', () =>
        recorded('same_account_inactive_target_listed'),
      );
    },
  });
  stops.push(data.stop);
  await flush();
  data.commands.openProfiles(true);
  await flush();
  const before = data.store.getSnapshot();
  // The recorded list answered inside the first epoch, before any context was named.
  expect(before.contextConfirmed).toBe(false);
  expect(editingFiles(before)?.files.map((file) => file.name)).toContain('second');
  expect(selectEditingChoices(before)).toEqual([]);
  data.commands.pickEditingTarget({ epoch: before.epoch, name: 'second' });
  expect(selectEditingTarget(data.store.getSnapshot()).pinned).toBe(false);

  holdConfig = false;
  fake.next('/api/config', 'GET').respond(config.status, config.body);
  await flush();
  const after = data.store.getSnapshot();
  expect(after.contextConfirmed).toBe(true);
  expect(after.epoch).toBe(before.epoch + 1);
  expect(selectEditingChoices(after).map((choice) => choice.key)).toContain('second');
  data.commands.pickEditingTarget({ epoch: after.epoch, name: 'second' });
  expect(selectEditingTarget(data.store.getSnapshot())).toMatchObject({
    name: 'second',
    pinned: true,
    inactive: true,
  });
  expect(fake.writes()).toEqual([]);
});
