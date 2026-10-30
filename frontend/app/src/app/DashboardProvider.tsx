import { useEffect, useMemo, type ReactElement } from 'react';
import type { CopyNode } from '../copy/text.ts';
import {
  DashboardContext,
  type DashboardContextValue,
  type FaultSet,
} from '../features/dashboard/context.ts';
import type { DashboardData } from './wiring.ts';

/** The session key a browser test sets to make named parts fail while drawing (X4 to X6). */
export const FAULTS_KEY = 'tp.faults';

/**
 * The parts a test asked to fail, read from the session key at every draw (a
 * browser test may add one after the first draw: X4, X5 warm). None on the
 * real page. Never throws.
 */
export function readFaults(): FaultSet {
  return {
    has: (part) => {
      try {
        const raw = window.sessionStorage.getItem(FAULTS_KEY) ?? '';
        return raw.split(',').some((entry) => entry.trim() === part);
      } catch {
        return false;
      }
    },
  };
}

function describe(error: unknown): { readonly message: string; readonly stack?: string } {
  if (error instanceof Error) {
    return error.stack === undefined
      ? { message: `${error.name}: ${error.message}` }
      : { message: `${error.name}: ${error.message}`, stack: error.stack };
  }
  return { message: String(error) };
}

/**
 * Starts the page's data layer while mounted (one poller, one write machine;
 * StrictMode's second mount starts it again) and hands features what they need
 * through DashboardContext: the store, the commands, one diagnostic per
 * distinct fault (C-20, F-107: a render fault, an uncaught error, an unhandled
 * rejection; never retried) and the test-only fault set.
 */
export function DashboardProvider({
  data,
  faults,
  children,
}: {
  readonly data: DashboardData;
  readonly faults: FaultSet;
  readonly children: CopyNode;
}): ReactElement {
  useEffect(() => {
    data.start();
    return () => {
      data.stop();
    };
  }, [data]);

  useEffect(() => {
    const onError = (event: ErrorEvent) => {
      const { message, stack } = describe(event.error ?? event.message);
      data.diagnostics.report(`error:${message}:${event.filename}:${String(event.lineno)}`, {
        kind: 'javascript_error',
        page: 'dashboard',
        message,
        ...(stack === undefined ? {} : { stack }),
      });
    };
    const onRejection = (event: PromiseRejectionEvent) => {
      const { message, stack } = describe(event.reason);
      data.diagnostics.report(`rejection:${message}`, {
        kind: 'unhandled_rejection',
        page: 'dashboard',
        message,
        ...(stack === undefined ? {} : { stack }),
      });
    };
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);
    return () => {
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
    };
  }, [data]);

  const value = useMemo<DashboardContextValue>(
    () => ({
      store: data.store,
      preview: data.preview,
      commands: data.commands,
      faults,
      reportFault: (part, error) => {
        const { message, stack } = describe(error);
        data.diagnostics.report(`render:${part}:${message}`, {
          kind: 'javascript_error',
          page: 'dashboard',
          message: `${part}: ${message}`,
          ...(stack === undefined ? {} : { stack }),
        });
      },
    }),
    [data, faults],
  );
  return <DashboardContext value={value}>{children}</DashboardContext>;
}
