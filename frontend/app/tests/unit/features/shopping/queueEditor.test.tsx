import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { own, ownPath } from '../../../../src/core/read.ts';
import {
  includeUpgrade,
  leaveOutUpgrade,
  moveUpgrade,
  queuePlan,
  reviewQueue,
  setExcluded,
  shopCatalog,
  type QueueBase,
  type QueueDraft,
} from '../../../../src/core/shoppingPlan.ts';
import { QueueEditor } from '../../../../src/features/shopping/QueueEditor.tsx';
import { selectQueueEditor } from '../../../../src/store/selectors/shoppingQueue.ts';
import { initialDashboardState } from '../../../../src/store/dashboardStore.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { renderRegion } from '../dashboard/harness.tsx';

const read = dashboardPagesCase('shopping_queue.shopping_read_before_any_save').body;
const BASE: QueueBase = {
  plan: queuePlan(ownPath(read, 'settings', 'shopping_plan')),
  catalog: shopCatalog(own(read, 'catalog')),
};

let latest: QueueDraft = {};
function Harness() {
  const [draft, setDraft] = useState<QueueDraft>({});
  latest = draft;
  return (
    <QueueEditor
      vm={selectQueueEditor(BASE, draft)}
      idBase="test"
      onEdit={(key, value) => {
        setDraft((current) => ({ ...current, [key]: value }));
      }}
      onInclude={(upgrade) => {
        setDraft((current) => includeUpgrade(BASE, current, upgrade));
      }}
      onLeaveOut={(upgrade) => {
        setDraft((current) => leaveOutUpgrade(current, upgrade));
      }}
      onMove={(upgrade, step) => {
        const next = moveUpgrade(BASE, latest, upgrade, step);
        if (next === null) return null;
        setDraft(next);
        return (
          reviewQueue(BASE, next).entries.find((entry) => entry.id === upgrade)?.position ?? null
        );
      }}
      onExclude={(upgrade, excluded) => {
        setDraft((current) => setExcluded(current, upgrade, excluded));
      }}
    />
  );
}
const queued = () =>
  [...document.querySelectorAll('.tp-shop__entry')].map((item) =>
    item.getAttribute('data-upgrade'),
  );

describe('F6-12 shared queue editor (recorded shopping_read_before_any_save)', () => {
  it('SQ6 named Up/Down keep focus on the moved entry, announce its position and stop at the ends', async () => {
    renderRegion(
      initialDashboardState(),
      <main>
        <Harness />
      </main>,
    );
    const user = userEvent.setup();
    expect(queued()).toEqual(['damage', 'health']);
    const down = document.getElementById('test:entry:damage:down');
    if (down === null) throw new Error('no Move down');
    expect(down).toHaveAccessibleName(/Move down.*Damage/u);
    down.focus();
    await user.keyboard('{Enter}');
    expect(queued()).toEqual(['health', 'damage']);
    expect(document.activeElement?.id).toBe('test:entry:damage:down');
    expect(screen.getByRole('status')).toHaveTextContent('Moved to position 2.');
    // Now last: Move down is unavailable with a reason and does nothing.
    expect(document.getElementById('test:entry:damage:down')).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await user.keyboard('{Enter}');
    expect(queued()).toEqual(['health', 'damage']);
  });

  it('SQ4 SQ5 the picker includes one queue across pages, keeps limits when left out, and typed text stays', () => {
    renderRegion(
      initialDashboardState(),
      <main>
        <Harness />
      </main>,
    );
    const tapHint = 'Blank means unlimited.';
    expect(screen.getAllByText(tapHint)).toHaveLength(1);
    expect(screen.getByText(tapHint)).toBeVisible();
    for (const input of screen.getAllByRole('textbox', { name: 'Tap attempts this battle' })) {
      expect(input).toHaveAccessibleDescription(tapHint);
    }
    fireEvent.click(document.getElementById('test:pick:attack_speed:included') ?? document.body);
    expect(queued()).toEqual(['damage', 'health', 'attack_speed']);
    const tap = document.getElementById('test:entry:damage:tap_limit');
    if (!(tap instanceof HTMLInputElement)) throw new Error('no tap limit');
    fireEvent.change(tap, { target: { value: '1.5e' } });
    expect(tap).toHaveValue('1.5e');
    expect(tap).toHaveAttribute('aria-invalid', 'true');
    expect(tap).toHaveAccessibleDescription(/Not a number/u);
    expect(tap).toHaveAccessibleDescription(/Blank means unlimited/u);
    // The teaching clause lives in the field's help, opened on demand and not in the page.
    expect(screen.queryByText(/Counts taps sent, not levels bought/u)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'About Tap attempts this battle' }));
    expect(screen.getByRole('tooltip')).toHaveTextContent('Counts taps sent, not levels bought.');
    expect(screen.getAllByText(tapHint)).toHaveLength(1);
    fireEvent.click(document.getElementById('test:pick:damage:included') ?? document.body);
    const damage = document.querySelector('[data-upgrade="damage"].tp-shop__entry');
    if (!(damage instanceof HTMLElement)) throw new Error('no damage entry');
    expect(within(damage).getByRole('checkbox', { name: 'Included in queue' })).not.toBeChecked();
    expect(tap).toHaveValue('1.5e');
    expect(screen.getByText('1 field needs a value before this queue can be sent.')).toBeVisible();
  });

  it('SQ4 Defense Absolute starts excluded with its reason; including it removes the exclusion', () => {
    renderRegion(
      initialDashboardState(),
      <main>
        <Harness />
      </main>,
    );
    const excluded = document.getElementById('test:pick:defense_absolute:excluded');
    expect(excluded).toBeChecked();
    expect(document.getElementById('test:pick:defense_absolute:reason')).not.toBeNull();
    fireEvent.click(
      document.getElementById('test:pick:defense_absolute:included') ?? document.body,
    );
    expect(document.getElementById('test:pick:defense_absolute:excluded')).not.toBeChecked();
    expect(queued().at(-1)).toBe('defense_absolute');
  });

  it('SQ3 the purchase rules use catalogue words; reserve 0 is a value', async () => {
    renderRegion(
      initialDashboardState(),
      <main>
        <Harness />
      </main>,
    );
    const reserve = document.getElementById('test:reserve');
    if (!(reserve instanceof HTMLInputElement)) throw new Error('no reserve');
    expect(reserve).toHaveValue('500');
    await act(async () => {
      fireEvent.change(reserve, { target: { value: '0' } });
      await Promise.resolve();
    });
    expect(reviewQueue(BASE, latest).plan).toMatchObject({ cash_reserve: 0 });
    expect(screen.getByRole('combobox', { name: 'Purchase pattern' })).toHaveDisplayValue(
      'First affordable in priority order',
    );
    expect(textOutsideCatalogue(document.body)).toEqual([]);
    expect(await axe(document.body)).toHaveNoViolations();
  });
});
