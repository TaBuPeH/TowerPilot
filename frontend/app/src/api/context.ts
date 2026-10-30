import { configContext } from '../core/config.ts';
import { id, own, ownPath } from '../core/read.ts';
import { readJsonItem, writeJsonItem, type KeyValueStorage } from './storage.ts';

/**
 * The context fence (safety rule S-014, spec 7.2 "every answer is fenced by
 * the context it was requested in"). The page's context is the active
 * emulator and account as `GET /api/config` names them: `active_instance`,
 * that instance's `serial` and `account` (`player/accounts.identity` reads
 * `instances.{instance}.account`), and `active_profile`. A change of any of
 * the four starts a new epoch: every read and write records the epoch it was
 * made in, and an answer from an older epoch is dropped, never applied and
 * never resent into the new context.
 *
 * The last context is recorded in this tab's session storage: it is "the
 * context this browser session recorded" of INV-1 (a Pause on a cold page),
 * and the key under which unresolved writes are restored after a reload.
 */
export interface PageContext {
  readonly instance: string;
  readonly serial: string;
  readonly account: string | null;
  readonly profile: string | null;
}

/** The mutation fence has exactly these four fields, including explicit null values. */
export function expectedContext(context: PageContext): PageContext {
  return {
    instance: context.instance,
    account: context.account,
    profile: context.profile,
    serial: context.serial,
  };
}

const SESSION_KEY = 'tp.context.v1';

/** The page context a config body names, or null when it does not name instance and serial. */
export function contextFromConfig(body: unknown): PageContext | null {
  const named = configContext(body);
  if (named === null) return null;
  const account = ownPath(body, 'instances', named.instance, 'account');
  const profile = own(body, 'active_profile');
  return Object.freeze({
    instance: named.instance,
    serial: named.serial,
    account: id(account) ?? null,
    profile: id(profile) ?? null,
  });
}

/** True when both name the same emulator, account and profile. */
export function sameContext(a: PageContext | null, b: PageContext | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    a.instance === b.instance &&
    a.serial === b.serial &&
    a.account === b.account &&
    a.profile === b.profile
  );
}

/** A stable storage key for a context (`none` for a record made before any context was known). */
export function contextKey(context: PageContext | null): string {
  return context === null
    ? 'none'
    : JSON.stringify([context.instance, context.serial, context.account, context.profile]);
}

/** A context read back from storage: every field checked, nothing invented. */
export function readStoredContext(value: unknown): PageContext | null {
  const instance = id(own(value, 'instance'));
  const serial = id(own(value, 'serial'));
  if (instance === undefined || serial === undefined) return null;
  const account = own(value, 'account');
  const profile = own(value, 'profile');
  if (
    (account !== null && id(account) === undefined) ||
    (profile !== null && id(profile) === undefined)
  ) {
    return null;
  }
  return Object.freeze({
    instance,
    serial,
    account: id(account) ?? null,
    profile: id(profile) ?? null,
  });
}

export type ContextChange =
  /** The config named no context, or the one already known. */
  | { readonly kind: 'unchanged' }
  /**
   * The first context this page learned (none was known): a new epoch too, because reads
   * started before it cannot be tied to it (they are dropped and read again).
   */
  | { readonly kind: 'established'; readonly context: PageContext; readonly epoch: number }
  /** A different context: a new epoch began. */
  | { readonly kind: 'changed'; readonly context: PageContext; readonly epoch: number };

export interface ContextTracker {
  /** The current epoch; every read and write records it at its start. */
  readonly epoch: () => number;
  /** The current context: the latest config's, or the session record before any config answered. */
  readonly current: () => PageContext | null;
  /** True once a config read of this page named the context. */
  readonly confirmed: () => boolean;
  /** The context this browser session recorded (INV-1's third source). */
  readonly sessionRecorded: () => PageContext | null;
  /** Judge an answered config body; a different context starts a new epoch. */
  readonly observeConfig: (body: unknown) => ContextChange;
}

export function createContextTracker(storage: KeyValueStorage | null): ContextTracker {
  let recorded = readStoredContext(readJsonItem(storage, SESSION_KEY));
  let current: PageContext | null = recorded;
  let confirmed = false;
  let epoch = 0;
  const record = (context: PageContext) => {
    recorded = context;
    writeJsonItem(storage, SESSION_KEY, context);
  };
  return {
    epoch: () => epoch,
    current: () => current,
    confirmed: () => confirmed,
    sessionRecorded: () => recorded,
    observeConfig: (body) => {
      const named = contextFromConfig(body);
      if (named === null) return { kind: 'unchanged' };
      const before = current;
      current = named;
      confirmed = true;
      record(named);
      if (before === null) {
        epoch += 1;
        return { kind: 'established', context: named, epoch };
      }
      if (sameContext(before, named)) return { kind: 'unchanged' };
      epoch += 1;
      return { kind: 'changed', context: named, epoch };
    },
  };
}
