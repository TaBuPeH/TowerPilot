import type { RecognitionAdvancedApi } from '../api/recognitionAdvanced.ts';
import type { PageContext } from '../api/context.ts';
import type { LaunchContext } from '../api/clickContext.ts';
import type { IntentRecord } from '../api/intents/types.ts';
import { own } from '../core/read.ts';
import {
  launchAnswer,
  launchRecordedIn,
  operationView,
  templateWriteAnswer,
} from '../core/recognition.ts';
import {
  cropCheck,
  EMPTY_CROP,
  restoreCrop,
  type CropDraft,
  type Rect,
  type RepairTarget,
  type Size,
} from '../core/recognitionImages.ts';
import { createStore, type Store } from './store.ts';

export interface StillFrame {
  readonly token: string;
  readonly src: string;
  readonly takenAt: number;
  readonly size: Size | null;
  readonly error: boolean;
}
export interface AdvancedTarget {
  readonly draft: CropDraft;
  readonly frame: StillFrame | null;
  readonly frameError: boolean;
  readonly frameBusy: boolean;
  readonly templates: unknown;
  readonly required: unknown;
  readonly catalogue: unknown;
  readonly plan: unknown;
  readonly readAt: number | null;
  readonly imageRevision: number;
  readonly readError: boolean;
  readonly busy: boolean;
  readonly selectedOperation: string;
  readonly analysis: unknown;
  readonly analysisAt: number | null;
  readonly analysisError: boolean;
  readonly analysisBusy: boolean;
  readonly snapshotError: boolean;
  readonly launch: IntentRecord | null;
  readonly crop: IntentRecord | null;
  readonly checking: boolean;
  readonly contextError: boolean;
}
export interface RecognitionAdvancedState {
  readonly context: PageContext | null;
  readonly key: string | null;
  readonly epoch: number;
  readonly online: boolean;
  readonly run: string | null;
  readonly targets: Readonly<Record<string, AdvancedTarget>>;
}
export interface RecognitionAdvancedStore extends Store<RecognitionAdvancedState> {
  readonly setTarget: (context: PageContext | null, epoch: number, online?: boolean) => void;
  readonly setRun: (run: string | null) => void;
  readonly refresh: () => Promise<void>;
  readonly readAnalysis: () => Promise<void>;
  readonly analyze: () => Promise<void>;
  readonly selectOperation: (operation: string) => void;
  readonly takeFrame: () => Promise<void>;
  readonly frameLoaded: (token: string, size: Size) => void;
  readonly frameFailed: (token: string) => void;
  readonly snapshotFailed: () => void;
  readonly edit: (field: keyof CropDraft, value: string) => void;
  readonly draw: (rect: Rect) => void;
  readonly repair: (target: RepairTarget) => void;
  readonly save: (replace?: {
    readonly name: string;
    readonly token: string;
    readonly draft: CropDraft;
  }) => Promise<void>;
  readonly dispose: () => void;
}
const empty = (): AdvancedTarget => ({
  draft: EMPTY_CROP,
  frame: null,
  frameError: false,
  frameBusy: false,
  templates: null,
  required: null,
  catalogue: null,
  plan: null,
  readAt: null,
  imageRevision: 0,
  readError: false,
  busy: false,
  selectedOperation: '',
  analysis: null,
  analysisAt: null,
  analysisError: false,
  analysisBusy: false,
  snapshotError: false,
  launch: null,
  crop: null,
  checking: false,
  contextError: false,
});
export function currentAdvanced(state: RecognitionAdvancedState): AdvancedTarget | null {
  return state.key === null ? null : (state.targets[state.key] ?? null);
}
export function advancedPending(intent: IntentRecord | null): boolean {
  return (
    intent !== null &&
    (intent.phase === 'sending' || intent.phase === 'unconfirmed') &&
    intent.readSettlement === null
  );
}
export function createRecognitionAdvancedStore(
  api: RecognitionAdvancedApi,
  now: () => number = Date.now,
): RecognitionAdvancedStore {
  const store = createStore<RecognitionAdvancedState>({
    context: null,
    key: null,
    epoch: 0,
    online: false,
    run: null,
    targets: {},
  });
  let disposed = false;
  let librarySequence = 0;
  let analysisSequence = 0;
  const requests = new Set<AbortController>();
  const change = (key: string, fn: (t: AdvancedTarget) => AdvancedTarget) => {
    store.update((s) => {
      const next = fn(s.targets[key] ?? empty());
      api.keepDraft(key, next.draft);
      return { ...s, targets: { ...s.targets, [key]: next } };
    });
  };
  const active = (key: string, epoch: number) =>
    !disposed && store.getSnapshot().key === key && store.getSnapshot().epoch === epoch;
  const sync = () => {
    const s = store.getSnapshot();
    if (s.key === null) return;
    const key = s.key;
    for (const intent of api.machine.snapshot()) {
      if (!api.sameContext(intent.context, s.context)) continue;
      if (intent.kind !== 'analysis_start' && intent.kind !== 'template_write') continue;
      change(key, (t) => {
        if (intent.kind === 'analysis_start') {
          if (t.launch === intent) return t;
          const answer = intent.answer === null ? null : launchAnswer(intent.answer);
          return {
            ...t,
            launch: intent,
            ...(answer?.kind === 'accepted' &&
            s.context !== null &&
            api.operationMatches(answer.operation, s.context)
              ? {
                  selectedOperation: answer.operation.id,
                  analysis: null,
                  analysisAt: null,
                  snapshotError: false,
                }
              : {}),
          };
        }
        const answer = intent.answer === null ? null : templateWriteAnswer(intent.answer);
        const invalidFrame =
          answer?.kind === 'refused' &&
          (answer.code === 'frame_context_changed' || answer.code === 'frame_expired');
        return t.crop === intent
          ? t
          : { ...t, crop: intent, ...(invalidFrame ? { frame: null, frameBusy: false } : {}) };
      });
    }
  };
  const unsubscribe = api.machine.subscribe(sync);
  const checked = async (key: string, epoch: number, context: PageContext) => {
    const controller = new AbortController();
    requests.add(controller);
    const named = await api.readClickContext(controller.signal);
    requests.delete(controller);
    return active(key, epoch) && named !== null && api.sameContext(named, context) ? named : null;
  };
  const refresh = async () => {
    const { key, epoch, run, context } = store.getSnapshot();
    if (key === null || context === null || disposed) return;
    const seq = ++librarySequence;
    const controller = new AbortController();
    requests.add(controller);
    const timer = setTimeout(() => {
      controller.abort();
    }, 10000);
    change(key, (t) => ({ ...t, busy: true }));
    const paths: readonly (readonly [
      'templates' | 'required' | 'catalogue' | 'plan',
      `/api/${string}`,
    ])[] = [
      ['templates', '/api/wizard/templates'],
      ['required', '/api/wizard/required'],
      ['catalogue', '/api/catalogue/modules'],
      ['plan', `/api/wizard/scan-plan${run === null ? '' : `?preset=${encodeURIComponent(run)}`}`],
    ];
    const results = await Promise.all(
      paths.map(async ([kind, path]) => ({
        kind,
        result: await api.read(kind, path, controller.signal),
      })),
    );
    const same = await checked(key, epoch, context);
    clearTimeout(timer);
    requests.delete(controller);
    if (!active(key, epoch) || seq !== librarySequence) return;
    change(key, (t) => {
      let next = {
        ...t,
        busy: false,
        readError: same === null || results.some((r) => r.result.kind !== 'answered'),
      };
      if (same === null) return next;
      for (const { kind, result } of results)
        if (result.kind === 'answered') next = { ...next, [kind]: result.answer.body };
      return { ...next, readAt: now(), imageRevision: next.imageRevision + 1 };
    });
  };
  const readAnalysis = async () => {
    const { key, epoch, context } = store.getSnapshot();
    const target = currentAdvanced(store.getSnapshot());
    if (key === null || context === null || target === null || disposed) return;
    const selected = target.selectedOperation;
    const seq = ++analysisSequence;
    const controller = new AbortController();
    requests.add(controller);
    const timer = setTimeout(() => {
      controller.abort();
    }, 10000);
    const started = api.sequence.next();
    change(key, (t) => ({ ...t, analysisBusy: true }));
    const result = await api.read(
      'analysis',
      `/api/screen-analysis/status${selected === '' ? '' : `?operation_id=${encodeURIComponent(selected)}`}`,
      controller.signal,
    );
    const same = await checked(key, epoch, context);
    clearTimeout(timer);
    requests.delete(controller);
    if (
      !active(key, epoch) ||
      seq !== analysisSequence ||
      currentAdvanced(store.getSnapshot())?.selectedOperation !== selected
    )
      return;
    const body = result.kind === 'answered' ? result.answer.body : result.answer?.body;
    const operation = operationView(own(body, 'operation'));
    if (same === null || (operation !== null && !api.operationMatches(operation, context))) {
      change(key, (t) => ({
        ...t,
        analysis: null,
        analysisAt: now(),
        analysisBusy: false,
        analysisError: true,
      }));
      return;
    }
    const launch = currentAdvanced(store.getSnapshot())?.launch;
    const recorded = launch?.key == null ? null : launchRecordedIn(body, launch.key);
    if (
      recorded !== null &&
      launch !== undefined &&
      launch !== null &&
      launch.phase === 'unconfirmed' &&
      started > (launch.lostSeq ?? launch.sentSeq)
    )
      api.machine.settleByRead(launch.id, 'applied');
    change(key, (t) => ({
      ...t,
      analysis: body,
      analysisAt: now(),
      analysisError: result.kind !== 'answered',
      analysisBusy: false,
      snapshotError: false,
      ...(selected === '' && recorded !== null
        ? { selectedOperation: recorded.id }
        : selected === '' && launch == null && operation?.kind === 'screen_analysis'
          ? { selectedOperation: operation.id }
          : {}),
    }));
  };
  const withCheck = async (
    fn: (key: string, context: LaunchContext, t: AdvancedTarget) => void,
  ) => {
    const { key, context, epoch } = store.getSnapshot();
    const target = currentAdvanced(store.getSnapshot());
    if (key === null || context === null || target === null || target.checking || disposed) return;
    change(key, (t) => ({ ...t, checking: true, contextError: false }));
    const same = await checked(key, epoch, context);
    if (!active(key, epoch)) return;
    change(key, (t) => ({ ...t, checking: false, contextError: same === null }));
    const latest = currentAdvanced(store.getSnapshot());
    if (same !== null && latest !== null) fn(key, same, latest);
  };
  const draw = (rect: Rect) => {
    const { key } = store.getSnapshot();
    if (key !== null)
      change(key, (t) => ({
        ...t,
        draft: {
          ...t.draft,
          x: String(rect.x),
          y: String(rect.y),
          w: String(rect.w),
          h: String(rect.h),
        },
      }));
  };
  return {
    ...store,
    refresh,
    readAnalysis,
    setTarget: (context, epoch, online = false) => {
      const key = context === null ? null : api.targetKey(context);
      const s = store.getSnapshot();
      if (s.key === key && s.epoch === epoch) {
        if (s.online !== online) store.update((v) => ({ ...v, online }));
        return;
      }
      for (const c of requests) c.abort();
      requests.clear();
      librarySequence++;
      analysisSequence++;
      if (s.key !== null)
        change(s.key, (t) => ({
          ...t,
          frame: null,
          frameBusy: false,
          busy: false,
          analysisBusy: false,
          checking: false,
        }));
      store.update((v) => ({
        ...v,
        key,
        context,
        epoch,
        online,
        targets:
          key === null
            ? v.targets
            : {
                ...v.targets,
                [key]: {
                  ...(v.targets[key] ?? empty()),
                  draft: v.targets[key]?.draft ?? restoreCrop(api.loadDraft(key)),
                  frame: null,
                },
              },
      }));
      api.machine.setContext(context, epoch);
      sync();
      if (key !== null) {
        void refresh();
      }
    },
    setRun: (run) => {
      if (store.getSnapshot().run === run) return;
      store.update((s) => ({ ...s, run }));
      const key = store.getSnapshot().key;
      if (key !== null) change(key, (t) => ({ ...t, plan: null }));
      void refresh();
    },
    selectOperation: (operation) => {
      const { key } = store.getSnapshot();
      analysisSequence++;
      if (key !== null)
        change(key, (t) => ({
          ...t,
          selectedOperation: operation,
          analysis: null,
          analysisAt: null,
          analysisBusy: false,
          snapshotError: false,
        }));
    },
    analyze: () =>
      withCheck((_key, context, t) => {
        if (advancedPending(t.launch)) return;
        api.launch(context);
      }),
    takeFrame: () =>
      withCheck((key, _context, t) => {
        if (!store.getSnapshot().online || t.frameBusy || advancedPending(t.crop)) return;
        const token = api.newToken();
        change(key, (v) => ({
          ...v,
          frame: {
            token,
            src: `/api/frame.png?ts=${encodeURIComponent(token)}`,
            takenAt: now(),
            size: null,
            error: false,
          },
          frameError: false,
          frameBusy: true,
        }));
      }),
    frameLoaded: (token, size) => {
      const { key } = store.getSnapshot();
      if (key !== null)
        change(key, (t) =>
          t.frame?.token === token ? { ...t, frame: { ...t.frame, size }, frameBusy: false } : t,
        );
    },
    frameFailed: (token) => {
      const { key } = store.getSnapshot();
      if (key !== null)
        change(key, (t) =>
          t.frame?.token === token ? { ...t, frame: null, frameBusy: false, frameError: true } : t,
        );
    },
    snapshotFailed: () => {
      const { key } = store.getSnapshot();
      if (key !== null) change(key, (t) => ({ ...t, snapshotError: true }));
    },
    edit: (field, value) => {
      const { key } = store.getSnapshot();
      if (key !== null) change(key, (t) => ({ ...t, draft: { ...t.draft, [field]: value } }));
    },
    draw,
    repair: (target) => {
      const { key } = store.getSnapshot();
      if (key !== null) {
        change(key, (t) => ({ ...t, draft: { ...t.draft, name: target.rel } }));
        if (target.learned !== null) draw(target.learned);
      }
    },
    save: async (replace) => {
      const initial = currentAdvanced(store.getSnapshot());
      if (initial === null) return;
      const frame = initial.frame;
      const draft = { ...initial.draft };
      const check = cropCheck(draft, frame?.size ?? null);
      if (frame === null || check.kind !== 'valid' || advancedPending(initial.crop)) return;
      const overwrite =
        replace !== undefined &&
        replace.name === draft.name &&
        replace.token === frame.token &&
        JSON.stringify(replace.draft) === JSON.stringify(draft);
      if (replace !== undefined && !overwrite) return;
      await withCheck((_key, context, t) => {
        if (
          advancedPending(t.crop) ||
          t.frame?.token !== frame.token ||
          !store.getSnapshot().online
        )
          return;
        api.saveTemplate(context, draft.name, frame.token, check.rect, overwrite);
      });
    },
    dispose: () => {
      disposed = true;
      for (const c of requests) c.abort();
      requests.clear();
      unsubscribe();
      api.machine.dispose();
    },
  };
}
