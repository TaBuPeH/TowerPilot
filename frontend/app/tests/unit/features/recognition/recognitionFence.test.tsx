import { screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { own } from '../../../../src/core/read.ts';
import { selectRecognitionPage } from '../../../../src/store/selectors/recognition.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import {
  dashboardCase,
  type DashboardCase,
  dashboardPagesCase,
  dashboardPagesCaseNames,
} from '../../../support/dashboardFixture.ts';
import { renderPage } from '../dashboard/harness.tsx';

vi.mock('../../../../src/app/recognitionAdvanced.ts', () => ({
  ConnectedAdvancedRepairs: () => null,
}));

const c = (name: string) => dashboardPagesCase(`recognition_context_fence.${name}`);
const config = c('read_config_the_page_context_comes_from');
const before = c('read_the_profile_after_the_malformed_requests_nothing_was_applied');
const pending = c('read_the_calibration_after_the_fence_refusal_it_still_awaits_apply');
const settled = c('read_the_calibration_whose_apply_the_page_must_settle_by_reading');
const lost = c('apply_by_operation_is_applied_and_the_answer_is_lost');

/**
 * Transport ordering needs the same operation before and after its lost apply. The
 * recorded predecessor is operation 112; change only its id to 114, or remove the
 * operation binding to model saved (legacy) observations with those same discoveries.
 */
function predecessor(saved = false): DashboardCase {
  const body = structuredClone(pending.body) as Record<string, unknown>;
  const operation = body['operation'] as Record<string, unknown>;
  const state = body['state'] as Record<string, unknown>;
  if (saved) {
    delete body['operation'];
    delete state['operation_id'];
  } else {
    operation['id'] = own(lost.request.body, 'operation_id');
    state['operation_id'] = operation['id'];
  }
  return { ...pending, body };
}

async function open(
  options: {
    readonly saved?: boolean;
    readonly calibrate?: () => DashboardCase;
    readonly config?: () => DashboardCase;
    readonly profile?: () => DashboardCase;
    readonly write?: DashboardCase | 'hold';
  } = {},
) {
  window.location.hash = '#recognition';
  const page = await renderPage({
    config: () => (options.config?.() ?? config).body,
    status: () => dashboardCase('status.processes_empty').body,
    before: (fake) => {
      fake.route('GET', /^\/api\/calibrate\/status/u, () => {
        const answer =
          options.calibrate?.() ?? (options.saved === true ? predecessor(true) : pending);
        return { status: answer.status, body: answer.body };
      });
      fake.route('GET', '/api/profile/mine.yaml', () => {
        const answer = options.profile?.() ?? before;
        return { status: answer.status, body: answer.body };
      });
      fake.route('POST', '/api/calibrate/apply', () =>
        options.write === 'hold'
          ? 'hold'
          : {
              status: options.write?.status ?? 200,
              body: options.write?.body ?? {},
            },
      );
    },
  });
  await page.advance(1_000);
  return page;
}

async function apply(page: Awaited<ReturnType<typeof open>>, saved = false) {
  await page.user.click(
    page.view.getByRole('button', {
      name: saved ? 'Apply saved observations' : 'Apply discoveries to this profile',
    }),
  );
  const dialog = page.view.getByRole('dialog');
  await page.user.click(within(dialog).getByRole('button', { name: 'Apply' }));
  await page.advance(100);
}
const line = () => document.querySelector<HTMLElement>('[data-kind="calibrate_apply"]');
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

it.each([false, true])(
  'R-FENCE click-time context switch sends nothing (saved=%s)',
  async (saved) => {
    let switched = false;
    const page = await open({
      saved,
      config: () =>
        switched ? c('read_config_after_another_client_switched_the_account') : config,
    });
    // The view was drawn for alpha. Only the click's GET sees another client's switch.
    switched = true;
    await apply(page, saved);
    expect(postBodies(page.fake, '/api/calibrate/apply')).toEqual([]);
    expect(document.querySelector('[data-not-sent="context_changed"]')).not.toBeNull();
  },
);

it.each(
  dashboardPagesCaseNames('recognition_context_fence').filter(
    (name) => c(name).request.path === '/api/calibrate/apply' && c(name).status >= 400,
  ),
)('R-FENCE %s page outcome', async (name) => {
  const answer = c(name);
  const page = await open({ write: answer });
  await apply(page);
  const code = own(answer.body, 'code');
  expect(line()?.dataset['outcome']).toBe(
    code === 'context_changed'
      ? 'apply_context_changed'
      : code === 'invalid_request'
        ? 'apply_invalid_request'
        : 'refused',
  );
  expect(line()?.textContent).toContain(own(answer.body, 'error'));
  if (code === 'context_changed')
    expect(line()?.textContent).toContain('Not applied: the emulator, account or profile changed');
  if (code === 'invalid_request') {
    const outcome = line();
    if (outcome === null) throw new Error('Missing apply outcome');
    expect(
      within(outcome).getByText('Not applied: this page sent an invalid request'),
    ).toBeVisible();
    await page.user.click(within(outcome).getByRole('button', { name: 'About this message' }));
    expect(screen.getByRole('tooltip')).toHaveTextContent('This is a page bug.');
    await page.user.keyboard('{Escape}');
  }
  const current = own(answer.body, 'current_context');
  if (current !== null && current !== undefined) {
    expect(line()?.querySelector('[data-current-context]')?.textContent).toContain('beta');
    expect(line()?.querySelector('[data-current-context]')?.textContent).toContain('theirs');
  } else expect(line()?.querySelector('[data-current-context]')).toBeNull();
  expect(line()?.textContent).not.toContain('Send the same request again');
  await page.advance(30_000);
  expect(postBodies(page.fake, '/api/calibrate/apply')).toHaveLength(1);
});

it('R-FENCE apply_by_operation_is_applied_and_the_answer_is_lost remains Unknown until its recorded read', async () => {
  let read = predecessor();
  const page = await open({ calibrate: () => read, write: 'hold' });
  await apply(page);
  expect(postBodies(page.fake, '/api/calibrate/apply')).toEqual([lost.request.body]);
  page.fake.next('/api/calibrate/apply', 'POST').lose();
  await page.advance(30_000);
  expect(line()?.dataset['outcome']).toBe('unknown');
  read = settled;
  page.data.commands.checkRecognition();
  await page.advance(1_000);
  expect(line()?.dataset['outcome']).toBe('applied_by_read');
  expect(postBodies(page.fake, '/api/calibrate/apply')).toHaveLength(1);
});

it('R-FENCE apply_saved_composed_for_alpha_is_applied_and_the_answer_is_lost remains Unknown until the matching profile read', async () => {
  let profile = before;
  const page = await open({ saved: true, profile: () => profile, write: 'hold' });
  await apply(page, true);
  expect(postBodies(page.fake, '/api/calibrate/apply')).toEqual([
    c('apply_saved_composed_for_alpha_is_applied_and_the_answer_is_lost').request.body,
  ]);
  page.fake.next('/api/calibrate/apply', 'POST').lose();
  await page.advance(30_000);
  expect(line()?.dataset['outcome']).toBe('unknown');
  profile = c('read_the_profile_after_the_matching_apply');
  page.data.commands.checkRecognition();
  await page.advance(1_000);
  expect(line()?.dataset['outcome']).toBe('applied_by_read');
  expect(selectRecognitionPage(page.data.store.getSnapshot()).outcomes[0]?.resendOffered).toBe(
    false,
  );
  expect(postBodies(page.fake, '/api/calibrate/apply')).toHaveLength(1);
});
