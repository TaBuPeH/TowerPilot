import { act, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { editorSource } from '../../../../src/store/selectors/editingTarget.ts';
import { selectCollectionPage } from '../../../../src/store/selectors/collection.ts';
import { selectRunEntry } from '../../../../src/store/selectors/runEntry.ts';
import { dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { renderPage } from '../dashboard/harness.tsx';

const recorded = (name: string) => dashboardPagesCase(`profile_target.${name}`);
const SOURCE = '/api/profile-src/second';
const stops: (() => void)[] = [];

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  for (const stop of stops.splice(0)) stop();
  window.location.hash = '';
  vi.useRealTimers();
});

async function open(editor: 'collection' | 'runs') {
  window.location.hash = editor === 'collection' ? '#collection' : '#runs/saved/farm';
  const page = await renderPage({
    config: () => recorded('inactive_editor_context_before_save').body,
    before: (fake) => {
      fake.route('GET', '/api/profile-files', () =>
        recorded('same_account_inactive_target_listed'),
      );
      fake.route('GET', SOURCE, () => 'hold');
      fake.route('GET', '/api/profile-src/mine', () =>
        dashboardPagesCase('collection_policies.read_before_the_first_save'),
      );
      fake.route('GET', '/api/vocab', () => dashboardPagesCase('profile_reads.vocab_ok'));
    },
  });
  stops.push(page.data.stop);
  await page.advance();
  return page;
}

for (const editor of ['collection', 'runs'] as const) {
  const prefix = editor === 'collection' ? 'PE4 C-03' : 'PE6 C-03';
  for (const via of ['mine', 'return', 'same name'] as const) {
    it(`${prefix} ${editor} stays blocked after an old second answer via ${via} until its own read arrives`, async () => {
      const page = await open(editor);
      const epoch = page.data.store.getSnapshot().epoch;
      act(() => {
        page.data.commands.pickEditingTarget({ epoch, name: 'second' });
      });
      const old = page.fake.next(SOURCE, 'GET');
      act(() => {
        if (via === 'mine') page.data.commands.pickEditingTarget({ epoch, name: 'mine' });
        if (via === 'return') page.data.commands.returnEditingToActive({ epoch });
        page.data.commands.pickEditingTarget({ epoch, name: 'second' });
      });
      old.respond(200, recorded('same_account_inactive_target_source').body);
      await page.advance();
      expect(page.data.store.getSnapshot().epoch).toBe(epoch);
      expect(editorSource(page.data.store.getSnapshot())).toBeNull();
      if (editor === 'collection') {
        expect(selectCollectionPage(page.data.store.getSnapshot(), null).sourceRead).toBe(
          'not_read',
        );
        expect(page.view.queryByRole('textbox', { name: 'Name shown' })).toBeNull();
      } else {
        expect(selectRunEntry(page.data.store.getSnapshot()).readable).toBe(false);
        expect(page.view.queryByRole('textbox', { name: 'Run label' })).toBeNull();
        const entry = page.view.container.querySelector<HTMLElement>('[data-run-entry]');
        if (entry === null) throw new Error('entry editor missing');
        expect(within(entry).queryByRole('button', { name: /Save field/u })).toBeNull();
      }
      expect(page.fake.writes()).toEqual([]);
      page.fake.next(SOURCE, 'GET').respond(200, recorded('inactive_target_read_after_save').body);
      await page.advance();
      if (editor === 'collection') {
        expect(page.view.getByRole('textbox', { name: 'Name shown' })).toHaveValue('Offline');
      } else {
        expect(page.view.getByRole('textbox', { name: 'Run label' })).toHaveValue('Offline farm');
      }
      expect(page.fake.writes()).toEqual([]);
    });
  }

  it(`${prefix} ${editor} rereads cached second after Return before rendering its fields`, async () => {
    const page = await open(editor);
    const epoch = page.data.store.getSnapshot().epoch;
    act(() => {
      page.data.commands.pickEditingTarget({ epoch, name: 'second' });
    });
    page.fake
      .next(SOURCE, 'GET')
      .respond(200, recorded('same_account_inactive_target_source').body);
    await page.advance();
    expect(editorSource(page.data.store.getSnapshot())).not.toBeNull();
    act(() => {
      page.data.commands.returnEditingToActive({ epoch });
      page.data.commands.pickEditingTarget({ epoch, name: 'second' });
    });
    expect(editorSource(page.data.store.getSnapshot())).toBeNull();
    expect(
      page.view.queryByRole('textbox', {
        name: editor === 'collection' ? 'Name shown' : 'Run label',
      }),
    ).toBeNull();
    page.fake.next(SOURCE, 'GET').respond(200, recorded('inactive_target_read_after_save').body);
    await page.advance();
    expect(
      page.view.getByRole('textbox', {
        name: editor === 'collection' ? 'Name shown' : 'Run label',
      }),
    ).toHaveValue(editor === 'collection' ? 'Offline' : 'Offline farm');
    expect(page.fake.writes()).toEqual([]);
  });
}
