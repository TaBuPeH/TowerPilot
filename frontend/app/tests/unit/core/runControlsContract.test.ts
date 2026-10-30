import { describe, expect, it } from 'vitest';
import { RESENDABLE } from '../../../src/api/intents/machine.ts';
import { ENDPOINTS, parseAnswer, type EndpointId } from '../../../src/api/wire.ts';
import { commandAnswer, settleCommand } from '../../../src/core/routines.ts';
import { savedSaveAnswer, settleSavedSave, type SentSave } from '../../../src/core/runControls.ts';
import { opaqueToken } from '../../../src/core/tokens.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from '../../support/dashboardFixture.ts';

/**
 * The Runs page's rules against every answer the backend recorded for them
 * (backend/tests/fixtures/dashboard_contract_pages_v1.json, families
 * run_controls_save and run_controls_live), each parsed at the app's wire
 * boundary first, as the page receives it.
 */
const SAVE = 'run_controls_save';
const LIVE = 'run_controls_live';

function wire(endpoint: EndpointId, found: DashboardCase) {
  const body = found.body as Record<string, unknown>;
  const text =
    Object.keys(body).length === 1 && typeof body['text'] === 'string'
      ? body['text']
      : JSON.stringify(body);
  return parseAnswer(ENDPOINTS[endpoint], found.status, text);
}

function cases(family: string, method: 'GET' | 'POST'): readonly string[] {
  return dashboardPagesCaseNames(family).filter(
    (name) => dashboardPagesCase(`${family}.${name}`).request.method === method,
  );
}

const PLAIN_PATH = /^routines\.[^.]+\.[^.]+$/u;

/** What the page records for a save whose body has the page's own form, else null. */
function pageSave(body: unknown): SentSave | null {
  if (typeof body !== 'object' || body === null) return null;
  const { blueprint, expected_revision: revision, changes } = body as Record<string, unknown>;
  if (typeof blueprint !== 'string' || blueprint.startsWith('bp_')) return null;
  if (typeof revision !== 'string' || typeof changes !== 'object' || changes === null) return null;
  const entries = Object.entries(changes);
  if (
    entries.length === 0 ||
    !entries.every(
      ([path, value]) =>
        PLAIN_PATH.test(path) &&
        (typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))),
    )
  )
    return null;
  return {
    blueprint,
    profileRevision: revision,
    changes: Object.fromEntries(entries),
  };
}

describe('the pages contract at the wire boundary', () => {
  it.each([
    ...cases(SAVE, 'POST').map((name) => [`${SAVE}.${name}`, 'runControlsSave'] as const),
    ...cases(SAVE, 'GET').map((name) => [`${SAVE}.${name}`, 'runControls'] as const),
    ...cases(LIVE, 'POST').map((name) => [`${LIVE}.${name}`, 'liveControls'] as const),
    ...cases(LIVE, 'GET').map((name) => [`${LIVE}.${name}`, 'battlesActive'] as const),
  ])('%s is an exact %s answer and keeps every member', (path, endpoint) => {
    const parsed = wire(endpoint, dashboardPagesCase(path));
    expect(parsed.contract).toBe('exact');
    expect(parsed.issues).toEqual([]);
  });
});

