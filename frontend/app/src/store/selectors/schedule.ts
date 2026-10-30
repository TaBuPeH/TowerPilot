import { entries, finite, list, own, ownPath, text } from '../../core/read.ts';
import { scheduleView } from '../../core/schedule.ts';
import { fromServer } from '../../copy/server.ts';
import { currentSchedule, type ScheduleState } from '../schedule.ts';
import { profileRevision, schedulePlan, WEEK_SLOTS } from '../scheduleDraft.ts';
import { viewEqual } from '../store.ts';
import { editingTargetVM } from './editingTarget.ts';

const messages = (value: unknown) =>
  (list(value) ?? []).flatMap((line) => {
    const shown = fromServer('message', line);
    return shown === null ? [] : [shown];
  });
export function selectSchedule(state: ScheduleState, now = Date.now()) {
  const e = currentSchedule(state);
  const intent = e?.intent ?? null;
  const observation = e?.observed;
  const serverTime = finite(own(observation, 'server_time'));
  const observed = finite(own(observation, 'observed_at'));
  const budget = finite(own(observation, 'fresh_for_sec'));
  const age =
    e?.observedAtMs === null || e?.observedAtMs === undefined
      ? null
      : Math.max(0, (now - e.observedAtMs) / 1000);
  const fresh =
    age !== null &&
    serverTime !== undefined &&
    observed !== undefined &&
    budget !== undefined &&
    serverTime - observed + age <= budget;
  const projection = scheduleView(
    observation === undefined ? null : { kind: 'answered', body: observation, sameContext: true },
  );
  const winnerReported =
    projection.line === 'winner_next_check' || projection.line === 'winner_no_clock_check';
  const noWinnerReported =
    projection.line === 'no_eligible_block' || projection.line === 'no_blocks_today';
  const available =
    fresh &&
    e?.observationFailed === false &&
    own(observation, 'available') === true &&
    (winnerReported || noWinnerReported);
  const current = own(observation, 'winner');
  const reason =
    text(own(observation, 'reason')) ??
    (own(observation, 'error') === 'Unknown emulator' ? 'unknown_emulator' : null);
  const sourceProfile = own(e?.source, 'profile');
  const rawProblems = [
    ...(list(own(intent?.answer?.body, 'problems')) ?? []),
    own(intent?.answer?.body, 'error'),
  ].filter((value): value is string => typeof value === 'string');
  const draft = e?.draft ?? null;
  const pending =
    intent?.phase === 'sending' ||
    (intent?.phase === 'unconfirmed' &&
      intent.readSettlement === null &&
      e?.resolvedIntent !== intent.id);
  const partial =
    intent?.phase === 'confirmed' &&
    intent.answer !== null &&
    !viewEqual(
      ownPath(intent.answer.body, 'profile', 'plan'),
      own(JSON.parse(intent.request.body), 'value'),
    );
  // F6-11 RL8: the newest request removed the whole plan section ({op:"delete"}).
  const removal = intent !== null && own(JSON.parse(intent.request.body), 'op') === 'delete';
  // F6-11-P2-04: a removal says removal, never "save", in every state.
  const outcome:
    | 'readApplied'
    | 'readRemoved'
    | 'readUnchanged'
    | 'sending'
    | 'removeSending'
    | 'unknown'
    | 'removeUnknown'
    | 'refused'
    | 'contextChanged'
    | 'removeRefused'
    | 'partial'
    | 'confirmed'
    | 'removed'
    | null =
    intent?.readSettlement === 'applied'
      ? removal
        ? 'readRemoved'
        : 'readApplied'
      : intent?.readSettlement === 'not_applied'
        ? 'readUnchanged'
        : intent?.phase === 'sending'
          ? removal
            ? 'removeSending'
            : 'sending'
          : intent?.phase === 'unconfirmed'
            ? removal
              ? 'removeUnknown'
              : 'unknown'
            : intent?.phase === 'refused'
              ? own(intent.answer?.body, 'code') === 'context_changed'
                ? 'contextChanged'
                : removal
                  ? 'removeRefused'
                  : 'refused'
              : partial
                ? 'partial'
                : intent?.phase === 'confirmed'
                  ? removal
                    ? 'removed'
                    : 'confirmed'
                  : null;
  const observationState:
    'unknownEmulator' | 'stale' | 'notRunning' | 'noWinner' | 'unavailable' | null =
    reason === 'unknown_instance' || reason === 'unknown_emulator'
      ? 'unknownEmulator'
      : projection.line === 'last_seen' ||
          (observed !== undefined && (!fresh || e?.observationFailed === true))
        ? 'stale'
        : [
              'not_seen_running',
              'scheduler_run_unconfirmed',
              'scheduler_stopping',
              'scheduler_starting',
            ].includes(projection.line)
          ? 'notRunning'
          : available && noWinnerReported
            ? 'noWinner'
            : available
              ? null
              : 'unavailable';
  const saveUnavailable:
    'clean' | 'reading' | 'checking' | 'sending' | 'unknown' | 'reconcile' | 'revision' =
    e?.baseRevision === null || e === null || e.readError
      ? 'revision'
      : e.busy
        ? 'reading'
        : e.checking
          ? 'checking'
          : intent?.phase === 'sending'
            ? 'sending'
            : pending
              ? 'unknown'
              : e.reconcile
                ? 'reconcile'
                : 'clean';
  const removeBlocked:
    'dirty' | 'revision' | 'reading' | 'checking' | 'sending' | 'unknown' | 'reconcile' | null =
    state.target.blocked !== null
      ? 'revision'
      : e === null || e.dirty
        ? 'dirty'
        : e.baseRevision === null || e.readError
          ? 'revision'
          : e.busy
            ? 'reading'
            : e.checking
              ? 'checking'
              : intent?.phase === 'sending'
                ? 'sending'
                : pending
                  ? 'unknown'
                  : e.reconcile
                    ? 'reconcile'
                    : null;
  return {
    target: editingTargetVM(state.target),
    sourceError: fromServer('message', e?.sourceError),
    activeProfile: fromServer('name', state.context?.profile),
    entry: e,
    profile: fromServer('name', state.target.name),
    instance: fromServer('name', state.context?.instance),
    account: fromServer('name', state.context?.account),
    noProfile: state.context !== null && state.target.name === null,
    canAdd:
      (draft?.days[e?.day ?? '']?.[0] ??
        list(ownPath(sourceProfile, 'plan', 'days', e?.day ?? ''))?.[0]) !== undefined,
    revision: fromServer('id', profileRevision(e?.source)),
    baseRevision: fromServer('id', e?.baseRevision),
    savedPlan:
      own(sourceProfile, 'plan') === undefined
        ? null
        : fromServer('detail', JSON.stringify(own(sourceProfile, 'plan'), null, 2)),
    dayNames: Object.keys(draft?.days ?? {}).map((key) => ({
      key,
      label: fromServer('name', key),
    })),
    blocks: (draft?.days[e?.day ?? ''] ?? []).map((block, index) => {
      const path = `plan.days.${e?.day ?? ''}[${index}]`;
      return {
        ...block,
        index,
        invalid: {
          block: rawProblems.some((problem) => problem.includes(`${path}.block`)),
          blueprint: rawProblems.some((problem) => problem.includes(`${path}.blueprint`)),
          count: rawProblems.some((problem) => problem.includes(`${path}.count`)),
          after: rawProblems.some(
            (problem) => problem.includes(`${path}.after`) || problem.includes(`${path}: until`),
          ),
          until: rawProblems.some(
            (problem) => problem.includes(`${path}.until`) || problem.includes(`${path}: until`),
          ),
        },
      };
    }),
    invalidWeek: Object.fromEntries(
      WEEK_SLOTS.map((slot) => [
        slot,
        rawProblems.some((problem) => problem.includes(`plan.week.${slot}:`)),
      ]),
    ),
    runs: entries(own(sourceProfile, 'blueprints')).map(([key]) => ({
      key,
      label: fromServer('name', key),
    })),
    empty: draft !== null && Object.values(draft.days).every((blocks) => blocks.length === 0),
    canSave:
      state.target.name !== null &&
      state.target.blocked === null &&
      e !== null &&
      e.dirty &&
      !e.busy &&
      !e.checking &&
      !e.readError &&
      !e.reconcile &&
      !pending &&
      e.baseRevision !== null &&
      draft !== null,
    pending,
    partial,
    saveUnavailable,
    outcome,
    /** F6-11 RL8: the newest request of this target removed the plan section. */
    removal,
    /** F6-11-P2-01: a click-time context check is running; why the last click sent nothing. */
    checking: e?.checking === true,
    notSent: e?.notSent ?? null,
    error: fromServer('message', own(intent?.answer?.body, 'error')),
    currentContext:
      outcome === 'contextChanged'
        ? fromServer(
            'detail',
            JSON.stringify(own(intent?.answer?.body, 'current_context'), null, 2),
          )
        : null,
    problems: [
      ...messages(own(e?.source, 'problems')),
      ...messages(own(intent?.answer?.body, 'problems')),
      ...messages([own(e?.source, 'validator_error')]),
    ],
    warnings: messages(own(e?.source, 'warnings')),
    hasDraftPlan: draft !== null,
    /** F6-11 RL8: the read profile has no plan and no draft started one. */
    /**
     * F6-11 RL8: the read profile has a plan section the person may remove; `removeBlocked`
     * says why Remove is unavailable now (unsaved changes first, then the Save reasons).
     */
    planSaved: own(sourceProfile, 'plan') !== undefined && draft !== null,
    removeBlocked,
    planMissing:
      draft === null &&
      e !== null &&
      !e.busy &&
      own(e.source, 'ok') === true &&
      own(own(e.source, 'profile'), 'plan') === undefined,
    submittedDiffers:
      draft !== null && !viewEqual(schedulePlan(draft), ownPath(e?.source, 'profile', 'plan')),
    observationState,
    winner: available ? fromServer('name', own(current, 'label')) : null,
    previousWinner: !available
      ? fromServer('name', ownPath(observation, 'observed_winner', 'label'))
      : null,
    observedAt: observed ?? null,
    timezone: fromServer('detail', own(observation, 'timezone')),
    offset: fromServer('detail', own(observation, 'utc_offset')),
    nextCheck:
      available &&
      ownPath(observation, 'next_clock_boundary', 'kind') === 'clock_proposal' &&
      ownPath(observation, 'next_clock_boundary', 'guarantees_handoff') === false
        ? fromServer('detail', ownPath(observation, 'next_clock_boundary', 'local_time'))
        : null,
    reason: fromServer('message', reason),
  };
}
export type ScheduleView = ReturnType<typeof selectSchedule>;
