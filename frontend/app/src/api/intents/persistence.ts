import {
  entries,
  finite,
  hasOwn,
  id,
  own,
  pid,
  record,
  revision,
  stringList,
  text,
} from '../../core/read.ts';
import { queuePlan } from '../../core/shoppingPlan.ts';
import { contextKey, readStoredContext } from '../context.ts';
import { profilePatchAnswer } from '../../core/profilePatch.ts';
import { keysWithPrefix, readJsonItem, writeJsonItem, type KeyValueStorage } from '../storage.ts';
import type { ApiPath } from '../transport.ts';
import { WRITE_ENDPOINT_IDS, type WriteEndpointId } from '../wire.ts';
import {
  INTENT_KINDS,
  unresolved,
  type IntentAnswer,
  type IntentMeta,
  type IntentRecord,
  type LostReason,
  type ReadSettlement,
} from './types.ts';

/**
 * Unresolved write records in this tab's session storage (ADR decision 5,
 * spec 7.6, OD-12): one key per context (emulator, account and profile), so a
 * record is restored on return or reload in its own context and hidden in any
 * other. Every unresolved record (in flight or unconfirmed) is kept until its
 * outcome is known, whether or not the person dismissed its line: Dismiss
 * (OD-10) hides only the line and clears no safety fact the record carries (an
 * unsettled Turn on's D-8 fence, an unresolved Allow game input, a Stop's
 * revision), so the dismissed flag is stored with it and the line stays hidden
 * after a reload (R9-01). A restored record is never resent: a request that
 * was in flight when the page went away is restored as unconfirmed ("page
 * reloaded"), and only a person's explicit resend may send it again.
 * Definite context_changed profile-patch refusals also remain, with their server
 * context, until a new explicit save supersedes them. They restore as refused:
 * a matching later read cannot change their outcome and no resend is offered.
 *
 * Sequence points belong to one page, so a restored record is placed before
 * every point of the new page (0): every read of the new page is a later read.
 */
export const STORAGE_PREFIX = 'tp.intents.v1:';

/** Every endpoint a write record may name: all writes but the C-20 diagnostics (never a record). */
const WRITE_ENDPOINTS: readonly WriteEndpointId[] = WRITE_ENDPOINT_IDS.filter(
  (id) => id !== 'diagnostics',
);

const READ_SETTLEMENTS: readonly ReadSettlement[] = [
  'off_confirmed',
  'applied',
  'not_applied',
  'stopped',
  'recorded',
];

const LOST_REASONS: readonly LostReason[] = [
  'window_expired',
  'network',
  'aborted',
  'page_reloaded',
];

/** Definite runtime refusals survive reload as refusals, with the server's context.
 * They cannot be settled by a read or resent; only a new explicit save supersedes them. */
export function retainedProfilePatchRefusal(item: {
  readonly kind: unknown;
  readonly phase: unknown;
  readonly answer: IntentAnswer | null | undefined;
  readonly supersededBySeq: number | null;
}): boolean {
  if (
    (item.kind !== 'profile_patch' && item.kind !== 'schedule_save') ||
    item.phase !== 'refused' ||
    item.supersededBySeq !== null ||
    item.answer == null ||
    item.answer.contract !== 'exact'
  )
    return false;
  const outcome = profilePatchAnswer(item.answer);
  return (
    outcome.kind === 'refused' &&
    (outcome.code !== 'context_changed' ||
      (item.answer.status === 409 &&
        readStoredContext(own(item.answer.body, 'current_context')) !== null))
  );
}

export function retainedContextRefusal(
  item: Parameters<typeof retainedProfilePatchRefusal>[0],
): boolean {
  return (
    retainedProfilePatchRefusal(item) &&
    own(item.answer?.body, 'code') === 'context_changed' &&
    readStoredContext(own(item.answer?.body, 'current_context')) !== null
  );
}

function stored(recordValue: IntentRecord): unknown {
  return {
    id: recordValue.id,
    kind: recordValue.kind,
    target: recordValue.target,
    context: recordValue.context,
    request: recordValue.request,
    key: recordValue.key,
    meta: recordValue.meta,
    sends: recordValue.sends,
    phase: recordValue.phase,
    answer: recordValue.answer,
    answeredAtMs: recordValue.answeredAtMs,
    lostReason: recordValue.lostReason,
    readSettlement: recordValue.readSettlement,
    superseded: recordValue.supersededBySeq !== null,
    dismissed: recordValue.dismissed,
    attemptRefusal: recordValue.attemptRefusal ?? null,
    accepted: recordValue.accepted === true,
  };
}

/** Keep unresolved records and definite runtime refusals under their context; remove empty keys. */
export function saveUnresolved(
  storage: KeyValueStorage | null,
  records: readonly IntentRecord[],
): void {
  if (storage === null) return;
  const groups = new Map<string, unknown[]>();
  for (const item of records) {
    if (!unresolved(item) && !retainedProfilePatchRefusal(item)) continue;
    const key = STORAGE_PREFIX + contextKey(item.context);
    const group = groups.get(key) ?? [];
    group.push(stored(item));
    groups.set(key, group);
  }
  for (const key of keysWithPrefix(storage, STORAGE_PREFIX)) {
    if (!groups.has(key)) writeJsonItem(storage, key, undefined);
  }
  for (const [key, group] of groups) writeJsonItem(storage, key, group);
}

