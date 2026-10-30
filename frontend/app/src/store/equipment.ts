import type { EquipmentApi } from '../api/equipment.ts';
import type { PageContext } from '../api/context.ts';
import type { IntentRecord } from '../api/intents/types.ts';
import { entries, finite, own, record, text } from '../core/read.ts';
import { createStore, viewEqual, type Store } from './store.ts';
import {
  configRevision,
  equipmentBody,
  equipmentDraft,
  invalidEquipment,
  loadoutOf,
  restoreEquipmentDraft,
  restoreChanged,
  restoreEligibility,
  restoreInvalid,
  restoreModuleRows,
  type EquipmentDraft,
  type EquipmentField,
  type EquipmentMode,
} from './equipmentDraft.ts';

export type EquipmentOutcome =
  | 'checking'
  | 'sending'
  | 'saved'
  | 'unknown'
  | 'refused'
  | 'conflict'
  | 'contextChanged'
  | 'contextUnconfirmed'
  | 'selectionChanged'
  | 'draftChanged'
  | 'acceptedCurrent'
  | 'readApplied'
  | 'readUnchanged';
interface Submission {
  readonly body: unknown;
  readonly original: unknown;
  readonly revision: string;
  readonly version: number;
}
export interface LoadoutEditor {
  readonly refusalContext: unknown;
  readonly reviewedVersion: number | null;
  readonly original: unknown;
  readonly draft: EquipmentDraft;
  readonly revision: string | null;
  readonly dirty: boolean;
  readonly version: number;
  readonly intent: IntentRecord | null;
  readonly submission: Submission | null;
  readonly outcome: EquipmentOutcome | null;
  readonly reconcile: boolean;
  readonly checked: boolean;
  readonly focus: string | null;
}
export interface EquipmentTarget {
  readonly profileConfigRevision: string | null;
  readonly config: unknown;
  readonly profile: unknown;
  readonly selected: string | null;
  readonly editors: Readonly<Record<string, LoadoutEditor>>;
  readonly busy: boolean;
  readonly readError: boolean;
  readonly readAt: number | null;
}
export interface EquipmentState {
  readonly context: PageContext | null;
  readonly epoch: number;
  readonly key: string | null;
  readonly targets: Readonly<Record<string, EquipmentTarget>>;
}
export interface EquipmentStore extends Store<EquipmentState> {
  readonly editRestoreCards: (value: string) => void;
  readonly editRestoreModule: (key: string, field: 'module' | 'slot', value: string) => void;
  readonly addRestoreModule: () => void;
  readonly removeRestoreModule: (key: string) => void;
  readonly setTarget: (context: PageContext | null, epoch: number) => void;
  readonly refresh: () => Promise<void>;
  readonly select: (name: string) => void;
  readonly edit: (field: EquipmentField, value: string) => void;
  readonly mode: (mode: EquipmentMode) => void;
  readonly save: () => void;
  readonly cancel: () => void;
  readonly reconcile: () => void;
  readonly approveConversion: (reviewed: boolean) => void;
  readonly rememberFocus: (id: string) => void;
  readonly dispose: () => void;
}
const emptyTarget = (): EquipmentTarget => ({
  profileConfigRevision: null,
  config: undefined,
  profile: undefined,
  selected: null,
  editors: {},
  busy: false,
  readError: false,
  readAt: null,
});
const newEditor = (original: unknown, revision: string | null): LoadoutEditor => ({
  refusalContext: null,
  reviewedVersion: null,
  original,
  revision,
  draft: equipmentDraft(original),
  dirty: false,
  version: 0,
  intent: null,
  submission: null,
  outcome: null,
  reconcile: false,
  checked: false,
  focus: null,
});
function putEditor(
  editors: Record<string, LoadoutEditor>,
  name: string,
  editor: LoadoutEditor,
): void {
  Object.defineProperty(editors, name, {
    value: editor,
    enumerable: true,
    writable: true,
    configurable: true,
  });
}
export const currentEquipment = (s: EquipmentState): EquipmentTarget | null =>
  s.key === null ? null : (s.targets[s.key] ?? null);