describe('RUN-05 RUN-06 RUN-07 every recorded saved-run save answer', () => {
  it.each(cases(SAVE, 'POST'))('RUN-07 %s is classified by its status and receipt', (name) => {
    const found = dashboardPagesCase(`${SAVE}.${name}`);
    const sent = pageSave(found.request.body);
    const answer = savedSaveAnswer(wire('runControlsSave', found), sent ?? {});
    if (found.status === 500) expect(answer).toEqual({ kind: 'unknown' });
    else if (found.status === 400 || found.status === 409) expect(answer.kind).toBe('refused');
    else if (sent === null) expect(answer).toEqual({ kind: 'unknown' });
    else
      expect(answer.kind).toBe(
        name === 'saved_switches_and_numbers_in_one_request' ? 'saved_partly' : 'saved',
      );
  });

  it('RUN-07 SR-2 a 200 that saved the run but kept shopping off (no shopping queue) is saved in part: only the held paths are kept', () => {
    const found = dashboardPagesCase(`${SAVE}.saved_switches_and_numbers_in_one_request`);
    const sent = pageSave(found.request.body);
    if (sent?.changes === undefined) throw new Error('not the page form');
    expect(savedSaveAnswer(wire('runControlsSave', found), sent)).toEqual({
      kind: 'saved_partly',
      profileRevision: (found.body as { profile_revision: string }).profile_revision,
      kept: Object.keys(sent.changes).filter((path) => path !== 'routines.shopping.enabled'),
    });
  });

  it('RUN-07 the 200s the page cannot prove are exactly the forms it never sends (nested, preset id, null, shopping plan)', () => {
    const unproved = cases(SAVE, 'POST').filter((name) => {
      const found = dashboardPagesCase(`${SAVE}.${name}`);
      return found.status === 200 && pageSave(found.request.body) === null;
    });
    expect(unproved).toEqual([
      'saved_null_returns_a_number_to_its_default',
      'saved_run_given_as_the_preset_id',
      'saved_shopping_plan_v2',
      'saved_with_the_nested_form_the_run_key_and_the_patch_key',
    ]);
  });

  it('RUN-06 the action lock refusal keeps its code', () => {
    const found = dashboardPagesCase(`${SAVE}.action_lock_another_action_in_progress`);
    expect(savedSaveAnswer(wire('runControlsSave', found), {})).toEqual({
      kind: 'refused',
      code: 'action_lock',
      conflict: true,
    });
  });

  /** A save of `changes` made against the read `read` (backend/tests/recording/dashboard_contract_cases_runs.py). */
  function sentAt(read: string, changes: Record<string, boolean | number>): SentSave {
    const body = dashboardPagesCase(`${SAVE}.${read}`).body as {
      blueprint: string;
      profile_revision: string;
    };
    return { blueprint: body.blueprint, profileRevision: body.profile_revision, changes };
  }
  function sentBy(post: string): SentSave {
    const sent = pageSave(dashboardPagesCase(`${SAVE}.${post}`).request.body);
    if (sent === null) throw new Error(`${post} is not the page's form`);
    return sent;
  }

  // F6C-R1-02 (root decision a): a read settles a lost save only on positive evidence; an
  // unchanged revision or a newer one without the sent values keeps it Unknown (null).
  it.each([
    [
      'a write that failed before the file changed (text 500)',
      () => sentBy('write_failed_nothing_saved_500'),
      'read_after_the_failed_write_the_revision_is_unchanged',
      null,
    ],
    [
      'a save written before its answer failed (text 500)',
      () => sentBy('saved_then_the_answer_failed_500'),
      'read_after_the_unanswered_save_holds_every_sent_value',
      'applied',
    ],
    [
      'a save whose answer was lost',
      () => sentAt('read_before_the_lost_answer', { 'routines.ad_gems.enabled': false }),
      'read_after_a_lost_answer_the_save_is_there',
      'applied',
    ],
    [
      'a save whose request was lost',
      () => sentAt('read_before_the_lost_request', { 'routines.ad_gems.enabled': false }),
      'read_after_a_lost_request_nothing_was_saved',
      null,
    ],
    [
      "a lost save overtaken by another client's save",
      () => sentAt('read_before_the_other_clients_save', { 'routines.ad_gems.enabled': false }),
      'read_after_a_lost_answer_a_new_revision_without_the_sent_values',
      null,
    ],
  ] as const)(
    'RUN-07 SR-8 F6C-R1-02 %s is settled by the read %s (null: stays Unknown)',
    (_label, sent, read, settled) => {
      const body = wire('runControls', dashboardPagesCase(`${SAVE}.${read}`)).body;
      const save = sent();
      if (save.blueprint === undefined || save.profileRevision === undefined || !save.changes)
        throw new Error('incomplete save');
      expect(
        settleSavedSave(body, {
          blueprint: save.blueprint,
          profileRevision: save.profileRevision,
          changes: save.changes,
        }),
      ).toBe(settled);
    },
  );

  it('SR-4 RUN-07 an identical resend of a committed save is refused as stale, so a saved-run save is never resendable', () => {
    const first = dashboardPagesCase(`${SAVE}.saved_then_the_answer_failed_500`);
    const again = dashboardPagesCase(
      `${SAVE}.identical_resend_after_a_committed_save_is_refused_as_stale`,
    );
    expect(again.request.body).toEqual(first.request.body);
    expect(
      savedSaveAnswer(wire('runControlsSave', again), sentBy('saved_then_the_answer_failed_500'))
        .kind,
    ).toBe('refused');
    expect(RESENDABLE).not.toContain('run_save');
  });
});

