import { assertProfilePatchContexts } from '../../../support/profilePatchAssertions.ts';
import {
  fencedCase,
  fencedSourceBefore,
  withRecordedFence,
} from '../../../support/profilePatchFenced.ts';
import { contextFromConfig } from '../../../../src/api/context.ts';
import { act, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { entries, own, stringList } from '../../../../src/core/read.ts';
import { selectRunEntry } from '../../../../src/store/selectors/runEntry.ts';
import {
  dashboardPagesCase,
  type DashboardCase,
  dashboardPagesCaseNames,
} from '../../../support/dashboardFixture.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { enterText, renderPage, type PageOptions } from '../dashboard/harness.tsx';

const fenceChecks: (() => void)[] = [];
afterEach(() => {
  fenceChecks.splice(0).forEach((check) => {
    check();
  });
});

const sample = (name: string) => {
  const context = contextFromConfig(CONFIG);
  if (context === null) throw new Error('Missing recorded runtime');
  return withRecordedFence(dashboardPagesCase(`run_entry_patch.${name}`), context);
};
const CONFIG = dashboardPagesCase(
  'run_templates_add_fenced.read_config_the_page_context_comes_from',
).body;
const INITIAL = sample('read_before_the_first_save');
const names = dashboardPagesCaseNames('run_entry_patch');
const PATCH = '/api/profile-patch';
const PROFILE = '/api/profile-src/mine';
const targetCase = (name: string) => dashboardPagesCase(`profile_target.${name}`);
function targetSource(receipt = 'inactive_rescue_policy_saved'): DashboardCase {
  const original = targetCase('same_account_inactive_target_source');
  const saved = targetCase(receipt);
  // The sequential recorded entry saves follow PE3-PE5. No intermediate GET was recorded;
  // place the preceding recorded receipt's exact profile/revision in the target GET envelope.
  return {
    ...original,
    body: {
      ...Object.fromEntries(entries(original.body)),
      profile: own(saved.body, 'profile'),
      revision: own(saved.body, 'revision'),
    },
  };
}
async function openTarget(options: PageOptions = {}, source = () => targetSource(), run = 'farm') {
  const page = await open(run, {
    ...options,
    config: options.config ?? (() => targetCase('inactive_editor_context_before_save').body),
    before: (fake) => {
      fake.route('GET', '/api/profile-files', () =>
        targetCase('same_account_inactive_target_listed'),
      );
      fake.route('GET', '/api/profile-src/second', source);
      options.before?.(fake);
    },
  });
  act(() => {
    page.data.commands.pickEditingTarget({
      epoch: page.data.store.getSnapshot().epoch,
      name: 'second',
    });
  });
  await page.advance(100);
  assertProfilePatchContexts(page.data.machine);
  fenceChecks.push(() => {
    assertProfilePatchContexts(page.data.machine);
  });
  return page;
}
function root() {
  const box = document.querySelector<HTMLElement>('[data-run-entry]');
  if (box === null) throw new Error('missing entry editor');
  return box;
}
const form = () => within(root());
function row(key: string) {
  const box = root().querySelector<HTMLElement>(`[data-entry-field="${key}"]`);
  if (box === null) throw new Error(`missing field ${key}`);
  return within(box);
}
async function open(
  run = 'farm',
  options: PageOptions = {},
  source: () => DashboardCase = () => INITIAL,
) {
  window.location.hash = `#runs/saved/${run}`;
  const page = await renderPage({
    ...options,
    config: options.config ?? (() => CONFIG),
    before: (fake) => {
      fake.route('GET', PROFILE, () => source());
      fake.route('GET', '/api/vocab', () => dashboardPagesCase('profile_reads.vocab_ok'));
      options.before?.(fake);
    },
  });
  await page.advance(100);
  return page;
}
async function save(page: Awaited<ReturnType<typeof open>>, field: string) {
  await page.user.click(row(field).getByRole('button', { name: 'Save field' }));
  await page.advance(100);
}
function sourceBefore(entry: DashboardCase): DashboardCase {
  const expected = own(entry.request.body, 'expected_revision');
  const prior = names
    .map(sample)
    .find(
      (item) => own(item.body, 'revision') === expected && own(item.body, 'profile') !== undefined,
    );
  // No intermediate GET exists: put the preceding recorded receipt's exact profile and
  // revision in the recorded GET envelope to test each field's exact request, without a model backend.
  return prior === undefined
    ? INITIAL
    : {
        ...INITIAL,
        body: {
          ...Object.fromEntries(entries(INITIAL.body)),
          profile: own(prior.body, 'profile'),
          revision: expected,
        },
      };
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  window.location.hash = '';
});