export const loadoutEditor = (
  target: EquipmentTarget | undefined | null,
  name: string,
): LoadoutEditor | null =>
  target !== undefined && target !== null && Object.hasOwn(target.editors, name)
    ? (target.editors[name] ?? null)
    : null;
export const currentLoadout = (s: EquipmentState): LoadoutEditor | null => {
  const target = currentEquipment(s);
  return target?.selected == null ? null : loadoutEditor(target, target.selected);
};
export function equipmentPending(e: LoadoutEditor): boolean {
  return e.outcome === 'checking' || e.outcome === 'sending' || e.outcome === 'unknown';
}
export function canSaveEquipment(s: EquipmentState, now: number): boolean {
  const target = currentEquipment(s);
  const e = currentLoadout(s);
  return (
    s.context !== null &&
    target !== null &&
    e !== null &&
    target.selected !== null &&
    loadoutOf(target.config, target.selected) !== undefined &&
    e.dirty &&
    !target.busy &&
    !target.readError &&
    target.readAt !== null &&
    now - target.readAt <= 30_000 &&
    e.revision !== null &&
    e.revision === configRevision(target.config) &&
    !e.reconcile &&
    !equipmentPending(e) &&
    (e.draft.mode !== 'manual' || restoreChanged(e.draft, e.original)) &&
    (equipmentDraft(e.original).mode !== 'manual' ||
      e.draft.mode === 'manual' ||
      e.reviewedVersion === e.version) &&
    restoreEligibility(e.draft, e.original, target.profile, target.selected, s.context.profile) ===
      null &&
    !restoreInvalid(e.draft) &&
    Object.keys(equipmentBody(e.draft, e.original)).length > 0 &&
    invalidEquipment(e.draft, target.profile).length === 0
  );
}

