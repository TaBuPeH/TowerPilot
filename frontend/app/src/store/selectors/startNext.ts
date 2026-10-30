import { tapsSetting } from '../../core/config.ts';
import { connectionView } from '../../core/connection.ts';
import { unknownFact } from '../../core/facts.ts';
import { gateOf } from '../../core/gate.ts';
import { collectionPermit, setupPermit } from '../../core/guards.ts';
import { entries, finite, list, own, ownPath, text } from '../../core/read.ts';
import {
  collectGate,
  readiness,
  runDefined,
  selectRun,
  transferHint,
  type ReadinessView,
} from '../../core/readiness.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { LaunchCommand } from '../commands.ts';
import { contextIntents } from '../intents.ts';
import {
  answeredBody,
  assessedRead,
  configReadOf,
  contextReadOf,
  latestRead,
  readAgeSec,
} from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { commandVM, type CommandVM } from '../viewModels.ts';
import { runControlsPath } from './gemSlot.ts';

export interface StartNextVM {
  /** Environment blockers apply to both buttons; each button's `enabled` already includes them (F-038). */
  readonly launchBlocker: 'connection_blocked' | 'input_off' | null;
  readonly readiness: {
    readonly line: ReadinessView['line'];
    readonly imageCount: number;
    readonly details: readonly {
      readonly kind: 'image' | 'setting' | 'reason';
      readonly value: ServerText<'message'>;
    }[];
  };
  readonly picker: {
    readonly runs: readonly {
      readonly name: ServerText<'id'>;
      readonly label: ServerText<'name'> | null;
      readonly readiness: ReadinessView['line'];
      readonly imageCount: number;
    }[];
    readonly selected: string | null;
  };
  readonly start: CommandVM<LaunchCommand>;
  readonly collect: CommandVM<LaunchCommand>;
  readonly reasons: readonly {
    readonly action: 'start' | 'collect';
    readonly code: string;
    readonly guardCode: string | null;
    readonly message: ServerText<'message'> | null;
  }[];
  readonly transferHint: ReturnType<typeof transferHint>;
}

export function selectStartNext(state: DashboardState): StartNextVM {
  const runsBody = answeredBody(state, 'runs');
  const selected = selectRun(runsBody, state.selectedRun);
  const ready = readiness(ownPath(runsBody, 'readiness', selected ?? ''));
  const status = latestRead(state, 'status');
  const body = status?.outcome.kind === 'answered' ? status.outcome.body : undefined;
  // A permit is observed at the read's own server time (never the browser clock, decision 5).
  const observedAt = finite(ownPath(body, 'harvest', 'server_time')) ?? NaN;
  const setup = setupPermit(own(body, 'setup_battle_guard'), observedAt);
  const collection = collectionPermit(own(body, 'collection_guard'), observedAt);
  const path = selected === null ? null : runControlsPath(selected);
  const connection = connectionView(
    contextReadOf(status),
    status === null ? 0 : readAgeSec(state, status),
  );
  const taps = tapsSetting(configReadOf(latestRead(state, 'config')));
  // An explicit environment blocker remains separate from either launch guard.
  const environmentReason =
    connection.word === 'blocked'
      ? 'connection_blocked'
      : taps.kind === 'known' && !taps.allowTaps
        ? 'input_off'
        : null;
  const preview = path === null ? null : (state.runControls[path] ?? null);
  const collect = collectGate(
    collection.kind === 'known' ? collection.value : null,
    contextReadOf(preview),
    state.context,
    selected ?? '',
    selected === 'combo',
    status === null ? unknownFact('not_read') : gateOf(assessedRead(status), null),
  );
  const intents = contextIntents(state);
  const startSending = intents.some((r) => r.kind === 'start' && r.phase === 'sending');
  const collectSending = intents.some((r) => r.kind === 'start_gems' && r.phase === 'sending');
  // F-038: Start is disabled by its own setup guard and by the environment blockers, each
  // with its own line (the readiness is rechecked at the click, F-040).
  const startReason = startSending
    ? 'sending'
    : selected === null
      ? 'pick_run'
      : setup.kind === 'unknown'
        ? setup.reason
        : environmentReason;
  const collectReason = collectSending
    ? 'sending'
    : selected === null
      ? 'pick_run'
      : collect.reason === 'ready'
        ? environmentReason
        : collect.reason;
  const command = selected === null ? null : { preset: selected, epoch: state.epoch };
  const reasons: StartNextVM['reasons'] = [
    ...(startReason === null
      ? []
      : [
          {
            action: 'start',
            code: startReason,
            guardCode: text(ownPath(body, 'setup_battle_guard', 'code')) ?? null,
            message: fromServer('message', ownPath(body, 'setup_battle_guard', 'message')),
          } satisfies StartNextVM['reasons'][number],
        ]),
    ...(collectReason === null
      ? []
      : [
          {
            action: 'collect',
            code: collectReason,
            guardCode: text(ownPath(body, 'collection_guard', 'code')) ?? null,
            message: fromServer('message', ownPath(body, 'collection_guard', 'message')),
          } satisfies StartNextVM['reasons'][number],
        ]),
  ];
  return {
    launchBlocker: environmentReason,
    readiness: {
      line: ready.line,
      imageCount: ready.imageCount,
      details: [
        ...ready.imageRows.flatMap((row) =>
          (list(own(row, 'docs')) ?? []).map((doc) => ({
            kind: 'image' as const,
            value: fromServer('message', own(doc, 'label')),
          })),
        ),
        ...ready.otherRows.flatMap<{
          kind: 'setting' | 'reason';
          value: ServerText<'message'> | null;
        }>((row) => {
          const docs = list(own(row, 'docs')) ?? [];
          return docs.length > 0
            ? docs.map((doc) => ({
                kind: 'setting' as const,
                value: fromServer('message', own(doc, 'label')),
              }))
            : (list(own(row, 'reasons')) ?? []).map((value) => ({
                kind: 'reason' as const,
                value: fromServer('message', value),
              }));
        }),
        ...(['needs_setup', 'readiness_check_failed'].includes(ready.line)
          ? [...(list(ready.missing) ?? []), ready.message].map((value) => ({
              kind: 'reason' as const,
              value: fromServer('message', value),
            }))
          : []),
      ].flatMap((detail) => (detail.value === null ? [] : [{ ...detail, value: detail.value }])),
    },
    picker: {
      selected,
      runs: entries(own(runsBody, 'runs')).flatMap(([key, run]) => {
        const name = fromServer('id', key);
        if (!runDefined(run) || name === null) return [];
        const ready = readiness(ownPath(runsBody, 'readiness', key));
        return [
          {
            name,
            label: fromServer('name', own(run, 'label')),
            readiness: ready.line,
            imageCount: ready.imageCount,
          },
        ];
      }),
    },
    // Readiness is rechecked by the click command; no cached readiness permits a POST.
    start: commandVM(true, startReason === null, startReason, startSending, command),
    collect: commandVM(
      true,
      collectReason === null && collect.enabled,
      collectReason,
      collectSending,
      command,
    ),
    reasons,
    transferHint: transferHint(body),
  };
}
