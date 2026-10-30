import { screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { e0Config, postBodies } from '../../../support/dataLayer.ts';
import { dashboardBody } from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { selectRunsPage } from '../../../../src/store/selectors/runs.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';

/**
 * The Runs page (brief backend/logs/reviews/ui-pages/runs-brief.md) on the
 * whole wired app with a fake transport. Saves are answered by the fake
 * transport in each test (the contract fixture has no saved-run save answers
 * yet; the shapes follow dashboard.py api_run_controls), never by the page.
 */
const SAVED = dashboardBody('run_controls.ad_gems_on_floating_off') as Record<string, unknown>;
const REVISION = String(SAVED['profile_revision']);

function savedWith(changes: {
  readonly revision?: string;
  readonly adInterval?: number;
}): Record<string, unknown> {
  const settings = structuredClone(SAVED['settings']) as {
    routines: { ad_gems: { interval_sec: number } };
  };
  if (changes.adInterval !== undefined) settings.routines.ad_gems.interval_sec = changes.adInterval;
  return { ...SAVED, settings, profile_revision: changes.revision ?? REVISION };
}

async function openRuns(hash: string, options: PageOptions = {}) {
  window.location.hash = hash;
  const page = await renderPage({
    status: () => statusBody(sample('status.off')),
    config: () => e0Config(true),
    ...options,
  });
  await page.advance(1_000);
  return page;
}

function savedRegion(): HTMLElement {
  const found = document.querySelector<HTMLElement>('[data-region="runsSaved"]');
  if (found === null) throw new Error('saved settings region missing');
  return found;
}

function liveRegion(): HTMLElement {
  const found = document.querySelector<HTMLElement>('[data-region="runsLive"]');
  if (found === null) throw new Error('live region missing');
  return found;
}

function savedInput(name: RegExp): HTMLInputElement {
  const input = within(savedRegion()).getByRole('textbox', { name });
  if (!(input instanceof HTMLInputElement)) throw new Error('not an input');
  return input;
}

function saveButton(region: HTMLElement, name: string): HTMLElement {
  return within(region).getByRole('button', { name });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('Runs page: saved settings', () => {
  it('RUN-01 the picker lists the defined runs by label, never Combo as a run with controls; a link opens exactly its run', async () => {
    await openRuns('#runs/saved/bp_farm', {
      before: (fake) => {
        fake.route('GET', '/api/runs', () => ({
          status: 200,
          body: dashboardBody('runs.combo_entry'),
        }));
      },
    });
    const picker = within(savedRegion()).getByRole('combobox', { name: 'Run' });
    const options = within(picker)
      .getAllByRole('option')
      .map((option) => option.getAttribute('value'));
    expect(options).not.toContain('combo');
    expect(picker).toHaveValue('bp_farm');
    expect(
      within(savedRegion()).getByText(
        'Day plans choose a run for each block; edit them on Schedule',
      ),
    ).toBeVisible();
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });

  it('RUN-01 S-081 a link to a run the profile does not list says so and never shows another run', async () => {
    await openRuns('#runs/saved/bp_missing');
    const picker = within(savedRegion()).getByRole('combobox', { name: 'Run' });
    expect(picker).toHaveValue('bp_missing');
    expect(savedRegion().querySelector('[data-run-unlisted="true"]')).not.toBeNull();
  });

  it('RUN-02 every control the read names is one labelled field with unit and range; switch-only routines share one row', async () => {
    await openRuns('#runs');
    const region = savedRegion();
    const controls = (SAVED['capabilities'] as { controls: string[] }).controls.filter((path) =>
      path.startsWith('routines.'),
    );
    const numbers = controls.filter((path) => !path.endsWith('.enabled'));
    const switches = controls.filter((path) => path.endsWith('.enabled'));
    // RL4 adds a separate form below the picker. This assertion concerns only the saved routine editor.
    const editor = region.querySelector<HTMLElement>('.tp-run-editor');
    if (editor === null) throw new Error('saved routine editor missing');
    expect(within(editor).getAllByRole('textbox')).toHaveLength(numbers.length);
    expect(within(editor).getAllByRole('checkbox')).toHaveLength(switches.length);
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('720');
    expect(within(region).getAllByText('1 to 3600 s')[0]).toBeVisible();
    const row = region.querySelector('.tp-run-row--switches');
    expect(row?.querySelectorAll('input[type="checkbox"]')).toHaveLength(4);
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });

  it('RUN-03 a typed value survives five polls, a forced re-read and a run switch, with focus kept; Discard restores the saved value', async () => {
    const page = await openRuns('#runs');
    const input = savedInput(/^Ad gems Every$/u);
    await page.user.clear(input);
    await page.user.type(input, '30');
    for (let poll = 0; poll < 5; poll += 1) await page.advance(2_600);
    page.data.commands.checkRun();
    await page.advance(1_000);
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('30');
    expect(savedInput(/^Ad gems Every$/u)).toHaveFocus();
    const picker = within(savedRegion()).getByRole('combobox', { name: 'Run' });
    const other = within(picker)
      .getAllByRole('option')
      .map((option) => option.getAttribute('value'))
      .find((value) => value !== 'bp_farm');
    if (other !== undefined && other !== null) {
      await page.user.selectOptions(picker, other);
      await page.advance(500);
      await page.user.selectOptions(picker, 'bp_farm');
      await page.advance(500);
      expect(savedInput(/^Ad gems Every$/u)).toHaveValue('30');
    }
    await page.user.click(within(savedRegion()).getByRole('button', { name: 'Discard' }));
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('720');
    expect(page.fake.writes()).toEqual([]);
  });

  it.each([
    ['', 'Enter a number'],
    ['abc', 'Use digits only, like 30 or 2.5'],
    ['1e3', 'Use digits only, like 30 or 2.5'],
    ['0.5', 'Below the allowed range'],
    ['4000', 'Above the allowed range'],
  ])(
    'RUN-04 %j stays as typed with its reason; Save is disabled and nothing is sent',
    async (typed, reason) => {
      const page = await openRuns('#runs');
      const input = savedInput(/^Ad gems Every$/u);
      await page.user.clear(input);
      if (typed !== '') await page.user.type(input, typed);
      expect(savedInput(/^Ad gems Every$/u)).toHaveValue(typed);
      expect(input).toHaveAttribute('aria-invalid', 'true');
      expect(input).toHaveAccessibleDescription(expect.stringContaining(reason));
      const save = saveButton(savedRegion(), 'Save run settings');
      expect(save).toHaveAttribute('aria-disabled', 'true');
      expect(save).toHaveAccessibleDescription('Fix the values marked above');
      await page.user.click(save);
      await page.advance(5_000);
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('RUN-05 SR-4 one click sends exactly one POST with only the changed path, the read revision and context; a double click and 60 s of polls send nothing more; the draft clears only on 200', async () => {
    const page = await openRuns('#runs');
    await page.user.clear(savedInput(/^Ad gems Every$/u));
    await page.user.type(savedInput(/^Ad gems Every$/u), '30');
    const save = saveButton(savedRegion(), 'Save run settings');
    await page.user.dblClick(save);
    await page.advance(0);
    const bodies = postBodies(page.fake, '/api/run-controls');
    expect(bodies).toEqual([
      {
        blueprint: 'farm',
        context: SAVED['context'],
        expected_revision: REVISION,
        changes: { 'routines.ad_gems.interval_sec': 30 },
      },
    ]);
    await page.advance(60_000);
    expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('30');
    // The answer, and the re-read that follows it, carry the new revision and value.
    const next = savedWith({ revision: 'f'.repeat(64), adInterval: 30 });
    page.fake.route('GET', /^\/api\/run-controls/, () => ({ status: 200, body: next }));
    page.fake.next('/api/run-controls', 'POST').respond(200, next);
    await page.advance(1_000);
    expect(within(savedRegion()).getByText('Saved')).toBeVisible();
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('30');
    expect(saveButton(savedRegion(), 'Save run settings')).toHaveAccessibleDescription(
      'No changes to save',
    );
    expect(within(savedRegion()).queryByRole('button', { name: 'Discard' })).toBeNull();
  });

  it.each([
    [409, 'Saved run changed; refresh before editing again'],
    [400, 'Invalid value: routines.ad_gems.interval_sec'],
  ])(
    'RUN-06 a %i keeps the draft, says why and Check saved values reads the run (a GET)',
    async (status, error) => {
      const page = await openRuns('#runs');
      await page.user.clear(savedInput(/^Ad gems Every$/u));
      await page.user.type(savedInput(/^Ad gems Every$/u), '30');
      await page.user.click(saveButton(savedRegion(), 'Save run settings'));
      await page.advance(0);
      page.fake.next('/api/run-controls', 'POST').respond(status, { ok: false, error });
      await page.advance(1_000);
      expect(within(savedRegion()).getByText('Not saved')).toBeVisible();
      expect(savedInput(/^Ad gems Every$/u)).toHaveValue('30');
      expect(savedRegion()).toHaveTextContent(error);
      const reads = page.fake.requests.filter((r) =>
        r.path.startsWith('/api/run-controls?'),
      ).length;
      expect(reads).toBeGreaterThan(0);
      expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
      await page.advance(60_000);
      expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
    },
  );

  it.each([
    ['a new revision with the value', { revision: 'e'.repeat(64), adInterval: 30 }, true],
    ['the same revision', { adInterval: 720 }, false],
    ['a new revision without it', { revision: 'd'.repeat(64), adInterval: 45 }, false],
  ] as const)(
    'RUN-07 SR-8 F6C-R1-02 a lost answer reads Unknown with no resend; 60 s of polls send nothing; a later read with %s settles it only by showing the value',
    async (_label, reread, holds) => {
      const page = await openRuns('#runs');
      await page.user.clear(savedInput(/^Ad gems Every$/u));
      await page.user.type(savedInput(/^Ad gems Every$/u), '30');
      await page.user.click(saveButton(savedRegion(), 'Save run settings'));
      await page.advance(0);
      page.fake.next('/api/run-controls', 'POST').lose();
      await page.advance(1_000);
      const unknown = 'No reply - it may or may not have saved';
      expect(within(savedRegion()).getByText(unknown)).toBeVisible();
      expect(
        within(savedRegion()).queryByRole('button', { name: 'Send same request again' }),
      ).toBeNull();
      // SR-8: the unknown line stays (no Dismiss); the editor's Check saved values is the way out.
      expect(within(savedRegion()).queryByRole('button', { name: 'Dismiss' })).toBeNull();
      expect(saveButton(savedRegion(), 'Save run settings')).toHaveAttribute(
        'aria-disabled',
        'true',
      );
      await page.advance(60_000);
      expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
      page.fake.route('GET', /^\/api\/run-controls/, () => ({
        status: 200,
        body: savedWith(reread),
      }));
      await page.user.click(
        within(savedRegion()).getByRole('button', { name: 'Check saved values' }),
      );
      await page.advance(1_000);
      const accept = () =>
        within(savedRegion()).queryByRole('button', {
          name: 'Continue from the saved values as read',
        });
      if (holds) {
        expect(
          within(savedRegion()).getByText('The saved settings now have your changes'),
        ).toBeVisible();
        expect(accept()).toBeNull();
      } else {
        // An unchanged read, or one without the value, cannot prove the save failed: Unknown,
        // Save still waits, and only the person's explicit continue opens it.
        expect(within(savedRegion()).getByText(unknown)).toBeVisible();
        expect(saveButton(savedRegion(), 'Save run settings')).toHaveAttribute(
          'aria-disabled',
          'true',
        );
        await page.advance(60_000);
        expect(within(savedRegion()).getByText(unknown)).toBeVisible();
        const button = accept();
        if (button === null) throw new Error('continue not offered');
        await page.user.click(button);
        await page.advance(0);
        expect(within(savedRegion()).getByText(unknown)).toBeVisible();
        expect(savedRegion().querySelector('[data-accepted]')).toHaveTextContent(
          'You continued from the saved values as read',
        );
        expect(accept()).toBeNull();
        expect(within(savedRegion()).getByRole('button', { name: 'Dismiss' })).toBeVisible();
      }
      expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
    },
  );

  it('RUN-02 has no axe violations and only catalogue or server text', async () => {
    await openRuns('#runs/saved/bp_farm');
    vi.useRealTimers();
    expect(await axe(document.body)).toHaveNoViolations();
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });
});

describe('Runs page: this battle', () => {
  const battleCase = (name: string) => () => ({
    status: 200,
    body: dashboardBody(`battles_active.${name}`),
  });
  function withBattle(name: string): PageOptions {
    return {
      before: (fake) => {
        fake.route('GET', '/api/battles/active', battleCase(name));
      },
    };
  }

  it('RUN-08 SR-1 live rows show the battle values with their marks; one Save sends one live-controls POST with control_revision and a key; the saved run is untouched', async () => {
    const page = await openRuns(
      '#runs/live',
      withBattle('commands_controls_applied_with_overrides_action_failed'),
    );
    const live = liveRegion();
    expect(within(live).getAllByText('Changed for this battle').length).toBeGreaterThan(0);
    expect(document.activeElement?.id).toBe('runsLive-title');
    const input = within(live).getByRole('textbox', { name: /^Ad gems Every$/u });
    await page.user.clear(input);
    await page.user.type(input, '600');
    await page.user.click(saveButton(live, 'Save changes to this battle'));
    await page.advance(0);
    const battle = (
      dashboardBody('battles_active.commands_controls_applied_with_overrides_action_failed') as {
        battle: { id: string; generation: string; operation_id: string; control_revision: number };
      }
    ).battle;
    const path = `/api/operations/${battle.operation_id}/live-controls`;
    const bodies = postBodies(page.fake, path);
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toEqual({
      battle_id: battle.id,
      generation: battle.generation,
      expected_revision: battle.control_revision,
      idempotency_key: expect.any(String) as unknown,
      changes: { 'routines.ad_gems.interval_sec': 600 },
    });
    expect(postBodies(page.fake, '/api/run-controls')).toEqual([]);
  });

  it.each([
    [
      'null_no_driver',
      'No tracked battle - live settings appear here while automation watches a battle',
    ],
    ['prepared', 'The battle has not started yet; live settings open when it is active'],
  ])('RUN-09 %s: no live editor, the reason in its place', async (name, line) => {
    await openRuns('#runs/live', withBattle(name));
    expect(within(liveRegion()).getByText(line)).toBeVisible();
    expect(
      within(liveRegion()).queryByRole('button', { name: 'Save changes to this battle' }),
    ).toBeNull();
  });

  it.each([
    ['stale_report', 'Waiting for a fresh battle report'],
    [
      'commands_controls_pending_some_paths_applied_action_completed',
      'Waiting for the last change to apply',
    ],
  ])(
    'RUN-09 %s: live Save is disabled with its reason even with a change typed',
    async (name, reason) => {
      const page = await openRuns('#runs/live', withBattle(name));
      const live = liveRegion();
      const input = within(live).getAllByRole('textbox')[0];
      if (input === undefined) throw new Error('no live field');
      await page.user.clear(input);
      await page.user.type(input, '61');
      const save = saveButton(live, 'Save changes to this battle');
      expect(save).toHaveAttribute('aria-disabled', 'true');
      expect(save).toHaveAccessibleDescription(reason);
      await page.user.click(save);
      await page.advance(0);
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('RUN-10 SR-4 a lost live answer offers Send same request again with the identical body and key; nothing is resent on its own', async () => {
    const page = await openRuns(
      '#runs/live',
      withBattle('commands_controls_applied_with_overrides_action_failed'),
    );
    const live = liveRegion();
    const input = within(live).getByRole('textbox', { name: /^Ad gems Every$/u });
    await page.user.clear(input);
    await page.user.type(input, '600');
    await page.user.click(saveButton(live, 'Save changes to this battle'));
    await page.advance(0);
    const first = page.fake.writes()[0];
    page.fake.next(/live-controls$/u, 'POST').lose();
    await page.advance(1_000);
    expect(within(live).getByText('No reply - it may have been recorded')).toBeVisible();
    await page.advance(60_000);
    expect(page.fake.writes()).toHaveLength(1);
    await page.user.click(within(live).getByRole('button', { name: 'Send same request again' }));
    await page.advance(0);
    const writes = page.fake.writes();
    expect(writes).toHaveLength(2);
    expect(writes[1]?.body).toBe(first?.body);
    expect(writes[1]?.path).toBe(first?.path);
  });

  it('RUN-10 a 409 reads "nothing was recorded" and keeps the draft', async () => {
    const page = await openRuns(
      '#runs/live',
      withBattle('commands_controls_applied_with_overrides_action_failed'),
    );
    const live = liveRegion();
    const input = within(live).getByRole('textbox', { name: /^Ad gems Every$/u });
    await page.user.clear(input);
    await page.user.type(input, '600');
    await page.user.click(saveButton(live, 'Save changes to this battle'));
    await page.advance(0);
    page.fake
      .next(/live-controls$/u, 'POST')
      .respond(409, dashboardBody('battle_actions.changed_expected_revision_refused'));
    await page.advance(1_000);
    expect(within(live).getByText('Not sent - nothing was recorded')).toBeVisible();
    expect(within(live).getByRole('textbox', { name: /^Ad gems Every$/u })).toHaveValue('600');
  });

  it('RUN-11 a live number outside the run saved range is refused here with its reason', async () => {
    const page = await openRuns(
      '#runs/live',
      withBattle('commands_controls_applied_with_overrides_action_failed'),
    );
    const live = liveRegion();
    const input = within(live).getByRole('textbox', { name: /^Ad gems Window$/u });
    await page.user.clear(input);
    await page.user.type(input, '61');
    expect(input).toHaveAccessibleDescription(expect.stringContaining('Above the allowed range'));
    expect(saveButton(live, 'Save changes to this battle')).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('RUN-12 #runs/saved/<run> focuses the saved settings heading; the page title names Runs', async () => {
    await openRuns('#runs/saved/bp_farm');
    expect(document.activeElement?.id).toBe('runsSaved-title');
    expect(document.title.startsWith('Runs')).toBe(true);
    expect(screen.getByRole('heading', { level: 1, name: 'Runs' })).toBeVisible();
  });
});

/** The battle the live tests edit, optionally changed (another generation, a keyed command). */
const BATTLE_CASE = 'commands_controls_applied_with_overrides_action_failed';
function battleBody(change: (battle: Record<string, unknown>) => void = () => undefined): unknown {
  const body = dashboardBody(`battles_active.${BATTLE_CASE}`) as {
    battle: Record<string, unknown>;
  };
  change(body.battle);
  return body;
}
function liveRoute(body: () => unknown): PageOptions {
  return {
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: body() }));
    },
  };
}
/** The battle read with one more controls command carrying `key` (and any field changed). */
function withKeyedCommand(key: string, fields: Record<string, unknown> = {}): unknown {
  return battleBody((battle) => {
    const commands = battle['commands'] as Record<string, unknown>[];
    const controls = commands.find((command) => command['kind'] === 'controls');
    battle['commands'] = [
      ...commands,
      { ...controls, id: 'controls-later', request_keys: [key], ...fields },
    ];
  });
}

type RunsPageHarness = Awaited<ReturnType<typeof openRuns>>;

async function typeLive(page: RunsPageHarness, value: string) {
  const input = within(liveRegion()).getByRole('textbox', { name: /^Ad gems Every$/u });
  await page.user.clear(input);
  await page.user.type(input, value);
}

async function typeSaved(page: RunsPageHarness, value: string) {
  await page.user.clear(savedInput(/^Ad gems Every$/u));
  await page.user.type(savedInput(/^Ad gems Every$/u), value);
}

function liveKey(page: RunsPageHarness): string {
  const [body] = page.fake
    .writes()
    .filter((write) => write.path.endsWith('/live-controls'))
    .map((write) => JSON.parse(write.body ?? '{}') as { idempotency_key?: string });
  if (body?.idempotency_key === undefined) throw new Error('no live change sent');
  return body.idempotency_key;
}

const UNRESOLVED = 'An earlier save has no answer yet; check the values first';

describe('Runs page: review fixes (F6R-02 to F6R-07)', () => {
  it('RUN-07 SR-2 F6R-02 F6C-R1-02 a malformed 200 (settings without the sent value) is Unknown, keeps the draft, and an unchanged later read keeps it Unknown', async () => {
    const page = await openRuns('#runs');
    await typeSaved(page, '30');
    await page.user.click(saveButton(savedRegion(), 'Save run settings'));
    await page.advance(0);
    // Reads of the run wait, so the answer alone decides what the line says.
    page.fake.route('GET', /^\/api\/run-controls/, () => 'hold');
    page.fake
      .next('/api/run-controls', 'POST')
      .respond(200, { ok: true, profile_revision: 'f'.repeat(64), settings: {} });
    await page.advance(0);
    const record = page.data.store.getSnapshot().intents.find((r) => r.kind === 'run_save');
    expect(record?.phase).toBe('unconfirmed');
    expect(
      within(savedRegion()).getByText(
        "The reply didn't confirm the save - it may or may not have saved",
      ),
    ).toBeVisible();
    expect(within(savedRegion()).queryByText('Saved', { exact: true })).toBeNull();
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('30');
    // The re-read reports the revision the save was made against: that cannot prove the save
    // failed (it may still be written), so the line stays unconfirmed and the draft stays.
    page.fake.next(/^\/api\/run-controls\?/u, 'GET').respond(200, SAVED);
    await page.advance(0);
    expect(
      within(savedRegion()).getByText(
        "The reply didn't confirm the save - it may or may not have saved",
      ),
    ).toBeVisible();
    expect(
      within(savedRegion()).getByRole('button', { name: 'Continue from the saved values as read' }),
    ).toBeVisible();
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('30');
    await page.advance(60_000);
    expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
  });

  it('RUN-07 F6R-03 a save outcome stays with its own run: another run shows none and saves freely; its own run shows it again', async () => {
    const page = await openRuns('#runs/saved/bp_farm', {
      before: (fake) => {
        fake.route('GET', '/api/runs', () => ({
          status: 200,
          body: dashboardBody('runs.not_ready_image_row'),
        }));
        fake.route('GET', /^\/api\/run-controls/, (request) => ({
          status: 200,
          body: {
            ...SAVED,
            blueprint: new URL(request.path, 'http://x').searchParams.get('blueprint'),
          },
        }));
      },
    });
    await typeSaved(page, '30');
    await page.user.click(saveButton(savedRegion(), 'Save run settings'));
    await page.advance(0);
    page.fake.next('/api/run-controls', 'POST').lose();
    await page.advance(1_000);
    const unknown = 'No reply - it may or may not have saved';
    expect(within(savedRegion()).getByText(unknown)).toBeVisible();
    const picker = within(savedRegion()).getByRole('combobox', { name: 'Run' });
    const farmSave = page.data.store.getSnapshot().intents.find((r) => r.kind === 'run_save');
    await page.user.selectOptions(picker, 'bp_other');
    await page.advance(1_000);
    expect(savedRegion().querySelector('.tp-run-outcome')).toBeNull();
    await typeSaved(page, '45');
    expect(saveButton(savedRegion(), 'Save run settings')).not.toHaveAttribute('aria-disabled');
    // Back on its own run: its own line, still Unknown (a read without the value proves nothing).
    await page.user.selectOptions(picker, 'bp_farm');
    await page.advance(1_000);
    expect(savedRegion().querySelector('.tp-run-outcome')).toHaveAttribute(
      'data-intent',
      farmSave?.id ?? 'missing',
    );
    expect(within(savedRegion()).getByText(unknown)).toBeVisible();
    expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
  });

  it('RUN-10 F6R-03 F6R-04 a live outcome stays with its battle: a replacement battle shows it only as another battle line, never as its own; its editor is free; the resend sends the original request', async () => {
    let battle: () => unknown = () => battleBody();
    const page = await openRuns(
      '#runs/live',
      liveRoute(() => battle()),
    );
    await typeLive(page, '600');
    await page.user.click(saveButton(liveRegion(), 'Save changes to this battle'));
    await page.advance(0);
    const first = page.fake.writes()[0];
    page.fake.next(/live-controls$/u, 'POST').lose();
    await page.advance(1_000);
    expect(within(liveRegion()).getByText('No reply - it may have been recorded')).toBeVisible();
    battle = () =>
      battleBody((next) => {
        next['id'] = '0000009a-0000-4000-8000-000000000000';
        next['generation'] = '0000009b-0000-4000-8000-000000000000';
      });
    page.data.commands.checkBattle();
    await page.advance(1_000);
    // The old change is one line, named as another battle's; the new battle has no line.
    const lines = liveRegion().querySelectorAll('.tp-run-outcome');
    expect(lines).toHaveLength(1);
    expect(lines[0]?.querySelector('[data-earlier-battle="true"]')).not.toBeNull();
    expect(within(liveRegion()).getByRole('textbox', { name: /^Ad gems Every$/u })).toHaveValue(
      '720',
    );
    await typeLive(page, '660');
    expect(saveButton(liveRegion(), 'Save changes to this battle')).not.toHaveAttribute(
      'aria-disabled',
    );
    battle = () => battleBody();
    page.data.commands.checkBattle();
    await page.advance(1_000);
    expect(liveRegion().querySelector('[data-earlier-battle="true"]')).toBeNull();
    await page.user.click(
      within(liveRegion()).getByRole('button', { name: 'Send same request again' }),
    );
    await page.advance(0);
    const writes = page.fake.writes();
    expect(writes).toHaveLength(2);
    expect(writes[1]?.path).toBe(first?.path);
    expect(writes[1]?.body).toBe(first?.body);
  });

  it('RUN-09 SR-6 F6R-04 an applied revision ahead of the control revision keeps live Save disabled with its reason', async () => {
    const page = await openRuns(
      '#runs/live',
      liveRoute(() =>
        battleBody((battle) => {
          battle['applied_revision'] = Number(battle['control_revision']) + 1;
        }),
      ),
    );
    await typeLive(page, '600');
    const save = saveButton(liveRegion(), 'Save changes to this battle');
    expect(save).toHaveAttribute('aria-disabled', 'true');
    expect(save).toHaveAccessibleDescription(
      'The version of these settings is not known; check them again',
    );
    await page.user.click(save);
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
  });

  it('RUN-10 SR-8 F6R-06 a later battle read listing the key settles a lost live change; a wrong key or another operation does not', async () => {
    let battle: () => unknown = () => battleBody();
    const page = await openRuns(
      '#runs/live',
      liveRoute(() => battle()),
    );
    await typeLive(page, '600');
    await page.user.click(saveButton(liveRegion(), 'Save changes to this battle'));
    await page.advance(0);
    page.fake.next(/live-controls$/u, 'POST').lose();
    await page.advance(1_000);
    const key = liveKey(page);
    for (const wrong of [
      withKeyedCommand('key-9999'),
      withKeyedCommand(key, { operation_id: '000000ff-0000-4000-8000-000000000000' }),
    ]) {
      battle = () => wrong;
      await page.user.click(
        within(liveRegion()).getByRole('button', { name: 'Check this battle' }),
      );
      await page.advance(1_000);
      expect(within(liveRegion()).getByText('No reply - it may have been recorded')).toBeVisible();
      expect(
        within(liveRegion()).getByRole('button', { name: 'Send same request again' }),
      ).toBeVisible();
      expect(saveButton(liveRegion(), 'Save changes to this battle')).toHaveAccessibleDescription(
        UNRESOLVED,
      );
    }
    battle = () => withKeyedCommand(key);
    await page.user.click(within(liveRegion()).getByRole('button', { name: 'Check this battle' }));
    await page.advance(1_000);
    // The keyed command copies the recorded controls command, which the battle applied, so the
    // settled line says so (F6-11-IR-01); a listed command still waiting reads "A later read
    // lists this change" (tests/unit/features/runs/runsLeftovers.test.tsx).
    expect(within(liveRegion()).getByText('Applied to this battle')).toBeVisible();
    expect(
      within(liveRegion()).queryByRole('button', { name: 'Send same request again' }),
    ).toBeNull();
    expect(saveButton(liveRegion(), 'Save changes to this battle')).not.toHaveAccessibleDescription(
      UNRESOLVED,
    );
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RUN-10 SR-8 F6R-07 an unknown live change offers Dismiss: it posts nothing, keeps the fence, and Check this battle settles it later', async () => {
    let battle: () => unknown = () => battleBody();
    const page = await openRuns(
      '#runs/live',
      liveRoute(() => battle()),
    );
    await typeLive(page, '600');
    await page.user.click(saveButton(liveRegion(), 'Save changes to this battle'));
    await page.advance(0);
    page.fake.next(/live-controls$/u, 'POST').lose();
    await page.advance(1_000);
    const key = liveKey(page);
    await page.user.click(within(liveRegion()).getByRole('button', { name: 'Dismiss' }));
    await page.advance(0);
    expect(within(liveRegion()).queryByText('No reply - it may have been recorded')).toBeNull();
    expect(document.activeElement?.id).toBe('runsLive-title');
    expect(page.fake.writes()).toHaveLength(1);
    expect(saveButton(liveRegion(), 'Save changes to this battle')).toHaveAccessibleDescription(
      UNRESOLVED,
    );
    await page.advance(60_000);
    expect(page.fake.writes()).toHaveLength(1);
    const reads = () =>
      page.fake.requests.filter((r) => r.method === 'GET' && r.path === '/api/battles/active')
        .length;
    battle = () => withKeyedCommand(key);
    const before = reads();
    await page.user.click(within(liveRegion()).getByRole('button', { name: 'Check this battle' }));
    await page.advance(1_000);
    expect(reads()).toBeGreaterThan(before);
    expect(saveButton(liveRegion(), 'Save changes to this battle')).not.toHaveAccessibleDescription(
      UNRESOLVED,
    );
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('SR-7 F6R-06 a saved and a live save drawn before a context change send nothing after it', async () => {
    let config: unknown = e0Config(true);
    const page = await openRuns('#runs/live', {
      config: () => config,
      ...liveRoute(() => battleBody()),
    });
    await typeSaved(page, '30');
    await typeLive(page, '600');
    const vm = selectRunsPage(page.data.store.getSnapshot());
    const saved = vm.saved.editor?.save.command ?? null;
    const live = vm.live.editor?.save.command ?? null;
    if (saved === null || live === null) throw new Error('both saves should be offered');
    const epoch = page.data.store.getSnapshot().epoch;
    config = e0Config(true, 'other', 'fake:2');
    page.data.poller.read('config');
    await page.advance(0);
    expect(page.data.store.getSnapshot().epoch).toBeGreaterThan(epoch);
    await page.data.commands.saveRun(saved);
    await page.data.commands.saveLive(live);
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    expect(
      page.data.store
        .getSnapshot()
        .notices.filter((notice) => notice.kind === 'command_not_sent')
        .map((notice) => notice.reason),
    ).toEqual(['context_changed']);
  });

  it('SR-2 F6R-06 drafts are kept across a context change and never carried to another battle generation', async () => {
    let config: unknown = e0Config(true);
    let generation = '0000001b-0000-4000-8000-000000000000';
    const page = await openRuns('#runs/live', {
      config: () => config,
      ...liveRoute(() =>
        battleBody((battle) => {
          battle['generation'] = generation;
        }),
      ),
    });
    await typeSaved(page, '30');
    await typeLive(page, '600');
    // Another emulator has its own saved draft (none); back on this one, the typed value is there.
    config = e0Config(true, 'other', 'fake:2');
    page.data.poller.read('config');
    await page.advance(1_000);
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('720');
    config = e0Config(true);
    page.data.poller.read('config');
    await page.advance(1_000);
    expect(savedInput(/^Ad gems Every$/u)).toHaveValue('30');
    // Another generation of the battle starts from its own values, never this draft.
    generation = '0000002b-0000-4000-8000-000000000000';
    page.data.commands.checkBattle();
    await page.advance(1_000);
    const live = () => within(liveRegion()).getByRole('textbox', { name: /^Ad gems Every$/u });
    expect(live()).toHaveValue('720');
    generation = '0000001b-0000-4000-8000-000000000000';
    page.data.commands.checkBattle();
    await page.advance(1_000);
    expect(live()).toHaveValue('600');
    expect(page.fake.writes()).toEqual([]);
  });

  it.each([
    ['runs.ready', 'Ready'],
    ['runs.not_ready_image_row', 'Not ready'],
  ])('RUN-13 %s: the run readiness line and the exact Recognition link', async (runsCase, line) => {
    await openRuns('#runs/saved/bp_farm', {
      before: (fake) => {
        fake.route('GET', '/api/runs', () => ({ status: 200, body: dashboardBody(runsCase) }));
      },
    });
    const region = savedRegion();
    expect(region.querySelector('.tp-runs__readiness')).toHaveTextContent(line);
    expect(
      within(region).getByRole('link', { name: 'Prepare runs on the Recognition page' }),
    ).toHaveAttribute('href', '#recognition');
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });
});
