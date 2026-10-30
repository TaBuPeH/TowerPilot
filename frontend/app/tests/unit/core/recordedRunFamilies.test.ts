import { describe, expect, it } from 'vitest';
import { ENDPOINTS, parseAnswer } from '../../../src/api/wire.ts';
import {
  mixedResetAnswerSchema,
  mixedResetReadAnswerSchema,
  queueBoundsSaveAnswerSchema,
  queueBoundsReadAnswerSchema,
  queueBoundsLiveAnswerSchema,
  queueBoundsBattleReadAnswerSchema,
} from '../../../src/contract/r12Families.ts';
import { entries, list, own, ownPath } from '../../../src/core/read.ts';
import {
  liveOverrideLeaves,
  liveResetGate,
  savedSaveAnswer,
} from '../../../src/core/runControls.ts';
import { opaqueToken } from '../../../src/core/tokens.ts';
import { commandAnswer, liveChangeProgress } from '../../../src/core/routines.ts';
import { planHolds, queuePlan, reviewQueue } from '../../../src/core/shoppingPlan.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';
import { bounds, boundsDraft, numericBoundsCases } from '../../support/shoppingBounds.ts';

const mixed = (name: string) => dashboardPagesCase(`run_controls_live_reset_all_mixed.${name}`);
const identity = (body: unknown) => ({
  id: String(ownPath(body, 'battle', 'id')),
  operationId: String(ownPath(body, 'battle', 'operation_id')),
  generation: String(ownPath(body, 'battle', 'generation')),
});