/** Owns one draft and immutable submission per context/loadout, never any game action. */
export function createEquipmentStore(
  api: EquipmentApi,
  now: () => number = Date.now,
): EquipmentStore {
  const store = createStore<EquipmentState>({ context: null, epoch: 0, key: null, targets: {} });
  let controller: AbortController | null = null;
  const checks = new Set<AbortController>();
  let disposed = false;
  const change = (key: string, transform: (t: EquipmentTarget) => EquipmentTarget) => {
    store.update((s) => ({
      ...s,
      targets: { ...s.targets, [key]: transform(s.targets[key] ?? emptyTarget()) },
    }));
    const t = store.getSnapshot().targets[key];
    if (t !== undefined)
      api.keepDraft(key, {
        selected: t.selected,
        editors: Object.fromEntries(entries(t.editors).map(([name, e]) => [name, e])),
      });
  };
  const editEntry = (transform: (e: LoadoutEditor) => LoadoutEditor) => {
    const { key } = store.getSnapshot();
    if (key === null) return;
    change(key, (t) => {
      const selected = t.selected;
      const current = selected === null ? null : loadoutEditor(t, selected);
      return selected === null || current === null
        ? t
        : { ...t, editors: { ...t.editors, [selected]: transform(current) } };
    });
  };
  const sync = () => {
    for (const intent of api.machine.snapshot()) {
      if (
        intent.request.path !== '/api/loadout-patch' ||
        intent.context === null ||
        intent.supersededBySeq !== null
      )
        continue;
      const key = api.targetKey(intent.context);
      const name = intent.meta.blueprint;
      const t = store.getSnapshot().targets[key];
      const e = name === undefined ? null : loadoutEditor(t, name);
      if (name === undefined || e === null || e.intent === intent) continue;
      const request: unknown = JSON.parse(intent.request.body);
      const sent = e.submission;
      const body = intent.answer?.body;
      const currentEpoch =
        store.getSnapshot().key === key && store.getSnapshot().epoch === intent.epoch;
      const accepted =
        currentEpoch &&
        intent.answer?.status === 200 &&
        own(body, 'ok') === true &&
        configRevision(body) !== null &&
        viewEqual(loadoutOf(body, name), own(request, 'body'));
      const currentRevision = configRevision(t?.config);
      if (
        accepted &&
        currentRevision !== intent.meta.profileRevision &&
        currentRevision !== configRevision(body)
      ) {
        change(key, (target) => ({
          ...target,
          editors: {
            ...target.editors,
            [name]: { ...e, intent, outcome: 'conflict', reconcile: true, checked: false },
          },
        }));
        continue;
      }
      const refused =
        intent.answer !== null &&
        intent.answer.status >= 400 &&
        intent.answer.status < 500 &&
        own(body, 'ok') === false;
      const held = intent.readSettlement === 'applied';
      const unchanged = intent.readSettlement === 'not_applied';
      const outcome: EquipmentOutcome = held
        ? 'readApplied'
        : unchanged
          ? 'readUnchanged'
          : intent.phase === 'sending'
            ? 'sending'
            : accepted
              ? 'saved'
              : refused
                ? own(body, 'code') === 'context_changed'
                  ? 'contextChanged'
                  : own(body, 'code') === 'revision_conflict'
                    ? 'conflict'
                    : 'refused'
                : intent.accepted === true
                  ? 'acceptedCurrent'
                  : 'unknown';
      change(key, (target) => ({
        ...target,
        ...(accepted
          ? {
              config: {
                ...Object.fromEntries(entries(target.config)),
                ...Object.fromEntries(entries(body)),
              },
              readAt: now(),
            }
          : {}),
        editors: {
          ...target.editors,
          [name]: {
            ...e,
            intent,
            outcome,
            refusalContext: outcome === 'contextChanged' ? own(body, 'current_context') : null,
            reconcile: e.reconcile || outcome === 'conflict' || outcome === 'contextChanged',
            checked: outcome === 'conflict' || outcome === 'contextChanged' ? false : e.checked,
            ...(accepted
              ? {
                  original: loadoutOf(body, name),
                  revision: configRevision(body),
                  dirty:
                    sent?.version !== e.version ||
                    !viewEqual(equipmentBody(e.draft, e.original), own(request, 'body')),
                }
              : {}),
          },
        },
      }));
    }
  };
  const unsubscribe = api.machine.subscribe(sync);
  const save = async () => {
    const s = store.getSnapshot();
    const target = currentEquipment(s);
    const e = currentLoadout(s);
    if (
      disposed ||
      !canSaveEquipment(s, now()) ||
      s.key === null ||
      s.context === null ||
      target?.selected == null ||
      e?.revision == null
    )
      return;
    const { key, context, epoch } = s;
    const name = target.selected;
    const revision = e.revision;
    const body = equipmentBody(e.draft, e.original);
    const submission = Object.freeze({ body, original: e.original, revision, version: e.version });
    change(key, (current) => ({
      ...current,
      editors: {
        ...current.editors,
        [name]: {
          ...e,
          outcome: 'checking',
          checked: false,
          refusalContext: null,
          submission,
        },
      },
    }));
    const check = new AbortController();
    checks.add(check);
    const named = await api.readClickContext(check.signal);
    if (!checks.delete(check)) return;
    const current = store.getSnapshot();
    const changed =
      current.key !== key ||
      current.epoch !== epoch ||
      !api.sameContext(current.context, context) ||
      (named !== null && !api.sameContext(named.context, context));
    if (changed || named === null) {
      change(key, (t) => {
        const entry = loadoutEditor(t, name);
        return entry === null
          ? t
          : {
              ...t,
              editors: {
                ...t.editors,
                [name]: {
                  ...entry,
                  outcome: changed ? 'contextChanged' : 'contextUnconfirmed',
                  refusalContext: changed ? (named?.context ?? current.context) : null,
                  reconcile: entry.reconcile || changed,
                  checked: false,
                },
              },
            };
      });
      return;
    }
    const latestTarget = currentEquipment(current);
    const latest = loadoutEditor(latestTarget, name);
    const outcome =
      named.revision !== submission.revision ||
      configRevision(latestTarget?.config) !== submission.revision ||
      latest?.revision !== submission.revision
        ? 'conflict'
        : latestTarget?.selected !== name
          ? 'selectionChanged'
          : latest.submission !== submission ||
              latest.original !== submission.original ||
              latestTarget.busy ||
              latestTarget.readError ||
              loadoutOf(latestTarget.config, name) === undefined
            ? 'draftChanged'
            : null;
    if (outcome !== null) {
      change(key, (t) => {
        const entry = loadoutEditor(t, name);
        return entry === null
          ? t
          : {
              ...t,
              editors: {
                ...t.editors,
                [name]: {
                  ...entry,
                  outcome,
                  reconcile: entry.reconcile || outcome === 'conflict',
                  checked: false,
                },
              },
            };
      });
      return;
    }
    // The click reserved and validated this body. Typing during the context read belongs
    // to the next draft; it neither replaces nor cancels the reserved submission.
    api.send(context, name, submission.revision, body);
  };
  const refresh = async () => {
    const { context, key, epoch } = store.getSnapshot();
    if (context === null || key === null || controller !== null) return;
    const request = new AbortController();
    controller = request;
    const timeout = setTimeout(() => {
      request.abort();
    }, 10_000);
    const started = api.sequence.next();
    change(key, (t) => ({ ...t, busy: true, readError: false }));
    const first = await api.readConfig(request.signal);
    const config = first.kind === 'answered' ? first.answer.body : undefined;
    const correct =
      api.configMatches(config, context) &&
      configRevision(config) !== null &&
      record(own(config, 'loadouts')) !== undefined;
    const source =
      correct && context.profile !== null
        ? await api.readProfile(context.profile, request.signal)
        : null;
    // Re-read config after the profile to reject a target/revision change between the two reads.
    const last = correct ? await api.readConfig(request.signal) : null;
    clearTimeout(timeout);
    if (
      controller !== request ||
      store.getSnapshot().key !== key ||
      store.getSnapshot().epoch !== epoch
    )
      return;
    controller = null;
    if (
      Object.values(store.getSnapshot().targets[key]?.editors ?? {}).some(
        (e) => e.outcome === 'saved' && (e.intent?.answerSeq ?? 0) > started,
      )
    ) {
      change(key, (t) => ({ ...t, busy: false }));
      return;
    }
    const profile =
      source?.kind === 'answered' &&
      own(source.answer.body, 'ok') === true &&
      own(source.answer.body, 'name') === context.profile
        ? source.answer.body
        : undefined;
    const valid =
      correct &&
      last?.kind === 'answered' &&
      api.configMatches(last.answer.body, context) &&
      configRevision(last.answer.body) === configRevision(config) &&
      (context.profile === null || profile !== undefined);
    if (!valid) {
      change(key, (t) => ({ ...t, busy: false, readError: true }));
      return;
    }
    change(key, (target) => {
      const editors: Record<string, LoadoutEditor> = { ...target.editors };
      for (const [name, original] of entries(own(config, 'loadouts'))) {
        const e = Object.hasOwn(editors, name) ? editors[name] : undefined;
        putEditor(
          editors,
          name,
          e === undefined
            ? newEditor(original, configRevision(config))
            : e.dirty || equipmentPending(e) || e.reconcile
              ? { ...e, checked: true }
              : {
                  ...e,
                  original,
                  draft: equipmentDraft(original),
                  revision: configRevision(config),
                  checked: true,
                },
        );
      }
      return {
        ...target,
        config,
        profile,
        profileConfigRevision: configRevision(config),
        busy: false,
        readError: false,
        readAt: now(),
        editors,
        selected: target.selected ?? Object.keys(editors).sort()[0] ?? null,
      };
    });
    sync();
    const target = store.getSnapshot().targets[key];
    for (const [name, e] of Object.entries(target?.editors ?? {})) {
      const intent = e.intent;
      if (
        intent?.phase !== 'unconfirmed' ||
        intent.readSettlement !== null ||
        started <= (intent.answerSeq ?? intent.sentSeq)
      )
        continue;
      const requestBody: unknown = JSON.parse(intent.request.body);
      const revision = configRevision(config);
      const applied =
        viewEqual(loadoutOf(config, name), own(requestBody, 'body')) &&
        (revision !== intent.meta.profileRevision ||
          (e.outcome === 'saved' && revision === configRevision(intent.answer?.body)));
      // Shared rule (Collection review F6C-R1-02): only a read holding the sent body settles the
      // save; an unchanged revision and the original value cannot prove it failed (it may still
      // commit), so the save stays unknown until the person reconciles explicitly.
      if (applied) {
        api.machine.settleByRead(intent.id, 'applied');
        change(key, (t) => {
          const current = loadoutEditor(t, name);
          if (current === null) return t;
          return {
            ...t,
            editors: {
              ...t.editors,
              [name]: {
                ...current,
                checked: true,
                reconcile: false,
                original: loadoutOf(config, name),
                revision,
                dirty:
                  current.submission?.version !== current.version ||
                  !viewEqual(
                    equipmentBody(current.draft, current.original),
                    own(requestBody, 'body'),
                  ),
              },
            },
          };
        });
      } else
        change(key, (t) => ({
          ...t,
          editors: {
            ...t.editors,
            [name]: { ...e, reconcile: intent.accepted !== true, checked: true },
          },
        }));
    }
  };
  return {
    ...store,
    editRestoreCards: (value) => {
      editEntry((e) => ({
        ...e,
        draft: { ...e.draft, restoreCards: value },
        dirty: true,
        version: e.version + 1,
      }));
    },
    editRestoreModule: (key, field, value) => {
      editEntry((e) => ({
        ...e,
        draft: {
          ...e.draft,
          restoreModules: restoreModuleRows(e.draft, e.original).map((row) =>
            row.key === key
              ? {
                  ...row,
                  [field]: value,
                  ...(field === 'module' ? { moduleEdited: true } : { slotEdited: true }),
                }
              : row,
          ),
        },
        dirty: true,
        version: e.version + 1,
      }));
    },
    addRestoreModule: () => {
      editEntry((e) => ({
        ...e,
        draft: {
          ...e.draft,
          restoreModules: [
            ...restoreModuleRows(e.draft, e.original),
            { key: `draft:${String(e.version)}`, module: '', slot: '' },
          ],
        },
        dirty: true,
        version: e.version + 1,
      }));
    },
    removeRestoreModule: (key) => {
      editEntry((e) => ({
        ...e,
        draft: {
          ...e.draft,
          restoreModules: restoreModuleRows(e.draft, e.original).filter((row) => row.key !== key),
        },
        dirty: true,
        version: e.version + 1,
      }));
    },
    refresh,
    setTarget: (context, epoch) => {
      const key = context === null ? null : api.targetKey(context);
      if (key === store.getSnapshot().key && epoch === store.getSnapshot().epoch) return;
      for (const check of checks) check.abort();
      controller?.abort();
      controller = null;
      const oldKey = store.getSnapshot().key;
      if (oldKey !== null) change(oldKey, (t) => ({ ...t, busy: false }));
      store.update((s) => ({ ...s, key, context, epoch }));
      api.machine.setContext(context, epoch);
      if (key === null) return;
      if (store.getSnapshot().targets[key] === undefined) {
        const saved = api.loadDraft(key);
        const editors: Record<string, LoadoutEditor> = {};
        for (const [name, raw] of entries(own(saved, 'editors'))) {
          const draft = restoreEquipmentDraft(own(raw, 'draft'));
          if (draft === null) continue;
          const revision = text(own(raw, 'revision')) ?? null;
          const sub = own(raw, 'submission');
          const subRevision = text(own(sub, 'revision'));
          const subVersion = finite(own(sub, 'version'));
          putEditor(editors, name, {
            ...newEditor(own(raw, 'original'), revision),
            // Definite context refusals leave the unresolved-intent journal, but the
            // original target's retained draft still needs its refusal explanation.
            outcome: own(raw, 'outcome') === 'contextChanged' ? 'contextChanged' : null,
            refusalContext:
              own(raw, 'outcome') === 'contextChanged' ? own(raw, 'refusalContext') : null,
            draft,
            dirty: own(raw, 'dirty') === true,
            version: finite(own(raw, 'version')) ?? 0,
            reconcile: own(raw, 'reconcile') === true,
            focus: text(own(raw, 'focus')) ?? null,
            submission:
              subRevision === undefined || subVersion === undefined
                ? null
                : {
                    body: own(sub, 'body'),
                    original: own(sub, 'original'),
                    revision: subRevision,
                    version: subVersion,
                  },
          });
        }
        change(key, () => ({
          ...emptyTarget(),
          selected: text(own(saved, 'selected')) ?? null,
          editors,
        }));
      }
      sync();
      void refresh();
    },
    select: (name) => {
      const key = store.getSnapshot().key;
      if (key !== null)
        change(key, (t) => (loadoutEditor(t, name) === null ? t : { ...t, selected: name }));
    },
    edit: (field, value) => {
      editEntry((e) => ({
        ...e,
        draft: { ...e.draft, values: { ...e.draft.values, [field]: value } },
        dirty: true,
        version: e.version + 1,
      }));
    },
    mode: (mode) => {
      editEntry((e) => ({
        ...e,
        draft: { ...e.draft, mode },
        dirty: true,
        version: e.version + 1,
      }));
    },
    save: () => {
      void save();
    },
    cancel: () => {
      const target = currentEquipment(store.getSnapshot());
      const latest =
        target?.selected == null ? undefined : loadoutOf(target.config, target.selected);
      editEntry((e) =>
        equipmentPending(e)
          ? e
          : {
              ...e,
              original: latest ?? e.original,
              draft: equipmentDraft(latest ?? e.original),
              revision: target?.readError === false ? configRevision(target.config) : e.revision,
              dirty: false,
              reconcile: false,
              reviewedVersion: null,
              version: e.version + 1,
            },
      );
    },
    reconcile: () => {
      const t = currentEquipment(store.getSnapshot());
      const name = t?.selected;
      if (t === null || name == null || t.busy || t.readError) return;
      const entry = loadoutEditor(t, name);
      if (
        entry === null ||
        !entry.checked ||
        entry.outcome === 'sending' ||
        entry.outcome === 'checking' ||
        loadoutOf(t.config, name) === undefined
      )
        return;
      editEntry((e) =>
        !e.checked || e.outcome === 'sending' || own(own(t.config, 'loadouts'), name) === undefined
          ? e
          : {
              ...e,
              original: loadoutOf(t.config, name),
              revision: configRevision(t.config),
              reconcile: false,
              reviewedVersion: null,
              outcome: e.outcome === 'unknown' ? 'acceptedCurrent' : e.outcome,
            },
      );
      if (entry.outcome === 'unknown' && entry.intent !== null) api.machine.accept(entry.intent.id);
    },
    approveConversion: (reviewed) => {
      editEntry((e) => ({ ...e, reviewedVersion: reviewed ? e.version : null }));
    },
    rememberFocus: (focus) => {
      editEntry((e) => (e.focus === focus ? e : { ...e, focus }));
    },
    dispose: () => {
      disposed = true;
      for (const check of checks) check.abort();
      checks.clear();
      controller?.abort();
      controller = null;
      unsubscribe();
      api.machine.dispose();
    },
  };
}
