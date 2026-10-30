import {
  capturedAge,
  diskState,
  eventKey,
  workerAlive,
  RUNTIME_FIELDS,
  RUNTIME_FILTERS,
} from '../../core/runtime.ts';
import { finite, list, own, ownPath } from '../../core/read.ts';
import { settingsView, settingsModule } from '../../core/settingsFields.ts';
import { fromServer } from '../../copy/server.ts';
import { answeredBody } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import type { RuntimeState } from '../runtime.ts';
import { settingsMatchContext, settingsModuleVM, settingsSavePending } from './settingsModule.ts';

type RuntimeModule =
  | 'loop-defaults'
  | 'missions-defaults'
  | 'fleet-defaults'
  | 'loop'
  | 'missions'
  | 'fleet'
  | 'tap'
  | 'logging';
const MODULES: readonly RuntimeModule[] = [
  'loop-defaults',
  'missions-defaults',
  'fleet-defaults',
  'loop',
  'missions',
  'fleet',
  'tap',
  'logging',
];
export function selectRuntimeSettings(state: DashboardState) {
  const view = settingsView(answeredBody(state, 'settings'));
  const pending = settingsSavePending(state, ['settings']);
  const read = state.reads.settings;
  const failed = read.latest?.outcome.kind === 'failed' ? read.latest.outcome : null;
  const instance = state.context?.instance;
  const config = answeredBody(state, 'config');
  return {
    emulator: fromServer(
      'name',
      instance === undefined ? null : ownPath(config, 'instances', instance, 'label'),
    ),
    instance: fromServer('id', instance),
    account: fromServer('name', state.context?.account),
    profile: fromServer('name', state.context?.profile),
    readFailure:
      failed === null
        ? null
        : {
            network: failed.reason === 'network',
            status: failed.status,
            error: fromServer('message', own(failed.body, 'error')),
          },
    loadedAt: view === null ? null : (read.lastAnswered?.arrivedAtMs ?? null),
    target: fromServer(
      'detail',
      state.context === null ? null : JSON.stringify(state.context, null, 2),
    ),
    modules: MODULES.map((moduleId) => {
      const vm = settingsModuleVM(state, view, moduleId, { pending });
      const module = view === null ? null : settingsModule(view, moduleId);
      const status: 'not_read' | 'unavailable' | 'other_context' | 'no_module' | 'ready' | 'stale' =
        view === null
          ? read.lastAnswered === null && read.latest === null
            ? 'not_read'
            : 'unavailable'
          : !settingsMatchContext(view, state.context)
            ? 'other_context'
            : vm === null
              ? 'no_module'
              : failed === null
                ? 'ready'
                : 'stale';
      return { id: moduleId, vm, status, scope: module?.scope ?? null };
    }),
  };
}

function serverJSON(value: unknown) {
  return value === undefined || value === null
    ? null
    : fromServer('detail', typeof value === 'string' ? value : JSON.stringify(value, null, 2));
}
export function runtimeFields(raw: unknown, keys: readonly string[]) {
  return keys.map((key) => ({
    key,
    value: serverJSON(own(raw, key)),
  }));
}
export function selectRuntime(state: RuntimeState) {
  const identity = state.identity.body;
  const server = own(identity, 'server');
  return {
    ...state,
    identityVM:
      state.identity.loadedAt === null
        ? null
        : {
            source: diskState(server),
            raw: serverJSON(identity),
            capturedAge: capturedAge(server, state.identity.loadedAt),
            server: runtimeFields(server, RUNTIME_FIELDS.server),
            runtime: runtimeFields(identity, RUNTIME_FIELDS.runtime),
            process: runtimeFields(own(identity, 'process_refresh'), RUNTIME_FIELDS.process),
            workers: (list(own(identity, 'workers')) ?? []).map((worker, index) => ({
              key: eventKey(worker, index),
              alive: workerAlive(worker),
              fields: runtimeFields(
                {
                  ...Object.fromEntries(
                    RUNTIME_FIELDS.worker.map((key) => [key, own(worker, key)]),
                  ),
                  pid: own(own(worker, 'worker'), 'pid'),
                  creation_time: own(own(worker, 'worker'), 'creation_time'),
                  source_revision: ownPath(worker, 'worker', 'build', 'source_revision'),
                  source_fingerprint: ownPath(worker, 'worker', 'build', 'source_fingerprint'),
                  captured_at: ownPath(worker, 'worker', 'build', 'captured_at'),
                },
                RUNTIME_FIELDS.worker,
              ),
            })),
          },
    diagnosticsVM:
      state.events.loadedAt === null
        ? null
        : {
            applied: serverJSON(state.applied),
            coverage: runtimeFields(own(state.events.body, 'coverage'), RUNTIME_FIELDS.coverage),
            writer: runtimeFields(own(state.events.body, 'writer_health'), RUNTIME_FIELDS.writer),
            rows: (list(own(state.events.body, 'events')) ?? []).map((event, index) => ({
              key: eventKey(event, index),
              kind: fromServer('detail', own(event, 'kind')),
              time: finite(own(event, 'wall_time')) ?? finite(own(event, 't')) ?? null,
              failure:
                own(own(event, '_log'), 'failure') === true
                  ? 'failure'
                  : own(own(event, '_log'), 'failure') === false
                    ? 'noFailure'
                    : 'unknownFailure',
              ids: runtimeFields(event, RUNTIME_FILTERS),
              // Reported evidence only; the server's failure flag remains authoritative.
              evidence: runtimeFields(event, [
                'outcome',
                'reason',
                'error',
                'exception_type',
                'level',
                'confirmed',
                'ok',
              ]).filter((field) => field.value !== null),
              raw: serverJSON(event),
            })),
          },
    identityError: fromServer('message', state.identity.serverError),
    eventsError: fromServer('message', state.events.serverError),
  };
}
export type RuntimeVM = ReturnType<typeof selectRuntime>;
export type RuntimeFields = ReturnType<typeof runtimeFields>;
