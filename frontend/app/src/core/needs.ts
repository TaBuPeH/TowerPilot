import type { ContextRead } from './connection.ts';
import { collectionGuard, setupGuard } from './guards.ts';
import { classifyStatus } from './operationStatus.ts';
import { entries, finite, id, list, own, ownPath, record } from './read.ts';
import { readiness, runDefined } from './readiness.ts';
import { scanFresh } from './scan.ts';
import { decide, type ChipDecision } from './vocabulary.ts';

export interface GuardImpact {
  readonly start: 'off' | 'permitted' | 'unconfirmed';
  readonly collection: 'on_hold' | 'confirmed_manual_battle' | 'permitted' | 'unconfirmed';
  readonly commonBlock: boolean;
  readonly setupMessage: unknown;
  readonly collectionMessage: unknown;
}

/** F-038/F-083/S-100: each impact is derived exclusively from its own guard. */
export function guardImpact(setup: unknown, collection: unknown, observedAt: number): GuardImpact {
  const start = setupGuard(setup, observedAt);
  const collect = collectionGuard(collection, observedAt);
  return {
    start:
      start.kind === 'permit'
        ? 'permitted'
        : start.refusal.kind === 'blocks'
          ? 'off'
          : 'unconfirmed',
    collection:
      collect.kind === 'permit'
        ? own(collection, 'evidence') === 'collection_handoff'
          ? 'confirmed_manual_battle'
          : 'permitted'
        : collect.refusal.kind === 'blocks'
          ? 'on_hold'
          : 'unconfirmed',
    commonBlock:
      start.kind === 'refused' &&
      start.refusal.kind === 'blocks' &&
      collect.kind === 'refused' &&
      collect.refusal.kind === 'blocks' &&
      start.refusal.code !== null &&
      start.refusal.code === collect.refusal.code,
    setupMessage: own(setup, 'message'),
    collectionMessage: own(collection, 'message'),
  };
}

export interface HandoffTarget {
  readonly operationId: string;
  readonly setupBattleId: string;
  /** Opaque equality-only evidence binding, never display or order it. */
  readonly evidenceRevision: string;
}

/**
 * F-033: produces only a dialog target, never consent or an attestation. "No
 * process is listed" (EV-1) is judged on a fresh process scan: an empty list
 * from a stale or failed scan proves nothing (F4-R2-01).
 */
export function handoffTarget(status: unknown, observedAt: number): HandoffTarget | null {
  const setup = own(status, 'setup_battle_guard');
  const collection = own(status, 'collection_guard');
  const impact = guardImpact(setup, collection, observedAt);
  const operationId = id(own(setup, 'operation_id'));
  const setupBattleId = id(own(setup, 'setup_battle_id'));
  const evidenceRevision = id(own(setup, 'evidence_revision'));
  const processes = list(own(status, 'processes'));
  if (
    !impact.commonBlock ||
    own(setup, 'code') !== 'setup_battle_unresolved' ||
    operationId === undefined ||
    own(collection, 'operation_id') !== operationId ||
    setupBattleId === undefined ||
    evidenceRevision === undefined ||
    processes?.length !== 0 ||
    scanFresh(own(status, 'process_refresh'), finite(ownPath(status, 'harvest', 'server_time')))
      .kind !== 'known'
  )
    return null;
  return { operationId, setupBattleId, evidenceRevision };
}

export interface SetupOutcome {
  readonly decision: ChipDecision;
  readonly operationId: string | null;
  readonly updatedAt: number | null;
  readonly error: unknown;
  readonly setupBattle: unknown;
  readonly finalScreen: unknown;
  readonly impact: GuardImpact;
}

/** F-082/F-083: detector strings remain raw, including foreign/partial evidence. */
export function setupOutcome(
  read: ContextRead,
  status: unknown,
  observedAt: number,
): SetupOutcome | null {
  const impact = guardImpact(
    own(status, 'setup_battle_guard'),
    own(status, 'collection_guard'),
    observedAt,
  );
  const body = read?.kind === 'answered' && read.sameContext ? read.body : undefined;
  const operation = own(body, 'operation');
  const operationId = id(own(operation, 'id')) ?? null;
  const setup = ownPath(body, 'state', 'setup_battle');
  const screen = ownPath(body, 'state', 'final_screen');
  const fields = {
    operationId,
    updatedAt: finite(own(operation, 'updated_at')) ?? null,
    error: own(operation, 'error'),
    setupBattle:
      operationId !== null && own(setup, 'operation_id') === operationId ? setup : undefined,
    finalScreen:
      operationId !== null && own(screen, 'operation_id') === operationId ? screen : undefined,
    impact,
  };
  if (read?.kind !== 'answered' || !read.sameContext)
    return { ...fields, decision: decide('F-082', 'unknown', 'setup_status_unavailable', null) };
  if (own(body, 'restore_pending') === true)
    return { ...fields, decision: decide('F-082', 'blocked', 'equipment_not_restored', null) };
  const state = own(operation, 'status');
  const partial =
    fields.setupBattle !== undefined && own(fields.setupBattle, 'status') !== 'finished';
  const unverified =
    fields.finalScreen !== undefined && own(fields.finalScreen, 'verified') !== true;
  if (
    state === 'failed' ||
    state === 'cancelled' ||
    state === 'interrupted' ||
    (classifyStatus(state) === 'final' && (partial || unverified))
  )
    return {
      ...fields,
      decision: decide(
        'F-083',
        impact.start === 'off' || impact.collection === 'on_hold' ? 'blocked' : 'unknown',
        state === 'failed'
          ? 'setup_failed'
          : partial || unverified
            ? 'setup_partial'
            : 'setup_tracking_closed',
        null,
      ),
    };
  return null;
}

export function statusNeed(read: ContextRead): ChipDecision | null {
  if (read?.kind !== 'answered' || !read.sameContext)
    return decide('F-081', 'unknown', 'no_live_status', null);
  const scan = scanFresh(
    own(read.body, 'process_refresh'),
    finite(ownPath(read.body, 'harvest', 'server_time')),
  );
  return scan.kind === 'known'
    ? null
    : decide('F-081', 'unknown', 'process_discovery_unconfirmed', null);
}

/** F-098: the first incomplete setup prerequisite, in onboarding order. */
export function finishSetupNeed(
  tools: unknown,
  accounts: unknown,
  config: unknown,
  runs: unknown,
): 'tools' | 'profile' | 'connection' | 'screens' | null {
  if (own(tools, 'installed') !== true) return 'tools';
  if (id(own(accounts, 'active')) === undefined) return 'profile';
  const instance = id(own(config, 'active_instance'));
  if (instance === undefined || id(ownPath(config, 'instances', instance, 'serial')) === undefined)
    return 'connection';
  const definitions = own(runs, 'runs');
  const readies = own(runs, 'readiness');
  if (
    record(definitions) === undefined ||
    !entries(definitions).some(
      ([name, run]) =>
        name !== 'combo' && runDefined(run) && readiness(own(readies, name)).line === 'ready',
    )
  )
    return 'screens';
  return null;
}

/** F-084: local expansion only, no inferred empty-state health claim. */
export function needsView<T>(
  items: readonly T[],
  expanded: boolean,
): {
  readonly count: number;
  readonly visible: readonly T[];
  readonly showAll: boolean;
  readonly line: 'issues_reported' | 'no_unresolved_issues_reported';
} {
  return {
    count: items.length,
    visible: expanded ? items : items.slice(0, 3),
    showAll: !expanded && items.length > 3,
    line: items.length === 0 ? 'no_unresolved_issues_reported' : 'issues_reported',
  };
}
