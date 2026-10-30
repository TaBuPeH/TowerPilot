import { contextKey, expectedContext, type PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * The Collection page's writes (collection brief C1 to C6) and every typed settings module
 * save: one record per click, the body frozen at the click, never resent (neither carries
 * an idempotency key; a lost answer is settled by a later read, store/fences.ts).
 */
export interface SettingsSave {
  /** The context the settings read named: the server refuses a save for another. */
  readonly settingsContext: {
    readonly instance: string;
    readonly account: string | null;
    readonly profile: string | null;
  };
  readonly module: string;
  readonly revision: string;
  readonly values: Readonly<Record<string, unknown>>;
  readonly draft: {
    readonly scope: string;
    readonly values: Readonly<Record<string, string | boolean>>;
  } | null;
}

/** POST /api/settings `{context, module, revision, values}` for one module. */
export function sendSettingsSave(
  machine: IntentMachine,
  save: SettingsSave,
  context: PageContext,
): SendResult {
  const target = `settings:${save.module}:${contextKey(context)}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'settings_save',
      target,
      endpoint: 'settingsSave',
      path: '/api/settings',
      body: {
        context: {
          account: save.settingsContext.account,
          instance: save.settingsContext.instance,
          profile: save.settingsContext.profile,
        },
        module: save.module,
        revision: save.revision,
        values: save.values,
      },
      key: null,
      meta: {
        module: save.module,
        values: save.values,
        baseRevision: save.revision,
        ...(save.draft === null ? {} : { draftScope: save.draft.scope, draft: save.draft.values }),
      },
      context,
      supersedes: [],
    }),
  );
}

export interface ProfilePatch {
  /** Some fenced delete contracts omit value; the shopping library keeps value:null. */
  readonly omitDeleteValue?: boolean;
  /** The record line it belongs to (a policy action, or the chores). */
  readonly target: string;
  /** The named file to patch; inactive Collection targets differ from context.profile. */
  readonly profile: string;
  readonly path: readonly string[];
  readonly remove: boolean;
  readonly value: unknown;
  readonly revision: string;
  readonly draft: {
    readonly scope: string;
    readonly values: Readonly<Record<string, string | boolean>>;
  } | null;
}

/** POST /api/profile-patch `{expected_revision, name, path, value}` or with `op: "delete"`. */
export function sendProfilePatch(
  machine: IntentMachine,
  patch: ProfilePatch,
  context: PageContext,
): SendResult {
  return sendOnce(machine, patch.target, context, () =>
    machine.send({
      kind: 'profile_patch',
      target: patch.target,
      endpoint: 'profilePatch',
      path: '/api/profile-patch',
      body: patch.remove
        ? {
            expected_revision: patch.revision,
            name: patch.profile,
            op: 'delete',
            path: patch.path,
            expected_context: expectedContext(context),
            ...(patch.omitDeleteValue === true ? {} : { value: null }),
          }
        : {
            expected_revision: patch.revision,
            name: patch.profile,
            path: patch.path,
            value: patch.value,
            expected_context: expectedContext(context),
          },
      key: null,
      meta: {
        name: patch.profile,
        baseRevision: patch.revision,
        patchPath: patch.path,
        patchRemove: patch.remove,
        ...(patch.remove ? {} : { patchValue: patch.value }),
        ...(patch.draft === null
          ? {}
          : { draftScope: patch.draft.scope, draft: patch.draft.values }),
      },
      context,
      supersedes: [],
    }),
  );
}
