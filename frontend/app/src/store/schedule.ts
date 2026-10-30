import type { PageContext } from '../api/context.ts';
import type { IntentRecord } from '../api/intents/types.ts';
import type { ScheduleApi } from '../api/schedule.ts';
import type { TargetCheckResult } from '../api/clickContext.ts';
import { editingTarget, type EditingTarget } from '../core/editingTarget.ts';
import { settleProfilePatch, sentPatch } from '../core/profilePatch.ts';
import { own, text } from '../core/read.ts';
import { createStore, viewEqual, type Store } from './store.ts';
import {
  FIRST_DAY,
  firstPlanDraft,
  planOf,
  profileRevision,
  restoreScheduleDraft,
  scheduleDraft,
  schedulePlan,
  type BlockField,
  type ScheduleDraft,
} from './scheduleDraft.ts';

export interface ScheduleEntry {
  readonly source: unknown;
  readonly observed: unknown;
  readonly observedAtMs: number | null;
  readonly observationFailed: boolean;
  readonly draft: ScheduleDraft | null;
  readonly baseRevision: string | null;
  /** Shared request sequence of the latest authoritative save answer for this target. */
  readonly acceptedSaveSeq: number;
  readonly day: string;
  readonly dirty: boolean;
  readonly version: number;
  readonly busy: boolean;
  readonly readError: boolean;
  readonly sourceError?: unknown;
  readonly intent: IntentRecord | null;
  readonly reconcile: boolean;
  readonly checked: boolean;
  readonly focus: string | null;
  readonly resolvedIntent: string | null;
  /** F6-11-P2-01: a Save or Remove whose click-time context check is still running. */
  readonly checking: boolean;
  /** F6-11-P2-01: why the last Save or Remove of this target was not sent. */
  readonly notSent: Exclude<TargetCheckResult, 'same'> | null;
  readonly recovery?:
    | {
        readonly id: string;
        readonly draft: ScheduleDraft;
        readonly revision: string;
        readonly version: number;
        readonly day: string;
      }
    | undefined;
}
/**
 * F6-11-P2-01: what a Save or Remove click did: `sent` once, nothing at all (`blocked`: the
 * page no longer allows it), or nothing because a config read started after the click could
 * not confirm, or no longer names, the emulator, account and profile the page was drawn for.
 */
export type ScheduleSendResult = 'sent' | 'blocked' | Exclude<TargetCheckResult, 'same'>;
export interface ScheduleState {
  readonly context: PageContext | null;
  readonly target: EditingTarget;
  readonly epoch: number;
  readonly key: string | null;
  readonly entries: Readonly<Record<string, ScheduleEntry>>;
}
export interface ScheduleStore extends Store<ScheduleState> {
  readonly setTarget: (context: PageContext | null, epoch: number, target?: EditingTarget) => void;
  readonly refresh: () => Promise<void>;
  readonly selectDay: (day: string) => void;
  readonly edit: (key: string, field: BlockField, value: string) => void;
  readonly mapWeek: (slot: string, value: string) => void;
  readonly add: () => string | null;
  /**
   * F6-11 RL8: remove the whole plan section of the profile read at `revision` (one request,
   * after the person confirmed). Only from a clean draft, and only after a config read started
   * after the click still names this target (F6-11-P2-01).
   */
  readonly removePlan: (revision: string) => Promise<ScheduleSendResult>;
  /** F6-11 RL8: draft a first plan for a profile that has none (nothing is sent). */
  readonly start: (run: string) => boolean;
  readonly move: (key: string, direction: -1 | 1) => number | null;
  readonly remove: (key: string) => string | null;
  readonly save: () => Promise<ScheduleSendResult>;
  readonly discard: () => void;
  readonly reconcile: () => void;
  readonly rememberFocus: (focus: string) => void;
  readonly dispose: () => void;
}
const empty = (): ScheduleEntry => ({
  source: undefined,
  observed: undefined,
  observedAtMs: null,
  observationFailed: false,
  draft: null,
  baseRevision: null,
  acceptedSaveSeq: 0,
  day: '',
  dirty: false,
  version: 0,
  busy: false,
  readError: false,
  intent: null,
  reconcile: false,
  checked: false,
  focus: null,
  resolvedIntent: null,
  checking: false,
  notSent: null,
});
export const currentSchedule = (state: ScheduleState): ScheduleEntry | null =>
  state.key === null ? null : (state.entries[state.key] ?? null);