describe('RL2 recorded mixed resets', () => {
  it.each(dashboardPagesCaseNames('run_controls_live_reset_all_mixed'))(
    'RL2 mixed contract and reset leaves: %s',
    (name) => {
      const entry = mixed(name);
      expect(
        parseAnswer(
          entry.request.method === 'POST' ? ENDPOINTS.liveControls : ENDPOINTS.battlesActive,
          entry.status,
          JSON.stringify(entry.body),
        ).contract,
      ).toBe('exact');
      const schema =
        entry.request.method === 'POST' ? mixedResetAnswerSchema : mixedResetReadAnswerSchema;
      expect(schema.safeParse({ status: entry.status, body: entry.body }).success).toBe(true);
      if (entry.request.method === 'POST') {
        expect(commandAnswer({ kind: 'answered', ...entry }, 'controls').kind).toBe(
          entry.status === 409 ? 'not_recorded' : 'recorded',
        );
      }
    },
  );

  it('RL2 partial resets leave exactly the other kind of override', () => {
    const paths = (name: string) =>
      liveOverrideLeaves(own(mixed(name).body, 'battle')).map((leaf) => leaf.path);
    expect(paths('read_after_the_plan_only_reset_the_routine_overrides_remain')).toEqual([
      'routines.shopping.enabled',
      'routines.shopping.interval_sec',
    ]);
    expect(paths('read_after_the_routines_only_reset_the_plan_override_remains')).toEqual([
      'shopping_plan',
    ]);
    const read = own(
      mixed('read_before_the_reset_the_mixed_overrides_are_in_force').body,
      'battle',
    );
    const pathsBefore = liveOverrideLeaves(read).map((leaf) => leaf.path);
    expect(liveResetGate(read, true, pathsBefore, 'none')).toMatchObject({
      enabled: true,
      command: { paths: pathsBefore },
    });
    expect(Object.fromEntries(pathsBefore.map((path) => [path, null]))).toEqual(
      own(mixed('accepted_null_for_shopping_plan_and_routine_overrides').request.body, 'changes'),
    );
  });

  it('RL2 accepted and identical replay wait for matching applied read with every reset leaf gone', () => {
    const answer = mixed('accepted_null_for_shopping_plan_and_routine_overrides').body;
    const applied = mixed('read_after_the_mixed_reset_applied').body;
    const progress = (body: unknown) =>
      liveChangeProgress(
        { kind: 'answered', sameContext: true, body },
        identity(answer),
        answer,
        null,
      );
    expect(progress(mixed('read_after_the_reset_was_accepted_before_it_applies').body)).toBe(
      'waiting',
    );
    expect(progress(applied)).toBe('applied');
    const replay = mixed('identical_resend_of_the_mixed_reset_answered_from_the_record').body;
    expect(ownPath(replay, 'command', 'id')).toBe(ownPath(answer, 'command', 'id'));
    expect(
      liveChangeProgress(
        { kind: 'answered', sameContext: true, body: applied },
        identity(answer),
        replay,
        null,
      ),
    ).toBe('applied');
    expect(
      liveChangeProgress(
        {
          kind: 'answered',
          sameContext: true,
          body: mixed('read_after_the_reset_was_accepted_before_it_applies').body,
        },
        identity(answer),
        replay,
        null,
      ),
    ).toBe('waiting');
    for (const field of ['id', 'generation', 'operationId'] as const) {
      expect(
        liveChangeProgress(
          { kind: 'answered', sameContext: true, body: applied },
          { ...identity(answer), [field]: 'another' },
          answer,
          null,
        ),
      ).toBe('not_listed');
    }
    for (const map of ['overrides', 'applied_overrides']) {
      for (const restored of [
        { shopping_plan: { version: 2 } },
        { routines: { shopping: { enabled: false } } },
      ]) {
        // Fault injection: retain the recorded applied command while restoring one supposedly
        // removed leaf in either map. Inconsistent evidence must not claim Applied.
        const body = {
          ...Object.fromEntries(entries(applied)),
          battle: {
            ...Object.fromEntries(entries(own(applied, 'battle'))),
            [map]: restored,
          },
        };
        expect(progress(body)).toBe('waiting');
      }
    }
    // Fault injection: a read drops the command payload while still claiming application.
    // The retained receipt continues to name every reset path that must be absent.
    const before = own(
      mixed('read_before_the_reset_the_mixed_overrides_are_in_force').body,
      'battle',
    );
    const unreadable = {
      ...Object.fromEntries(entries(applied)),
      battle: {
        ...Object.fromEntries(entries(own(applied, 'battle'))),
        applied_overrides: own(before, 'applied_overrides'),
        commands: [{ ...Object.fromEntries(entries(own(replay, 'command'))), payload: {} }],
      },
    };
    expect(progress(unreadable)).toBe('waiting');
  });

  it.each(['overrides', 'applied_overrides'])(
    'RL2 unreadable %s never proves a reset applied, including contract-drift reads',
    (map) => {
      const answer = mixed('accepted_null_for_shopping_plan_and_routine_overrides').body;
      const applied = mixed('read_after_the_mixed_reset_applied');
      for (const value of [
        undefined,
        null,
        'unreadable',
        [],
        { routines: null },
        { routines: { shopping: [] } },
      ]) {
        // Fault injection: keep the applied command but remove or corrupt a parent map.
        // No such backend recording exists; contract drift must never count as leaf absence.
        const battle = Object.fromEntries(entries(own(applied.body, 'battle')));
        if (value === undefined) Reflect.deleteProperty(battle, map);
        else battle[map] = value;
        const body = { ...Object.fromEntries(entries(applied.body)), battle };
        const parsed = parseAnswer(ENDPOINTS.battlesActive, applied.status, JSON.stringify(body));
        expect(parsed.contract).toBe('drift');
        expect(
          liveChangeProgress(
            { kind: 'answered', sameContext: true, body: parsed.body },
            identity(answer),
            answer,
            null,
          ),
        ).toBe('waiting');
      }
    },
  );
});

