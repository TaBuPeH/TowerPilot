import { entries, finite, id, list, own, ownPath, stringList, text } from './read.ts';
import { configContext, configRevision, type PauseContext } from './config.ts';
import { readiness, runDefined } from './readiness.ts';

/**
 * Local game accounts and automation profiles (profiles brief P1 to P5; the
 * pages contract families accounts_write, profile_files_write, profile_reads).
 * The server's answers and reads judged into plain values: which accounts and
 * profiles exist, what a write's answer proves, what a later read settles,
 * and the operator's tier input. Nothing here sends, reads or renders.
 *
 * A local account is a name chosen on this computer; it is not a game login
 * and is never detected. A profile file persisted is not active, an active
 * profile is not valid, and a valid one is not learned or verified: each is
 * reported separately.
 */

/** The six writes of the Profiles page; none carries an idempotency key, none is ever resent. */
export type ProfileWriteKind =
  | 'account_select'
  | 'account_create'
  | 'account_tier'
  | 'profile_activate'
  | 'profile_copy'
  | 'profile_promote';

export const PROFILE_WRITE_KINDS: readonly ProfileWriteKind[] = [
  'account_select',
  'account_create',
  'account_tier',
  'profile_activate',
  'profile_copy',
  'profile_promote',
];

export function profileWriteKind(kind: string): kind is ProfileWriteKind {
  return PROFILE_WRITE_KINDS.some((entry) => entry === kind);
}

/**
 * How much of the page context a later read must share with a write for that read to
 * settle it (F6P-03). The accounts list and the emulator's binding span the emulator
 * (`emulator`); `max_tier` is the active profile's, so a tier is judged only under the
 * account and profile it was sent for (`account_and_profile`); the profile file list hides
 * other accounts' profiles (dashboard.py filters them), so a file write is judged only
 * under the account it was sent for (`account`).
 */
export type SettlementScope = 'emulator' | 'account' | 'account_and_profile';

export function settlementScope(kind: ProfileWriteKind): SettlementScope {
  switch (kind) {
    case 'account_select':
    case 'account_create':
      return 'emulator';
    case 'account_tier':
      return 'account_and_profile';
    case 'profile_activate':
    case 'profile_copy':
    case 'profile_promote':
      return 'account';
  }
}

/**
 * Why a copy or promotion destination cannot be written, or null when it can (P5). The
 * shipped starter stays generic; the active profile is never replaced in place (F6P-04:
 * a promotion may persist problems it does not enforce, so a new file is validated and
 * activated by an explicit Activate later); a listed name needs the person's explicit
 * consent for exactly this destination.
 */
export type DestinationCheck = 'enter_name' | 'starter_name' | 'active_name' | 'confirm_overwrite';

export function destinationCheck(
  name: string,
  files: ProfileFilesView,
  consented: boolean,
): DestinationCheck | null {
  const trimmed = name.trim();
  if (trimmed === '') return 'enter_name';
  if (trimmed === STARTER_PROFILE) return 'starter_name';
  if (files.active !== null && trimmed === files.active) return 'active_name';
  const listed = files.files.some((file) => file.name === trimmed);
  return listed && !consented ? 'confirm_overwrite' : null;
}

/** One stored local account (`GET /api/accounts`). */
export interface AccountEntry {
  readonly id: string;
  readonly label: string | null;
}

export interface AccountsView {
  readonly accounts: readonly AccountEntry[];
  /** The account bound to this emulator, or null when none is (`active: null`). */
  readonly active: string | null;
  /** The operator's or a scan's highest unlocked tier of the active profile, or null. */
  readonly maxTier: number | null;
  /** True when this installation has personal settings an import could copy (P2). */
  readonly hasPersonalSettings: boolean | null;
  readonly instance: string | null;
}

/** A readable accounts answer, or null when the body is not one. */
export function accountsView(body: unknown): AccountsView | null {
  const rows = list(own(body, 'accounts'));
  const active = own(body, 'active');
  if (rows === undefined || (active !== null && id(active) === undefined)) return null;
  const accounts = rows.flatMap((row): AccountEntry[] => {
    const rowId = id(own(row, 'id'));
    return rowId === undefined ? [] : [{ id: rowId, label: text(own(row, 'label')) ?? null }];
  });
  const personal = own(body, 'has_personal_settings');
  return {
    accounts,
    active: id(active) ?? null,
    maxTier: finite(own(body, 'max_tier')) ?? null,
    hasPersonalSettings: typeof personal === 'boolean' ? personal : null,
    instance: id(own(body, 'instance')) ?? null,
  };
}

