import {
  automationSlot,
  automationWord,
  type OperationRowView,
  type StopControl,
} from '../../core/automation.ts';
import { battleIdentity, battleView, freshBattleReport } from '../../core/battle.ts';
import { blueprintOf } from '../../core/gemSource.ts';
import { entries, finite, id, list, own, ownPath, stringList } from '../../core/read.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { ClearFlagCommand, StopCommand } from '../commands.ts';
import { controlOf, type LastKnownStop } from '../lastKnownStops.ts';
import { noticedOperations } from '../ownerNotice.ts';
import { answeredBody, contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { chipVM, type ChipVM } from '../viewModels.ts';
import {
  controlFacts,
  highestStopRevision,
  latestOnTarget,
  type ControlFacts,
} from './controlFacts.ts';

/**
 * Control / Automation (spec 7.3, 3.6.4; INV-3, S-027, S-028, S-050, S-058 to
 * S-061, S-092). One row per operation id and per process owner, each with its
 * own chip and Stop. A rendered Stop is enabled unless its target identity is
 * incomplete (INV-3), and a click while one is in flight sends nothing new.
 * Every cancel sends the highest revision this context knows for its id
 * (S-050), and every command carries the epoch it was drawn in (S-014).
 *
 * `stops` is every Stop the slot offers: each row's own, then every last known
 * Stop of this context that no current row covers (S-092, B-02), then every
 * operation an owner notice of this context named that neither covers (INV-3;
 * dismissed notices included, OD-10), the last two marked `lastKnown`. A read
 * that cannot prove the owner gone (a failed read, an answered
 * `status_failed`, a missing harvest) therefore keeps a working Stop in the
 * normal slot, not only in the render fallback, and dismissing an owner notice
 * never takes its operation's Stop away.
 */
export type StopOrigin = 'read' | 'last_known' | 'owner_notice';

export interface StopTargetVM {
  /** `operation:<id>` or `process:<pid>:<created>`; stable for focus. */
  readonly key: string;
  readonly kind: 'cancel' | 'kill' | 'disabled' | 'none';
  readonly shown: boolean;
  readonly enabled: boolean;
  /** Why it is disabled (INV-3: waiting_for_fresh_status, created_missing, operation_unidentified, process_unidentified). */
  readonly reason: string | null;
  readonly sending: boolean;
  readonly operation: ServerText<'id'> | null;
  readonly pid: number | null;
  /** Kept from an earlier read or an owner notice of this context: the newest read does not show it (S-092). */
  readonly lastKnown: boolean;
  /** Where the page learned of the target: the newest read, a last known Stop, or an owner notice. */
  readonly origin: StopOrigin;
  readonly command: StopCommand | null;
}

export interface OperationRowVM {
  readonly key: string;
  readonly source: 'process_row' | 'operation' | 'owner_operation' | 'owner_process';
  readonly operation: ServerText<'id'> | null;
  readonly pid: number | null;
  readonly seen: ChipVM;
  readonly stop: StopTargetVM;
  readonly purpose: 'gems' | 'run' | 'learning' | 'process';
  readonly label: ServerText<'name'> | null;
  readonly created: number | null;
  readonly details: {
    readonly cmdline: ServerText<'detail'> | null;
    readonly instance: ServerText<'name'> | null;
    readonly step: ServerText<'detail'> | null;
    readonly recovery: ServerText<'detail'> | null;
  };
  readonly battle: {
    readonly line: 'wave' | 'last_wave' | 'wave_pending' | 'waiting' | 'none' | 'unknown';
    readonly wave: number | null;
    readonly ageSec: number | null;
  };
  readonly scheduledStop: boolean | null;
  readonly clearFlag: ClearFlagCommand | null;
}

export interface AutomationSlotVM {
  /** The summary chip when there is no row (AS1 to AS5), else null. */
  readonly summary: ChipVM | null;
  /** The slot's one word for the 9.1 matrix: its one row's chip, or the summary. */
  readonly word: ChipVM;
  readonly rows: readonly OperationRowVM[];
  /** Every Stop of the slot: the rows' shown Stops, then the last known ones no row covers. */
  readonly stops: readonly StopTargetVM[];
  readonly setup: { readonly step: ServerText<'detail'> | null } | null;
}

/** Presentation facts only; core still owns every observed state and Stop decision. */
function rowDetails(state: DashboardState, row: OperationRowView) {
  const raw = row.raw;
  const operation = row.source === 'process_row' ? own(raw, 'operation') : raw;
  const kind =
    row.source === 'owner_operation' ? own(raw, 'operation_kind') : own(operation, 'kind');
  const runner = own(raw, 'runner');
  const purpose: OperationRowVM['purpose'] =
    kind === 'gems' || runner === 'gem_collector' || runner === 'gems'
      ? 'gems'
      : kind === 'calibrate' || runner === 'calibrate'
        ? 'learning'
        : kind === 'run' || runner === 'orchestrator'
          ? 'run'
          : 'process';
  const context = own(operation, 'context') ?? own(raw, 'operation_context');
  const battle = own(answeredBody(state, 'battlesActive'), 'battle');
  const blueprint =
    own(battle, 'operation_id') === row.operationId ? id(own(battle, 'blueprint')) : undefined;
  const runs = own(answeredBody(state, 'runs'), 'runs');
  const run =
    id(own(context, 'selected_run')) ??
    (blueprint === undefined
      ? undefined
      : entries(runs).find(([key]) => blueprintOf(key) === blueprint)?.[0]);
  const label =
    purpose === 'run'
      ? fromServer(
          'name',
          own(raw, 'label') ?? (run === undefined ? undefined : ownPath(runs, run, 'label')),
        )
      : null;
  const command =
    typeof own(raw, 'cmdline') === 'string'
      ? own(raw, 'cmdline')
      : stringList(own(raw, 'cmdline'))?.join(' ');
  const flag = own(raw, 'stop_flag') ?? own(answeredBody(state, 'status'), 'stop_flag');
  const scheduledStop =
    row.source === 'process_row' && purpose === 'run' && typeof flag === 'boolean' ? flag : null;
  return {
    purpose,
    label:
      label ?? (purpose === 'process' ? fromServer('name', runner ?? own(raw, 'script')) : null),
    created: finite(own(raw, 'created')) ?? null,
    details: {
      cmdline: fromServer('detail', command),
      instance: fromServer('name', own(context, 'instance')),
      step: fromServer('detail', own(operation, 'step')),
      recovery: fromServer('detail', own(operation, 'recovery_action')),
    },
    battle: rowBattle(state, row),
    scheduledStop,
  };
}

function rowBattle(state: DashboardState, row: OperationRowView): OperationRowVM['battle'] {
  const empty = { wave: null, ageSec: null };
  if (row.seen.line === 'waiting_to_track_battle') return { ...empty, line: 'waiting' };
  const read = latestRead(state, 'battlesActive');
  const body = read?.outcome.kind === 'answered' ? read.outcome.body : undefined;
  const battle = own(body, 'battle');
  if (battle === null) return { ...empty, line: 'none' };
  if (row.operationId === null || own(battle, 'operation_id') !== row.operationId)
    return { ...empty, line: 'unknown' };
  const identity = battleIdentity(battle, read === null ? NaN : read.arrivedAtMs / 1000);
  const shown = identity.kind === 'known' ? identity.value : null;
  const elapsed = read === null ? 0 : readAgeSec(state, read);
  const context = contextReadOf(read);
  const view = battleView(context, shown, elapsed);
  const wave = finite(ownPath(battle, 'runtime', 'observed_wave'));
  if (wave === undefined || wave < 0) return { ...empty, line: 'wave_pending' };
  const report = freshBattleReport(context, shown, elapsed);
  const age =
    report.kind === 'known'
      ? report.value.ageSec
      : view.evidence === 'record'
        ? undefined
        : finite(view.params?.['ageSec']);
  return {
    line: row.seen.word === 'running' && view.evidence === 'EV-9' ? 'wave' : 'last_wave',
    wave,
    ageSec: age === undefined || age < 0 ? null : age,
  };
}

function setupProgress(state: DashboardState): AutomationSlotVM['setup'] {
  const read = latestRead(state, 'calibrate');
  const body = read?.outcome.kind === 'answered' ? read.outcome.body : undefined;
  if (own(body, 'running') !== true) return null;
  const steps = list(ownPath(body, 'state', 'phases', 'bootstrap', 'steps')) ?? [];
  const active = steps.find((step) => own(step, 'status') === 'running');
  return {
    step: fromServer('detail', own(active, 'label') ?? ownPath(body, 'activity', 'message')),
  };
}

function sendingOn(facts: ControlFacts, target: string): boolean {
  return latestOnTarget(facts, target)?.phase === 'sending';
}

export function stopTargetVM(
  state: DashboardState,
  facts: ControlFacts,
  key: string,
  stop: StopControl,
  origin: StopOrigin = 'read',
): StopTargetVM {
  const lastKnown = origin !== 'read';
  switch (stop.kind) {
    case 'cancel': {
      const target = `operation:${stop.operationId}`;
      // S-050: the highest revision any source of this context names for exactly this id.
      const known = highestStopRevision(facts, stop.operationId);
      return {
        key,
        kind: 'cancel',
        shown: true,
        enabled: true,
        reason: null,
        sending: sendingOn(facts, target),
        operation: fromServer('id', stop.operationId),
        pid: null,
        lastKnown,
        origin,
        command: {
          kind: 'cancel',
          operationId: stop.operationId,
          expectedRevision: Math.max(stop.expectedRevision, known ?? stop.expectedRevision),
          epoch: state.epoch,
        },
      };
    }
    case 'kill': {
      const target = `process:${stop.pid.toString()}:${stop.created.toString()}`;
      return {
        key,
        kind: 'kill',
        shown: true,
        enabled: true,
        reason: null,
        sending: sendingOn(facts, target),
        operation: null,
        pid: stop.pid,
        lastKnown,
        origin,
        command: { kind: 'kill', pid: stop.pid, created: stop.created, epoch: state.epoch },
      };
    }
    case 'disabled':
      return {
        key,
        kind: 'disabled',
        shown: true,
        enabled: false,
        reason: stop.reason,
        sending: false,
        operation: stop.operationId === null ? null : fromServer('id', stop.operationId),
        pid: stop.pid,
        lastKnown,
        origin,
        command: null,
      };
    case 'none':
      return {
        key,
        kind: 'none',
        shown: false,
        enabled: false,
        reason: 'stop_it_there',
        sending: false,
        operation: null,
        pid: null,
        lastKnown,
        origin,
        command: null,
      };
  }
}

/** A last known Stop as a control (S-092), with the highest revision known now. */
export function lastKnownStopVM(
  state: DashboardState,
  facts: ControlFacts,
  stop: LastKnownStop,
): StopTargetVM {
  return stopTargetVM(state, facts, stop.key, controlOf(stop), 'last_known');
}

/**
 * INV-3: a Stop for each operation an owner notice of this context named (dismissed or not)
 * whose id is not in `covered`, with the highest revision known for it; waiting for a fresh
 * status when no valid revision is known.
 */
export function noticedStopVMs(
  state: DashboardState,
  facts: ControlFacts,
  covered: ReadonlySet<string>,
): readonly StopTargetVM[] {
  return noticedOperations(state, facts)
    .filter((operationId) => !covered.has(operationId))
    .map((operationId) => {
      const revision = highestStopRevision(facts, operationId);
      return stopTargetVM(
        state,
        facts,
        `operation:${operationId}`,
        revision === null
          ? { kind: 'disabled', reason: 'waiting_for_fresh_status', operationId, pid: null }
          : { kind: 'cancel', operationId, expectedRevision: revision },
        'owner_notice',
      );
    });
}

/** The operation ids a list of Stops already stands for. */
export function operationIds(stops: readonly StopTargetVM[]): ReadonlySet<string> {
  return new Set(
    stops.flatMap((stop) => (stop.command?.kind === 'cancel' ? [stop.command.operationId] : [])),
  );
}

/** True when a current row's working Stop already stands for the last known Stop's target. */
function coveredByRow(rows: readonly OperationRowView[], stop: LastKnownStop): boolean {
  return rows.some((row) => {
    switch (stop.kind) {
      case 'cancel':
        return row.stop.kind === 'cancel' && row.stop.operationId === stop.operationId;
      case 'kill':
        // One pid is one process at a time: a current kill of that pid governs it.
        return row.stop.kind === 'kill' && row.stop.pid === stop.pid;
      case 'disabled':
        // A row of the same operation or process draws its own Stop, working or not.
        return (
          (stop.operationId !== null && row.operationId === stop.operationId) ||
          (stop.pid !== null && row.pid === stop.pid)
        );
    }
  });
}

export function selectAutomationSlot(state: DashboardState): AutomationSlotVM {
  const facts = controlFacts(state);
  const idleAt = state.lastIdleAtMs;
  const view = automationSlot({
    status: facts.status,
    extraClaims: facts.claims,
    lastIdleAgeSec: idleAt === null ? null : Math.max(0, Math.floor((state.nowMs - idleAt) / 1000)),
  });
  const named = new Set<string>();
  const unique = view.rows.filter((row) => {
    if (row.operationId === null) return true;
    if (named.has(row.operationId)) return false;
    named.add(row.operationId);
    return true;
  });
  const rows: OperationRowVM[] = unique.map((row) => {
    const details = rowDetails(state, row);
    return {
      ...details,
      key: row.operationId === null ? row.key : `operation:${row.operationId}`,
      source: row.source,
      operation: row.operationId === null ? null : fromServer('id', row.operationId),
      pid: row.pid,
      seen: chipVM(row.seen),
      stop: stopTargetVM(state, facts, row.key, row.stop),
      clearFlag:
        row.source === 'process_row' &&
        row.operationId !== null &&
        details.scheduledStop === true &&
        row.stop.kind === 'cancel'
          ? {
              operationId: row.operationId,
              expectedRevision: row.stop.expectedRevision,
              epoch: state.epoch,
            }
          : null,
    };
  });
  const retained = state.lastKnownStops
    .filter((stop) => !coveredByRow(view.rows, stop))
    .map((stop) => lastKnownStopVM(state, facts, stop));
  const known = [...rows.map((row) => row.stop).filter((stop) => stop.shown), ...retained];
  const covered = new Set([
    ...operationIds(known),
    ...state.lastKnownStops.flatMap((stop) =>
      stop.kind === 'disabled' && stop.operationId !== null ? [stop.operationId] : [],
    ),
    ...view.rows.flatMap((row) => (row.operationId === null ? [] : [row.operationId])),
  ]);
  return {
    summary: view.summary === null ? null : chipVM(view.summary),
    word: chipVM(automationWord(view)),
    rows,
    stops: [...known, ...noticedStopVMs(state, facts, covered)],
    setup: setupProgress(state),
  };
}