describe('RUN-08 RUN-10 every recorded live change answer', () => {
  it.each(cases(LIVE, 'POST'))('RUN-10 %s is classified by its status and command', (name) => {
    const found = dashboardPagesCase(`${LIVE}.${name}`);
    const parsed = wire('liveControls', found);
    const answer = commandAnswer(
      { kind: 'answered', status: parsed.status, body: parsed.body },
      'controls',
    );
    const expected =
      found.status === 200
        ? 'recorded'
        : [400, 404, 409].includes(found.status)
          ? 'not_recorded'
          : 'unknown';
    expect(answer.kind).toBe(expected);
  });

  function identity(read: string) {
    const battle = (
      dashboardPagesCase(`${LIVE}.${read}`).body as {
        battle: { id: string; generation: string; operation_id: string };
      }
    ).battle;
    return { id: battle.id, generation: battle.generation, operationId: battle.operation_id };
  }
  function keyOf(post: string): string {
    const body = dashboardPagesCase(`${LIVE}.${post}`).request.body as { idempotency_key: string };
    return body.idempotency_key;
  }
  function settled(read: string, key: string, shown = identity(read)) {
    const parsed = wire('battlesActive', dashboardPagesCase(`${LIVE}.${read}`));
    const token = opaqueToken(key);
    if (token === null) throw new Error('no key');
    return settleCommand(
      { kind: 'answered', body: parsed.body, sameContext: true },
      shown,
      token,
      'controls',
    ).kind;
  }

  it('RUN-10 SR-8 a read listing the key settles the change; the unknown answer is settled the same way', () => {
    expect(
      settled('read_after_the_change_lists_the_command_and_its_key', keyOf('accepted_switch_off')),
    ).toBe('recorded');
    expect(
      settled(
        'read_after_the_unknown_lists_the_key',
        keyOf('admitted_then_the_readback_failed_unknown'),
      ),
    ).toBe('recorded');
  });

  it('RUN-10 a read without the key, with another key, or for another generation settles nothing', () => {
    const key = keyOf('accepted_switch_off');
    expect(settled('read_after_a_refusal_lists_no_command', key)).toBe('unknown');
    expect(settled('read_before_the_first_change', key)).toBe('unknown');
    expect(settled('read_after_the_change_lists_the_command_and_its_key', 'another-key')).toBe(
      'unknown',
    );
    const shown = identity('read_after_the_change_lists_the_command_and_its_key');
    expect(
      settled('read_after_the_change_lists_the_command_and_its_key', key, {
        ...shown,
        generation: 'another-generation',
      }),
    ).toBe('unknown');
  });

  it('RUN-10 an identical retry is answered from the record: the same command, recorded', () => {
    const unknown = dashboardPagesCase(`${LIVE}.admitted_then_the_readback_failed_unknown`);
    const retry = dashboardPagesCase(
      `${LIVE}.identical_retry_answered_from_the_record_once_the_readback_works`,
    );
    expect(retry.request).toEqual(unknown.request);
    const parsed = wire('liveControls', retry);
    expect(
      commandAnswer({ kind: 'answered', status: parsed.status, body: parsed.body }, 'controls')
        .kind,
    ).toBe('recorded');
    expect((retry.body as { command: { id: string } }).command.id).toBe(
      (unknown.body as { command_id: string }).command_id,
    );
  });
});