/** One profile file (`GET /api/profile-files`): its own name, never a path to act on. */
export interface ProfileFile {
  readonly name: string;
  /** A scan draft: not runnable, promoted into a profile first. */
  readonly draft: boolean;
  /** The shipped starter (`default`): copied, never edited. */
  readonly starter: boolean;
  readonly active: boolean;
  /** The server's note for this file, or null. */
  readonly note: string | null;
  /** The file's modification time (server seconds), or null. */
  readonly mtime: number | null;
  /** The file name the server lists (a promotion names a draft by it). */
  readonly file: string | null;
}

export interface ProfileFilesView {
  readonly files: readonly ProfileFile[];
  readonly active: string | null;
}

/** The shipped starter's name (dashboard.py: profiles/default.yaml "stays generic"). */
export const STARTER_PROFILE = 'default';

export function profileFilesView(body: unknown): ProfileFilesView | null {
  const rows = list(own(body, 'profiles'));
  const active = own(body, 'active');
  if (rows === undefined || (active !== null && id(active) === undefined)) return null;
  const files = rows.flatMap((row): ProfileFile[] => {
    const name = id(own(row, 'name'));
    if (name === undefined) return [];
    return [
      {
        name,
        draft: own(row, 'draft') === true,
        starter: name === STARTER_PROFILE,
        active: own(row, 'active') === true,
        note: text(own(row, 'note')) ?? null,
        mtime: finite(own(row, 'mtime')) ?? null,
        file: id(own(row, 'file')) ?? null,
      },
    ];
  });
  return { files, active: id(active) ?? null };
}

/** What a profile's source read says about it (`GET /api/profile-src/<name>`). */
export interface ProfileSourceView {
  readonly name: string;
  readonly problems: readonly string[];
  readonly warnings: readonly string[];
  /** The validator could not run: the profile's validity is unknown, never assumed. */
  readonly validatorError: string | null;
  /** Who set the highest unlocked tier: `operator` (typed on this page) or another source. */
  readonly tierSetBy: string | null;
  readonly maxTier: number | null;
}

export function profileSourceView(body: unknown): ProfileSourceView | null {
  const name = id(own(body, 'name'));
  const problems = stringList(own(body, 'problems'));
  const warnings = stringList(own(body, 'warnings'));
  if (own(body, 'ok') !== true || name === undefined || problems === undefined) return null;
  const player = own(own(body, 'profile'), 'player');
  return {
    name,
    problems,
    warnings: warnings ?? [],
    validatorError: text(own(body, 'validator_error')) ?? null,
    tierSetBy: text(own(player, 'max_tier_verified_by')) ?? null,
    maxTier: finite(own(player, 'max_tier')) ?? null,
  };
}

/**
 * Whether a profile may be activated, judged from its source read: the server
 * refuses a profile with problems (profile_files_write.activate_a_profile_with_problems_is_refused);
 * a profile whose validator could not run is not offered either (its validity is unknown).
 */
export type ActivationCheck =
  | { readonly kind: 'ready' }
  | {
      readonly kind: 'blocked';
      readonly reason: 'draft' | 'problems' | 'validator_unavailable' | 'unreadable';
    };

export function activationCheck(
  file: ProfileFile | null,
  source: ProfileSourceView | null,
): ActivationCheck {
  if (file?.draft === true) return { kind: 'blocked', reason: 'draft' };
  // The read must be of that very profile (the route answers the name it was asked for).
  if (source === null || file === null || source.name !== file.name)
    return { kind: 'blocked', reason: 'unreadable' };
  if (source.validatorError !== null) return { kind: 'blocked', reason: 'validator_unavailable' };
  if (source.problems.length > 0) return { kind: 'blocked', reason: 'problems' };
  return { kind: 'ready' };
}

/** The operator's highest unlocked tier (P3): the server accepts a whole number from 1 to 20. */
export const TIER_MIN = 1;
export const TIER_MAX = 20;

export type TierCheck =
  | { readonly kind: 'valid'; readonly value: number }
  | { readonly kind: 'invalid'; readonly reason: 'empty' | 'not_whole' | 'out_of_range' };

/** Judge typed text, never clamped or rounded (accounts_write.tier_zero, tier_21, tier_fraction). */
export function checkTier(raw: string): TierCheck {
  const trimmed = raw.trim();
  if (trimmed === '') return { kind: 'invalid', reason: 'empty' };
  if (!/^\d+$/u.test(trimmed)) return { kind: 'invalid', reason: 'not_whole' };
  const value = Number(trimmed);
  if (value < TIER_MIN || value > TIER_MAX) return { kind: 'invalid', reason: 'out_of_range' };
  return { kind: 'valid', value };
}