/** A saved changes map: routine scalars or a full version 2 shopping plan. */
function readChanges(value: unknown): Readonly<Record<string, unknown>> | null {
  if (record(value) === undefined) return null;
  const out: Record<string, unknown> = {};
  for (const [path, item] of entries(value)) {
    if (path === 'shopping_plan' && queuePlan(item) !== null) {
      out[path] = item;
      continue;
    }
    const number = finite(item);
    if (typeof item === 'boolean') out[path] = item;
    else if (number !== undefined) out[path] = number;
    else return null;
  }
  return Object.freeze(out);
}

/** A saved raw draft: dotted paths to the text or switch a person entered (F6R-02). */
function readRawDraft(value: unknown): Readonly<Record<string, string | boolean>> | null {
  if (record(value) === undefined) return null;
  const out: Record<string, string | boolean> = {};
  for (const [path, item] of entries(value)) {
    if (typeof item !== 'string' && typeof item !== 'boolean') return null;
    out[path] = item;
  }
  return Object.freeze(out);
}

function readMeta(value: unknown): IntentMeta | null {
  if (record(value) === undefined) return null;
  // Each field is checked below before it is kept; `patchValue` is any JSON by design.
  const meta: Record<string, unknown> = {};
  for (const name of [
    'draftScope',
    'battleLabel',
    'name',
    'module',
    'serial',
    'instance',
    'emulator',
    'index',
  ]) {
    const field = own(value, name);
    if (field === undefined) continue;
    const valid = text(field);
    if (valid === undefined) return null;
    meta[name] = valid;
  }
  const maxTier = own(value, 'maxTier');
  if (maxTier !== undefined) {
    const valid = finite(maxTier);
    if (valid === undefined) return null;
    meta['maxTier'] = valid;
  }
  const existed = own(value, 'existed');
  if (existed !== undefined) {
    if (typeof existed !== 'boolean') return null;
    meta['existed'] = existed;
  }
  const unbind = own(value, 'unbind');
  if (unbind !== undefined) {
    if (unbind !== true) return null;
    meta['unbind'] = true;
  }
  const draft = own(value, 'draft');
  if (draft !== undefined) {
    const valid = readRawDraft(draft);
    if (valid === null) return null;
    meta['draft'] = valid;
  }
  for (const name of [
    'operationId',
    'preset',
    'setupBattleId',
    'evidenceRevision',
    'battleId',
    'generation',
    'action',
    'blueprint',
    'profileRevision',
  ]) {
    const field = own(value, name);
    if (field === undefined) continue;
    const valid = id(field);
    if (valid === undefined) return null;
    meta[name] = valid;
  }
  const expected = own(value, 'expectedRevision');
  if (expected !== undefined) {
    const valid = revision(expected);
    if (valid === undefined) return null;
    meta['expectedRevision'] = valid;
  }
  const processId = own(value, 'pid');
  if (processId !== undefined) {
    const valid = pid(processId);
    if (valid === undefined) return null;
    meta['pid'] = valid;
  }
  const created = own(value, 'created');
  if (created !== undefined) {
    const valid = finite(created);
    if (valid === undefined) return null;
    meta['created'] = valid;
  }
  const changes = own(value, 'changes');
  if (changes !== undefined) {
    const valid = readChanges(changes);
    if (valid === null) return null;
    meta['changes'] = valid;
  }
  // A settings save's sent values and a patch's value: JSON as the body froze it (read
  // back to judge a later read, never re-sent from here).
  const values = own(value, 'values');
  if (values !== undefined) {
    if (record(values) === undefined) return null;
    meta['values'] = Object.freeze(Object.fromEntries(entries(values)));
  }
  const base = own(value, 'baseRevision');
  if (base !== undefined) {
    const valid = id(base);
    if (valid === undefined) return null;
    meta['baseRevision'] = valid;
  }
  const patchPath = own(value, 'patchPath');
  if (patchPath !== undefined) {
    const valid = stringList(patchPath);
    if (valid === undefined) return null;
    meta['patchPath'] = Object.freeze([...valid]);
  }
  const resetPaths = own(value, 'resetPaths');
  if (resetPaths !== undefined) {
    const valid = stringList(resetPaths);
    if (valid === undefined) return null;
    meta['resetPaths'] = Object.freeze([...valid]);
  }
  const patchRemove = own(value, 'patchRemove');
  if (patchRemove !== undefined) {
    if (typeof patchRemove !== 'boolean') return null;
    meta['patchRemove'] = patchRemove;
  }
  if (hasOwn(value, 'patchValue')) meta['patchValue'] = own(value, 'patchValue');
  return Object.freeze(meta);
}

