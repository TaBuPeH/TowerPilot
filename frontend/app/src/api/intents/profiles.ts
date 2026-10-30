import { contextKey, expectedContext, type PageContext } from '../context.ts';
import type { ApiPath } from '../transport.ts';
import type { WriteEndpointId } from '../wire.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';
import type { IntentKind, IntentMeta } from './types.ts';

/**
 * The Profiles page's writes (profiles brief P1 to P5): one record per click,
 * the body frozen at the click, never resent (none carries an idempotency
 * key; a lost answer is settled by a later read of this emulator's accounts or
 * profile files, store/fences.ts). The account binding writes (select,
 * create, activate) share one target per emulator, the copies and promotions
 * another, the tier one per account and profile: a newer request on a target
 * makes the older line history (S-011) without touching its record's fence.
 */
export type ProfileWrite =
  | {
      readonly kind: 'account_select';
      readonly id: string;
    }
  | {
      readonly kind: 'account_create';
      readonly id: string;
      readonly label: string | null;
      readonly importCurrent: boolean;
    }
  | { readonly kind: 'account_tier'; readonly maxTier: number }
  /** `name: null` unbinds the active profile (F6-14 PE1): the scheduler runs its legacy rules. */
  | { readonly kind: 'profile_activate'; readonly name: string | null }
  | {
      readonly kind: 'profile_copy';
      readonly from: string;
      readonly name: string;
      readonly overwrite: boolean;
      readonly existed: boolean;
    }
  | {
      readonly kind: 'profile_promote';
      /** The draft's file name as the profile list names it (`main.draft.yaml`). */
      readonly draft: string;
      readonly name: string;
      readonly base: string;
      readonly overwrite: boolean;
      readonly existed: boolean;
    };

interface Request {
  readonly endpoint: WriteEndpointId;
  readonly path: ApiPath;
  readonly body: Readonly<Record<string, unknown>>;
  readonly meta: IntentMeta;
}

function request(write: ProfileWrite): Request {
  switch (write.kind) {
    case 'account_select':
      return {
        endpoint: 'accountsWrite',
        path: '/api/accounts',
        body: { id: write.id },
        meta: { name: write.id },
      };
    case 'account_create':
      return {
        endpoint: 'accountsWrite',
        path: '/api/accounts',
        body: {
          id: write.id,
          ...(write.label === null ? {} : { label: write.label }),
          create: true,
          ...(write.importCurrent ? { import_current: true } : {}),
        },
        meta: { name: write.id },
      };
    case 'account_tier':
      return {
        endpoint: 'accountTier',
        path: '/api/account-tier',
        body: { max_tier: write.maxTier },
        meta: { maxTier: write.maxTier },
      };
    case 'profile_activate':
      return {
        endpoint: 'profileActivate',
        path: '/api/profile-activate',
        body: { name: write.name },
        meta: write.name === null ? { unbind: true } : { name: write.name },
      };
    case 'profile_copy':
      return {
        endpoint: 'profileCopy',
        path: '/api/profile-copy',
        body: {
          from: write.from,
          name: write.name,
          ...(write.overwrite ? { overwrite: true } : {}),
        },
        meta: { name: write.name, existed: write.existed },
      };
    case 'profile_promote':
      return {
        endpoint: 'profilePromote',
        path: '/api/profile-promote',
        body: {
          draft: write.draft,
          name: write.name,
          base: write.base,
          ...(write.overwrite ? { overwrite: true } : {}),
        },
        meta: { name: write.name, existed: write.existed },
      };
  }
}

/** The target a Profiles write replaces older lines on. */
export function profileTarget(kind: IntentKind, context: PageContext): string {
  const emulator = `${context.instance}:${context.serial}`;
  switch (kind) {
    case 'account_tier':
      return `profiles:tier:${contextKey(context)}`;
    case 'profile_copy':
    case 'profile_promote':
      return `profiles:files:${emulator}`;
    default:
      return `profiles:binding:${emulator}`;
  }
}

export function sendProfileWrite(
  machine: IntentMachine,
  write: ProfileWrite,
  context: PageContext,
  draft: {
    readonly scope: string;
    readonly values: Readonly<Record<string, string | boolean>>;
  } | null,
): SendResult {
  const target = profileTarget(write.kind, context);
  const built = request(write);
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: write.kind,
      target,
      endpoint: built.endpoint,
      path: built.path,
      body: { ...built.body, expected_context: expectedContext(context) },
      key: null,
      meta: {
        ...built.meta,
        ...(draft === null ? {} : { draftScope: draft.scope, draft: draft.values }),
      },
      context,
      supersedes: [],
    }),
  );
}