/** What a Profiles write sent, as its record keeps it (api/intents IntentMeta). */
export interface SentProfileWrite {
  readonly context?: (PauseContext & { readonly account: string | null }) | null;
  /** The account id, the profile name, or the copy or promotion destination. */
  readonly name?: string | undefined;
  readonly maxTier?: number | undefined;
  /** The original context's profile; required to settle a tier from its saved source. */
  readonly profile?: string | null | undefined;
  /** The destination was listed when the person clicked (an overwrite cannot be told apart). */
  readonly existed?: boolean | undefined;
  /** F6-14 PE1: the activation named no profile (unbind). */
  readonly unbind?: boolean | undefined;
}

export type ProfileWriteAnswer =
  /** An unbind receipt accepts the request; only the later configuration proves it. */
  | { readonly kind: 'accepted' }
  /** The server's receipt for exactly this request. */
  | {
      readonly kind: 'done';
      /** A promotion's validation problems: saved with them, never activated (P5). */
      readonly problems: readonly string[];
      /** A promotion: whether the abilities were verified (false is not a failure). */
      readonly abilitiesVerified: boolean | null;
      /** Activation: the backup file the server made of the previous configuration. */
      readonly backup: string | null;
    }
  /**
   * The server answered that it did not do this request (400, 404, 409 with `ok: false`).
   * An account creation refused after its profile file was written leaves that file
   * (accounts_write.create_then_the_config_save_failed_leaves_the_profile_file): a
   * refusal never proves that nothing changed, only that this request was not done.
   */
  | {
      readonly kind: 'refused';
      readonly code: string | null;
      readonly error: string | null;
      readonly problems: readonly string[];
    }
  /** No receipt: a 5xx, a text page, an answer that is not this request's. */
  | { readonly kind: 'unknown' };

function sameAccount(a: string, b: string): boolean {
  // The server stores account identifiers in lower case (accounts_write.create_a_fresh_account:
  // sent "Alpha", answered active "alpha").
  return a.toLowerCase() === b.toLowerCase();
}

function receipt(kind: ProfileWriteKind, body: unknown, sent: SentProfileWrite): boolean {
  if (own(body, 'ok') !== true) return false;
  switch (kind) {
    case 'account_select':
    case 'account_create': {
      const active = id(own(body, 'active'));
      return active !== undefined && sent.name !== undefined && sameAccount(active, sent.name);
    }
    case 'account_tier':
      return sent.maxTier !== undefined && own(body, 'max_tier') === sent.maxTier;
    case 'profile_activate':
      // F6-14 PE1: an unbind's receipt names no active profile.
      return sent.unbind === true
        ? own(body, 'active') === null
        : sent.name !== undefined && own(body, 'active') === sent.name;
    case 'profile_copy':
      return sent.name !== undefined && own(body, 'name') === sent.name;
    case 'profile_promote':
      return (
        sent.name !== undefined && own(body, 'name') === sent.name && own(body, 'applied') === true
      );
  }
}

export function profileWriteAnswer(
  kind: ProfileWriteKind,
  answer: { readonly status: number; readonly body: unknown },
  sent: SentProfileWrite,
): ProfileWriteAnswer {
  if (answer.status === 200 && receipt(kind, answer.body, sent)) {
    if (kind === 'profile_activate' && sent.unbind === true) return { kind: 'accepted' };
    const verified = own(answer.body, 'abilities_verified');
    return {
      kind: 'done',
      problems: stringList(own(answer.body, 'problems')) ?? [],
      abilitiesVerified: typeof verified === 'boolean' ? verified : null,
      backup: text(own(answer.body, 'backup')) ?? null,
    };
  }
  if ([400, 404, 409].includes(answer.status) && own(answer.body, 'ok') === false)
    return {
      kind: 'refused',
      code: id(own(answer.body, 'code')) ?? null,
      error: text(own(answer.body, 'error')) ?? null,
      problems: stringList(own(answer.body, 'problems')) ?? [],
    };
  return { kind: 'unknown' };
}

/**
 * One lifecycle rule for repeat-unbind prevention, including restored records.
 * A receipt accepts an unbind, but only D9's configuration proof resolves it.
 * Dismissal and replacement of the outcome line never remove this fence.
 */
export function unbindUnresolved(
  record: {
    readonly kind: string;
    readonly meta: SentProfileWrite;
    readonly phase: string;
    readonly answer: { readonly status: number; readonly body: unknown } | null;
    readonly readSettlement: string | null;
  },
  settlement: string | null = record.readSettlement,
): boolean {
  return (
    record.kind === 'profile_activate' &&
    record.meta.unbind === true &&
    settlement === null &&
    (record.phase === 'sending' ||
      record.phase === 'unconfirmed' ||
      (record.answer !== null &&
        profileWriteAnswer('profile_activate', record.answer, record.meta).kind === 'accepted'))
  );
}