describe('SQ5 recorded queue bounds', () => {
  it.each(dashboardPagesCaseNames('shopping_queue_bounds'))(
    'SQ5 recorded contract and answer: %s',
    (name) => {
      const entry = bounds(name);
      const live =
        entry.request.path !== '/api/run-controls' &&
        !entry.request.path.startsWith('/api/run-controls?');
      const endpoint = live
        ? entry.request.method === 'POST'
          ? ENDPOINTS.liveControls
          : ENDPOINTS.battlesActive
        : entry.request.method === 'POST'
          ? ENDPOINTS.runControlsSave
          : ENDPOINTS.runControls;
      expect(parseAnswer(endpoint, entry.status, JSON.stringify(entry.body)).contract).toBe(
        'exact',
      );
      const schema = live
        ? entry.request.method === 'POST'
          ? queueBoundsLiveAnswerSchema
          : queueBoundsBattleReadAnswerSchema
        : entry.request.method === 'POST'
          ? queueBoundsSaveAnswerSchema
          : queueBoundsReadAnswerSchema;
      expect(schema.safeParse({ status: entry.status, body: entry.body }).success).toBe(true);
      if (entry.request.method !== 'POST') return;
      if (live) {
        expect(commandAnswer({ kind: 'answered', ...entry }, 'controls').kind).toBe(
          entry.status === 400 ? 'not_recorded' : 'recorded',
        );
      } else {
        expect(
          savedSaveAnswer(entry, {
            blueprint: 'farm',
            profileRevision: String(own(entry.request.body, 'expected_revision')),
            changes: Object.fromEntries(entries(own(entry.request.body, 'changes'))),
          }).kind,
        ).toBe(entry.status === 400 ? 'refused' : 'saved');
      }
    },
  );

  it.each([...numericBoundsCases('saved'), ...numericBoundsCases('live')])(
    'SQ5 numeric bounds draft sends the exact recorded plan without range validation: %s',
    (name) => {
      const before = bounds('read_before_the_refusals').body;
      const reviewed = reviewQueue(
        { plan: queuePlan(ownPath(before, 'settings', 'shopping_plan')), catalog: [] },
        boundsDraft(name),
      );
      expect(reviewed.problems).toBe(0);
      expect(reviewed.plan).toEqual(ownPath(bounds(name).request.body, 'changes', 'shopping_plan'));
    },
  );

  it('SQ5 refusals leave saved and live state unchanged; accepted boundary values have positive readbacks', () => {
    expect(bounds('read_after_the_refusals_nothing_changed').body).toEqual(
      bounds('read_before_the_refusals').body,
    );
    const live = bounds('read_after_the_live_refusals_nothing_was_recorded').body;
    expect(ownPath(live, 'battle', 'commands')).toEqual([]);
    expect(ownPath(live, 'battle', 'overrides')).toEqual({});
    const accepted = bounds('live_boundary_values_accepted').body;
    expect(
      liveChangeProgress(
        {
          kind: 'answered',
          sameContext: true,
          body: bounds('read_after_the_live_boundary_values_applied').body,
        },
        identity(accepted),
        accepted,
        null,
      ),
    ).toBe('applied');
    expect(
      planHolds(
        ownPath(
          bounds('read_after_the_boundary_values_were_saved').body,
          'settings',
          'shopping_plan',
        ),
        ownPath(bounds('saved_boundary_values_accepted').request.body, 'changes', 'shopping_plan'),
      ),
    ).toBe(true);
  });
});

it.each(['overrides', 'applied_overrides'])(
  'RL2 F6-11-P3-01 lost receipt and incomplete payload still check every submitted leaf in %s',
  (map) => {
    const answer = mixed('accepted_null_for_shopping_plan_and_routine_overrides');
    const applied = mixed('read_after_the_mixed_reset_applied');
    const before = mixed('read_before_the_reset_the_mixed_overrides_are_in_force');
    const paths = entries(own(answer.request.body, 'changes')).map(([path]) => path);
    const progress = (body: unknown) =>
      liveChangeProgress(
        { kind: 'answered', sameContext: true, body },
        identity(applied.body),
        undefined,
        opaqueToken(own(answer.request.body, 'idempotency_key')),
        paths,
      );
    expect(progress(applied.body)).toBe('applied');
    for (const payload of [{}, { shopping_plan: null }]) {
      // Lost receipt; the later command omits some or all of the submitted reset payload.
      const body = {
        ...Object.fromEntries(entries(applied.body)),
        battle: {
          ...Object.fromEntries(entries(own(applied.body, 'battle'))),
          [map]: own(own(before.body, 'battle'), map),
          commands: [
            {
              ...Object.fromEntries(entries(own(answer.body, 'command'))),
              ...Object.fromEntries(
                entries(
                  (list(ownPath(applied.body, 'battle', 'commands')) ?? []).find(
                    (command) => own(command, 'id') === ownPath(answer.body, 'command', 'id'),
                  ),
                ),
              ),
              payload,
            },
          ],
        },
      };
      const parsed = parseAnswer(ENDPOINTS.battlesActive, applied.status, JSON.stringify(body));
      expect(parsed.contract).toBe('exact');
      expect(progress(parsed.body)).toBe('waiting');
    }
  },
);