describe('PE6 target-bound entry editor', () => {
  it('PE6 an unlisted explicit run remains named and never falls back to another entry', async () => {
    const page = await openTarget({}, () => targetSource(), 'dissonance');
    expect(form().getByLabelText('Entry run')).toHaveValue('dissonance');
    expect(selectRunEntry(page.data.store.getSnapshot()).fields).toEqual([]);
    expect(form().queryByLabelText('Run label')).toBeNull();
    expect(page.fake.writes()).toEqual([]);
  });
  it.each(
    names.filter((name) => name.startsWith('saved_label') || name.startsWith('saved_loadout')),
  )('PE6 F6-14c existing label/loadout targets keep their original fence: %s', async (name) => {
    const entry = sample(name);
    const path = stringList(own(entry.request.body, 'path')) ?? [];
    const fieldKey = path.slice(2).join('.');
    const previous = sourceBefore(entry);
    // Modified recording: retain this older test's active source/profile under second's
    // identity. runEntryTargetMore covers every new unmodified inactive request body.
    const source: DashboardCase = {
      ...targetCase('same_account_inactive_target_source'),
      body: { ...Object.fromEntries(entries(previous.body)), name: 'second' },
    };
    const page = await openTarget(
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => entry);
        },
      },
      () => source,
      path[1],
    );
    const value = own(entry.request.body, 'value');
    const raw =
      typeof value === 'boolean'
        ? value
        : value == null
          ? ''
          : Array.isArray(value)
            ? value.join('\n')
            : typeof value === 'string' || typeof value === 'number'
              ? String(value)
              : '';
    const field = selectRunEntry(page.data.store.getSnapshot()).fields.find(
      (item) => item.key === fieldKey,
    );
    if (field === undefined) throw new Error('missing recorded entry field');
    act(() => {
      page.data.commands.editDraft(field.scope, 'value', raw);
      if (own(entry.request.body, 'op') === 'delete')
        page.data.commands.editDraft(field.scope, 'remove', true);
    });
    await save(page, fieldKey);
    if (fieldKey !== 'label' && fieldKey !== 'loadout') {
      expect(page.fake.writes()).toEqual([]);
      expect(
        row(fieldKey).getByText('Return editing to the active profile to change this field.'),
      ).toBeVisible();
      expect(
        selectRunEntry(page.data.store.getSnapshot()).fields.find((item) => item.key === fieldKey)
          ?.raw,
      ).toEqual(raw);
      return;
    }
    expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
      {
        ...Object.fromEntries(entries(entry.request.body)),
        name: 'second',
        expected_context: own(
          targetCase('inactive_run_entry_label_saved').request.body,
          'expected_context',
        ),
      },
    ]);
  });
  it.each([
    'read_config_after_editor_active_profile_switch',
    'read_config_after_editor_instance_switch',
    'read_config_after_editor_serial_change',
  ])('PE6 click-time %s sends nothing and retains the target draft', async (name) => {
    let config = targetCase('inactive_editor_context_before_save').body;
    const page = await openTarget({ config: () => config });
    await page.user.type(row('label').getByLabelText('Run label'), ' draft');
    const scope = selectRunEntry(page.data.store.getSnapshot()).fields[0]?.scope ?? '';
    config = targetCase(name).body;
    await save(page, 'label');
    expect(page.fake.writes()).toEqual([]);
    expect(page.data.store.getSnapshot().runDrafts[scope]?.['value']).toBe('Second farm draft');
    expect(form().getByRole('status')).toHaveTextContent('Nothing sent');
  });
  it('PE6 rechecks target generation after held click-time reads and keeps the draft', async () => {
    let hold = false;
    const page = await openTarget({
      before: (fake) => {
        fake.route('GET', '/api/profile-files', () =>
          hold ? 'hold' : targetCase('same_account_inactive_target_listed'),
        );
      },
    });
    await page.user.type(row('label').getByLabelText('Run label'), ' draft');
    hold = true;
    await page.user.click(row('label').getByRole('button', { name: 'Save field' }));
    act(() => {
      page.data.commands.returnEditingToActive({ epoch: page.data.store.getSnapshot().epoch });
      page.data.commands.pickEditingTarget({
        epoch: page.data.store.getSnapshot().epoch,
        name: 'second',
      });
    });
    await act(async () => {
      const response = targetCase('same_account_inactive_target_listed');
      page.fake.next('/api/profile-files').respond(response.status, response.body);
      await Promise.resolve();
    });
    await page.advance(100);
    expect(page.fake.writes()).toEqual([]);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Second farm draft');
    expect(form().getByRole('status')).toHaveTextContent('Nothing sent');
  });
  it('PE6 deleted target at the click sends nothing and keeps the named draft', async () => {
    let deleted = false;
    const page = await openTarget({
      before: (fake) => {
        fake.route('GET', '/api/profile-files', () =>
          targetCase(
            deleted ? 'inactive_target_list_after_deletion' : 'same_account_inactive_target_listed',
          ),
        );
      },
    });
    await page.user.type(row('label').getByLabelText('Run label'), ' draft');
    deleted = true;
    await save(page, 'label');
    expect(page.fake.writes()).toEqual([]);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Second farm draft');
    expect(form().getByRole('status')).toHaveTextContent('Nothing sent');
  });
  it('PE6 a stale target revision keeps its draft and requires explicit continuation', async () => {
    const page = await openTarget({
      before: (fake) => {
        // Ordering injection: a legitimate entry edit receives the recorded revision refusal.
        // The refusal case itself patches a different path; no inactive entry refusal is recorded.
        fake.route('POST', PATCH, () => targetCase('inactive_target_stale_revision'));
      },
    });
    await page.user.type(row('label').getByLabelText('Run label'), ' draft');
    await save(page, 'label');
    expect(row('label').getByLabelText('Run label')).toHaveValue('Second farm draft');
    expect(selectRunEntry(page.data.store.getSnapshot()).fields[0]?.reason).toBe('revision');
    expect(page.fake.writes()).toHaveLength(1);
  });
  it('PE6 Unknown settles only from a later target source read holding the entry value', async () => {
    let source = targetSource();
    let readable = true;
    const page = await openTarget({
      before: (fake) => {
        fake.route('GET', '/api/profile-src/second', () => (readable ? source : 'hold'));
        fake.route('POST', PATCH, () => {
          readable = false;
          // Transport loss wraps the recorded entry receipt; no fabricated error body.
          return 'hold';
        });
      },
    });
    await page.user.clear(row('label').getByLabelText('Run label'));
    await page.user.type(row('label').getByLabelText('Run label'), 'Offline farm');
    await save(page, 'label');
    await act(async () => {
      page.fake.next(PATCH).lose();
      await Promise.resolve();
    });
    await page.advance(100);
    expect(row('label').getByText('Unknown')).toBeInTheDocument();
    await act(async () => {
      await page.data.poller.readNow('profileSource');
    });
    expect(row('label').getByText('Unknown')).toBeInTheDocument();
    source = targetSource('inactive_run_entry_label_saved');
    readable = true;
    await act(async () => {
      page.fake.next('/api/profile-src/second').respond(source.status, source.body);
      await Promise.resolve();
    });
    await page.advance(100);
    expect(root().querySelector('[data-outcome="applied"]')).not.toBeNull();
    expect(row('label').getByLabelText('Run label')).toHaveValue('Offline farm');
    expect(page.fake.writes()).toHaveLength(1);
  });
  it('PE6 active-only Runs sections are disabled with a visible reason until Return', async () => {
    const page = await openTarget();
    const sections = document.querySelectorAll<HTMLFieldSetElement>('[data-runs-active-only]');
    expect(sections.length).toBeGreaterThanOrEqual(3);
    for (const section of sections) {
      expect(section).toBeDisabled();
      expect(section).toHaveTextContent('Active profile:mine');
      expect(section).toHaveTextContent('Return editing to the active profile to change these');
      for (const button of within(section).queryAllByRole('button')) expect(button).toBeDisabled();
    }
    act(() => {
      page.data.commands.returnEditingToActive({ epoch: page.data.store.getSnapshot().epoch });
    });
    await page.advance(100);
    expect(document.querySelector('fieldset[data-runs-active-only][disabled]')).toBeNull();
    expect(page.fake.writes()).toEqual([]);
  });
  it('PE6 reads the target run list, fields, references and revision, retaining separate active drafts', async () => {
    const page = await openTarget();
    expect(row('label').getByLabelText('Run label')).toHaveValue('Second farm');
    expect(form().getByLabelText('Entry run').querySelectorAll('option')).toHaveLength(4);
    expect(row('policies.uw').getByRole('option', { name: 'Calm' })).toBeInTheDocument();
    expect(row('loadout').getByRole('option', { name: 'plain' })).toBeInTheDocument();
    await page.user.clear(row('label').getByLabelText('Run label'));
    await page.user.type(row('label').getByLabelText('Run label'), 'Second draft');
    const targetScope = selectRunEntry(page.data.store.getSnapshot()).fields[0]?.scope;
    act(() => {
      page.data.commands.returnEditingToActive({ epoch: page.data.store.getSnapshot().epoch });
    });
    await page.advance(100);
    expect(row('label').getByLabelText('Run label')).toHaveValue('');
    await page.user.type(row('label').getByLabelText('Run label'), 'Active draft');
    act(() => {
      page.data.commands.pickEditingTarget({
        epoch: page.data.store.getSnapshot().epoch,
        name: 'second',
      });
    });
    await page.advance(100);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Second draft');
    expect(selectRunEntry(page.data.store.getSnapshot()).fields[0]?.scope).toBe(targetScope);
    expect(page.fake.writes()).toEqual([]);
  });

  it('PE6 saves label and loadout once with the exact recorded inactive bodies and target revision', async () => {
    let source = targetSource();
    let receipt = 'inactive_run_entry_label_saved';
    const page = await openTarget(
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => {
            source = targetSource(receipt);
            return targetCase(receipt);
          });
        },
      },
      () => source,
    );
    await page.user.clear(row('label').getByLabelText('Run label'));
    await page.user.type(row('label').getByLabelText('Run label'), 'Offline farm');
    await save(page, 'label');
    receipt = 'inactive_run_entry_loadout_saved';
    await page.user.selectOptions(row('loadout').getByLabelText('Run equipment'), 'plain');
    await save(page, 'loadout');
    expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
      targetCase('inactive_run_entry_label_saved').request.body,
      targetCase('inactive_run_entry_loadout_saved').request.body,
    ]);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Offline farm');
    expect(row('loadout').getByLabelText('Run equipment')).toHaveValue('plain');
    expect(textOutsideCatalogue(root())).toEqual([]);
    vi.useRealTimers();
    expect(await axe(root())).toHaveNoViolations();
    act(() => {
      page.data.stop();
    });
  });
});

