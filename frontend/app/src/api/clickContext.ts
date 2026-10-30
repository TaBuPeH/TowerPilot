import { readEndpoint } from './client.ts';
import { contextFromConfig, sameContext, type PageContext } from './context.ts';
import { editingTarget } from '../core/editingTarget.ts';
import { configRevision } from '../core/config.ts';
import { profileFilesView } from '../core/profiles.ts';
import { ownPath } from '../core/read.ts';
import { GET_TIMEOUT_MS } from './poller.ts';
import { browserTimers, type Timers } from './timers.ts';
import type { Transport } from './transport.ts';
import type { Endpoint } from './wire.ts';

/** A new config read for each explicit click, never joined to an earlier read. */
function createClickTimeConfigRead<T>(
  transport: Transport,
  config: Endpoint,
  project: (body: unknown) => T | null,
  timers: Timers,
  path: '/api/config' | '/api/profile-files' = '/api/config',
) {
  return async (signal?: AbortSignal): Promise<T | null> => {
    const controller = new AbortController();
    const timeout = timers.setTimeout(() => {
      controller.abort();
    }, GET_TIMEOUT_MS);
    try {
      const outcome = await readEndpoint(
        transport,
        config,
        path,
        signal === undefined ? controller.signal : AbortSignal.any([controller.signal, signal]),
      );
      if (outcome.kind !== 'answered' || outcome.answer.status !== 200) return null;
      return project(outcome.answer.body);
    } finally {
      timers.clearTimeout(timeout);
    }
  };
}

export interface LaunchContext extends PageContext {
  readonly rendering: unknown;
}

/** `instances.<instance>.rendering` of a config body (an object, or null when absent). */
export function configRendering(body: unknown, instance: string): unknown {
  return ownPath(body, 'instances', instance, 'rendering') ?? null;
}

/** The C5 launch fence, including the rendering descriptor from this exact config body. */
export function launchContextFromConfig(body: unknown): LaunchContext | null {
  const context = contextFromConfig(body);
  return context === null
    ? null
    : { ...context, rendering: configRendering(body, context.instance) };
}

/** Existing editor callers retain their four-field page context. */
export function createClickTimeContextRead(
  transport: Transport,
  config: Endpoint,
  timers: Timers = browserTimers,
) {
  return createClickTimeConfigRead(transport, config, contextFromConfig, timers);
}

/** A revision and context from the same independent click-time config answer. */
export function createClickTimeRevisionContextRead(
  transport: Transport,
  config: Endpoint,
  timers: Timers = browserTimers,
) {
  return createClickTimeConfigRead(
    transport,
    config,
    (body) => {
      const context = contextFromConfig(body);
      const revision = configRevision(body);
      return context === null || revision === null ? null : { context, revision };
    },
    timers,
  );
}

export function createClickTimeLaunchContextRead(
  transport: Transport,
  config: Endpoint,
  timers: Timers = browserTimers,
) {
  return createClickTimeConfigRead(transport, config, launchContextFromConfig, timers);
}

export type TargetCheckResult =
  'same' | 'context_changed' | 'context_unconfirmed' | 'target_missing' | 'target_unconfirmed';

/** D5: fresh independent reads after each click. Caller rechecks epoch/generation before send. */
export function createClickTimeTargetCheck(
  transport: Transport,
  endpoints: { readonly config: Endpoint; readonly profileFiles: Endpoint },
  timers: Timers = browserTimers,
) {
  const readContext = createClickTimeContextRead(transport, endpoints.config, timers);
  const readFiles = createClickTimeConfigRead(
    transport,
    endpoints.profileFiles,
    profileFilesView,
    timers,
    '/api/profile-files',
  );
  return async (
    drawn: PageContext,
    name: string,
    signal?: AbortSignal,
  ): Promise<TargetCheckResult> => {
    const [context, files] = await Promise.all([readContext(signal), readFiles(signal)]);
    if (context === null) return 'context_unconfirmed';
    if (!sameContext(context, drawn)) return 'context_changed';
    if (files === null) return 'target_unconfirmed';
    return editingTarget(context, { name, generation: 0 }, files).blocked === null
      ? 'same'
      : 'target_missing';
  };
}