function readAnswer(value: unknown): IntentAnswer | null | undefined {
  if (value === null) return null;
  const status = own(value, 'status');
  const contract = own(value, 'contract');
  const issues = stringList(own(value, 'issues'));
  if (
    typeof status !== 'number' ||
    !Number.isInteger(status) ||
    (contract !== 'exact' && contract !== 'drift') ||
    issues === undefined
  ) {
    return undefined;
  }
  return Object.freeze({ status, body: own(value, 'body'), contract, issues });
}

/** One stored record, checked field by field; null when anything is missing or malformed. */
export function readStoredRecord(value: unknown): IntentRecord | null {
  const recordId = id(own(value, 'id'));
  const kind = INTENT_KINDS.find((entry) => entry === own(value, 'kind'));
  const target = id(own(value, 'target'));
  const rawContext = own(value, 'context');
  const context = rawContext === null ? null : readStoredContext(rawContext);
  const request = own(value, 'request');
  const endpoint = WRITE_ENDPOINTS.find((entry) => entry === own(request, 'endpoint'));
  const path = text(own(request, 'path'));
  const body = text(own(request, 'body'));
  const rawKey = own(value, 'key');
  const key = rawKey === null ? null : id(rawKey);
  const meta = readMeta(own(value, 'meta'));
  const sends = own(value, 'sends');
  const phase = own(value, 'phase');
  const answer = readAnswer(own(value, 'answer'));
  const rawLost = own(value, 'lostReason');
  const lost = rawLost === null ? null : LOST_REASONS.find((entry) => entry === rawLost);
  const superseded = own(value, 'superseded');
  // Absent in records written before the field existed: their lines were shown.
  const dismissed = own(value, 'dismissed') ?? false;
  const rawAnsweredAt = own(value, 'answeredAtMs');
  const answeredAtMs = rawAnsweredAt === null ? null : finite(rawAnsweredAt);
  // Absent in records written before the field existed: nothing was settled.
  const rawSettlement = own(value, 'readSettlement') ?? null;
  const readSettlement =
    rawSettlement === null ? null : READ_SETTLEMENTS.find((entry) => entry === rawSettlement);
  // Absent in records written before the field existed: no attempt was refused.
  const rawRefusal = own(value, 'attemptRefusal') ?? null;
  const attemptRefusal = rawRefusal === null ? null : readAnswer(rawRefusal);
  // Absent in records written before the field existed: the person accepted nothing.
  const accepted = own(value, 'accepted') ?? false;
  const refused = retainedProfilePatchRefusal({
    kind,
    phase,
    answer,
    supersededBySeq: superseded === false ? null : 0,
  });
  if (
    recordId === undefined ||
    kind === undefined ||
    target === undefined ||
    (rawContext !== null && context === null) ||
    endpoint === undefined ||
    path?.startsWith('/api/') !== true ||
    body === undefined ||
    key === undefined ||
    meta === null ||
    typeof sends !== 'number' ||
    !Number.isSafeInteger(sends) ||
    sends < 1 ||
    (phase !== 'sending' && phase !== 'unconfirmed' && !refused) ||
    (refused && (readSettlement !== null || lost !== null || accepted !== false)) ||
    answer === undefined ||
    lost === undefined ||
    typeof superseded !== 'boolean' ||
    typeof dismissed !== 'boolean' ||
    answeredAtMs === undefined ||
    readSettlement === undefined ||
    attemptRefusal === undefined ||
    typeof accepted !== 'boolean'
  ) {
    return null;
  }
  // Consumers parse the frozen body to recover their draft. Reject corrupt storage here,
  // before any editor can observe it; every write body is a JSON object.
  try {
    const parsed: unknown = JSON.parse(body);
    if (record(parsed) === undefined) return null;
  } catch {
    return null;
  }
  const apiPath: ApiPath = `/api/${path.slice('/api/'.length)}`;
  return Object.freeze({
    id: recordId,
    kind,
    target,
    context,
    epoch: -1,
    request: Object.freeze({ endpoint, path: apiPath, body }),
    key,
    meta,
    firstSentSeq: 0,
    sentSeq: 0,
    sends,
    phase: refused ? 'refused' : 'unconfirmed',
    answer,
    answerSeq: answer === null ? null : 0,
    answeredAtMs,
    lostReason: refused ? null : phase === 'sending' ? 'page_reloaded' : (lost ?? 'page_reloaded'),
    // A settlement a read proved before the reload stays proven (R8-06): no resend is offered.
    readSettlement,
    supersededBySeq: superseded ? 0 : null,
    dismissed,
    restored: true,
    attemptRefusal,
    accepted,
  });
}

/** Every stored record of every context, each checked; malformed ones are skipped, never invented. */
export function restoreUnresolved(storage: KeyValueStorage | null): readonly IntentRecord[] {
  if (storage === null) return [];
  const out: IntentRecord[] = [];
  const seen = new Set<string>();
  for (const key of keysWithPrefix(storage, STORAGE_PREFIX)) {
    const group = readJsonItem(storage, key);
    if (!Array.isArray(group)) continue;
    for (const item of group) {
      const restored = readStoredRecord(item);
      if (restored === null || seen.has(restored.id)) continue;
      if (STORAGE_PREFIX + contextKey(restored.context) !== key) continue;
      seen.add(restored.id);
      out.push(restored);
    }
  }
  return out;
}
