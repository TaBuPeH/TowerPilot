import { within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { e0Config, postBodies } from '../../../support/dataLayer.ts';
import {
  dashboardCase,
  dashboardPagesCase,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';

/**
 * The Runs page replaying the backend's own recorded scenarios
 * (backend/tests/fixtures/dashboard_contract_pages_v1.json, families
 * run_controls_save and run_controls_live): every GET and POST answer below is
 * a recorded case, and the page's request body is compared with the recorded
 * request the backend answered.
 */
const save = (name: string) => dashboardPagesCase(`run_controls_save.${name}`);
const live = (name: string) => dashboardPagesCase(`run_controls_live.${name}`);

/** A recorded answer as the transport delivers it: JSON, or the text page a 500 records. */
function answerOf(found: DashboardCase): { readonly status: number; readonly body: unknown } {
  const body = found.body as Record<string, unknown>;
  return {
    status: found.status,
    body: Object.keys(body).length === 1 && typeof body['text'] === 'string' ? body['text'] : body,
  };
}

async function openRuns(hash: string, options: PageOptions = {}) {
  window.location.hash = hash;
  const page = await renderPage({
    status: () => statusBody(sample('status.off')),
    config: () => e0Config(true),
    ...options,
  });
  // Drain the reads started by mounting; setup needs no elapsed polling time.
  await page.advance();
  return page;
}

function region(name: 'runsSaved' | 'runsLive'): HTMLElement {
  const found = document.querySelector<HTMLElement>(`[data-region="${name}"]`);
  if (found === null) throw new Error(`${name} missing`);
  return found;
}

function readsFrom(path: RegExp, read: () => DashboardCase): NonNullable<PageOptions['before']> {
  return (fake) => {
    fake.route('GET', path, () => answerOf(read()));
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('Runs page on the recorded saved-run save (run_controls_save)', () => {
  const RUN = /^\/api\/run-controls\?/u;

  it('RUN-05 SR-2 saved_switch_off: the page sends the recorded body; the 200 reads Saved and the draft goes with the read that holds it', async () => {
    let read = save('read_before_the_first_save');
    const page = await openRuns('#runs/saved/bp_farm', { before: readsFrom(RUN, () => read) });
    const saved = region('runsSaved');
    await page.user.click(within(saved).getByRole('checkbox', { name: 'Ad gems On' }));
    await page.user.click(within(saved).getByRole('button', { name: 'Save run settings' }));
    await page.advance(0);
    const recorded = save('saved_switch_off');
    expect(postBodies(page.fake, '/api/run-controls')).toEqual([recorded.request.body]);
    read = save('read_after_the_save_holds_the_new_values');
    const answer = answerOf(recorded);
    page.fake.next('/api/run-controls', 'POST').respond(answer.status, answer.body);
    await page.advance(1_000);
    expect(within(region('runsSaved')).getByText('Saved')).toBeVisible();
    expect(
      within(region('runsSaved')).getByRole('checkbox', { name: 'Ad gems On' }),
    ).not.toBeChecked();
    expect(within(region('runsSaved')).queryByRole('button', { name: 'Discard' })).toBeNull();
    await page.advance(60_000);
    expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
  });

  it('RUN-07 SR-2 saved_switches_and_numbers_in_one_request: saved in part (shopping stays off without a queue); only the held values leave the draft', async () => {
    // The run at revision 3 (the recorded answer that made it; a 200 is the read's shape).
    let read = save('saved_numbers_with_the_revision_of_the_last_save');
    const page = await openRuns('#runs/saved/bp_farm', { before: readsFrom(RUN, () => read) });
    const saved = () => region('runsSaved');
    for (const name of ['Abilities', 'Floating gems On', 'Rewards', 'Shopping On'])
      await page.user.click(within(saved()).getByRole('checkbox', { name }));
    const every = within(saved()).getByRole('textbox', { name: /^Shopping Every$/u });
    await page.user.clear(every);
    await page.user.type(every, '120');
    await page.user.click(within(saved()).getByRole('button', { name: 'Save run settings' }));
    await page.advance(0);
    const recorded = save('saved_switches_and_numbers_in_one_request');
    expect(postBodies(page.fake, '/api/run-controls')).toEqual([recorded.request.body]);
    read = { ...recorded, request: { method: 'GET', path: '/api/run-controls?blueprint=farm' } };
    const answer = answerOf(recorded);
    page.fake.next('/api/run-controls', 'POST').respond(answer.status, answer.body);
    await page.advance(1_000);
    expect(within(saved()).getByText('Saved - not every value was kept')).toBeVisible();
    // The switch the server kept off keeps what the person chose, marked as not saved.
    expect(within(saved()).getByRole('checkbox', { name: 'Shopping On' })).toBeChecked();
    expect(within(saved()).getByText('1 change not saved')).toBeVisible();
    expect(within(saved()).getByRole('checkbox', { name: 'Rewards' })).not.toBeChecked();
    expect(within(saved()).getByRole('textbox', { name: /^Shopping Every$/u })).toHaveValue('120');
  });

  it.each([
    [
      'saved_then_the_answer_failed_500',
      'read_before_the_unanswered_save',
      'read_after_the_unanswered_save_holds_every_sent_value',
      'The saved settings now have your changes',
      false,
    ],
    // F6C-R1-02: the unchanged revision cannot prove a failure (the write may still land).
    [
      'write_failed_nothing_saved_500',
      'read_before_the_failed_write',
      'read_after_the_failed_write_the_revision_is_unchanged',
      "The reply didn't confirm the save - it may or may not have saved",
      true,
    ],
  ] as const)(
    'RUN-07 SR-4 SR-8 F6C-R1-02 %s: the text 500 is Unknown, nothing is resent for 60 s, and only a read holding the values settles it',
    async (post, before, after, wording, draftStays) => {
      let read: () => DashboardCase | 'hold' = () => save(before);
      const page = await openRuns('#runs/saved/bp_farm', {
        before: (fake) => {
          fake.route('GET', RUN, () => {
            const found = read();
            return found === 'hold' ? 'hold' : answerOf(found);
          });
        },
      });
      const saved = () => region('runsSaved');
      const recorded = save(post);
      const changes = (recorded.request.body as { changes: Record<string, unknown> }).changes;
      await page.user.click(within(saved()).getByRole('checkbox', { name: 'Ad gems On' }));
      if ('routines.ad_gems.interval_sec' in changes) {
        const every = within(saved()).getByRole('textbox', { name: /^Ad gems Every$/u });
        await page.user.clear(every);
        await page.user.type(every, String(changes['routines.ad_gems.interval_sec']));
      }
      await page.user.click(within(saved()).getByRole('button', { name: 'Save run settings' }));
      await page.advance(0);
      expect(postBodies(page.fake, '/api/run-controls')).toEqual([recorded.request.body]);
      read = () => 'hold';
      const answer = answerOf(recorded);
      page.fake.next('/api/run-controls', 'POST').respond(answer.status, answer.body);
      await page.advance(0);
      expect(
        within(saved()).getByText(
          "The reply didn't confirm the save - it may or may not have saved",
        ),
      ).toBeVisible();
      expect(within(saved()).queryByRole('button', { name: 'Send same request again' })).toBeNull();
      expect(within(saved()).getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
      await page.advance(60_000);
      expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
      read = () => save(after);
      await page.user.click(within(saved()).getByRole('button', { name: 'Check saved values' }));
      await page.advance(1_000);
      expect(within(saved()).getByText(wording)).toBeVisible();
      expect(within(saved()).getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
      expect(within(saved()).queryByRole('button', { name: 'Discard' }) !== null).toBe(draftStays);
      expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
    },
  );

  it.each([
    'stale_revision_another_client_saved_first',
    'context_changed_after_the_read',
    'starter_profile_is_read_only',
    'save_of_a_profile_with_a_problem',
    'action_lock_another_action_in_progress',
  ])(
    'RUN-06 SR-5 %s: Not saved with the server reason; the draft stays and nothing is resent',
    async (refusal) => {
      const page = await openRuns('#runs/saved/bp_farm', {
        before: readsFrom(RUN, () => save('read_before_the_conflicts')),
      });
      const saved = () => region('runsSaved');
      await page.user.click(within(saved()).getByRole('checkbox', { name: 'Ad gems On' }));
      await page.user.click(within(saved()).getByRole('button', { name: 'Save run settings' }));
      await page.advance(0);
      const answer = answerOf(save(refusal));
      page.fake.next('/api/run-controls', 'POST').respond(answer.status, answer.body);
      await page.advance(1_000);
      expect(within(saved()).getByText('Not saved')).toBeVisible();
      expect(saved()).toHaveTextContent((answer.body as { error: string }).error);
      expect(within(saved()).getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
      await page.advance(60_000);
      expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
    },
  );
});

describe('Runs page on the recorded live change (run_controls_live)', () => {
  const BATTLE = /^\/api\/battles\/active$/u;
  const recorded = live('accepted_switch_off');
  const key = (recorded.request.body as { idempotency_key: string }).idempotency_key;

  async function sendSwitchOff(read: () => DashboardCase) {
    const page = await openRuns('#runs/live', {
      before: readsFrom(BATTLE, read),
      newKey: () => key,
    });
    await page.user.click(within(region('runsLive')).getByRole('checkbox', { name: 'Ad gems On' }));
    await page.user.click(
      within(region('runsLive')).getByRole('button', { name: 'Save changes to this battle' }),
    );
    await page.advance(0);
    const [write] = page.fake.writes();
    expect(write?.path).toBe(recorded.request.path);
    expect(JSON.parse(write?.body ?? 'null')).toEqual(recorded.request.body);
    return page;
  }

  it('RUN-08 accepted_switch_off: the page sends the recorded body and key; the 200 is recorded and the next read waits for it to apply', async () => {
    let read = live('read_before_the_first_change');
    const page = await sendSwitchOff(() => read);
    read = live('read_after_the_change_lists_the_command_and_its_key');
    page.fake.next(/live-controls$/u, 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    const region_ = region('runsLive');
    expect(within(region_).getByText('Sent to this battle - waiting to apply')).toBeVisible();
    expect(
      within(region_).getByRole('button', { name: 'Save changes to this battle' }),
    ).toHaveAccessibleDescription('Waiting for the last change to apply');
    expect(page.fake.writes()).toHaveLength(1);
  });

  it.each(['resend', 'read'] as const)(
    'RUN-10 SR-4 SR-8 admitted_then_the_readback_failed_unknown, then the %s: unconfirmed, never resent on its own, settled',
    async (way) => {
      let read = live('read_before_the_first_change');
      const page = await sendSwitchOff(() => read);
      const unknown = live('admitted_then_the_readback_failed_unknown');
      page.fake.next(/live-controls$/u, 'POST').respond(unknown.status, unknown.body);
      await page.advance(1_000);
      const box = () => region('runsLive');
      expect(
        within(box()).getByText("The reply didn't confirm the change - it may have been recorded"),
      ).toBeVisible();
      expect(box()).toHaveTextContent((unknown.body as { error: string }).error);
      await page.advance(60_000);
      expect(page.fake.writes()).toHaveLength(1);
      if (way === 'resend') {
        await page.user.click(
          within(box()).getByRole('button', { name: 'Send same request again' }),
        );
        await page.advance(0);
        const writes = page.fake.writes();
        expect(writes).toHaveLength(2);
        expect(writes[1]?.body).toBe(writes[0]?.body);
        // The backend answers an identical retry from its record.
        page.fake.next(/live-controls$/u, 'POST').respond(recorded.status, recorded.body);
        await page.advance(1_000);
        expect(within(box()).getByText('Sent to this battle - waiting to apply')).toBeVisible();
      } else {
        read = live('read_after_the_change_lists_the_command_and_its_key');
        await page.user.click(within(box()).getByRole('button', { name: 'Check this battle' }));
        await page.advance(1_000);
        expect(within(box()).getByText('A later read lists this change')).toBeVisible();
        expect(page.fake.writes()).toHaveLength(1);
      }
      expect(within(box()).queryByRole('button', { name: 'Send same request again' })).toBeNull();
    },
  );

  it.each([
    'stale_control_revision_refused',
    'second_change_while_the_first_is_not_applied_refused',
    'stale_generation_refused',
    'new_key_refused_the_battle_has_ended',
    'unknown_battle_refused',
    'action_lock_another_action_in_progress',
  ])(
    'RUN-10 %s: nothing was recorded, with the server reason; the draft stays',
    async (refusal) => {
      const page = await sendSwitchOff(() => live('read_before_the_first_change'));
      const answer = live(refusal);
      page.fake.next(/live-controls$/u, 'POST').respond(answer.status, answer.body);
      await page.advance(1_000);
      expect(within(region('runsLive')).getByText('Not sent - nothing was recorded')).toBeVisible();
      expect(region('runsLive')).toHaveTextContent((answer.body as { error: string }).error);
      expect(
        within(region('runsLive')).getByRole('checkbox', { name: 'Ad gems On' }),
      ).not.toBeChecked();
      await page.advance(60_000);
      expect(page.fake.writes()).toHaveLength(1);
    },
  );
});

describe('Runs review fixes (frontend-f6-runs-review F6R-01 to F6R-04)', () => {
  const RUN = /^\/api\/run-controls\?/u;
  const BATTLE = /^\/api\/battles\/active$/u;
  const KEY = (live('accepted_switch_off').request.body as { idempotency_key: string })
    .idempotency_key;
  const UNCONFIRMED = "The reply didn't confirm the change - it may have been recorded";
  const box = () => region('runsLive');
  const writesTo = (page: Awaited<ReturnType<typeof openRuns>>, end: string) =>
    page.fake.writes().filter((write) => write.path.endsWith(end));

  /** A page reload: the old page goes, a new one starts on the same session storage. */
  async function reload(
    page: Awaited<ReturnType<typeof openRuns>>,
    hash: string,
    options: PageOptions,
  ) {
    page.view.unmount();
    page.data.stop();
    return openRuns(hash, { ...options, storage: page.storage });
  }

  async function sendLiveOff(read: () => DashboardCase | 'hold', options: PageOptions = {}) {
    const page = await openRuns('#runs/live', {
      before: (fake) => {
        fake.route('GET', BATTLE, () => {
          const found = read();
          return found === 'hold' ? 'hold' : answerOf(found);
        });
      },
      newKey: () => KEY,
      ...options,
    });
    await page.user.click(within(box()).getByRole('checkbox', { name: 'Ad gems On' }));
    await page.user.click(
      within(box()).getByRole('button', { name: 'Save changes to this battle' }),
    );
    await page.advance(0);
    return page;
  }

  it.each(['action_lock_another_action_in_progress', 'stale_control_revision_refused'])(
    'RUN-10 SR-4 SR-8 F6R-01 unknown, then an explicit identical retry refused (%s): the original stays unknown with its resend and key; the refusal is shown beside it; a keyed read settles it',
    async (refusal) => {
      let read: () => DashboardCase = () => live('read_before_the_first_change');
      const page = await sendLiveOff(() => read());
      const unknown = live('admitted_then_the_readback_failed_unknown');
      page.fake.next(/live-controls$/u, 'POST').respond(unknown.status, unknown.body);
      await page.advance(1_000);
      expect(within(box()).getByText(UNCONFIRMED)).toBeVisible();
      await page.user.click(within(box()).getByRole('button', { name: 'Send same request again' }));
      await page.advance(0);
      const refused = live(refusal);
      page.fake.next(/live-controls$/u, 'POST').respond(refused.status, refused.body);
      await page.advance(1_000);
      const writes = writesTo(page, '/live-controls');
      expect(writes).toHaveLength(2);
      expect(writes[1]?.body).toBe(writes[0]?.body);
      // The first request may still have been recorded: nothing says otherwise.
      expect(within(box()).getByText(UNCONFIRMED)).toBeVisible();
      expect(within(box()).queryByText('Not sent - nothing was recorded')).toBeNull();
      expect(box().querySelector('[data-attempt-refused="true"]')).not.toBeNull();
      expect(box()).toHaveTextContent((refused.body as { error: string }).error);
      expect(within(box()).getByRole('button', { name: 'Send same request again' })).toBeVisible();
      const record = page.data.machine.snapshot().find((entry) => entry.kind === 'live_controls');
      expect(record).toMatchObject({ phase: 'unconfirmed', key: KEY, sends: 2 });
      expect(JSON.stringify(page.storage.entries?.() ?? {})).toContain(KEY);
      await page.advance(60_000);
      expect(writesTo(page, '/live-controls')).toHaveLength(2);
      read = () => live('read_after_the_change_lists_the_command_and_its_key');
      await page.user.click(within(box()).getByRole('button', { name: 'Check this battle' }));
      await page.advance(1_000);
      expect(within(box()).getByText('A later read lists this change')).toBeVisible();
      expect(box().querySelector('[data-attempt-refused="true"]')).toBeNull();
      expect(writesTo(page, '/live-controls')).toHaveLength(2);
    },
  );

  it('RUN-10 F6R-01 control: a refusal of the first and only attempt still says nothing was recorded', async () => {
    const page = await sendLiveOff(() => live('read_before_the_first_change'));
    const refused = live('action_lock_another_action_in_progress');
    page.fake.next(/live-controls$/u, 'POST').respond(refused.status, refused.body);
    await page.advance(1_000);
    expect(within(box()).getByText('Not sent - nothing was recorded')).toBeVisible();
    expect(box().querySelector('[data-attempt-refused="true"]')).toBeNull();
  });

  it('RUN-03 SR-2 SR-4 F6R-02 F6C-R1-02 a held saved save, then a reload: the raw 030 stays, an unchanged read keeps it Unknown, Save opens only by the explicit continue; one POST in all', async () => {
    const options: PageOptions = {
      before: readsFrom(RUN, () => save('read_before_the_first_save')),
    };
    const page = await openRuns('#runs/saved/bp_farm', options);
    const every = () =>
      within(region('runsSaved')).getByRole('textbox', { name: /^Ad gems Every$/u });
    await page.user.clear(every());
    await page.user.type(every(), '030');
    await page.user.click(
      within(region('runsSaved')).getByRole('button', { name: 'Save run settings' }),
    );
    await page.advance(0);
    expect(writesTo(page, '/api/run-controls')).toHaveLength(1);
    const next = await reload(page, '#runs/saved/bp_farm', options);
    await next.advance(1_000);
    expect(every()).toHaveValue('030');
    expect(within(region('runsSaved')).getByRole('button', { name: 'Discard' })).toBeVisible();
    expect(
      within(region('runsSaved')).getByText('No reply - it may or may not have saved'),
    ).toBeVisible();
    const saveRun = () =>
      within(region('runsSaved')).getByRole('button', { name: 'Save run settings' });
    expect(saveRun()).toHaveAttribute('aria-disabled', 'true');
    await next.user.click(
      within(region('runsSaved')).getByRole('button', {
        name: 'Continue from the saved values as read',
      }),
    );
    await next.advance(0);
    expect(saveRun()).not.toHaveAttribute('aria-disabled');
    expect(
      within(region('runsSaved')).getByText('No reply - it may or may not have saved'),
    ).toBeVisible();
    // The continue is kept with the record across a reload; still nothing is sent.
    const again = await reload(next, '#runs/saved/bp_farm', options);
    await again.advance(1_000);
    expect(region('runsSaved').querySelector('[data-accepted]')).not.toBeNull();
    await again.advance(60_000);
    expect(again.fake.writes()).toEqual([]);
    expect(next.fake.writes()).toEqual([]);
  });

  it('RUN-07 SR-2 SR-4 F6R-02 a saved save answered by a text 500 while reads fail, then a reload: Unknown, the raw 0600 and the switch stay, and newer invalid typing too; nothing is resent', async () => {
    let read: () => DashboardCase | 'fail' = () => save('read_before_the_unanswered_save');
    const options: PageOptions = {
      before: (fake) => {
        fake.route('GET', RUN, () => {
          const found = read();
          return found === 'fail' ? { status: 502, body: 'Bad gateway' } : answerOf(found);
        });
      },
    };
    const page = await openRuns('#runs/saved/bp_farm', options);
    const saved = () => region('runsSaved');
    const every = () => within(saved()).getByRole('textbox', { name: /^Ad gems Every$/u });
    await page.user.click(within(saved()).getByRole('checkbox', { name: 'Ad gems On' }));
    await page.user.clear(every());
    await page.user.type(every(), '0600');
    await page.user.click(within(saved()).getByRole('button', { name: 'Save run settings' }));
    await page.advance(0);
    // Newer typing while the save is unresolved: invalid, and kept as typed.
    const window = () => within(saved()).getByRole('textbox', { name: /^Ad gems Window$/u });
    await page.user.clear(window());
    await page.user.type(window(), '1e3');
    read = () => 'fail';
    const failed = answerOf(save('saved_then_the_answer_failed_500'));
    page.fake.next('/api/run-controls', 'POST').respond(failed.status, failed.body);
    await page.advance(30_000);
    const next = await reload(page, '#runs/saved/bp_farm', options);
    await next.advance(30_000);
    expect(
      within(saved()).getByText("The reply didn't confirm the save - it may or may not have saved"),
    ).toBeVisible();
    expect(next.fake.writes()).toEqual([]);
    // The unchanged read: nothing was written; the person's values are all still there.
    read = () => save('read_after_the_failed_write_the_revision_is_unchanged');
    await next.user.click(within(saved()).getByRole('button', { name: 'Check saved values' }));
    await next.advance(1_000);
    expect(every()).toHaveValue('0600');
    expect(window()).toHaveValue('1e3');
    expect(within(saved()).getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
    expect(next.fake.writes()).toEqual([]);
  });

  it('RUN-10 SR-2 SR-4 F6R-02 an unknown live change, then a reload: the switch stays off, Unknown and the resend stay; one POST in all', async () => {
    const read = () => live('read_before_the_first_change');
    const page = await sendLiveOff(read);
    const unknown = live('admitted_then_the_readback_failed_unknown');
    page.fake.next(/live-controls$/u, 'POST').respond(unknown.status, unknown.body);
    await page.advance(30_000);
    const next = await reload(page, '#runs/live', {
      before: readsFrom(BATTLE, read),
      newKey: () => KEY,
    });
    await next.advance(30_000);
    expect(within(box()).getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
    expect(within(box()).getByText(UNCONFIRMED)).toBeVisible();
    expect(within(box()).getByRole('button', { name: 'Send same request again' })).toBeVisible();
    expect(next.fake.writes()).toEqual([]);
  });

  it('RUN-07 SR-4 F6R-03 the recorded committed-save 409 is followed by the read that holds every sent value: the line says the saved settings now have your changes; nothing is resent', async () => {
    let read = save('read_before_the_unanswered_save');
    const page = await openRuns('#runs/saved/bp_farm', { before: readsFrom(RUN, () => read) });
    const saved = () => region('runsSaved');
    await page.user.click(within(saved()).getByRole('checkbox', { name: 'Ad gems On' }));
    const every = within(saved()).getByRole('textbox', { name: /^Ad gems Every$/u });
    await page.user.clear(every);
    await page.user.type(every, '600');
    await page.user.click(within(saved()).getByRole('button', { name: 'Save run settings' }));
    await page.advance(0);
    const conflict = save('identical_resend_after_a_committed_save_is_refused_as_stale');
    expect(postBodies(page.fake, '/api/run-controls')).toEqual([conflict.request.body]);
    read = save('read_after_the_unanswered_save_holds_every_sent_value');
    page.fake.next('/api/run-controls', 'POST').respond(conflict.status, conflict.body);
    await page.advance(1_000);
    expect(saved().querySelector('.tp-run-outcome')).toHaveAttribute('data-outcome', 'applied');
    expect(within(saved()).getByText('The saved settings now have your changes')).toBeVisible();
    expect(within(saved()).queryByText('Not saved')).toBeNull();
    expect(within(saved()).queryByRole('button', { name: 'Discard' })).toBeNull();
    await page.advance(60_000);
    expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
  });

  it('RUN-06 SR-5 F6R-03 control: a 409 whose read shows another client saved first says so and keeps the draft', async () => {
    let read = save('read_before_the_conflicts');
    const page = await openRuns('#runs/saved/bp_farm', { before: readsFrom(RUN, () => read) });
    const saved = () => region('runsSaved');
    await page.user.click(within(saved()).getByRole('checkbox', { name: 'Ad gems On' }));
    await page.user.click(within(saved()).getByRole('button', { name: 'Save run settings' }));
    await page.advance(0);
    const conflict = save('stale_revision_another_client_saved_first');
    expect(postBodies(page.fake, '/api/run-controls')).toEqual([conflict.request.body]);
    read = save('read_after_the_stale_refusal_holds_the_other_clients_save');
    page.fake.next('/api/run-controls', 'POST').respond(conflict.status, conflict.body);
    await page.advance(1_000);
    expect(
      within(saved()).getByText('Not saved - the saved run changed since you read it'),
    ).toBeVisible();
    expect(within(saved()).getByRole('checkbox', { name: 'Ad gems On' })).not.toBeChecked();
    expect(within(saved()).getByRole('button', { name: 'Save run settings' })).not.toHaveAttribute(
      'aria-disabled',
    );
    await page.advance(60_000);
    expect(postBodies(page.fake, '/api/run-controls')).toHaveLength(1);
  });

  it('RUN-10 SR-4 F6R-04 an unknown live change, then the battle ends: its line stays, names that battle and keeps its resend; the identical retry is answered from the record', async () => {
    let read: () => DashboardCase = () => live('read_before_the_first_change');
    const page = await sendLiveOff(() => read());
    const unknown = live('admitted_then_the_readback_failed_unknown');
    page.fake.next(/live-controls$/u, 'POST').respond(unknown.status, unknown.body);
    await page.advance(1_000);
    read = () => dashboardCase('battles_active.null_no_driver');
    await page.user.click(within(box()).getByRole('button', { name: 'Check this battle' }));
    await page.advance(1_000);
    expect(
      within(box()).getByText(
        'No tracked battle - live settings appear here while automation watches a battle',
      ),
    ).toBeVisible();
    const earlier = box().querySelector('[data-earlier-battle="true"]');
    expect(earlier).toHaveTextContent('Change to another battle:');
    expect(within(box()).getByText(UNCONFIRMED)).toBeVisible();
    expect(within(box()).queryByRole('checkbox')).toBeNull();
    await page.advance(60_000);
    expect(writesTo(page, '/live-controls')).toHaveLength(1);
    await page.user.click(within(box()).getByRole('button', { name: 'Send same request again' }));
    await page.advance(0);
    const writes = writesTo(page, '/live-controls');
    expect(writes).toHaveLength(2);
    expect(writes[1]?.path).toBe(writes[0]?.path);
    expect(writes[1]?.body).toBe(writes[0]?.body);
    const ended = live('identical_retry_after_the_battle_ended_answered_from_the_record');
    page.fake.next(/live-controls$/u, 'POST').respond(ended.status, ended.body);
    await page.advance(1_000);
    expect(within(box()).getByText('That battle recorded the change')).toBeVisible();
    expect(within(box()).queryByRole('button', { name: 'Send same request again' })).toBeNull();
  });

  it('RUN-10 SR-8 F6R-04 Check that battle reads the original battle by id; its record listing the key settles the change; a read without it does not', async () => {
    const shown = live('read_after_the_change_lists_the_command_and_its_key').body as {
      battle: Record<string, unknown>;
    };
    let detail: unknown = { battle: { ...shown.battle, commands: [] }, context: {}, events: [] };
    const page = await sendLiveOff(() => live('read_before_the_first_change'), {
      before: (fake) => {
        fake.route('GET', BATTLE, () => answerOf(live('read_before_the_first_change')));
        fake.route('GET', /^\/api\/battles\/[^/?]+\?instance=main$/u, () => ({
          status: 200,
          body: detail,
        }));
      },
    });
    page.fake.next(/live-controls$/u, 'POST').lose();
    await page.advance(1_000);
    page.fake.route('GET', BATTLE, () => ({
      status: 200,
      body: dashboardCase('battles_active.null_no_driver').body,
    }));
    await page.user.click(within(box()).getByRole('button', { name: 'Check this battle' }));
    await page.advance(1_000);
    const check = () => within(box()).getByRole('button', { name: 'Check that battle' });
    await page.user.click(check());
    await page.advance(1_000);
    const battleId = String(shown.battle['id']);
    expect(
      page.fake.requests.some(
        (request) =>
          request.method === 'GET' && request.path === `/api/battles/${battleId}?instance=main`,
      ),
    ).toBe(true);
    expect(within(box()).getByText('No reply - it may have been recorded')).toBeVisible();
    detail = { battle: shown.battle, context: {}, events: [] };
    await page.user.click(check());
    await page.advance(1_000);
    expect(within(box()).getByText('A later read lists this change')).toBeVisible();
    expect(within(box()).queryByRole('button', { name: 'Send same request again' })).toBeNull();
    expect(writesTo(page, '/live-controls')).toHaveLength(1);
  });
});