describe('RL3 and RL6 saved entry editor', () => {
  it('RL3 entry groups preserve field order, individual save targets and all settings', async () => {
    const page = await open();
    expect(form().getByRole('heading', { level: 4, name: 'Identity' })).toBeVisible();
    expect(form().getByRole('heading', { level: 4, name: 'Entry behavior' })).toBeVisible();
    expect(form().getByRole('heading', { level: 4, name: 'Policies' })).toBeVisible();
    expect(
      [...root().querySelectorAll('[data-entry-field]')].map((field) =>
        field.getAttribute('data-entry-field'),
      ),
    ).toEqual(selectRunEntry(page.data.store.getSnapshot()).fields.map((field) => field.key));
    expect(
      root().querySelector('[data-entry-section="identity"] [data-entry-field="tier"]'),
    ).not.toBeNull();
    expect(
      root().querySelector('[data-entry-section="behavior"] [data-entry-field="max_wave"]'),
    ).not.toBeNull();
    expect(
      root().querySelector('[data-entry-section="policies"] [data-entry-field="shopping"]'),
    ).not.toBeNull();
    expect(page.fake.writes()).toEqual([]);
  });

  it('RL3 an untouched missing label stays neutral; invalid typed text has a visible local error and sends nothing', async () => {
    const page = await open();
    const input = row('label').getByLabelText('Run label');
    expect(input).toHaveValue('');
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
    expect(row('label').getByRole('button', { name: 'Save field' })).toHaveAccessibleDescription(
      'Enter a nonblank label.',
    );
    await page.user.type(input, ' ');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(row('label').getByRole('alert')).toHaveTextContent('Enter a nonblank label.');
    expect(row('label').getByRole('alert')).toBeVisible();
    expect(input).toHaveAccessibleDescription('Enter a nonblank label.');
    await save(page, 'label');
    expect(page.fake.writes()).toEqual([]);
  });

  it('RL3 F6-11-P2-06 sends 31 perk bans once, shows the recorded refusal and keeps the raw draft under its original target', async () => {
    const entry = sample('perk_bans_thirty_one_entries');
    const items = stringList(own(entry.request.body, 'value'));
    if (items === undefined) throw new Error('missing recorded perk bans');
    const raw = items.join('\n');
    const page = await open(
      'farm',
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => entry);
        },
      },
      () => sourceBefore(entry),
    );
    const input = row('perk_bans').getByLabelText('Perk bans (one per line)');
    await page.user.clear(input);
    // One input event: 237 keystrokes each redrew the page and alone exceeded the timeout under load.
    await enterText(page.user, input, raw);
    const before = selectRunEntry(page.data.store.getSnapshot()).fields.find(
      (field) => field.key === 'perk_bans',
    );
    expect(row('perk_bans').getByRole('button', { name: 'Save field' })).not.toHaveAttribute(
      'aria-disabled',
    );
    expect(before?.command?.value).toEqual(items);
    await save(page, 'perk_bans');
    expect(JSON.parse(page.fake.writes()[0]?.body ?? '{}')).toEqual(entry.request.body);
    expect(row('perk_bans').getByLabelText('Perk bans (one per line)')).toHaveValue(raw);
    expect(root().querySelector('[data-refusal] [data-server-text="message"]')?.textContent).toBe(
      own(entry.body, 'error'),
    );
    await page.user.click(row('perk_bans').getByText('1 problem'));
    expect(
      root().querySelector('.tp-save-outcome__problems [data-server-text="message"]')?.textContent,
    ).toBe(own(entry.body, 'error'));
    expect(page.data.store.getSnapshot().runDrafts[before?.scope ?? '']?.['value']).toBe(raw);
    const record = page.data.machine.snapshot().findLast((item) => item.kind === 'profile_patch');
    expect(record?.target).toBe(before?.scope);
    expect(record?.phase).toBe('refused');
    expect(page.data.machine.resend(record?.id ?? '')).toBe('not_resendable');
    await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
    await page.advance(20_000);
    expect(row('perk_bans').getByLabelText('Perk bans (one per line)')).toHaveValue(raw);
    expect(page.data.store.getSnapshot().runDrafts[before?.scope ?? '']?.['value']).toBe(raw);
    expect(page.fake.writes()).toHaveLength(1);
  });

  it.each(names.filter((name) => name.startsWith('saved_')))(
    'RL3 one exact request per field: %s',
    async (name) => {
      const entry = sample(name);
      const path = stringList(own(entry.request.body, 'path')) ?? [];
      const field = path.slice(2).join('.');
      let source = sourceBefore(entry);
      const page = await open(
        path[1],
        {
          before: (fake) => {
            fake.route('POST', PATCH, () => {
              // The receipt itself is recorded; its profile/revision are returned by the following GET.
              source = {
                ...INITIAL,
                body: {
                  ...Object.fromEntries(entries(INITIAL.body)),
                  profile: own(entry.body, 'profile'),
                  revision: own(entry.body, 'revision'),
                },
              };
              return entry;
            });
          },
        },
        () => source,
      );
      const value = own(entry.request.body, 'value');
      const control = root().querySelector<
        HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
      >(
        `[data-entry-field="${field}"] input:not([type="checkbox"]), [data-entry-field="${field}"] select, [data-entry-field="${field}"] textarea, [data-entry-field="${field}"] > .tp-check-field:not(.tp-run-entry__remove) > input[type="checkbox"]`,
      );
      if (own(entry.request.body, 'op') === 'delete' && field === 'perk_bans')
        await page.user.click(row(field).getByLabelText('Leave the game’s perk bans alone'));
      else if (control instanceof HTMLSelectElement)
        await page.user.selectOptions(control, typeof value === 'string' ? value : '');
      else if (control instanceof HTMLInputElement && control.type === 'checkbox') {
        if (control.checked !== value) await page.user.click(control);
      } else if (control !== null) {
        await page.user.clear(control);
        const raw =
          value === null
            ? ''
            : Array.isArray(value)
              ? value.join('\n')
              : typeof value === 'number' || typeof value === 'string'
                ? String(value)
                : '';
        // A list arrives in one input event (see enterText); a single value is typed.
        if (raw !== '' && Array.isArray(value)) await enterText(page.user, control, raw);
        else if (raw !== '') await page.user.type(control, raw);
      }
      await save(page, field);
      expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
        entry.request.body,
      ]);
      await page.advance(10_000);
      expect(page.fake.writes()).toHaveLength(1);
      expect(
        selectRunEntry(page.data.store.getSnapshot()).fields.find((item) => item.key === 'tier')
          ?.revision,
      ).toBe(own(entry.body, 'revision'));
    },
  );

  it.each(names.filter((name) => sample(name).status === 400 || sample(name).status === 404))(
    'RL3 server refusal keeps raw draft and exact text: %s',
    async (name) => {
      // Simulate server validation changing after the page drew: a locally valid edit receives
      // each genuine recorded refusal. Local invalid inputs are separately checked below.
      const entry = sample(name);
      const page = await open('farm', {
        before: (fake) => {
          fake.route('POST', PATCH, () => entry);
        },
      });
      await page.user.clear(row('tier').getByLabelText('Run tier'));
      await page.user.type(row('tier').getByLabelText('Run tier'), '3');
      await save(page, 'tier');
      expect(row('tier').getByLabelText('Run tier')).toHaveValue('3');
      expect(root().querySelector('[data-refusal]')?.textContent).toBe(own(entry.body, 'error'));
      expect(page.fake.writes()).toHaveLength(1);
    },
  );

  it.each(['0', '-1', '2.5', 'NaN', 'two', ''])(
    'RL3 invalid raw tier %s sends nothing',
    async (raw) => {
      const page = await open();
      const input = row('tier').getByLabelText('Run tier');
      await page.user.clear(input);
      if (raw !== '') await page.user.type(input, raw);
      await save(page, 'tier');
      expect(input).toHaveValue(raw);
      expect(input).toHaveAttribute('aria-invalid', 'true');
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('RL3 stale revision retains the draft until explicit continuation from the read', async () => {
    let source = INITIAL;
    const page = await open(
      'farm',
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => {
            source = sample('read_after_the_stale_refusals_holds_the_other_clients_save');
            return sample('stale_revision_409');
          });
        },
      },
      () => source,
    );
    await page.user.type(row('tier').getByLabelText('Run tier'), '3');
    await save(page, 'tier');
    expect(row('tier').getByLabelText('Run tier')).toHaveValue('13');
    expect(row('tier').getByRole('button', { name: 'Save field' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
    await page.advance(100);
    await page.user.click(row('tier').getByRole('button', { name: 'Continue from the read' }));
    expect(row('tier').getByRole('button', { name: 'Save field' })).not.toHaveAttribute(
      'aria-disabled',
    );
    expect(page.fake.writes()).toHaveLength(1);
  });

  it.each(['starter_profile_refused_with_a_flag', 'scan_draft_refused'])(
    'RL3 %s uses server refusal and Profiles link',
    async (name) => {
      const entry = sample(name);
      const profile = own(entry.request.body, 'name');
      // Recorded starter/draft refusals have no matching readable blueprint GET; retain the
      // recorded editable profile shape under that identity to exercise a refusal after drawing.
      const page = await open('farm', {
        config: () => ({ ...Object.fromEntries(entries(CONFIG)), active_profile: profile }),
        before: (fake) => {
          fake.route('GET', `/api/profile-src/${String(profile)}`, () => ({
            ...INITIAL,
            body: { ...Object.fromEntries(entries(INITIAL.body)), name: profile },
          }));
          fake.route('POST', PATCH, () => entry);
        },
      });
      await page.user.type(row('tier').getByLabelText('Run tier'), '3');
      await save(page, 'tier');
      expect(root().querySelector('[data-refusal]')?.textContent).toBe(own(entry.body, 'error'));
      expect(row('tier').getByRole('link', { name: 'Open Profiles' })).toHaveAttribute(
        'href',
        '#profiles',
      );
    },
  );

  it.each([
    'validator_unavailable_nothing_written_500',
    'write_failed_nothing_saved_500',
    'written_then_the_answer_failed_500',
  ])('RL3 %s is Unknown, never resent, settled only by its recorded read', async (name) => {
    let source = INITIAL;
    const page = await open(
      'farm',
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => sample(name));
        },
      },
      () => source,
    );
    await page.user.clear(row('tier').getByLabelText('Run tier'));
    await page.user.type(row('tier').getByLabelText('Run tier'), '3');
    await save(page, 'tier');
    expect(root().querySelector('[data-outcome="unknown"]')).not.toBeNull();
    const record = page.data.machine.snapshot().at(-1);
    expect(page.data.machine.resend(record?.id ?? '')).toBe('not_resendable');
    source = sample(
      name === 'written_then_the_answer_failed_500'
        ? 'read_after_an_error_that_followed_the_write_holds_the_change'
        : name === 'write_failed_nothing_saved_500'
          ? 'read_after_the_failed_write_the_revision_is_unchanged'
          : 'read_after_the_validator_failure_the_revision_is_unchanged',
    );
    await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
    await page.advance(100);
    expect(
      root().querySelector(
        `[data-outcome="${name === 'written_then_the_answer_failed_500' ? 'applied' : 'unknown'}"]`,
      ),
    ).not.toBeNull();
    await page.advance(20_000);
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL3 Unknown needs a newer read and explicit continuation, which sends nothing', async () => {
    const page = await open('farm', {
      before: (fake) => {
        fake.route('POST', PATCH, () => sample('write_failed_nothing_saved_500'));
      },
    });
    await page.user.clear(row('tier').getByLabelText('Run tier'));
    await page.user.type(row('tier').getByLabelText('Run tier'), '3');
    await save(page, 'tier');
    await page.user.click(row('tier').getByRole('button', { name: 'Continue from the read' }));
    expect(row('tier').getByRole('button', { name: 'Save field' })).not.toHaveAttribute(
      'aria-disabled',
    );
    expect(root().querySelector('[data-outcome="unknown"]')).not.toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
    expect(page.data.machine.snapshot().at(-1)?.accepted).toBe(true);
  });

  it('RL3 a held save blocks duplicate clicks and preserves newer typing after its receipt', async () => {
    let source = INITIAL;
    const entry = sample('saved_label');
    const page = await open(
      'farm',
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => 'hold');
        },
      },
      () => source,
    );
    await page.user.type(row('label').getByLabelText('Run label'), 'Daily coin farm');
    await save(page, 'label');
    await save(page, 'label');
    await page.user.type(row('label').getByLabelText('Run label'), ' later');
    source = {
      ...INITIAL,
      body: {
        ...Object.fromEntries(entries(INITIAL.body)),
        profile: own(entry.body, 'profile'),
        revision: own(entry.body, 'revision'),
      },
    };
    await act(async () => {
      page.fake.next(PATCH).respond(entry.status, entry.body);
      await Promise.resolve();
    });
    await page.advance(100);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Daily coin farm later');
    const newer = selectRunEntry(page.data.store.getSnapshot()).fields.find(
      (field) => field.key === 'label',
    );
    expect(newer?.reason).toBe('revision');
    expect(newer?.command).toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL3 unchecking a pending perk-ban deletion retains the newer list after its receipt', async () => {
    const entry = sample('saved_perk_bans_deleted_leaves_the_games_bans_alone');
    let source = sourceBefore(entry);
    const page = await open(
      'farm',
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => 'hold');
        },
      },
      () => source,
    );
    await page.user.type(row('perk_bans').getByLabelText('Perk bans (one per line)'), 'coins');
    await page.user.click(row('perk_bans').getByLabelText('Leave the game’s perk bans alone'));
    await save(page, 'perk_bans');
    expect(JSON.parse(page.fake.writes()[0]?.body ?? '{}')).toEqual(entry.request.body);
    await page.user.click(row('perk_bans').getByLabelText('Leave the game’s perk bans alone'));
    source = {
      ...INITIAL,
      body: {
        ...Object.fromEntries(entries(INITIAL.body)),
        profile: own(entry.body, 'profile'),
        revision: own(entry.body, 'revision'),
      },
    };
    await act(async () => {
      page.fake.next(PATCH).respond(entry.status, entry.body);
      await Promise.resolve();
    });
    await page.advance(100);
    expect(row('perk_bans').getByLabelText('Perk bans (one per line)')).toHaveValue('coins');
    expect(row('perk_bans').getByLabelText('Leave the game’s perk bans alone')).not.toBeChecked();
    expect(row('perk_bans').getByRole('button', { name: 'Save field' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await page.user.click(row('perk_bans').getByRole('button', { name: 'Continue from the read' }));
    expect(
      selectRunEntry(page.data.store.getSnapshot()).fields.find(
        (field) => field.key === 'perk_bans',
      )?.command,
    ).toMatchObject({ value: ['coins'], remove: false, revision: own(entry.body, 'revision') });
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL3 a confirmed field advances the revision for the next field', async () => {
    let source = INITIAL;
    let next = sample('saved_label');
    const page = await open(
      'farm',
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => {
            const reply = next;
            source = {
              ...INITIAL,
              body: {
                ...Object.fromEntries(entries(INITIAL.body)),
                profile: own(reply.body, 'profile'),
                revision: own(reply.body, 'revision'),
              },
            };
            return reply;
          });
        },
      },
      () => source,
    );
    await page.user.type(row('label').getByLabelText('Run label'), 'Daily coin farm');
    await save(page, 'label');
    next = sample('saved_tier');
    await page.user.clear(row('tier').getByLabelText('Run tier'));
    await page.user.type(row('tier').getByLabelText('Run tier'), '2');
    await save(page, 'tier');
    expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
      sample('saved_label').request.body,
      next.request.body,
    ]);
  });

  it('RL3 an explicit read retries failed vocabulary and restores the editor', async () => {
    let ready = false;
    const page = await open('farm', {
      before: (fake) => {
        // Transport loss is injected around the genuine vocabulary case; no new server data.
        fake.route('GET', '/api/vocab', () =>
          ready ? dashboardPagesCase('profile_reads.vocab_ok') : 'hold',
        );
      },
    });
    await act(async () => {
      page.fake.next('/api/vocab').lose();
      await Promise.resolve();
    });
    await page.advance(100);
    expect(selectRunEntry(page.data.store.getSnapshot()).fields).toEqual([]);
    ready = true;
    await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
    await page.advance(100);
    expect(row('tier').getByLabelText('Run tier')).toHaveValue('1');
    expect(page.fake.writes()).toEqual([]);
  });

  it('RL3 a changed click-time context sends nothing and keeps the original scoped draft', async () => {
    let config = CONFIG;
    const page = await open('farm', { config: () => config });
    await page.user.type(row('label').getByLabelText('Run label'), 'Draft');
    config = dashboardPagesCase(
      'run_templates_add_fenced.read_config_after_another_client_switched_the_account',
    ).body;
    await save(page, 'label');
    expect(page.fake.writes()).toEqual([]);
    expect(
      Object.values(page.data.store.getSnapshot().runDrafts).some(
        (draft) => draft['value'] === 'Draft',
      ),
    ).toBe(true);
  });

  it('RL3 raw scoped drafts survive polls and reload', async () => {
    const page = await open();
    await page.user.clear(row('tier').getByLabelText('Run tier'));
    await page.user.type(row('tier').getByLabelText('Run tier'), 'two');
    await page.advance(10_000);
    const storage = page.storage;
    page.view.unmount();
    page.data.stop();
    await open('farm', { storage });
    expect(row('tier').getByLabelText('Run tier')).toHaveValue('two');
  });

  it('RL6 exact policy editor targets, RL3 axe and catalogue', async () => {
    const page = await open('farm_deep');
    expect(
      row('policies.gather').getByRole('link', { name: 'Edit gathering policy' }),
    ).toHaveAttribute('href', '#collection/policy/gems_only');
    for (const key of ['policies.uw', 'policies.rescue'])
      expect(row(key).getByRole('link', { name: 'Open Abilities' })).toHaveAttribute(
        'href',
        key === 'policies.uw'
          ? '#abilities/uw_policies/farm_cl'
          : '#abilities/rescue_policies/wall_drain',
      );
    expect(row('shopping').queryByRole('link')).toBeNull();
    expect(textOutsideCatalogue(root())).toEqual([]);
    vi.useRealTimers();
    expect(await axe(root())).toHaveNoViolations();
    act(() => {
      page.data.stop();
    });
  });
});

