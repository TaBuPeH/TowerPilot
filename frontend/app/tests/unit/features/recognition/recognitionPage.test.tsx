import { act, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import {
  dashboardCase,
  dashboardPagesCase,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';

/**
 * The Recognition page (backend/logs/reviews/ui-pages/screens-brief.md, R1 to R8, roadmap
 * item 9) on the whole wired app with a fake transport. Every answer is a recorded case of
 * the pages contract (recognition_reads, calibrate_writes) or the Dashboard contract (runs,
 * readiness, battles_active, status). Advanced repairs (R9 to R11) are their own suite.
 */
vi.mock('../../../../src/app/recognitionAdvanced.ts', () => ({
  ConnectedAdvancedRepairs: () => null,
}));

const pages = (path: string) => dashboardPagesCase(path);
const v1 = (path: string) => dashboardCase(path);
const answer = (found: DashboardCase) => ({ status: found.status, body: found.body });

const CONTEXT = {
  instance: 'main',
  account: null,
  serial: 'fake:1',
  rendering: null,
  profile: 'mine',
};

interface Reads {
  readonly calibrate?: () => DashboardCase | 'hold';
  readonly operation?: () => DashboardCase | 'hold';
  readonly battle?: () => DashboardCase;
  readonly runs?: () => DashboardCase;
  readonly readiness?: () => DashboardCase;
  readonly taps?: boolean;
  readonly hash?: string;
}

async function openRecognition(reads: Reads = {}, options: PageOptions = {}) {
  window.location.hash = reads.hash ?? '#recognition';
  const page = await renderPage({
    config: () => ({
      active_instance: 'main',
      active_profile: 'mine',
      instances: { main: { serial: 'fake:1', allow_taps: reads.taps ?? true } },
    }),
    status: () => statusBody(sample('status.off')),
    ...options,
    before: (fake) => {
      fake.route('GET', '/api/calibrate/status', () => {
        const found = (
          reads.calibrate ??
          (() =>
            pages(
              'recognition_reads.calibrate_status_without_an_operation_reads_the_latest_of_the_context',
            ))
        )();
        return found === 'hold' ? 'hold' : answer(found);
      });
      fake.route('GET', /^\/api\/calibrate\/status\?operation_id=/u, () => {
        const found = (reads.operation ?? reads.calibrate ?? (() => 'hold' as const))();
        return found === 'hold' ? 'hold' : answer(found);
      });
      fake.route('GET', '/api/battles/active', () =>
        answer((reads.battle ?? (() => v1('battles_active.null_no_driver')))()),
      );
      fake.route('GET', '/api/runs', () => answer((reads.runs ?? (() => v1('runs.ready')))()));
      fake.route('GET', /^\/api\/readiness\?preset=/u, () =>
        answer((reads.readiness ?? (() => v1('readiness.not_ready_without_message')))()),
      );
      options.before?.(fake);
    },
  });
  await page.advance(1_000);
  return page;
}

type Page = Awaited<ReturnType<typeof openRecognition>>;

function region(name: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(`[data-region="${name}"]`);
  if (found === null) throw new Error(`${name} missing`);
  return found;
}

function dialog(): HTMLElement {
  const found = document.querySelector<HTMLElement>('dialog[open]');
  if (found === null) throw new Error('no open dialog');
  return found;
}

const posts = (page: Page, path: string) => postBodies(page.fake, path);

vi.setConfig({ testTimeout: 30_000 });

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('Recognition page R1 learn screens', () => {
  it('R1 opening the page only reads, and cancelling the consent sends nothing', async () => {
    const page = await openRecognition();
    expect(page.fake.writes()).toEqual([]);
    const learn = region('recognitionLearn');
    expect(
      within(learn).getByRole('checkbox', {
        name: 'Also capture battle controls in one test battle',
      }),
    ).toHaveAccessibleDescription(
      'Starts and ends one setup test battle. Never an existing battle or tournament.',
    );
    expect(
      within(learn)
        .getByText('Starts and ends one setup test battle. Never an existing battle or tournament.')
        .closest('details'),
    ).toBeNull();
    await page.user.click(within(learn).getByRole('button', { name: 'Learn screens' }));
    const open = dialog();
    expect(within(open).getByText(/Starts no battle/u)).toBeInTheDocument();
    expect(within(open).getByText(/Keeps every existing image/u)).toBeInTheDocument();
    await page.user.click(within(open).getByRole('button', { name: 'Cancel' }));
    await page.advance(5_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('R1 R7 a confirmed learn sends one start with the exact context, its consents and a key', async () => {
    const page = await openRecognition({}, { newKey: () => 'key-learn-1' });
    const learn = region('recognitionLearn');
    await page.user.click(
      within(learn).getByRole('checkbox', {
        name: 'Also capture battle controls in one test battle',
      }),
    );
    await page.user.click(within(learn).getByRole('button', { name: 'Learn screens' }));
    expect(within(dialog()).getByText(/Starts one normal test battle/u)).toBeInTheDocument();
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start learning' }));
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/start')).toEqual([
      {
        bootstrap: true,
        flows: true,
        phases: 'c,m',
        fresh: true,
        overwrite: false,
        allow_navigation: true,
        expected_context: CONTEXT,
        idempotency_key: 'key-learn-1',
      },
    ]);
  });
});

describe('Recognition page R1 unknown outcomes and reload', () => {
  it('R1 a lost start stays Unknown, is never resent across 60 s of polls, and a read naming its key settles it', async () => {
    let settled = false;
    const page = await openRecognition(
      {
        calibrate: () =>
          settled
            ? pages(
                'calibrate_writes.read_the_latest_calibration_of_the_context_after_a_lost_answer',
              )
            : pages('recognition_reads.calibrate_status_of_the_first_account'),
      },
      { newKey: () => 'k-s1' },
    );
    page.fake.route('POST', '/api/calibrate/start', () => 'hold');
    await page.user.click(
      within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' }),
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start learning' }));
    await page.advance(100);
    page.fake.next('/api/calibrate/start', 'POST').lose();
    await page.advance(60_000);
    expect(posts(page, '/api/calibrate/start')).toHaveLength(1);
    const operation = region('recognitionOperation');
    const line = operation.querySelector<HTMLElement>('[data-outcome]');
    expect(line?.dataset['outcome']).toBe('unknown');
    expect(
      within(operation).getByRole('button', { name: 'Send the same request again' }),
    ).toBeInTheDocument();
    // A replacement is never started while the first outcome is unknown.
    const learn = within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' });
    expect(learn).toHaveAttribute('aria-disabled', 'true');
    expect(
      within(region('recognitionLearn')).getByText('The last request has no answer yet'),
    ).toBeInTheDocument();
    settled = true;
    await page.user.click(
      within(region('recognitionTarget')).getByRole('button', { name: 'Read status again' }),
    );
    await page.advance(3_000);
    expect(operation.querySelector<HTMLElement>('[data-outcome]')?.dataset['outcome']).toBe(
      'recorded',
    );
    expect(posts(page, '/api/calibrate/start')).toHaveLength(1);
  });

  it('R1 a person may go on without the answer: the outcome stays Unknown and nothing is sent', async () => {
    const page = await openRecognition({}, { newKey: () => 'k-go-on' });
    page.fake.route('POST', '/api/calibrate/start', () => 'hold');
    await page.user.click(
      within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' }),
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start learning' }));
    await page.advance(100);
    page.fake.next('/api/calibrate/start', 'POST').lose();
    await page.advance(2_000);
    const operation = region('recognitionOperation');
    await page.user.click(
      within(operation).getByRole('button', { name: 'Go on without this answer' }),
    );
    await page.advance(1_000);
    expect(operation.querySelector<HTMLElement>('[data-outcome]')?.dataset['outcome']).toBe(
      'unknown',
    );
    expect(
      within(operation).getByText(/You chose to go on without this answer/u),
    ).toBeInTheDocument();
    expect(
      within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' }),
    ).not.toHaveAttribute('aria-disabled', 'true');
    expect(posts(page, '/api/calibrate/start')).toHaveLength(1);
  });

  it('R1 an explicit resend sends the identical body and key, answered by the same operation', async () => {
    const page = await openRecognition({}, { newKey: () => 'cal-start-1' });
    page.fake.route('POST', '/api/calibrate/start', () => 'hold');
    const basic = region('recognitionMore');
    await page.user.click(within(basic).getByText('Basic scan'));
    await page.user.click(
      within(basic).getByRole('checkbox', { name: "Let the scan tap this emulator's menus" }),
    );
    await page.user.click(within(basic).getByRole('button', { name: 'Start basic scan' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start scan' }));
    await page.advance(100);
    page.fake.next('/api/calibrate/start', 'POST').lose();
    await page.advance(2_000);
    page.fake.route('POST', '/api/calibrate/start', () =>
      answer(pages('calibrate_writes.start_retry_with_the_same_key_answers_the_same_operation')),
    );
    await page.user.click(
      within(region('recognitionOperation')).getByRole('button', {
        name: 'Send the same request again',
      }),
    );
    await page.advance(1_000);
    const sent = posts(page, '/api/calibrate/start');
    expect(sent).toHaveLength(2);
    expect(sent[1]).toEqual(sent[0]);
    expect(
      region('recognitionOperation').querySelector<HTMLElement>('[data-outcome]')?.dataset[
        'outcome'
      ],
    ).toBe('accepted');
  });

  it('R1 an accepted start is followed by its id, also after a reload, which sends nothing', async () => {
    const page = await openRecognition(
      { operation: () => pages('calibrate_writes.read_the_running_calibration_by_operation') },
      { newKey: () => 'cal-start-1' },
    );
    page.fake.route('POST', '/api/calibrate/start', () =>
      answer(pages('calibrate_writes.start_basic_phases_accepted')),
    );
    const basic = region('recognitionMore');
    await page.user.click(within(basic).getByText('Basic scan'));
    await page.user.click(within(basic).getByRole('button', { name: 'Start basic scan' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start scan' }));
    await page.advance(3_000);
    const byId = '/api/calibrate/status?operation_id=000000a1-0000-4000-8000-000000000000';
    expect(page.fake.requests.some((request) => request.path === byId)).toBe(true);
    const storage = page.storage;
    page.view.unmount();
    page.data.stop();
    const again = await openRecognition(
      { operation: () => pages('calibrate_writes.read_the_running_calibration_by_operation') },
      { storage },
    );
    await again.advance(2_000);
    expect(again.fake.requests.some((request) => request.path === byId)).toBe(true);
    expect(again.fake.writes()).toEqual([]);
    const shown = region('recognitionOperation').querySelector<HTMLElement>('[data-stage]');
    expect(shown?.dataset['stage']).toBe('accepted');
    expect(shown?.dataset['source']).toBe('kept');
  });
});

describe('Recognition page R2 stages', () => {
  it.each([
    ['calibrate_status_setup_battle_active_no_final_screen_yet', 'running', 'Running'],
    ['calibrate_status_awaiting_apply_with_the_sealed_evidence', 'awaiting_apply', 'Waiting'],
    [
      'calibrate_status_failed_keeps_the_error_and_the_evidence_that_was_sealed',
      'failed',
      'Failed',
    ],
    ['calibrate_status_aborted_by_a_person_keeps_the_partial_evidence', 'cancelled', null],
    [
      'calibrate_status_restoration_required_with_the_restore_file_present',
      'restoration_required',
      'Blocked',
    ],
  ] as const)(
    'R2 %s shows stage %s by its own status, never a guess',
    async (name, stage, word) => {
      const page = await openRecognition({ calibrate: () => pages(`recognition_reads.${name}`) });
      const operation = region('recognitionOperation');
      expect(operation.querySelector<HTMLElement>('[data-stage]')?.dataset['stage']).toBe(stage);
      const chip = operation.querySelector<HTMLElement>('.tp-chip');
      expect(chip?.dataset['state'] ?? null).toBe(word === null ? null : word.toLowerCase());
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('R2 R8 a worker exit is not success: an interrupted scan says it ended before finishing', async () => {
    await openRecognition({
      calibrate: () => pages('calibrate_writes.read_the_calibration_whose_worker_exited'),
    });
    const operation = region('recognitionOperation');
    expect(operation.querySelector<HTMLElement>('[data-stage]')?.dataset['stage']).toBe(
      'interrupted',
    );
    expect(within(operation).getByText('Ended before finishing')).toBeInTheDocument();
    expect(within(operation).queryByText('Finished')).toBeNull();
  });

  it('R2 an unreadable followed operation stays unknown with the server words', async () => {
    const page = await openRecognition(
      {
        operation: () => pages('recognition_reads.calibrate_status_unknown_operation_409'),
      },
      { newKey: () => 'cal-start-1' },
    );
    page.fake.route('POST', '/api/calibrate/start', () =>
      answer(pages('calibrate_writes.start_basic_phases_accepted')),
    );
    const basic = region('recognitionMore');
    await page.user.click(within(basic).getByText('Basic scan'));
    await page.user.click(within(basic).getByRole('button', { name: 'Start basic scan' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start scan' }));
    await page.advance(3_000);
    const unread = region('recognitionOperation').querySelector('[data-operation-unread]');
    expect(unread).not.toBeNull();
    expect(region('recognitionOperation').querySelector('[data-stage]')).toBeNull();
  });
});

describe('Recognition page R3 evidence and roadmap item 9', () => {
  it('R3 RM9 the report is shown image by image; targeted groups list only what it wrote; gaps and metrics are not reported', async () => {
    await openRecognition({
      calibrate: () =>
        pages(
          'recognition_reads.calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence',
        ),
    });
    const evidence = region('recognitionEvidence');
    const group = (name: string) => evidence.querySelector<HTMLElement>(`[data-group="${name}"]`);
    expect(group('range')?.dataset['count']).toBe('1');
    expect(group('range')?.querySelector('[data-entry-status="verified"]')).not.toBeNull();
    expect(group('first_perk')?.querySelector('[data-entry-status="written"]')).not.toBeNull();
    expect(group('rare_dialog')?.querySelector('[data-entry-status="stale"]')).not.toBeNull();
    expect(group('module')?.dataset['count']).toBe('0');
    // Targeted groups and the remaining report share one home per image.
    const images = [...evidence.querySelectorAll('[data-entry-status]')].map(
      (entry) => entry.querySelector('[data-server-text]')?.textContent,
    );
    expect(images.length).toBeGreaterThan(0);
    expect(new Set(images).size).toBe(images.length);
    expect(within(group('module') ?? evidence).getByText('Not in this report')).toBeInTheDocument();
    expect(within(evidence).getByText('Target gap summary: not reported')).toBeInTheDocument();
    expect(
      within(evidence).getByText('Scan timing and comparable-scan metrics: not reported'),
    ).toBeInTheDocument();
    const timeline = region('recognitionOperation');
    expect(timeline.querySelector('[data-final-screen="verified"]')).not.toBeNull();
    expect(timeline.querySelector('[data-setup-battle]')).not.toBeNull();
  });

  it('R3 a self score alone is never shown as verified', async () => {
    // Modified recorded case (no recorded report has status "verified" without the
    // calibrator's verified verdict): the first entry keeps self 1 and loses `verified`.
    const found = pages(
      'recognition_reads.calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence',
    );
    const body = structuredClone(found.body) as { report: { entries: Record<string, unknown>[] } };
    const first = body.report.entries[0];
    if (first === undefined) throw new Error('no entry');
    first['verified'] = false;
    await openRecognition({ calibrate: () => ({ ...found, body }) });
    const range = region('recognitionEvidence').querySelector<HTMLElement>('[data-group="range"]');
    expect(range?.querySelector('[data-entry-status="verified"]')).toBeNull();
    expect(range?.querySelector('[data-entry-status="other"]')).not.toBeNull();
    expect(within(range ?? document.body).queryByText('Verified on screen')).toBeNull();
  });

  it('R3 a stale image outside every group is read on the page, not folded into the other-entries disclosure', async () => {
    const found = pages(
      'recognition_reads.calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence',
    );
    const body = structuredClone(found.body) as { report: { entries: Record<string, unknown>[] } };
    const first = body.report.entries[0];
    if (first === undefined) throw new Error('no entry');
    body.report.entries.push(
      { ...first, rel: 'misc/stale-one.png', status: 'stale', verified: false },
      { ...first, rel: 'misc/written-one.png', status: 'written', verified: false, reason: null },
      { ...first, rel: 'misc/routine-one.png', status: 'verified', verified: true, reason: null },
    );
    await openRecognition({ calibrate: () => ({ ...found, body }) });
    const evidence = region('recognitionEvidence');
    const attention = evidence.querySelector<HTMLElement>('[data-report-attention]');
    expect(attention).not.toBeNull();
    expect(within(attention ?? document.body).getByText('misc/stale-one.png')).toBeVisible();
    expect(attention?.querySelector('[data-entry-status="stale"]')).not.toBeNull();
    expect(within(attention ?? document.body).getByText('misc/written-one.png')).toBeVisible();
    expect(attention?.closest('details')).toBeNull();
    // Only a verified image without a reported reason stays behind the disclosure.
    const routine = evidence.querySelector<HTMLElement>('[data-report-entries]');
    expect(routine?.closest('details')).not.toBeNull();
    expect(routine).toHaveTextContent('misc/routine-one.png');
    expect(routine).not.toHaveTextContent('misc/stale-one.png');
    expect(routine).not.toHaveTextContent('misc/written-one.png');
  });

  it('R3 unverified copies, changed results and restoration required stay distinct', async () => {
    await openRecognition({
      calibrate: () =>
        pages('recognition_reads.calibrate_status_shared_files_of_an_untracked_calibration'),
    });
    const evidence = region('recognitionEvidence');
    expect(evidence.querySelector('[data-entry-status="unverified_copy"]')).not.toBeNull();
    // Untracked observations are offered as an explicit apply, never applied on their own.
    expect(
      within(region('recognitionOperation')).getByRole('button', {
        name: 'Apply saved observations',
      }),
    ).toBeInTheDocument();
  });

  it('R3 a result changed after it was sealed is unavailable, not empty', async () => {
    await openRecognition({
      calibrate: () =>
        pages(
          'recognition_reads.calibrate_status_report_changed_after_it_was_sealed_is_unavailable',
        ),
    });
    const errors = region('recognitionEvidence').querySelector('[data-artifact-errors]');
    expect(errors?.textContent).toContain(
      'Saved calibrate_report.json changed after this operation',
    );
  });
});

describe('R17 Prepare route preselection', () => {
  it('R17 Prepare selects a known run on entry, leaves readiness unchecked and preserves a manual pick through polls', async () => {
    const page = await openRecognition({ hash: '#recognition?run=bp_farm' });
    const picker = within(region('recognitionRun')).getByRole('combobox', { name: 'Run' });
    expect(picker).toHaveValue('bp_farm');
    expect(
      within(region('recognitionRun')).getByRole('button', { name: 'Prepare this run' }),
    ).toHaveAttribute('aria-disabled', 'true');
    await page.user.selectOptions(picker, '');
    await page.advance(10_000);
    expect(picker).toHaveValue('');
    expect(
      page.fake.requests.filter((request) => request.path.startsWith('/api/readiness')),
    ).toEqual([]);
    expect(page.fake.writes()).toEqual([]);
  });

  it.each(['missing', '', '%E0%A4%A'])(
    'R17 Prepare leaves the picker for unknown or invalid run %s',
    async (run) => {
      const page = await openRecognition({ hash: `#recognition?run=${run}` });
      expect(within(region('recognitionRun')).getByRole('combobox', { name: 'Run' })).toHaveValue(
        '',
      );
      expect(page.data.store.getSnapshot().recognitionRun).toBeNull();
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('R17 Prepare follows a query change on the mounted page without checking readiness', async () => {
    const page = await openRecognition();
    act(() => {
      window.location.hash = '#recognition?run=bp_farm';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    await page.advance();
    expect(within(region('recognitionRun')).getByRole('combobox', { name: 'Run' })).toHaveValue(
      'bp_farm',
    );
    act(() => {
      window.location.hash = '#recognition?run=missing';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    await page.advance();
    expect(within(region('recognitionRun')).getByRole('combobox', { name: 'Run' })).toHaveValue('');
    expect(
      page.fake.requests.filter((request) => request.path.startsWith('/api/readiness')),
    ).toEqual([]);
  });

  it('R17 Prepare waits for current-context runs, then clears a selection absent after a context change', async () => {
    let profile = 'mine';
    let held = true;
    const page = await openRecognition(
      { hash: '#recognition?run=bp_farm' },
      {
        config: () => ({
          active_instance: 'main',
          active_profile: profile,
          instances: { main: { serial: 'fake:1', allow_taps: true } },
        }),
        before: (fake) => {
          fake.route('GET', '/api/runs', () =>
            held ? 'hold' : { status: 200, body: { runs: { different: { defined: true } } } },
          );
        },
      },
    );
    expect(page.data.store.getSnapshot().recognitionRun).toBeNull();
    await act(async () => {
      page.fake.next('/api/runs').respond(200, v1('runs.ready').body);
      await Promise.resolve();
    });
    await page.advance();
    expect(within(region('recognitionRun')).getByRole('combobox', { name: 'Run' })).toHaveValue(
      'bp_farm',
    );
    profile = 'other';
    held = false;
    act(() => {
      page.data.poller.read('config');
    });
    await page.advance(30_000);
    expect(page.data.store.getSnapshot().context?.profile).toBe('other');
    expect(page.data.store.getSnapshot().recognitionRun).toBeNull();
    expect(within(region('recognitionRun')).getByRole('combobox', { name: 'Run' })).toHaveValue('');
    expect(page.fake.writes()).toEqual([]);
  });
});

describe('Recognition page R4 runs, readiness, apply and prepare', () => {
  it('R4 choosing a run sends nothing; Check reads its readiness once and shows how, where and alternatives', async () => {
    const page = await openRecognition();
    const run = region('recognitionRun');
    const readinessReads = () =>
      page.fake.requests.filter((request) => request.path.startsWith('/api/readiness'));
    await page.user.selectOptions(within(run).getByRole('combobox', { name: 'Run' }), 'bp_farm');
    await page.advance(5_000);
    expect(readinessReads()).toEqual([]);
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(within(run).getByRole('button', { name: 'Check this run' }));
    await page.advance(1_000);
    expect(readinessReads().map((request) => request.path)).toEqual([
      '/api/readiness?preset=bp_farm',
    ]);
    await page.advance(30_000);
    expect(readinessReads()).toHaveLength(1);
    const readiness = run.querySelector<HTMLElement>('[data-readiness]');
    expect(readiness?.dataset['readiness']).toBe('images_missing');
    expect(within(run).getByText('Crop it in Calibrate.')).toBeInTheDocument();
    expect(within(run).getByText('buttons/exit_battle.png')).toBeInTheDocument();
    expect(within(run).getByText('a configuration value, not an image')).toBeInTheDocument();
    expect(page.fake.writes()).toEqual([]);
  });

  it('R4 Prepare waits for a readiness check and sends one consented battle-only start', async () => {
    const page = await openRecognition({}, { newKey: () => 'key-prep' });
    const run = region('recognitionRun');
    const prepare = () => within(run).getByRole('button', { name: 'Prepare this run' });
    await page.user.selectOptions(within(run).getByRole('combobox', { name: 'Run' }), 'bp_farm');
    expect(prepare()).toHaveAttribute('aria-disabled', 'true');
    expect(within(run).getByText('Check this run first')).toBeInTheDocument();
    await page.user.click(within(run).getByRole('button', { name: 'Check this run' }));
    await page.advance(1_000);
    await page.user.click(prepare());
    expect(within(dialog()).getByText(/starts one normal test battle/u)).toBeInTheDocument();
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start preparing' }));
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/start')).toEqual([
      {
        bootstrap: true,
        flows: true,
        battle_only: true,
        phases: 'c,m',
        overwrite: false,
        allow_navigation: true,
        selected_run: 'bp_farm',
        expected_context: CONTEXT,
        idempotency_key: 'key-prep',
      },
    ]);
  });

  it('R4 a readiness answer for another emulator does not describe this page', async () => {
    const page = await openRecognition({
      readiness: () => v1('readiness.context_other_than_the_page'),
    });
    const run = region('recognitionRun');
    await page.user.selectOptions(within(run).getByRole('combobox', { name: 'Run' }), 'bp_farm');
    await page.user.click(within(run).getByRole('button', { name: 'Check this run' }));
    await page.advance(1_000);
    expect(run.querySelector('[data-other-context]')).not.toBeNull();
    expect(within(run).getByRole('button', { name: 'Prepare this run' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('R4 an operation awaiting apply is applied only on a confirmed click, with its id and revision', async () => {
    const page = await openRecognition({
      calibrate: () => pages('calibrate_writes.read_the_calibration_after_the_write_failed'),
    });
    page.fake.route('POST', '/api/calibrate/apply', () =>
      answer(pages('calibrate_writes.apply_again_after_the_write_failure')),
    );
    const operation = region('recognitionOperation');
    await page.user.click(
      within(operation).getByRole('button', { name: 'Apply discoveries to this profile' }),
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    expect(posts(page, '/api/calibrate/apply')).toEqual([]);
    await page.user.click(
      within(operation).getByRole('button', { name: 'Apply discoveries to this profile' }),
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Apply' }));
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/apply')).toEqual([
      {
        operation_id: '000000df-0000-4000-8000-000000000000',
        expected_revision: 6,
        expected_context: { instance: 'main', account: null, profile: 'mine', serial: 'fake:1' },
      },
    ]);
    expect(operation.querySelector<HTMLElement>('[data-outcome]')?.dataset['outcome']).toBe(
      'applied',
    );
  });

  it('R4 a lost apply stays Unknown and is never resent on its own; unsaved profile settings hold the apply', async () => {
    const page = await openRecognition({
      calibrate: () => pages('calibrate_writes.read_the_calibration_after_the_write_failed'),
    });
    page.fake.route('POST', '/api/calibrate/apply', () => 'hold');
    const operation = region('recognitionOperation');
    await page.user.click(
      within(operation).getByRole('button', { name: 'Apply discoveries to this profile' }),
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Apply' }));
    await page.advance(100);
    page.fake.next('/api/calibrate/apply', 'POST').lose();
    await page.advance(60_000);
    expect(posts(page, '/api/calibrate/apply')).toHaveLength(1);
    expect(operation.querySelector<HTMLElement>('[data-outcome]')?.dataset['outcome']).toBe(
      'unknown',
    );
    expect(within(operation).getByText('The last apply has no answer yet')).toBeInTheDocument();
  });

  it('R4 an open saved-settings draft of this context holds the apply with its reason', async () => {
    const page = await openRecognition({
      calibrate: () => pages('calibrate_writes.read_the_calibration_after_the_write_failed'),
    });
    const scope = `saved:${JSON.stringify(['main', 'fake:1', null, 'mine'])}:bp_farm`;
    page.data.commands.editDraft(scope, 'routines.ad_gems.enabled', true);
    await page.advance(100);
    const operation = region('recognitionOperation');
    expect(
      within(operation).getByRole('button', { name: 'Apply discoveries to this profile' }),
    ).toHaveAttribute('aria-disabled', 'true');
    expect(
      within(operation).getByText('Unsaved settings of this profile are open'),
    ).toBeInTheDocument();
  });
});

describe('Recognition page R5 to R8', () => {
  it('R5 Capture now sends one observe with the context and key only; it never taps', async () => {
    const page = await openRecognition({}, { newKey: () => 'obs-1' });
    page.fake.route('POST', '/api/calibrate/observe', () =>
      answer(pages('calibrate_writes.observe_accepted')),
    );
    await page.user.click(
      within(region('recognitionObserve')).getByRole('button', { name: 'Capture now' }),
    );
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/observe')).toEqual([
      { expected_context: CONTEXT, idempotency_key: 'obs-1' },
    ]);
    expect(posts(page, '/api/calibrate/start')).toEqual([]);
  });

  it.each([
    ['601', 'At most 600 seconds'],
    ['0', 'At least 1 second'],
    ['12.5', 'Use whole seconds'],
    ['', 'Enter a number of seconds'],
  ])(
    'R5 a watch of "%s" stays as typed, is refused before sending (%s), never clamped',
    async (typed, why) => {
      const page = await openRecognition();
      const observe = region('recognitionObserve');
      const field = within(observe).getByRole('textbox', { name: 'Watch for (seconds, 1 to 600)' });
      await page.user.clear(field);
      if (typed !== '') await page.user.type(field, typed);
      expect(field).toHaveValue(typed);
      expect(field).toHaveAttribute('aria-invalid', 'true');
      expect(within(observe).getByRole('button', { name: 'Watch' })).toHaveAttribute(
        'aria-disabled',
        'true',
      );
      expect(within(observe).getByText(why)).toBeInTheDocument();
      await page.user.click(within(observe).getByRole('button', { name: 'Watch' }));
      await page.advance(1_000);
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('R5 a valid watch sends exactly its seconds', async () => {
    const page = await openRecognition({}, { newKey: () => 'obs-3' });
    page.fake.route('POST', '/api/calibrate/observe', () =>
      answer(pages('calibrate_writes.observe_watch_30_seconds')),
    );
    const observe = region('recognitionObserve');
    const field = within(observe).getByRole('textbox', { name: 'Watch for (seconds, 1 to 600)' });
    await page.user.clear(field);
    await page.user.type(field, '30');
    await page.user.click(within(observe).getByRole('button', { name: 'Watch' }));
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/observe')).toEqual([
      { watch: 30, expected_context: CONTEXT, idempotency_key: 'obs-3' },
    ]);
  });

  it('R6 scan choices are kept for this emulator and account across a reload; taps off needs the menu consent', async () => {
    const page = await openRecognition({
      taps: false,
      calibrate: () => pages('recognition_reads.calibrate_status_taps_not_allowed'),
    });
    const more = region('recognitionMore');
    await page.user.click(within(more).getByText('Basic scan'));
    await page.user.click(within(more).getByRole('checkbox', { name: 'Bots' }));
    const start = () =>
      within(region('recognitionMore')).getByRole('button', { name: 'Start basic scan' });
    expect(start()).toHaveAttribute('aria-disabled', 'true');
    expect(
      within(more).getByText('Taps are off: allow menu navigation for this scan'),
    ).toBeInTheDocument();
    await page.user.click(
      within(more).getByRole('checkbox', { name: "Let the scan tap this emulator's menus" }),
    );
    expect(start()).not.toHaveAttribute('aria-disabled', 'true');
    const storage = page.storage;
    page.view.unmount();
    page.data.stop();
    const again = await openRecognition(
      {
        taps: false,
        calibrate: () => pages('recognition_reads.calibrate_status_taps_not_allowed'),
      },
      { storage, newKey: () => 'key-basic' },
    );
    const restored = region('recognitionMore');
    await again.user.click(within(restored).getByText('Basic scan'));
    expect(within(restored).getByRole('checkbox', { name: 'Bots' })).toBeChecked();
    expect(within(restored).getByRole('checkbox', { name: 'Global presets' })).not.toBeChecked();
    expect(
      within(restored).getByRole('checkbox', { name: "Let the scan tap this emulator's menus" }),
    ).toBeChecked();
    expect(again.fake.writes()).toEqual([]);
    await again.user.click(start());
    await again.user.click(within(dialog()).getByRole('button', { name: 'Start scan' }));
    await again.advance(1_000);
    expect(posts(again, '/api/calibrate/start')).toEqual([
      {
        phases: 'c,m,b',
        fresh: true,
        overwrite: false,
        allow_navigation: true,
        expected_context: CONTEXT,
        idempotency_key: 'key-basic',
      },
    ]);
  });

  it('R7 a tracked battle blocks every start with its reason; capture still works because it never taps', async () => {
    const page = await openRecognition({ battle: () => v1('battles_active.prepared') });
    const learn = within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' });
    expect(learn).toHaveAttribute('aria-disabled', 'true');
    expect(
      within(region('recognitionLearn')).getByText('A battle is tracked on this emulator'),
    ).toBeInTheDocument();
    expect(region('recognitionTarget').querySelector('[data-battle-tracked]')).not.toBeNull();
    expect(
      within(region('recognitionObserve')).getByRole('button', { name: 'Capture now' }),
    ).not.toHaveAttribute('aria-disabled', 'true');
    await page.user.click(learn);
    expect(document.querySelector('dialog[open]')).toBeNull();
    expect(page.fake.writes()).toEqual([]);
  });

  it('R7 a battle that appears during the click-time reads stops the start: nothing is sent', async () => {
    let battle = false;
    const page = await openRecognition({
      battle: () => v1(battle ? 'battles_active.prepared' : 'battles_active.null_no_driver'),
    });
    await page.user.click(
      within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' }),
    );
    battle = true;
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start learning' }));
    await page.advance(2_000);
    expect(posts(page, '/api/calibrate/start')).toEqual([]);
    expect(document.querySelector('[data-not-sent="battle_tracked"]')).not.toBeNull();
  });

  it('R7 automation running on the emulator blocks starts and capture with its reason', async () => {
    const page = await openRecognition(
      {},
      { status: () => v1('status.processes_two_rows_same_runner').body },
    );
    const capture = within(region('recognitionObserve')).getByRole('button', {
      name: 'Capture now',
    });
    expect(capture).toHaveAttribute('aria-disabled', 'true');
    expect(
      within(region('recognitionObserve')).getAllByText('Automation is running on this emulator'),
    ).toHaveLength(2);
    expect(
      within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' }),
    ).toHaveAttribute('aria-disabled', 'true');
    await page.user.click(capture);
    await page.advance(1_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('R8 the equipped-modules scan sends phase e with its consent, and a refusal is shown as sent', async () => {
    const page = await openRecognition({}, { newKey: () => 'cal-start-11' });
    page.fake.route('POST', '/api/calibrate/start', () =>
      answer(pages('calibrate_writes.start_equipped_phase_before_modules_are_done_is_refused')),
    );
    const more = region('recognitionMore');
    await page.user.click(within(more).getByText('Equipped modules'));
    await page.user.click(within(more).getByRole('button', { name: 'Scan equipped modules' }));
    expect(within(dialog()).getByText(/equips the originals again/u)).toBeInTheDocument();
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start' }));
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/start')).toEqual([
      {
        phases: 'e',
        fresh: true,
        overwrite: false,
        allow_navigation: true,
        expected_context: CONTEXT,
        idempotency_key: 'cal-start-11',
      },
    ]);
    const line = region('recognitionOperation').querySelector<HTMLElement>('[data-outcome]');
    expect(line?.dataset['outcome']).toBe('refused');
    expect(line?.textContent).toContain('Finish phase 1 Modules first.');
    expect(line?.textContent).toContain('modules_not_done');
  });

  it('R8 restoration required blocks every launch and Stop; the page says the stop did not restore', async () => {
    const page = await openRecognition({
      calibrate: () =>
        pages(
          'recognition_reads.calibrate_status_restoration_required_with_the_restore_file_present',
        ),
    });
    expect(
      within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' }),
    ).toHaveAttribute('aria-disabled', 'true');
    expect(
      within(region('recognitionObserve')).getByRole('button', { name: 'Capture now' }),
    ).toHaveAttribute('aria-disabled', 'true');
    expect(region('recognitionTarget').querySelector('[data-restore-pending]')).not.toBeNull();
    expect(
      within(region('recognitionOperation')).queryByRole('button', { name: 'Stop this scan' }),
    ).toBeNull();
    expect(page.fake.writes()).toEqual([]);
  });

  it.each([
    [
      'stop_running_calibration_asks_the_worker_to_stop',
      'stopping',
      'Asked to stop; not stopped yet',
    ],
    [
      'stop_a_worker_that_cannot_be_verified_is_released_while_the_lease_is_free',
      'released',
      'Released: the worker could not be verified',
    ],
  ] as const)(
    'R2 R8 Stop sends its operation and revision once; %s is shown as %s, never as stopped',
    async (name, code, words) => {
      const page = await openRecognition({
        calibrate: () =>
          pages('recognition_reads.calibrate_status_setup_battle_active_no_final_screen_yet'),
      });
      // Modified recorded answer: no recorded Stop answer names operation 9e (the recorded
      // running read); the answer's own operation id is replaced by the requested one.
      const recorded = pages(`calibrate_writes.${name}`);
      const body = structuredClone(recorded.body) as { operation: Record<string, unknown> };
      body.operation['id'] = '0000009e-0000-4000-8000-000000000000';
      page.fake.route('POST', '/api/calibrate/stop', () => ({ status: recorded.status, body }));
      const operation = region('recognitionOperation');
      await page.user.click(within(operation).getByRole('button', { name: 'Stop this scan' }));
      expect(within(dialog()).getByText(/does not restore equipment/u)).toBeInTheDocument();
      await page.user.click(within(dialog()).getByRole('button', { name: 'Ask it to stop' }));
      await page.advance(1_000);
      expect(posts(page, '/api/calibrate/stop')).toEqual([
        { operation_id: '0000009e-0000-4000-8000-000000000000', expected_revision: 2 },
      ]);
      const line = operation.querySelector<HTMLElement>('[data-kind="calibrate_stop"]');
      expect(line?.dataset['outcome']).toBe(code);
      expect(line?.textContent).toContain(words);
      await page.advance(30_000);
      expect(posts(page, '/api/calibrate/stop')).toHaveLength(1);
    },
  );
});

describe('Recognition page accessibility and wording', () => {
  it.each([
    'calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence',
    'calibrate_status_restoration_required_with_the_restore_file_present',
    'calibrate_status_shared_files_of_an_untracked_calibration',
  ])(
    'R1 R2 R3 %s: every visible word comes from the catalogue or the server, and axe is clean',
    async (name) => {
      const page = await openRecognition({ calibrate: () => pages(`recognition_reads.${name}`) });
      await page.user.selectOptions(
        within(region('recognitionRun')).getByRole('combobox', { name: 'Run' }),
        'bp_farm',
      );
      await page.user.click(
        within(region('recognitionRun')).getByRole('button', { name: 'Check this run' }),
      );
      await page.advance(1_000);
      expect(textOutsideCatalogue(page.view.container)).toEqual([]);
      vi.useRealTimers();
      expect(await axe(page.view.container)).toHaveNoViolations();
    },
  );
});

describe('Recognition compliance round 1', () => {
  it('R7 F6R10-R1-01 a battle read that fails at the click sends nothing, whatever an older read said', async () => {
    let failing = false;
    const page = await openRecognition(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/battles/active', () =>
            failing
              ? { status: 503, body: 'Service Unavailable' }
              : answer(v1('battles_active.null_no_driver')),
          );
        },
      },
    );
    await page.user.click(
      within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' }),
    );
    failing = true;
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start learning' }));
    await page.advance(2_000);
    expect(posts(page, '/api/calibrate/start')).toEqual([]);
    expect(document.querySelector('[data-not-sent="safety_read_failed"]')).not.toBeNull();
  });

  it('R8 F6R10-R1-01 a calibrator read that fails at the click sends nothing; the stale facts are labelled', async () => {
    let failing = false;
    const page = await openRecognition(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/calibrate/status', () =>
            failing
              ? { status: 503, body: 'Service Unavailable' }
              : answer(
                  pages(
                    'recognition_reads.calibrate_status_without_an_operation_reads_the_latest_of_the_context',
                  ),
                ),
          );
        },
      },
    );
    await page.user.click(
      within(region('recognitionLearn')).getByRole('button', { name: 'Learn screens' }),
    );
    failing = true;
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start learning' }));
    await page.advance(2_000);
    expect(posts(page, '/api/calibrate/start')).toEqual([]);
    expect(document.querySelector('[data-not-sent="safety_read_failed"]')).not.toBeNull();
    // The polled read failed too: the shown facts are the last answered read, and they permit nothing.
    await page.advance(10_000);
    expect(region('recognitionTarget').querySelector('[data-stale]')).not.toBeNull();
    expect(
      within(region('recognitionObserve')).getByRole('button', { name: 'Capture now' }),
    ).toHaveAttribute('aria-disabled', 'true');
  });

  it("R2 R3 F6R10-R1-02 a followed operation that cannot be read shows none of the newest operation's evidence", async () => {
    const page = await openRecognition(
      {
        calibrate: () =>
          pages(
            'recognition_reads.calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence',
          ),
        operation: () => pages('recognition_reads.calibrate_status_unknown_operation_409'),
      },
      { newKey: () => 'cal-start-1' },
    );
    page.fake.route('POST', '/api/calibrate/start', () =>
      answer(pages('calibrate_writes.start_basic_phases_accepted')),
    );
    const more = region('recognitionMore');
    await page.user.click(within(more).getByText('Basic scan'));
    await page.user.click(within(more).getByRole('button', { name: 'Start basic scan' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start scan' }));
    await page.advance(3_000);
    const evidence = region('recognitionEvidence');
    expect(evidence.querySelector('[data-evidence-unavailable]')).not.toBeNull();
    expect(evidence.querySelector('[data-entry-status]')).toBeNull();
    expect(evidence.querySelector('[data-group]')).toBeNull();
    const operation = region('recognitionOperation');
    expect(operation.querySelector('[data-final-screen]')).toBeNull();
    expect(operation.querySelector('[data-stage]')).toBeNull();
    expect(operation.querySelector('[data-operation-unread]')).not.toBeNull();
  });

  it('R2 F6R10-R1-02 a followed operation whose newest read failed keeps its own last answer, labelled stale', async () => {
    let failing = false;
    const page = await openRecognition(
      {
        operation: () =>
          failing ? 'hold' : pages('calibrate_writes.read_the_running_calibration_by_operation'),
      },
      { newKey: () => 'cal-start-1' },
    );
    page.fake.route('POST', '/api/calibrate/start', () =>
      answer(pages('calibrate_writes.start_basic_phases_accepted')),
    );
    page.fake.route('GET', /^\/api\/calibrate\/status\?operation_id=/u, () =>
      failing
        ? { status: 503, body: 'Service Unavailable' }
        : answer(pages('calibrate_writes.read_the_running_calibration_by_operation')),
    );
    const more = region('recognitionMore');
    await page.user.click(within(more).getByText('Basic scan'));
    await page.user.click(within(more).getByRole('button', { name: 'Start basic scan' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start scan' }));
    await page.advance(3_000);
    failing = true;
    await page.advance(5_000);
    const operation = region('recognitionOperation');
    expect(operation.querySelector<HTMLElement>('[data-stage]')?.dataset['stage']).toBe('accepted');
    expect(region('recognitionEvidence').querySelector('[data-stale]')).not.toBeNull();
  });

  it('R4 F6R10-R1-03 applying saved observations reads the config at the click; another profile sends nothing', async () => {
    let profile = 'mine';
    const page = await openRecognition(
      {
        calibrate: () =>
          pages('recognition_reads.calibrate_status_shared_files_of_an_untracked_calibration'),
      },
      {
        config: () => ({
          active_instance: 'main',
          active_profile: profile,
          instances: { main: { serial: 'fake:1', allow_taps: true } },
        }),
      },
    );
    page.fake.route('POST', '/api/calibrate/apply', () =>
      answer(pages('calibrate_writes.apply_saved_observations_of_no_operation')),
    );
    const operation = region('recognitionOperation');
    await page.user.click(
      within(operation).getByRole('button', { name: 'Apply saved observations' }),
    );
    profile = 'theirs';
    await page.user.click(within(dialog()).getByRole('button', { name: 'Apply' }));
    await page.advance(2_000);
    expect(posts(page, '/api/calibrate/apply')).toEqual([]);
    expect(document.querySelector('[data-not-sent="context_changed"]')).not.toBeNull();
  });

  it('R4 F6R10-R1-03 with the same context at the click, saved observations are applied once', async () => {
    const page = await openRecognition({
      calibrate: () =>
        pages('recognition_reads.calibrate_status_shared_files_of_an_untracked_calibration'),
    });
    page.fake.route('POST', '/api/calibrate/apply', () =>
      answer(pages('calibrate_writes.apply_saved_observations_of_no_operation')),
    );
    const operation = region('recognitionOperation');
    await page.user.click(
      within(operation).getByRole('button', { name: 'Apply saved observations' }),
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Apply' }));
    await page.advance(2_000);
    expect(posts(page, '/api/calibrate/apply')).toEqual([
      {
        saved_observations: true,
        expected_context: { instance: 'main', account: null, profile: 'mine', serial: 'fake:1' },
      },
    ]);
  });

  it('R1 R4 F6R10-R1-05 a failed capture keeps its discoveries applicable; the failure stays shown', async () => {
    const page = await openRecognition({
      calibrate: () =>
        pages(
          'recognition_reads.calibrate_status_failed_keeps_the_error_and_the_evidence_that_was_sealed',
        ),
    });
    // Modified recorded answer: no recorded apply answer names operation 94 (the recorded
    // failed read); the failed-capture apply answer's operation id is replaced by the sent one.
    const recorded = pages(
      'calibrate_writes.apply_after_a_failed_capture_keeps_the_failure_on_the_operation',
    );
    const reply = structuredClone(recorded.body) as { operation: Record<string, unknown> };
    reply.operation['id'] = '00000094-0000-4000-8000-000000000000';
    page.fake.route('POST', '/api/calibrate/apply', () => ({
      status: recorded.status,
      body: reply,
    }));
    const operation = region('recognitionOperation');
    expect(operation.querySelector<HTMLElement>('[data-stage]')?.dataset['stage']).toBe('failed');
    await page.user.click(
      within(operation).getByRole('button', { name: 'Apply discoveries to this profile' }),
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Apply' }));
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/apply')).toEqual([
      {
        operation_id: '00000094-0000-4000-8000-000000000000',
        expected_revision: 3,
        expected_context: { instance: 'main', account: null, profile: 'mine', serial: 'fake:1' },
      },
    ]);
    expect(
      operation.querySelector<HTMLElement>('[data-kind="calibrate_apply"]')?.dataset['outcome'],
    ).toBe('applied');
    expect(operation.querySelector<HTMLElement>('[data-stage]')?.dataset['stage']).toBe('failed');
  });

  it('R1 R4 F6R10-R1-05 an interrupted capture offers its apply too', async () => {
    await openRecognition({
      calibrate: () => pages('calibrate_writes.read_the_calibration_whose_worker_exited'),
    });
    expect(
      within(region('recognitionOperation')).getByRole('button', {
        name: 'Apply discoveries to this profile',
      }),
    ).not.toHaveAttribute('aria-disabled', 'true');
  });

  it.each([
    [3, 'applied_by_read'],
    [2, 'unknown'],
  ] as const)(
    'R4 F6R10-R1-07 a lost apply is settled only by its operation recording the sent revision (recorded %s: %s)',
    async (recordedRevision, outcome) => {
      let after = false;
      // Modified recorded read: no recorded read follows an apply of operation 94; the
      // recorded read after the failed-capture apply gets the sent operation id and the
      // tested apply revision.
      const read = pages('calibrate_writes.read_the_failed_capture_after_the_apply');
      const applied = structuredClone(read.body) as {
        operation: { id: string; result_refs: Record<string, unknown> };
      };
      applied.operation.id = '00000094-0000-4000-8000-000000000000';
      applied.operation.result_refs['apply_request_revision'] = recordedRevision;
      const page = await openRecognition({
        calibrate: () =>
          after
            ? { ...read, body: applied }
            : pages(
                'recognition_reads.calibrate_status_failed_keeps_the_error_and_the_evidence_that_was_sealed',
              ),
      });
      page.fake.route('POST', '/api/calibrate/apply', () => 'hold');
      const operation = region('recognitionOperation');
      await page.user.click(
        within(operation).getByRole('button', { name: 'Apply discoveries to this profile' }),
      );
      await page.user.click(within(dialog()).getByRole('button', { name: 'Apply' }));
      await page.advance(100);
      page.fake.next('/api/calibrate/apply', 'POST').lose();
      after = true;
      await page.advance(10_000);
      expect(
        operation.querySelector<HTMLElement>('[data-kind="calibrate_apply"]')?.dataset['outcome'],
      ).toBe(outcome);
      expect(posts(page, '/api/calibrate/apply')).toHaveLength(1);
    },
  );

  it.each([
    ['read_the_calibration_that_was_asked_to_stop', 'recorded'],
    ['read_the_calibration_whose_worker_exited', 'unknown'],
  ] as const)(
    'R2 R8 F6R10-R1-07 a lost stop is settled only by a read showing it past the sent revision (%s: %s)',
    async (name, outcome) => {
      let after = false;
      // Modified recorded read: no recorded read follows a stop of operation 9e (the recorded
      // running read); the recorded read's operation id is replaced by the stopped one.
      const read = pages(`calibrate_writes.${name}`);
      const later = structuredClone(read.body) as { operation: { id: string } };
      later.operation.id = '0000009e-0000-4000-8000-000000000000';
      const page = await openRecognition({
        calibrate: () =>
          after
            ? { ...read, body: later }
            : pages('recognition_reads.calibrate_status_setup_battle_active_no_final_screen_yet'),
      });
      page.fake.route('POST', '/api/calibrate/stop', () => 'hold');
      const operation = region('recognitionOperation');
      await page.user.click(within(operation).getByRole('button', { name: 'Stop this scan' }));
      await page.user.click(within(dialog()).getByRole('button', { name: 'Ask it to stop' }));
      await page.advance(100);
      page.fake.next('/api/calibrate/stop', 'POST').lose();
      after = true;
      await page.advance(10_000);
      expect(
        operation.querySelector<HTMLElement>('[data-kind="calibrate_stop"]')?.dataset['outcome'],
      ).toBe(outcome);
      expect(posts(page, '/api/calibrate/stop')).toHaveLength(1);
    },
  );

  it('R6 F6R10-R1-07 every choice reaches the body; fresh off and replace on are sent as chosen', async () => {
    const page = await openRecognition({}, { newKey: () => 'key-all' });
    const more = region('recognitionMore');
    await page.user.click(within(more).getByText('Basic scan'));
    for (const name of ['Global presets', 'Guardians', 'Bots', 'Workshop presets'])
      await page.user.click(within(more).getByRole('checkbox', { name }));
    await page.user.click(
      within(more).getByRole('checkbox', { name: 'Start the steps over (otherwise resume them)' }),
    );
    await page.user.click(
      within(more).getByRole('checkbox', { name: 'Replace images that already exist' }),
    );
    await page.user.click(
      within(more).getByRole('checkbox', { name: "Let the scan tap this emulator's menus" }),
    );
    await page.user.click(within(more).getByRole('button', { name: 'Start basic scan' }));
    expect(
      within(dialog()).getByText('Replaces existing images with new captures.'),
    ).toBeInTheDocument();
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start scan' }));
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/start')).toEqual([
      {
        phases: 'c,m,g,u,b,w',
        fresh: false,
        overwrite: true,
        allow_navigation: true,
        expected_context: CONTEXT,
        idempotency_key: 'key-all',
      },
    ]);
  });

  it('R6 F6R10-R1-07 no step chosen is refused before sending; a refusal keeps the choices', async () => {
    const page = await openRecognition(
      {
        taps: false,
        calibrate: () => pages('recognition_reads.calibrate_status_of_the_first_account'),
      },
      { newKey: () => 'k-t1' },
    );
    const more = region('recognitionMore');
    await page.user.click(within(more).getByText('Basic scan'));
    await page.user.click(within(more).getByRole('checkbox', { name: 'Cards and presets' }));
    await page.user.click(within(more).getByRole('checkbox', { name: 'Modules' }));
    expect(within(more).getByText('Choose at least one step')).toBeInTheDocument();
    await page.user.click(within(more).getByRole('checkbox', { name: 'Cards and presets' }));
    // The read says taps are allowed, so the click is sent; the server refuses it as recorded.
    page.fake.route('POST', '/api/calibrate/start', () =>
      answer(pages('calibrate_writes.start_taps_off_409')),
    );
    await page.user.click(within(more).getByRole('button', { name: 'Start basic scan' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Start scan' }));
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/start')).toHaveLength(1);
    expect(
      region('recognitionOperation').querySelector<HTMLElement>('[data-kind="calibrate_start"]')
        ?.dataset['outcome'],
    ).toBe('refused');
    expect(within(more).getByRole('checkbox', { name: 'Cards and presets' })).toBeChecked();
    expect(within(more).getByRole('checkbox', { name: 'Modules' })).not.toBeChecked();
  });

  it("R6 F6R10-R1-07 choices belong to their emulator and account: another account's page starts from the defaults", async () => {
    const page = await openRecognition();
    const more = region('recognitionMore');
    await page.user.click(within(more).getByText('Basic scan'));
    await page.user.click(within(more).getByRole('checkbox', { name: 'Bots' }));
    const storage = page.storage;
    page.view.unmount();
    page.data.stop();
    await openRecognition(
      {},
      {
        storage,
        config: () => ({
          active_instance: 'main',
          active_profile: 'theirs',
          instances: { main: { serial: 'fake:1', allow_taps: true } },
        }),
      },
    );
    const other = region('recognitionMore');
    expect(within(other).getByRole('checkbox', { name: 'Bots', hidden: true })).not.toBeChecked();
    expect(
      within(other).getByRole('checkbox', { name: 'Cards and presets', hidden: true }),
    ).toBeChecked();
  });
});

describe('Recognition launch context', () => {
  it('R1 R5 F6R10-R1-04 a launch sends the rendering descriptor the click-time config read names', async () => {
    // The config body is the test's own (as e0Config): a prepared scan persists
    // instances.<instance>.rendering as {width, height, dpi} (backend/device/layout.py activate).
    const rendering = { width: 1080, height: 2560, dpi: 360 };
    const page = await openRecognition(
      {},
      {
        newKey: () => 'key-rendering',
        config: () => ({
          active_instance: 'main',
          active_profile: 'mine',
          instances: { main: { serial: 'fake:1', allow_taps: true, rendering } },
        }),
      },
    );
    await page.user.click(
      within(region('recognitionObserve')).getByRole('button', { name: 'Capture now' }),
    );
    await page.advance(1_000);
    expect(posts(page, '/api/calibrate/observe')).toEqual([
      { expected_context: { ...CONTEXT, rendering }, idempotency_key: 'key-rendering' },
    ]);
  });
});