/** Owns target-bound drafts, read fences and revision reconciliation; never schedules a run. */
export function createScheduleStore(api: ScheduleApi, now: () => number = Date.now): ScheduleStore {
  const store = createStore<ScheduleState>({
    context: null,
    target: editingTarget(null, undefined, null),
    epoch: 0,
    key: null,
    entries: {},
  });
  let controller: AbortController | null = null;
  let counter = 0;
  const submitted = new Map<
    string,
    { readonly version: number; readonly plan: unknown; readonly remove?: true }
  >();
  const change = (key: string, transform: (entry: ScheduleEntry) => ScheduleEntry) => {
    store.update((s) => ({
      ...s,
      entries: { ...s.entries, [key]: transform(s.entries[key] ?? empty()) },
    }));
    const entry = store.getSnapshot().entries[key];
    if (entry !== undefined)
      api.keepDraft(key, {
        draft:
          (entry.dirty || entry.recovery !== undefined) && entry.draft !== null
            ? entry.draft
            : undefined,
        revision: entry.baseRevision,
        day: entry.day,
        focus: entry.focus,
        recovery: entry.recovery,
        version: entry.version,
      });
  };
  const rememberSubmission = (
    intent: IntentRecord,
    entry: ScheduleEntry,
    plan: unknown,
    remove = false,
  ) => {
    submitted.set(intent.id, { version: entry.version, plan, ...(remove ? { remove: true } : {}) });
    const key = store.getSnapshot().key;
    if (key === null || entry.draft === null || entry.baseRevision === null) return;
    const recovery = {
      id: intent.id,
      draft: entry.draft,
      revision: entry.baseRevision,
      version: entry.version,
      day: entry.day,
    };
    change(key, (current) => ({ ...current, recovery }));
  };
  const activeChange = (transform: (entry: ScheduleEntry) => ScheduleEntry) => {
    const key = store.getSnapshot().key;
    if (key !== null) change(key, transform);
  };
  const editDraft = (transform: (draft: ScheduleDraft, day: string) => ScheduleDraft) => {
    activeChange((entry) =>
      entry.draft === null
        ? entry
        : {
            ...entry,
            draft: transform(entry.draft, entry.day),
            dirty: true,
            version: entry.version + 1,
          },
    );
  };
  const sync = () => {
    const seen = new Set<string>();
    for (const intent of [...api.machine.snapshot()].reverse()) {
      if (
        intent.kind !== 'schedule_save' ||
        intent.context === null ||
        intent.supersededBySeq !== null
      )
        continue;
      const key = api.targetKey(
        intent.context,
        text(own(JSON.parse(intent.request.body), 'name')) ?? intent.context.profile,
      );
      // A target keeps its entry across active-binding changes. Older records from another
      // runtime binding remain history, even when the machine did not supersede them.
      if (seen.has(key)) continue;
      seen.add(key);
      const entry = store.getSnapshot().entries[key];
      if (entry === undefined || entry.intent === intent) continue;
      const sent = submitted.get(intent.id);
      const accepted = intent.phase === 'confirmed' && own(intent.answer?.body, 'ok') === true;
      const exact =
        sent !== undefined &&
        sent.version === entry.version &&
        viewEqual(planOf(intent.answer?.body), sent.plan);
      change(key, (e) => ({
        ...e,
        intent,
        ...(api.draftSettlement(intent) === 'refused' && e.recovery?.id === intent.id
          ? {
              draft: e.version === e.recovery.version ? e.recovery.draft : e.draft,
              day: e.version === e.recovery.version ? e.recovery.day : e.day,
              baseRevision: e.recovery.revision,
              dirty: true,
              recovery: undefined,
            }
          : {}),
        ...(accepted ? { recovery: undefined } : {}),
        ...(accepted
          ? {
              source: intent.answer?.body,
              baseRevision: profileRevision(intent.answer?.body),
              acceptedSaveSeq: Math.max(e.acceptedSaveSeq, intent.answerSeq ?? intent.sentSeq),
            }
          : {}),
        ...(accepted && exact ? { dirty: false } : {}),
        // F6-11 RL8: an accepted removal leaves no plan to edit (Start a day plan returns).
        ...(accepted && exact && sent.remove === true ? { draft: null, day: '' } : {}),
        reconcile:
          e.reconcile ||
          own(intent.answer?.body, 'code') === 'revision_conflict' ||
          (accepted && sent !== undefined && !viewEqual(planOf(intent.answer?.body), sent.plan)),
        checked:
          own(intent.answer?.body, 'code') === 'revision_conflict' || accepted ? false : e.checked,
      }));
    }
  };
  const unsubscribe = api.machine.subscribe(sync);

  const refresh = async () => {
    const state = store.getSnapshot();
    const { context, key, epoch, target } = state;
    if (context === null || key === null || controller !== null) return;
    const request = new AbortController();
    controller = request;
    const started = api.sequence.next();
    change(key, (e) => ({ ...e, busy: true, readError: false }));
    const config = await api.readConfig(request.signal);
    const body = config.kind === 'answered' ? config.answer.body : undefined;
    const correct = api.configMatches(body, context);
    const [profile, status] = correct
      ? await Promise.all([
          target.name === null || target.blocked !== null
            ? Promise.resolve(null)
            : api.readProfile(target.name, request.signal),
          api.readStatus(context, request.signal),
        ])
      : [null, null];
    if (
      controller !== request ||
      store.getSnapshot().key !== key ||
      store.getSnapshot().epoch !== epoch
    )
      return;
    controller = null;
    // Revisions are opaque hashes. Order evidence with the same sequence as writes,
    // so an older read cannot undo an accepted source or its validation metadata.
    const sourceObsolete = started <= (store.getSnapshot().entries[key]?.acceptedSaveSeq ?? 0);
    const source =
      !sourceObsolete &&
      profile?.kind === 'answered' &&
      own(profile.answer.body, 'name') === target.name &&
      own(profile.answer.body, 'ok') === true
        ? profile.answer.body
        : undefined;
    const observation = status?.answer?.body;
    const statusMatches =
      (status?.kind === 'failed' &&
        status.answer?.status === 400 &&
        own(observation, 'error') === 'Unknown emulator') ||
      api.reportMatches(observation, context);
    change(key, (e) => {
      const draft =
        e.dirty || e.recovery !== undefined || source === undefined
          ? e.draft
          : scheduleDraft(own(source, 'profile'));
      return {
        ...e,
        busy: false,
        readError: !correct || (!sourceObsolete && target.name !== null && source === undefined),
        sourceError: !correct || sourceObsolete ? undefined : own(profile?.answer?.body, 'error'),
        ...(source === undefined
          ? {}
          : {
              source,
              draft,
              baseRevision:
                e.dirty || e.recovery !== undefined ? e.baseRevision : profileRevision(source),
              day: e.day || Object.keys(draft?.days ?? {})[0] || '',
              checked: true,
            }),
        observed: statusMatches ? observation : e.observed,
        observedAtMs: statusMatches ? now() : e.observedAtMs,
        observationFailed: !statusMatches,
      };
    });
    const e = store.getSnapshot().entries[key];
    const intent = e?.intent;
    if (
      source !== undefined &&
      intent?.phase === 'unconfirmed' &&
      started > (intent.answerSeq ?? intent.sentSeq)
    ) {
      const sent = sentPatch(JSON.parse(intent.request.body));
      const revision = profileRevision(source);
      // Shared rule (core/profilePatch.ts settleProfilePatch; Collection review F6C-R1-02):
      // only a read of this profile holding the sent plan (or, for a removal, no plan) settles
      // the request; an unchanged revision cannot prove it failed (it may still commit), so it
      // stays unknown until the person reconciles explicitly.
      if (sent !== null && settleProfilePatch(source, sent) === 'applied') {
        api.machine.settleByRead(intent.id, 'applied');
        if (api.draftSettlement(intent, 'applied') === 'pending') return;
        const submittedVersion = submitted.get(intent.id)?.version ?? e?.recovery?.version;
        change(key, (current) => ({
          ...(sent.remove
            ? // F6-11 RL8: a removal is sent only from a clean draft, so a dirty draft now holds
              // edits typed after it (F6-11-P2-03, also when restored after a reload): they stay
              // a draft under this target; only the unchanged draft goes with the plan.
              (
                current.recovery === undefined
                  ? current.dirty
                  : current.version !== submittedVersion
              )
              ? { ...current, baseRevision: revision }
              : { ...current, baseRevision: revision, dirty: false, draft: null, day: '' }
            : {
                ...current,
                baseRevision: revision,
                dirty:
                  submittedVersion !== current.version ||
                  !viewEqual(
                    current.draft === null ? undefined : schedulePlan(current.draft),
                    sent.value,
                  ),
              }),
          recovery: undefined,
        }));
      } else change(key, (current) => ({ ...current, reconcile: true }));
    }
  };

  /** A request of this target is in flight, or its answer was lost and nobody settled it. */
  const unresolved = (e: ScheduleEntry) =>
    e.intent?.phase === 'sending' ||
    (e.intent?.phase === 'unconfirmed' &&
      e.intent.readSettlement === null &&
      e.resolvedIntent !== e.intent.id);
  const canSave = (e: ScheduleEntry) =>
    e.draft !== null &&
    e.dirty &&
    !e.busy &&
    !e.readError &&
    e.baseRevision !== null &&
    !e.reconcile &&
    !unresolved(e);
  /**
   * F6-11-P2-01: the one way a Save or Remove leaves this page. `ready` is checked at the
   * click, again after a config read started after the click (which must still name exactly
   * the target the page was drawn for), and `send` checks it once more at the send boundary.
   * One check per target at a time; a changed or unconfirmed context sends nothing, keeps the
   * draft under its original target and says why (a new click is needed).
   */
  const fencedSend = async (
    send: (context: PageContext, entry: ScheduleEntry) => boolean,
    ready: (entry: ScheduleEntry) => boolean,
  ): Promise<ScheduleSendResult> => {
    const { context, key, epoch, target } = store.getSnapshot();
    const clicked = key === null ? undefined : store.getSnapshot().entries[key];
    if (
      context === null ||
      target.name === null ||
      target.blocked !== null ||
      key === null ||
      clicked === undefined ||
      clicked.checking ||
      !ready(clicked)
    )
      return 'blocked';
    change(key, (e) => ({ ...e, checking: true, notSent: null }));
    let named: TargetCheckResult;
    try {
      if (target.inactive) named = await api.checkTarget(context, target.name);
      else {
        const checked = await api.checkContext(context);
        named =
          checked === 'same'
            ? 'same'
            : checked === 'changed'
              ? 'context_changed'
              : 'context_unconfirmed';
      }
    } finally {
      change(key, (e) => ({ ...e, checking: false }));
    }
    const now = store.getSnapshot();
    const e = now.entries[key];
    if (
      now.key !== key ||
      now.epoch !== epoch ||
      now.target.generation !== target.generation ||
      now.target.blocked !== null ||
      e === undefined
    ) {
      change(key, (current) => ({ ...current, notSent: 'context_changed' }));
      return 'blocked';
    }
    if (named !== 'same') {
      const reason = named;
      change(key, (current) => ({ ...current, notSent: reason }));
      // The page catches up with the server's binding through its own read.
      if (named === 'context_changed') void refresh();
      return reason;
    }
    return send(context, e) ? 'sent' : 'blocked';
  };

  return {
    ...store,
    setTarget: (context, epoch, target = editingTarget(context, undefined, null)) => {
      const key = context === null ? null : api.targetKey(context, target.name);
      const previous = store.getSnapshot();
      if (key === previous.key && epoch === previous.epoch && viewEqual(target, previous.target))
        return;
      controller?.abort();
      controller = null;
      const oldKey = store.getSnapshot().key;
      if (oldKey !== null) change(oldKey, (e) => ({ ...e, busy: false }));
      // Draft scope excludes the active binding; scheduler evidence still belongs to the
      // full runtime context. Clear mismatches atomically with the context/target change.
      store.update((s) => {
        const entries = { ...s.entries };
        const entry = key === null ? undefined : entries[key];
        if (
          key !== null &&
          context !== null &&
          entry !== undefined &&
          !api.reportMatches(entry.observed, context)
        )
          entries[key] = {
            ...entry,
            observed: undefined,
            observedAtMs: null,
            observationFailed: true,
          };
        return { ...s, context, target, epoch, key, entries };
      });
      api.machine.setContext(context, epoch);
      if (key === null || context === null) return;
      if (store.getSnapshot().entries[key] === undefined) {
        const stored = api.loadDraft(key);
        const draft = restoreScheduleDraft(own(stored, 'draft'));
        const rawRecovery = own(stored, 'recovery');
        const recoveryDraft = restoreScheduleDraft(own(rawRecovery, 'draft'));
        const recoveryId = text(own(rawRecovery, 'id'));
        const recoveryRevision = text(own(rawRecovery, 'revision'));
        const recoveryVersion = own(rawRecovery, 'version');
        change(key, () => ({
          ...empty(),
          draft,
          dirty: draft !== null,
          baseRevision: text(own(stored, 'revision')) ?? null,
          day: text(own(stored, 'day')) ?? '',
          focus: text(own(stored, 'focus')) ?? null,
          version: typeof own(stored, 'version') === 'number' ? Number(own(stored, 'version')) : 0,
          recovery:
            recoveryDraft !== null &&
            recoveryId !== undefined &&
            recoveryRevision !== undefined &&
            typeof recoveryVersion === 'number'
              ? {
                  id: recoveryId,
                  draft: recoveryDraft,
                  revision: recoveryRevision,
                  version: recoveryVersion,
                  day: text(own(rawRecovery, 'day')) ?? '',
                }
              : undefined,
        }));
      }
      sync();
      void refresh();
    },
    refresh,
    selectDay: (day) => {
      activeChange((e) => (e.draft?.days[day] === undefined ? e : { ...e, day }));
    },
    edit: (key, field, value) => {
      editDraft((draft, day) => ({
        ...draft,
        days: {
          ...draft.days,
          [day]: (draft.days[day] ?? []).map((block) =>
            block.key === key ? { ...block, [field]: value } : block,
          ),
        },
      }));
    },
    mapWeek: (slot, value) => {
      editDraft((draft) => ({ ...draft, week: { ...draft.week, [slot]: value } }));
    },
    add: () => {
      const e = currentSchedule(store.getSnapshot());
      const seed =
        e?.draft?.days[e.day]?.[0] ??
        scheduleDraft(own(e?.source, 'profile'))?.days[e?.day ?? '']?.[0];
      if (seed === undefined) return null;
      const key = `added:${++counter}:${now()}`;
      editDraft((draft, day) => ({
        ...draft,
        days: { ...draft.days, [day]: [...(draft.days[day] ?? []), { ...seed, key }] },
      }));
      return key;
    },
    start: (run) => {
      let started = false;
      activeChange((e) => {
        if (e.draft !== null || e.source === undefined || own(e.source, 'ok') !== true) return e;
        started = true;
        return {
          ...e,
          draft: firstPlanDraft(run),
          day: FIRST_DAY,
          dirty: true,
          version: e.version + 1,
        };
      });
      return started;
    },
    move: (key, direction) => {
      let position: number | null = null;
      editDraft((draft, day) => {
        const blocks = [...(draft.days[day] ?? [])];
        const from = blocks.findIndex((b) => b.key === key);
        const to = from + direction;
        const moving = blocks[from];
        if (moving === undefined || to < 0 || to >= blocks.length) return draft;
        blocks.splice(from, 1);
        blocks.splice(to, 0, moving);
        position = to + 1;
        return { ...draft, days: { ...draft.days, [day]: blocks } };
      });
      return position;
    },
    remove: (key) => {
      const e = currentSchedule(store.getSnapshot());
      const blocks = e?.draft?.days[e.day] ?? [];
      const index = blocks.findIndex((b) => b.key === key);
      const remaining = blocks.filter((b) => b.key !== key);
      editDraft((draft, day) => ({ ...draft, days: { ...draft.days, [day]: remaining } }));
      return remaining[Math.min(index, remaining.length - 1)]?.key ?? null;
    },
    save: () =>
      fencedSend((context, e) => {
        if (!canSave(e) || e.draft === null || e.baseRevision === null) return false;
        const plan = schedulePlan(e.draft);
        const result = api.send(context, e.baseRevision, plan, store.getSnapshot().target.name);
        if (result.kind !== 'sent') return false;
        rememberSubmission(result.record, e, plan);
        return true;
      }, canSave),
    removePlan: (revision) => {
      const ready = (e: ScheduleEntry) =>
        e.draft !== null &&
        planOf(e.source) !== undefined &&
        !e.dirty &&
        !e.busy &&
        !e.readError &&
        !e.reconcile &&
        e.baseRevision === revision &&
        !unresolved(e);
      return fencedSend((context, e) => {
        if (!ready(e)) return false;
        const result = api.remove(context, revision, store.getSnapshot().target.name);
        if (result.kind !== 'sent') return false;
        rememberSubmission(result.record, e, undefined, true);
        return true;
      }, ready);
    },
    discard: () => {
      activeChange((e) => ({
        ...e,
        draft: scheduleDraft(own(e.source, 'profile')),
        baseRevision: profileRevision(e.source),
        dirty: false,
        version: e.version + 1,
      }));
    },
    reconcile: () => {
      activeChange((e) =>
        !e.checked || e.busy || e.intent?.phase === 'sending'
          ? e
          : {
              ...e,
              reconcile: false,
              baseRevision: profileRevision(e.source),
              resolvedIntent: e.intent?.id ?? null,
            },
      );
    },
    rememberFocus: (focus) => {
      activeChange((e) => ({ ...e, focus }));
    },
    dispose: () => {
      controller?.abort();
      controller = null;
      unsubscribe();
      api.machine.dispose();
    },
  };
}
