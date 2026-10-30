import { act, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { entries, own } from '../../../../src/core/read.ts';
import { selectRunsAdd } from '../../../../src/store/selectors/runsAdd.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import type { DashboardCase } from '../../../support/dashboardFixture.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../../support/dashboardFixture.ts';

const sample = (name: string) => dashboardPagesCase(`run_templates_add.${name}`);
const fenced = (name: string) => dashboardPagesCase(`run_templates_add_fenced.${name}`);
const CONFIG = fenced('read_config_the_page_context_comes_from').body;
const SOURCE = fenced('read_before_the_first_add');
const ADD = '/api/run-templates/add';
const PROFILE = '/api/profile-src/mine';
const form = () => {
  const found = document.querySelector<HTMLElement>('[data-run-add]');
  if (found === null) throw new Error('no add form');
  return within(found);
};

async function open(options: PageOptions = {}, source: () => DashboardCase = () => SOURCE) {
  window.location.hash = '#runs';
  const page = await renderPage({
    ...options,
    config: options.config ?? (() => CONFIG),
    before: (fake) => {
      fake.route('GET', PROFILE, () => source());
      fake.route('GET', '/api/run-templates', () => sample('read_the_recipes_the_page_offers'));
      options.before?.(fake);
    },
  });
  await page.advance(100);
  await page.user.click(form().getByText('Add a run', { exact: true }));
  return page;
}

const FIELD_NAMES: Readonly<Record<string, string>> = {
  label: 'New run label',
  tier: 'Tier',
  loadout: 'Equipment',
  count: 'Run count',
  enable_uw: 'Include Ultimate Weapon rules',
  enable_rescue: 'Include rescue rules',
  gem_entry_max: 'Tournament gem entry cap',
  dissonant_tab: 'Workshop tab to disable',
  perk_bans: 'Perk bans (one per line)',
};

async function fill(page: Awaited<ReturnType<typeof open>>, entry: DashboardCase) {
  const body = entry.request.body as {
    run_id: string;
    template: string;
    options: Record<string, string | number | boolean | string[]>;
  };
  await page.user.selectOptions(form().getByLabelText('Recipe'), body.template);
  await page.user.clear(form().getByLabelText('Run identifier'));
  await page.user.type(form().getByLabelText('Run identifier'), body.run_id);
  for (const [field, value] of Object.entries(body.options)) {
    const input = form().getByLabelText(FIELD_NAMES[field] ?? field);
    if (typeof value === 'boolean') {
      if (value) await page.user.click(input);
    } else if (input.tagName === 'SELECT') await page.user.selectOptions(input, String(value));
    else {
      await page.user.clear(input);
      await page.user.type(input, Array.isArray(value) ? value.join('\n') : String(value));
    }
  }
}

async function save(page: Awaited<ReturnType<typeof open>>) {
  await page.user.click(form().getByRole('button', { name: 'Save new run' }));
  await page.advance(100);
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('RL4 Add a run', () => {
  it.each(
    dashboardPagesCaseNames('run_templates_add_coded').filter(
      (name) =>
        own(dashboardPagesCase(`run_templates_add_coded.${name}`).body, 'code') !== undefined,
    ),
  )(
    'RL4 coded refusal %s shows server text, catalogue explanation, and keeps the draft',
    async (name) => {
      const answer = dashboardPagesCase(`run_templates_add_coded.${name}`);
      // Race/validation replay: preserve the recorded refusal while submitting a locally valid
      // fenced request; malformed choices and unavailable profiles are server boundary cases.
      const page = await open({
        before: (fake) => {
          fake.route('POST', ADD, () => answer);
        },
      });
      await fill(page, fenced('added_with_the_matching_revision_and_context'));
      await save(page);
      expect(form().getByText('Run was not added. Your draft is kept.')).toBeVisible();
      expect(
        document.querySelector('[data-run-add-outcome] [data-server-text="message"]')?.textContent,
      ).toBe(String(own(answer.body, 'error')));
      expect(form().getByLabelText('Run identifier')).toHaveValue('fenced_one');
      expect(document.querySelector('[data-run-add-outcome]')?.querySelectorAll('p')).toHaveLength(
        2,
      );
      expect(textOutsideCatalogue(page.view.container)).toEqual([]);
      expect(page.fake.writes()).toHaveLength(1);
    },
  );

  it.each([
    ['added_a_coin_run_with_every_choice_and_both_optional_policies', 'Tier', 'tier'],
    ['added_a_shard_run', 'Run count', 'count'],
    ['added_a_tournament_run', 'Tournament gem entry cap', 'gem_entry_max'],
  ])(
    'RL4 explicitly clearing a numeric choice in %s keeps its blank with a reason and sends nothing',
    async (name, label, field) => {
      const page = await open();
      await fill(page, sample(name));
      await page.user.clear(form().getByLabelText(label));
      expect(form().getByLabelText(label)).toHaveValue('');
      expect(form().getByRole('button', { name: 'Save new run' })).toHaveAttribute(
        'aria-disabled',
        'true',
      );
      expect(form().getByText(/^Enter a label and whole-number choices/u)).toBeVisible();
      await save(page);
      expect(page.fake.writes()).toEqual([]);
      expect(selectRunsAdd(page.data.store.getSnapshot()).draft[field]).toBe('');
    },
  );

  it('RL4 leaving Runs while the config preflight is held sends nothing and keeps the original draft', async () => {
    const page = await open();
    await fill(page, fenced('added_with_the_matching_revision_and_context'));
    const scope = selectRunsAdd(page.data.store.getSnapshot()).scope;
    page.fake.route('GET', '/api/config', () => 'hold');
    await page.user.click(form().getByRole('button', { name: 'Save new run' }));
    await page.advance(0);
    await act(async () => {
      window.location.hash = '';
      await Promise.resolve();
    });
    await page.advance(100);
    expect(page.data.store.getSnapshot().runsOpen).toBe(false);
    await act(async () => {
      page.fake.next('/api/config').respond(200, CONFIG);
      await Promise.resolve();
    });
    await page.advance(100);
    expect(page.fake.writes()).toEqual([]);
    expect(page.data.store.getSnapshot().runDrafts[scope]?.['run_id']).toBe('fenced_one');
  });
  it.each([
    'added_a_coin_run_with_every_choice_and_both_optional_policies',
    'added_a_tournament_run',
    'added_a_shard_run',
    'added_a_dissonance_run',
  ])('RL4 one request with recorded choices: %s', async (name) => {
    const entry = sample(name);
    let source = SOURCE;
    const page = await open(
      {
        before: (fake) => {
          fake.route('POST', ADD, () => entry);
        },
      },
      () => source,
    );
    await fill(page, entry);
    await save(page);
    expect(page.fake.writes()).toHaveLength(1);
    // The recipe family predates the guards. Compare its exact non-guard members and verify both added guards separately.
    const { expected_revision, expected_context, ...body } = JSON.parse(
      page.fake.writes()[0]?.body ?? '{}',
    ) as Record<string, unknown>;
    expect(body).toEqual(entry.request.body);
    expect(expected_revision).toBe(own(SOURCE.body, 'revision'));
    expect(expected_context).toEqual(
      own(fenced('added_with_the_matching_revision_and_context').request.body, 'expected_context'),
    );
    expect(page.fake.writes().every((request) => request.path === ADD)).toBe(true);
    // The old recipe response lacks the guarded route's revision: it stays Unknown
    // until this recorded source holds the new run and all copied policies.
    expect(form().getByText(/^Unknown: the add/u)).toBeVisible();
    source = sample('read_after_the_adds_holds_every_new_run_and_its_copied_policies');
    await page.user.click(form().getByRole('button', { name: 'Check profile' }));
    await page.advance(100);
    expect(form().getByText('New run saved.')).toBeVisible();
    expect(page.data.store.getSnapshot().runsRun).toBe(own(entry.request.body, 'run_id'));
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL4 a guarded add matches the full recorded request and selects the new saved run', async () => {
    const entry = fenced('added_with_the_matching_revision_and_context');
    const page = await open({
      before: (fake) => {
        fake.route('POST', ADD, () => entry);
      },
    });
    await fill(page, entry);
    await save(page);
    expect(JSON.parse(page.fake.writes()[0]?.body ?? '{}')).toEqual(entry.request.body);
    expect(form().getByText('New run saved.')).toBeVisible();
    expect(page.data.store.getSnapshot().runsRun).toBe('fenced_one');
    expect(form().getByLabelText('Run identifier')).toHaveValue('');
  });

  it.each([
    'run_id_taken',
    'run_id_with_a_capital',
    'run_id_with_a_space',
    'run_id_starting_with_a_digit',
    'run_id_longer_than_64_characters',
  ])('RL4 local refusal sends nothing and keeps typed id: %s', async (name) => {
    const page = await open();
    const raw = own(sample(name).request.body, 'run_id');
    if (typeof raw !== 'string') throw new Error('missing fixture id');
    await page.user.type(form().getByLabelText('Run identifier'), raw);
    await page.user.type(form().getByLabelText('New run label'), 'Fresh run');
    expect(form().getByRole('button', { name: 'Save new run' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await save(page);
    expect(page.fake.writes()).toEqual([]);
    expect(form().getByLabelText('Run identifier')).toHaveValue(raw);
  });

  it.each(
    dashboardPagesCaseNames('run_templates_add').filter(
      (name) => sample(name).status === 400 && name !== 'written_then_the_answer_failed',
    ),
  )('RL4 F6-11-P2-02 uncoded server 400 stays Unknown with its exact text: %s', async (name) => {
    // Fault injection replays each recorded server refusal to a well-formed fenced request, including races and server-side validation.
    const entry = sample(name);
    const page = await open({
      before: (fake) => {
        fake.route('POST', ADD, () => entry);
      },
    });
    await fill(page, fenced('added_with_the_matching_revision_and_context'));
    await save(page);
    expect(form().getByText(/^Unknown: the add/u)).toBeVisible();
    expect(form().getByRole('button', { name: 'Save new run' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(
      document.querySelector('[data-run-add-outcome] [data-server-text="message"]')?.textContent,
    ).toBe(own(entry.body, 'error') ?? own(entry.body, 'text'));
    expect(form().getByLabelText('Run identifier')).toHaveValue('fenced_one');
    expect(page.fake.writes()).toHaveLength(1);
  });

  it.each(['starter_profile_refused_with_the_guards', 'scan_draft_refused_with_the_guards'])(
    'RL4 %s keeps the form and links to Profiles',
    async (name) => {
      // Recorded refusal replay: the backend is authoritative for starter/draft status, even if the page's source was previously personal.
      const entry = fenced(name);
      const page = await open({
        before: (fake) => {
          fake.route('POST', ADD, () => entry);
        },
      });
      await fill(page, fenced('added_with_the_matching_revision_and_context'));
      await save(page);
      expect(form().getByText('Run was not added. Your draft is kept.')).toBeVisible();
      expect(form().getByRole('link', { name: 'Open Profiles' })).toHaveAttribute(
        'href',
        '#profiles',
      );
      expect(form().getByLabelText('Run identifier')).toHaveValue('fenced_one');
    },
  );

  it.each([
    'stale_revision_409_nothing_written',
    'composed_for_alpha_is_refused_after_the_switch',
    'expected_context_not_an_object',
  ])('RL4 guarded refusal %s retains draft and shows server reason', async (name) => {
    // Controlled response ordering: the server refuses after the client's click-time reads.
    const entry = fenced(name);
    const page = await open({
      before: (fake) => {
        fake.route('POST', ADD, () => entry);
      },
    });
    await fill(page, fenced('added_with_the_matching_revision_and_context'));
    await save(page);
    expect(form().getByText('Run was not added. Your draft is kept.')).toBeVisible();
    expect(form().getByLabelText('Run identifier')).toHaveValue('fenced_one');
    expect(
      document.querySelector('[data-run-add-outcome] [data-server-text="message"]')?.textContent,
    ).toBe(own(entry.body, 'error'));
    if (name.includes('alpha'))
      expect(
        document.querySelector('[data-run-add-outcome] [data-server-text="detail"]')?.textContent,
      ).toContain('beta');
    expect(page.fake.writes()).toHaveLength(1);
  });

  it.each(['written_then_the_answer_failed', 'run_id_taken', 'ordinary_filesystem_error'])(
    'RL4 F6-11-P2-02 %s locks Unknown until a positive profile read without resending',
    async (name) => {
      let source = SOURCE;
      const entry = sample(
        name === 'ordinary_filesystem_error' ? 'written_then_the_answer_failed' : name,
      );
      // Replay a recorded failure after a valid lost_run request to test the client fence.
      // For the filesystem variation, change only the post-write error text: backup cleanup
      // can raise an ordinary OSError through the same catch after the profile was saved.
      const answer =
        name === 'ordinary_filesystem_error'
          ? {
              ...entry,
              body: {
                ...Object.fromEntries(entries(entry.body)),
                error: '[Errno 13] Permission denied while removing a profile backup',
              },
            }
          : entry;
      const page = await open(
        {
          before: (fake) => {
            fake.route('POST', ADD, () => answer);
          },
        },
        () => source,
      );
      await fill(page, fenced('added_and_the_answer_is_lost'));
      await save(page);
      expect(form().getByText(/^Unknown: the add/u)).toBeVisible();
      expect(
        document.querySelector('[data-run-add-outcome] [data-server-text="message"]')?.textContent,
      ).toBe(own(answer.body, 'error'));
      expect(form().getByRole('button', { name: 'Save new run' })).toHaveAttribute(
        'aria-disabled',
        'true',
      );
      expect(form().getByText(/^Wait for this add to finish/u)).toBeVisible();
      await save(page);
      await page.user.click(form().getByRole('button', { name: 'Check profile' }));
      await page.advance(100);
      expect(form().getByText(/^Unknown: the add/u)).toBeVisible();
      expect(form().getByLabelText('Run identifier')).toHaveValue('lost_run');
      const record = page.data.machine.snapshot().findLast((item) => item.kind === 'run_add');
      expect(record?.phase).toBe('unconfirmed');
      expect(page.data.machine.resend(record?.id ?? '')).toBe('not_resendable');
      await page.advance(20_000);
      expect(page.fake.writes()).toHaveLength(1);
      source = fenced('read_settles_the_lost_answer_the_run_is_there_once');
      await page.user.click(form().getByRole('button', { name: 'Check profile' }));
      await page.advance(100);
      expect(form().getByText('New run saved.')).toBeVisible();
      expect(page.data.machine.snapshot().find((item) => item.id === record?.id)?.phase).toBe(
        'confirmed',
      );
      expect(page.fake.writes()).toHaveLength(1);
    },
  );

  it.each([
    ['added_and_the_answer_is_lost', 'read_settles_the_lost_answer_the_run_is_there_once'],
    ['written_then_the_answer_failed', 'read_after_an_error_that_followed_the_write_holds_the_run'],
  ])(
    'RL4 %s stays Unknown on unchanged reads, never resends, then settles by presence',
    async (name, after) => {
      let source = SOURCE;
      const entry = fenced(name);
      const page = await open(
        {
          before: (fake) => {
            fake.route('POST', ADD, () =>
              name === 'added_and_the_answer_is_lost' ? 'hold' : entry,
            );
          },
        },
        () => source,
      );
      await fill(page, entry);
      await save(page);
      if (name === 'added_and_the_answer_is_lost') {
        await act(async () => {
          page.fake.next(ADD).lose();
          await Promise.resolve();
        });
        await page.advance(100);
      }
      expect(form().getByText(/^Unknown: the add/u)).toBeVisible();
      await page.user.click(form().getByRole('button', { name: 'Check profile' }));
      await page.advance(100);
      expect(form().getByText(/^Unknown: the add/u)).toBeVisible();
      const record = page.data.machine.snapshot().at(-1);
      expect(page.data.machine.resend(record?.id ?? '')).toBe('not_resendable');
      await page.advance(20_000);
      expect(page.fake.writes()).toHaveLength(1);
      source = fenced(after);
      await page.user.click(form().getByRole('button', { name: 'Check profile' }));
      await page.advance(100);
      expect(form().getByText('New run saved.')).toBeVisible();
      expect(page.fake.writes()).toHaveLength(1);
    },
  );

  it('RL4 click-time context change sends nothing and keeps draft in its original scope', async () => {
    let config = CONFIG;
    const page = await open({ config: () => config });
    await fill(page, fenced('added_with_the_matching_revision_and_context'));
    config = fenced('read_config_after_another_client_switched_the_account').body;
    await save(page);
    expect(page.fake.writes()).toEqual([]);
    expect(
      Object.values(page.data.store.getSnapshot().runDrafts).some(
        (draft) => draft['run_id'] === 'fenced_one',
      ),
    ).toBe(true);
  });

  it('RL4 the raw scoped draft survives reads and reload without sending', async () => {
    const page = await open();
    await fill(page, fenced('added_with_the_matching_revision_and_context'));
    await page.user.clear(form().getByLabelText('Tier'));
    await page.user.type(form().getByLabelText('Tier'), 'two');
    await page.advance(10_000);
    const storage = page.storage;
    page.view.unmount();
    page.data.stop();
    const reloaded = await open({ storage });
    expect(form().getByLabelText('Tier')).toHaveValue('two');
    expect(form().getByLabelText('Run identifier')).toHaveValue('fenced_one');
    expect(reloaded.fake.writes()).toEqual([]);
  });

  it('RL4 uses the receipt revision for a chained add while the follow-up read is pending', async () => {
    const first = fenced('added_with_the_matching_revision_and_context');
    const second = fenced('added_again_with_the_revision_the_answer_returned');
    const page = await open({
      before: (fake) => {
        fake.route('POST', ADD, (request) =>
          own(JSON.parse(request.body ?? '{}'), 'run_id') === 'fenced_one' ? first : second,
        );
      },
    });
    await fill(page, first);
    page.fake.route('GET', PROFILE, () => 'hold');
    // Let the click-time profile read finish, then hold the automatic read after the receipt.
    const saving = page.user.click(form().getByRole('button', { name: 'Save new run' }));
    await page.advance(0);
    await saving;
    await act(async () => {
      page.fake.next(PROFILE).respond(SOURCE.status, SOURCE.body);
      await Promise.resolve();
    });
    await page.advance(100);
    const vm = selectRunsAdd(page.data.store.getSnapshot());
    expect(vm.baseRevision).toBe(own(first.body, 'revision'));
    await fill(page, second);
    expect(selectRunsAdd(page.data.store.getSnapshot()).command?.revision).toBe(
      own(first.body, 'revision'),
    );
    // Modified recorded source: no read between these two adds was recorded. The first
    // receipt's exact profile and revision are exposed through the existing source-read shape.
    const between = {
      ...(SOURCE.body as Record<string, unknown>),
      profile: own(first.body, 'profile'),
      revision: own(first.body, 'revision'),
    };
    const savingSecond = page.user.click(form().getByRole('button', { name: 'Save new run' }));
    await page.advance(0);
    await savingSecond;
    await act(async () => {
      page.fake.next(PROFILE).respond(200, between);
      await Promise.resolve();
    });
    await page.advance(0);
    // A pre-click read may have been pending; the command requires its own later read.
    if (page.fake.held.some((held) => held.request.path === PROFILE && !held.settled())) {
      await act(async () => {
        page.fake.next(PROFILE).respond(200, between);
        await Promise.resolve();
      });
    }
    await page.advance(100);
    expect(
      page.fake.writes().map((request) => JSON.parse(request.body ?? '{}') as unknown),
    ).toEqual([first.request.body, second.request.body]);
    expect(page.data.store.getSnapshot().runsRun).toBe('fenced_two');
  });

  it('RL4 continuing from an unchanged read leaves the original add Unknown when a new identical add is refused stale', async () => {
    let source = SOURCE;
    let attempts = 0;
    const lost = fenced('added_and_the_answer_is_lost');
    const page = await open(
      {
        before: (fake) => {
          fake.route('POST', ADD, () => {
            attempts += 1;
            return attempts === 1
              ? 'hold'
              : fenced('the_identical_resend_is_refused_stale_not_applied_twice');
          });
        },
      },
      () => source,
    );
    await fill(page, lost);
    await save(page);
    await act(async () => {
      page.fake.next(ADD).lose();
      await Promise.resolve();
    });
    await page.advance(100);
    await page.user.click(form().getByRole('button', { name: 'Check profile' }));
    await page.advance(100);
    await page.user.click(form().getByRole('button', { name: 'Continue from the current state' }));
    await save(page);
    expect(page.fake.writes()).toHaveLength(2);
    expect(form().getByText(/^Not added again/u)).toBeVisible();
    expect(document.querySelector('[data-earlier-add="unknown"]')).not.toBeNull();
    const [original, refused] = page.data.machine
      .snapshot()
      .filter((item) => item.kind === 'run_add');
    expect(original?.phase).toBe('unconfirmed');
    expect(refused?.phase).toBe('refused');
    source = fenced('read_settles_the_lost_answer_the_run_is_there_once');
    await page.user.click(form().getByRole('button', { name: 'Check profile' }));
    await page.advance(100);
    expect(page.data.machine.snapshot().find((item) => item.id === original?.id)?.phase).toBe(
      'confirmed',
    );
    expect(page.fake.writes()).toHaveLength(2);
  });

  it('RL4 a newly taken id in the click-time profile read sends nothing', async () => {
    let source = SOURCE;
    const page = await open({}, () => source);
    await fill(page, fenced('added_and_the_answer_is_lost'));
    source = fenced('read_settles_the_lost_answer_the_run_is_there_once');
    await save(page);
    expect(page.fake.writes()).toEqual([]);
    expect(form().getByText(/^That run identifier is already taken/u)).toBeVisible();
    expect(form().getByLabelText('Run identifier')).toHaveValue('lost_run');
  });

  it('RL4 all recipe forms use catalogue text and pass axe', async () => {
    const page = await open();
    await page.user.selectOptions(form().getByLabelText('Recipe'), 'dissonance');
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });
});

it.each(['missing', 'blank', 'unchanged', 'changed'])(
  'RL4 F6-11-P3-02 %s revision after a lost add keeps or clears the submitted draft',
  async (revisionCase) => {
    let source = SOURCE;
    const entry = fenced('added_and_the_answer_is_lost');
    const page = await open(
      {
        before: (fake) => {
          fake.route('POST', ADD, () => 'hold');
        },
      },
      () => source,
    );
    await fill(page, entry);
    await save(page);
    await act(async () => {
      page.fake.next(ADD).lose();
      await Promise.resolve();
    });
    await page.advance(100);
    expect(form().getByText(/^Unknown: the add/u)).toBeVisible();
    const after = fenced('read_settles_the_lost_answer_the_run_is_there_once');
    const body = Object.fromEntries(entries(after.body));
    if (revisionCase === 'missing') delete body['revision'];
    if (revisionCase === 'blank') body['revision'] = '';
    if (revisionCase === 'unchanged') body['revision'] = own(SOURCE.body, 'revision');
    source = { ...after, body };
    await page.user.click(form().getByRole('button', { name: 'Check profile' }));
    await page.advance(100);
    const record = page.data.machine.snapshot().findLast((item) => item.kind === 'run_add');
    const drafts = Object.values(page.data.store.getSnapshot().runDrafts);
    if (revisionCase === 'changed') {
      expect(form().getByText('New run saved.')).toBeVisible();
      expect(record?.phase).toBe('confirmed');
      expect(drafts.some((draft) => draft['run_id'] === 'lost_run')).toBe(false);
    } else {
      expect(form().getByText(/^Unknown: the add/u)).toBeVisible();
      expect(record?.phase).toBe('unconfirmed');
      expect(form().getByLabelText('Run identifier')).toHaveValue('lost_run');
      expect(drafts.some((draft) => draft['run_id'] === 'lost_run')).toBe(true);
    }
    expect(page.fake.writes()).toHaveLength(1);
  },
);