describe('refused RunEntry draft after a failed source reread', () => {
  it.each([false, true])(
    'keeps a current HTTP 200 source without a revision editable but unsavable; inactive=%s',
    async (inactive) => {
      const current = inactive ? targetSource() : INITIAL;
      const malformed = {
        ...current,
        body: Object.fromEntries(entries(current.body).filter(([key]) => key !== 'revision')),
      };
      const page = inactive
        ? await openTarget({}, () => malformed)
        : await open('farm', {}, () => malformed);
      const input = row('label').getByLabelText('Run label');
      expect(input).toBeEnabled();
      await page.user.clear(input);
      await page.user.type(input, 'Keep editing');
      expect(input).toHaveValue('Keep editing');
      const vm = selectRunEntry(page.data.store.getSnapshot());
      expect(vm.readable).toBe(false);
      expect(vm.readFailure).toBeNull();
      expect(vm.fields.every((field) => !field.readOnly)).toBe(true);
      expect(vm.fields.every((field) => field.command === null)).toBe(true);
      expect(row('label').getByRole('button', { name: 'Save field' })).toHaveAttribute(
        'aria-disabled',
        'true',
      );
      await save(page, 'label');
      expect(page.fake.writes()).toEqual([]);
    },
  );

  async function refused() {
    const original = fencedCase('active_run_entry_label_saved');
    const before = fencedSourceBefore('active_run_entry_label_saved');
    const page = await open(
      'farm',
      {
        before: (fake) => {
          fake.route('POST', PATCH, () => fencedCase('active_patch_refused_after_account_switch'));
        },
      },
      () => before,
    );
    await page.user.clear(row('label').getByLabelText('Run label'));
    await page.user.type(row('label').getByLabelText('Run label'), 'Offline farm');
    const captured = selectRunEntry(page.data.store.getSnapshot()).fields.find(
      (field) => field.key === 'label',
    )?.command;
    if (captured == null) throw new Error('missing valid original command');
    await save(page, 'label');
    expect(page.fake.writes().map((write) => JSON.parse(write.body ?? '{}') as unknown)).toEqual([
      original.request.body,
    ]);
    await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
    await page.advance(100);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Offline farm');
    expect(form().getByText('Not saved: the account or profile changed')).toBeVisible();
    return { page, captured, before };
  }

  async function failRead(page: Awaited<ReturnType<typeof open>>) {
    page.fake.route('GET', PROFILE, () => 'hold');
    await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
    await page.advance(100);
    act(() => {
      page.fake.next(PROFILE).lose();
    });
    await page.advance(100);
  }

  it('retains the refused raw draft read-only, explains failure and recovers without resending', async () => {
    const { page, before } = await refused();
    await failRead(page);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Offline farm');
    expect(row('label').getByLabelText('Run label')).toBeDisabled();
    expect(form().getByText('Not saved: the account or profile changed')).toBeVisible();
    expect(form().getByText('The server could not be reached.')).toBeVisible();
    expect(form().queryByText('network', { exact: true })).toBeNull();
    expect(
      form().getByText('The last read is shown read-only. Your draft and save result are kept.'),
    ).toBeVisible();
    expect(form().queryByRole('button', { name: 'Continue from the read' })).toBeNull();
    for (const button of form().getAllByRole('button', { name: 'Save field' }))
      expect(button).toBeDisabled();
    expect(row('label').getByRole('button', { name: 'Clear label' })).toBeDisabled();
    expect(row('label').getByRole('button', { name: 'Discard field draft' })).toBeDisabled();
    const vm = selectRunEntry(page.data.store.getSnapshot());
    expect(
      vm.fields.every(
        (field) =>
          field.command === null && field.clearCommand === null && field.continuation === null,
      ),
    ).toBe(true);
    await page.advance(60_000);
    expect(page.fake.writes()).toHaveLength(1);
    page.fake.route('GET', PROFILE, () => before);
    await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
    await page.advance(100);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Offline farm');
    expect(row('label').getByLabelText('Run label')).toBeEnabled();
    expect(row('label').getByRole('button', { name: 'Save field' })).toBeEnabled();
    expect(form().getByText('Not saved: the account or profile changed')).toBeVisible();
    expect(form().queryByText('The server could not be reached.')).toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('refuses a captured RunEntry command after source failure without an additional POST', async () => {
    const { page, captured } = await refused();
    await failRead(page);
    await act(async () => {
      await page.data.commands.patchRunEntry(captured);
    });
    await page.advance(100);
    expect(page.fake.writes()).toHaveLength(1);
  });

  it.each(['http', 'invalid body'])(
    'keeps the refusal and explains the %s read failure using client copy',
    async (failure) => {
      const { page } = await refused();
      page.fake.route('GET', PROFILE, () =>
        failure === 'http'
          ? { status: 503, body: { error: 'Profile source temporarily unavailable' } }
          : { status: 200, body: 'not JSON' },
      );
      await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
      await page.advance(100);
      expect(row('label').getByLabelText('Run label')).toHaveValue('Offline farm');
      expect(row('label').getByLabelText('Run label')).toBeDisabled();
      expect(form().getByText('The endpoint did not return a readable result.')).toBeVisible();
      expect(form().queryByText(failure === 'http' ? 'http_503' : 'body_not_json')).toBeNull();
      if (failure === 'http')
        expect(form().getByText('Profile source temporarily unavailable')).toBeVisible();
      expect(form().getByText('Not saved: the account or profile changed')).toBeVisible();
      expect(textOutsideCatalogue(root())).toEqual([]);
      expect(page.fake.writes()).toHaveLength(1);
    },
  );

  it.each([false, true])(
    'rechecks a reserved command after click-time reads; source failure=%s',
    async (failure) => {
      const page = await open();
      await page.user.type(row('label').getByLabelText('Run label'), 'Reserved label');
      const captured = selectRunEntry(page.data.store.getSnapshot()).fields.find(
        (field) => field.key === 'label',
      )?.command;
      if (captured == null) throw new Error('missing reserved command');
      page.fake.route('GET', '/api/config', () => 'hold');
      page.fake.route('POST', PATCH, () => sample('saved_label'));
      let sending: Promise<void> | undefined;
      act(() => {
        sending = page.data.commands.patchRunEntry(captured);
      });
      await page.advance(100);
      const check = page.fake.next('/api/config');
      // Invalid newer text must not replace or invalidate the reserved submission.
      await page.user.clear(row('label').getByLabelText('Run label'));
      if (failure) await failRead(page);
      await act(async () => {
        check.respond(200, CONFIG);
        await sending;
      });
      await page.advance(100);
      expect(page.fake.writes()).toHaveLength(failure ? 0 : 1);
      if (!failure)
        expect(JSON.parse(page.fake.writes()[0]?.body ?? '{}')).toMatchObject({
          value: 'Reserved label',
        });
    },
  );

  it.each([
    'read_config_after_editor_active_profile_switch',
    'read_config_after_editor_instance_switch',
    'read_config_after_editor_serial_change',
  ])('hides failed-read history and refuses captured commands after %s', async (name) => {
    const { page, captured } = await refused();
    await failRead(page);
    page.fake.route('GET', '/api/config', () => targetCase(name));
    await act(async () => {
      await page.data.poller.readNow('config');
    });
    await page.advance(100);
    expect(form().queryByDisplayValue('Offline farm')).toBeNull();
    expect(form().queryByText('Not saved: the account or profile changed')).toBeNull();
    await act(async () => {
      await page.data.commands.patchRunEntry(captured);
    });
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('does not retain history across an account change or a fresh context epoch', async () => {
    const { page, captured } = await refused();
    await failRead(page);
    const previous = page.data.store.getSnapshot();
    act(() => {
      page.data.store.dispatch({
        type: 'context',
        context: { ...captured.context, account: 'other-account' },
        epoch: previous.epoch + 1,
        confirmed: true,
        sessionContext: null,
      });
    });
    expect(selectRunEntry(page.data.store.getSnapshot()).fields).toEqual([]);
    expect(form().queryByDisplayValue('Offline farm')).toBeNull();
    await act(async () => {
      await page.data.commands.patchRunEntry(captured);
    });
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('does not expose retained fields from an earlier inactive-target selection', async () => {
    const page = await openTarget();
    await page.user.type(row('label').getByLabelText('Run label'), ' retained draft');
    const captured = selectRunEntry(page.data.store.getSnapshot()).fields.find(
      (field) => field.key === 'label',
    )?.command;
    if (captured == null) throw new Error('missing original selection command');
    page.fake.route('GET', '/api/profile-src/second', () => 'hold');
    await page.user.click(form().getByRole('button', { name: 'Read entry settings' }));
    await page.advance(100);
    act(() => {
      page.fake.next('/api/profile-src/second').lose();
    });
    await page.advance(100);
    expect(row('label').getByLabelText('Run label')).toHaveValue('Second farm retained draft');
    for (const input of root().querySelectorAll('input, textarea, select')) {
      if (input !== form().getByLabelText('Entry run')) expect(input).toBeDisabled();
    }
    act(() => {
      page.data.commands.returnEditingToActive({ epoch: page.data.store.getSnapshot().epoch });
      page.data.commands.pickEditingTarget({
        epoch: page.data.store.getSnapshot().epoch,
        name: 'second',
      });
    });
    await page.advance(100);
    expect(form().queryByDisplayValue('Second farm retained draft')).toBeNull();
    expect(selectRunEntry(page.data.store.getSnapshot()).fields).toEqual([]);
    await act(async () => {
      await page.data.commands.patchRunEntry(captured);
    });
    expect(page.data.store.getSnapshot().notices.at(-1)).toMatchObject({
      kind: 'command_not_sent',
      reason: 'target_changed',
    });
    expect(page.fake.writes()).toEqual([]);
  });
});