/**
 * A read can confirm the requested state, but absence never proves a lost request
 * finished without applying: the original handler may still commit later. Leave
 * inconclusive requests unresolved and journaled. Account bindings use `GET /api/accounts`,
 * tiers require the original profile's explicit `player.max_tier` from its source,
 * and file writes use `GET /api/profile-files`. Accounts can report a fallback tier.
 */
export function settleProfileWrite(
  kind: ProfileWriteKind,
  sent: SentProfileWrite,
  reads: {
    readonly accounts: unknown;
    readonly files: unknown;
    readonly source?: unknown;
    readonly config?: unknown;
  },
): 'applied' | null {
  const { name } = sent;
  switch (kind) {
    case 'account_select': {
      const view = accountsView(reads.accounts);
      if (view === null || name === undefined) return null;
      return view.active !== null && sameAccount(view.active, name) ? 'applied' : null;
    }
    case 'account_create': {
      const view = accountsView(reads.accounts);
      if (view === null || name === undefined) return null;
      return view.accounts.some((entry) => sameAccount(entry.id, name)) ? 'applied' : null;
    }
    case 'account_tier': {
      const view = profileSourceView(reads.source);
      if (
        view === null ||
        view.name !== sent.profile ||
        view.maxTier === null ||
        sent.maxTier === undefined
      )
        return null;
      return view.maxTier === sent.maxTier ? 'applied' : null;
    }
    case 'profile_activate': {
      if (sent.unbind === true) {
        const context = configContext(reads.config);
        const expected = sent.context;
        if (context === null || expected == null || configRevision(reads.config) === null)
          return null;
        const account = id(ownPath(reads.config, 'instances', context.instance, 'account')) ?? null;
        const profile = own(reads.config, 'active_profile');
        return context.instance === expected.instance &&
          context.serial === expected.serial &&
          account === expected.account &&
          (profile === null || profile === undefined)
          ? 'applied'
          : null;
      }
      const view = profileFilesView(reads.files);
      if (view === null) return null;
      if (name === undefined) return null;
      return view.active === name ? 'applied' : null;
    }
    case 'profile_copy':
    case 'profile_promote': {
      const view = profileFilesView(reads.files);
      if (view === null || name === undefined || sent.existed === undefined) return null;
      const listed = view.files.some((file) => file.name === name && !file.draft);
      if (!listed) return null;
      // An overwrite of an existing profile cannot be told from the list: it stays unknown.
      return sent.existed ? null : 'applied';
    }
  }
}

/**
 * P4: what this installation's setup reads say, each kept distinct (connection
 * tools missing, a setup step running, original modules waiting to be
 * restored, runs whose recognition is not ready): codes only, never a claim
 * that a saved account has learned its screens.
 */
export interface SetupState {
  readonly tools: 'installed' | 'not_installed' | 'unknown';
  readonly calibration: 'running' | 'restore_pending' | 'idle' | 'unknown';
  readonly runs: {
    readonly ready: number;
    readonly notReady: number;
    readonly unknown: number;
  } | null;
}

export function setupState(reads: {
  readonly tools: unknown;
  readonly calibrate: unknown;
  readonly runs: unknown;
}): SetupState {
  const installed = own(reads.tools, 'installed');
  const running = own(reads.calibrate, 'running');
  const restore = own(reads.calibrate, 'restore_pending');
  const runEntries = entries(own(reads.runs, 'runs')).filter(
    ([key, run]) => key !== 'combo' && runDefined(run),
  );
  const lines = runEntries.map(([key]) => readiness(ownPath(reads.runs, 'readiness', key)).line);
  return {
    tools: typeof installed === 'boolean' ? (installed ? 'installed' : 'not_installed') : 'unknown',
    calibration:
      running === true
        ? 'running'
        : restore === true
          ? 'restore_pending'
          : running === false && restore === false
            ? 'idle'
            : 'unknown',
    runs:
      own(reads.runs, 'runs') === undefined
        ? null
        : {
            ready: lines.filter((line) => line === 'ready').length,
            notReady: lines.filter(
              (line) => line === 'not_ready' || line === 'needs_setup' || line === 'images_missing',
            ).length,
            unknown: lines.filter(
              (line) => line === 'readiness_unknown' || line === 'readiness_check_failed',
            ).length,
          },
  };
}
